"""Agregación de baja tensión al transformador (modelo LV open-source).

En el modelo de datos, cada elemento aguas abajo (cliente, red BT, luminaria,
semáforo/cámara) queda vinculado a su fuente mediante ``PARENTCIRCUITSOURCEGUID``,
que apunta al ``CIRCUITSOURCEGUID`` del transformador/cabecera que lo energiza.
Esto permite, para el análisis de BT, **sumar todos los elementos al
transformador** sin necesidad de un flujo trifásico que cruce el salto de tensión
MT→BT: cada puesto de transformación define una **zona de baja tensión** lumped.

Reglas de negocio implementadas:

* **Totalizador (§7.5):** si un punto de carga tiene un medidor ``TOTALIZADOR``,
  su energía representa el edificio completo; los medidores individuales bajo él
  **no se vuelven a sumar** (evita doble conteo). La diferencia
  ``totalizador − Σ individuales`` es la señal de hurto más limpia (N3).
* **Semáforos y cámaras:** por regulación son alumbrado público **no medido** y se
  agregan al AP de la zona, no a la energía facturada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ptnt.ref.structure_catalog import StructureCatalog
from ptnt.topology.graph import RadialGraph


@dataclass
class LVZone:
    """Zona de baja tensión agregada a un puesto de transformación."""

    site_id: str
    node: str
    kva_installed: float
    customers_count: int
    energy_billed_kwh: float
    energy_ap_unmetered_kwh: float     # luminarias + semáforos/cámaras
    energy_totalizer_kwh: float        # suma de totalizadores del puesto
    energy_individuals_under_totalizer_kwh: float
    traffic_camera_count: int = 0
    streetlight_count: int = 0
    totalizer_residual_kwh: float = 0.0   # totalizador − Σ individuales (señal N3)
    detail: dict = field(default_factory=dict)


def _streetlight_energy_kwh(item: dict, catalog: StructureCatalog | None) -> float:
    """Energía mensual de una luminaria/semáforo desde su código de estructura o
    campos directos (P_lámpara + P_balastro)·horas·días/1000."""

    hours = float(item.get("hours", 12.0) or 12.0)
    days = float(item.get("days_month", 30) or 30)
    code = item.get("structure_code")
    lamp = float(item.get("lamp_w", 0.0) or 0.0)
    aux = 0.0
    if catalog and code and code in catalog:
        lamp = catalog.lamp_power_w(code) or lamp
        aux = catalog.ballast_loss_w(code)
    else:
        aux = float(item.get("aux_w", 0.0) or 0.0)
    return (lamp + aux) * hours * days / 1000.0


def _nearest_upstream_transformer(graph: RadialGraph, node: str) -> str | None:
    """Devuelve el site_id del primer puesto de transformación aguas arriba de
    ``node`` (incluyéndolo). ``None`` si no hay ninguno en el camino a la fuente."""

    sites = graph.model.transformer_sites
    try:
        camino = graph.trace_upstream(node)
    except Exception:
        return None
    for n in camino:
        if n in sites:
            return sites[n]["site_id"]
    return None


def aggregate_to_transformers(
    graph: RadialGraph, catalog: StructureCatalog | None = None
) -> dict[str, LVZone]:
    """Agrega los elementos de BT a **su** puesto de transformación.

    Cada elemento (cliente, luminaria, semáforo/cámara) se asigna al **primer
    puesto aguas arriba** (no a todo el subárbol), de modo que en un tronco con
    varios transformadores en serie no haya doble conteo entre zonas. Respeta el
    TOTALIZADOR (los individuales bajo él no se re-suman).
    """

    model = graph.model
    # Inicializa una zona por puesto
    acc: dict[str, dict] = {}
    node_by_site: dict[str, str] = {}
    for node, ts in model.transformer_sites.items():
        sid = ts["site_id"]
        node_by_site[sid] = node
        acc[sid] = dict(
            kva=float(ts.get("kva", (ts.get("kvas") or [0])[0] or 0)),
            billed=0.0, totalizer=0.0, individuals_under=0.0, cust=0,
            ap=0.0, sl=0, tc=0,
        )

    # Clientes -> transformador aguas arriba
    for n, clientes in model.customer_nodes.items():
        sid = _nearest_upstream_transformer(graph, n)
        if sid is None or sid not in acc:
            continue
        z = acc[sid]
        for c in clientes:
            e = float(c.get("energy_kwh", 0.0) or 0.0)
            z["cust"] += 1
            if c.get("totalizador"):
                z["totalizer"] += e
                z["billed"] += e
            elif c.get("is_under_totalizer"):
                z["individuals_under"] += e   # NO se suma a billed
            else:
                z["billed"] += e

    # Luminarias -> transformador aguas arriba (AP no medido)
    for n, lums in model.streetlight_nodes.items():
        sid = _nearest_upstream_transformer(graph, n)
        if sid is None or sid not in acc:
            continue
        for l in lums:
            if not l.get("is_metered"):
                acc[sid]["ap"] += _streetlight_energy_kwh(l, catalog)
                acc[sid]["sl"] += 1

    # Semáforos / cámaras -> transformador aguas arriba (AP no medido por regulación)
    for n, tls in model.traffic_light_nodes.items():
        sid = _nearest_upstream_transformer(graph, n)
        if sid is None or sid not in acc:
            continue
        for s in tls:
            acc[sid]["ap"] += _streetlight_energy_kwh(s, catalog)
            acc[sid]["tc"] += 1

    zones: dict[str, LVZone] = {}
    for sid, z in acc.items():
        zones[sid] = LVZone(
            site_id=sid, node=node_by_site[sid], kva_installed=z["kva"],
            customers_count=z["cust"], energy_billed_kwh=z["billed"],
            energy_ap_unmetered_kwh=z["ap"], energy_totalizer_kwh=z["totalizer"],
            energy_individuals_under_totalizer_kwh=z["individuals_under"],
            traffic_camera_count=z["tc"], streetlight_count=z["sl"],
            totalizer_residual_kwh=(z["totalizer"] - z["individuals_under"]) if z["totalizer"] > 0 else 0.0,
        )
    return zones
