"""Recolección de estadísticas por nivel para alimentar la focalización.

Traduce los resultados del pipeline de red (grafo, zonas BT, balance) y del
pipeline comercial (ranking de clientes) a las estructuras que consume
``build_survey_plan``.
"""

from __future__ import annotations

import pandas as pd

from ptnt.ntl.network_signals import n1_zone_balance_residual
from ptnt.topology.graph import NetworkModel, RadialGraph


def bind_commercial_customers(
    model: NetworkModel, cuentas: list[str], coords: pd.DataFrame | None = None
) -> dict[str, str]:
    """Vincula cuentas contrato reales a los nodos de cliente de la red.

    En producción la vinculación es por ``CUENTACONTRATO`` → ``CONEXIONCONSUMIDOR``
    → ``PuntoCarga`` (§E3.3). Para el análisis integrado sobre red sintética (o
    cuando la vinculación aún no está resuelta), asigna las cuentas del padrón
    comercial a los nodos de cliente en orden estable, de modo que las señales de
    comportamiento del cliente puedan agregarse a ramal, puesto y sector.

    Devuelve ``{customer_id_red: cuenta_contrato}`` y **modifica el modelo** para
    que el ``customer_id`` sea la cuenta contrato (clave común de todo el sistema).
    """

    mapping: dict[str, str] = {}
    coord_map: dict[str, tuple[float, float]] = {}
    if coords is not None and not coords.empty and {"x", "y"}.issubset(coords.columns):
        for _, r in coords.iterrows():
            coord_map[str(r["contract_account"])] = (r.get("x"), r.get("y"))

    i = 0
    for _node, clientes in model.customer_nodes.items():
        for c in clientes:
            if i >= len(cuentas):
                return mapping
            cuenta = str(cuentas[i])
            mapping[str(c.get("customer_id", ""))] = cuenta
            c["contract_account"] = cuenta
            c["customer_id"] = cuenta
            if cuenta in coord_map:
                c["x"], c["y"] = coord_map[cuenta]
            i += 1
    return mapping


def collect_branch_stats(
    graph: RadialGraph,
    *,
    suspect_customers: set[str] | None = None,
    recoverable_by_customer: dict[str, float] | None = None,
    min_nodes: int = 2,
    min_customers: int = 3,
) -> list[dict]:
    """Estadísticas por **ramal**: clientes, km de red, sospechosos y energía.

    Un ramal es la unidad que una cuadrilla recorre como tramo de calle (§E4.4).
    Las derivaciones de **un solo cliente** son acometidas, no ramales: se excluyen
    con ``min_customers`` porque no son un objetivo de recorrido (ese cliente ya
    aparece en el nivel CLIENTE).
    """

    suspect_customers = suspect_customers or set()
    recoverable_by_customer = recoverable_by_customer or {}
    ramales = graph.branch_decomposition(min_nodes=min_nodes)
    model = graph.model
    filas: list[dict] = []

    for bid, nodos in ramales.items():
        clientes = []
        for n in nodos:
            clientes.extend(model.customer_nodes.get(n, []))
        if len(clientes) < max(1, min_customers):
            continue
        ids = [str(c.get("customer_id", "")) for c in clientes]
        sospechosos = sum(1 for i in ids if i in suspect_customers)
        energia = sum(float(c.get("energy_kwh", 0) or 0) for c in clientes)
        recuperable = sum(recoverable_by_customer.get(i, 0.0) for i in ids)
        filas.append({
            "branch_id": bid,
            "feeder_code": model.feeder_code,
            "customers": len(clientes),
            "suspect_customers": sospechosos,
            "network_km": graph.total_length_km(nodos),
            "energy_kwh": energia,
            "recoverable_kwh": recuperable,
        })
    return filas


def collect_transformer_stats(
    graph: RadialGraph,
    lv_zones: dict,
    transformer_loading: pd.DataFrame | None = None,
    *,
    suspect_customers: set[str] | None = None,
    recoverable_by_customer: dict[str, float] | None = None,
) -> list[dict]:
    """Estadísticas por **puesto de transformación** para la focalización."""

    suspect_customers = suspect_customers or set()
    recoverable_by_customer = recoverable_by_customer or {}
    model = graph.model
    carga_por_site: dict[str, float] = {}
    if transformer_loading is not None and not transformer_loading.empty:
        for _, r in transformer_loading.iterrows():
            carga_por_site[str(r.get("site_id"))] = float(r.get("loading_ratio", 0) or 0)

    # clientes por puesto según la traza (primer puesto aguas arriba)
    asignacion = graph.assign_customers_to_transformers()
    clientes_por_site: dict[str, list[str]] = {}
    for cust_id, site in asignacion.items():
        if site:
            clientes_por_site.setdefault(site, []).append(str(cust_id))

    filas: list[dict] = []
    nodo_por_site = {ts["site_id"]: n for n, ts in model.transformer_sites.items()}
    for site_id, zona in lv_zones.items():
        ids = clientes_por_site.get(site_id, [])
        sospechosos = sum(1 for i in ids if i in suspect_customers)
        recuperable = sum(recoverable_by_customer.get(i, 0.0) for i in ids)
        nodo = nodo_por_site.get(site_id)
        filas.append({
            "site_id": site_id,
            "feeder_code": model.feeder_code,
            "customers": int(zona.get("customers", len(ids))),
            "suspect_customers": sospechosos,
            "energy_kwh": float(zona.get("billed_kwh", 0) or 0),
            "recoverable_kwh": recuperable,
            "totalizer_residual_kwh": float(zona.get("totalizer_residual_kwh", 0) or 0),
            "loading_ratio": carga_por_site.get(site_id, 0.5),
            "network_km": graph.total_length_km(graph.trace_downstream(nodo)) if nodo else 0.0,
        })
    return filas


def collect_zone_stats(
    graph: RadialGraph,
    *,
    e_input_by_zone: dict[str, float] | None = None,
    loss_tech_by_zone: dict[str, float] | None = None,
) -> list[dict]:
    """Estadísticas por **zona de protección** (residuo de balance, señal N1)."""

    model = graph.model
    switches = set(model.switch_nodes.keys())
    if not switches:
        return []
    zonas = graph.protection_zones(switches)
    e_input_by_zone = e_input_by_zone or {}
    loss_tech_by_zone = loss_tech_by_zone or {}

    filas: list[dict] = []
    for zid, nodos in zonas.items():
        clientes = []
        for n in nodos:
            clientes.extend(model.customer_nodes.get(n, []))
        if not clientes:
            continue
        facturado = sum(float(c.get("energy_kwh", 0) or 0) for c in clientes)
        entrada = e_input_by_zone.get(zid, facturado * 1.08)  # sin medición: estimada
        tecnicas = loss_tech_by_zone.get(zid, facturado * 0.04)
        sig = n1_zone_balance_residual(zid, entrada, facturado, tecnicas)
        filas.append({
            "zone_id": zid,
            "feeder_code": model.feeder_code,
            "customers": len(clientes),
            "residual_kwh": float(sig.evidence.get("residuo_kwh", 0)),
            "signal_value": sig.value,
            "network_km": graph.total_length_km(nodos),
            "confidence_index": 100.0,
        })
    return filas
