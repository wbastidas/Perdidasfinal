"""Exportador OpenDSS (§8.7).

Genera un script ``.dss`` completo a partir del ``NetworkModel``: ``Circuit``,
``LineCode`` (por conductor), ``Line`` (por tramo), ``Transformer`` (por puesto) y
``Load`` (por nodo de cliente). Permite validar el motor propio contra OpenDSS y
usar OpenDSS en niveles de detalle.

No requiere OpenDSS instalado para *generar* el script; solo para ejecutarlo.
"""

from __future__ import annotations

from ptnt.ref.catalogs import ConductorCatalog
from ptnt.topology.graph import NetworkModel


def export_to_dss(
    model: NetworkModel,
    conductors: ConductorCatalog,
    *,
    base_kv_mt: float = 13.8,
    base_kv_bt: float = 0.22,
) -> str:
    """Devuelve el contenido del script ``.dss`` como texto."""

    lines: list[str] = []
    lines.append(f"Clear")
    lines.append(
        f"New Circuit.{_id(model.feeder_code)} basekv={base_kv_mt} "
        f"bus1={_bus(model.source_node)} phases=3 pu=1.0"
    )
    lines.append("")

    # LineCodes por conductor usado
    usados = {e.conductor_code for e in model.edges if e.conductor_code}
    lines.append("! --- LineCodes (por conductor) ---")
    for code in sorted(usados):
        cond = conductors.get_or_none(code)
        if cond is None:
            continue
        lines.append(
            f"New LineCode.{_id(code)} nphases=3 R1={cond.r_ohm_km_20c} "
            f"X1={cond.x_ohm_km} Units=km normamps={cond.ampacidad_a}"
        )
    lines.append("")

    # Lines (tramos)
    lines.append("! --- Lines (tramos) ---")
    for e in model.edges:
        if e.conductor_code not in usados:
            continue
        lines.append(
            f"New Line.{_id(e.segment_id)} bus1={_bus(e.from_node)} "
            f"bus2={_bus(e.to_node)} phases={e.n_phases} "
            f"linecode={_id(e.conductor_code)} length={e.length_km} units=km"
        )
    lines.append("")

    # Transformadores por puesto
    lines.append("! --- Transformers (puestos) ---")
    for node, ts in model.transformer_sites.items():
        kva = ts.get("kva", ts["kvas"][0] if ts.get("kvas") else 50)
        site = _id(ts.get("site_id", node))
        lv_bus = _bus(f"LV_{node}")
        lines.append(
            f"New Transformer.{site} phases=3 windings=2 "
            f"buses=({_bus(node)}, {lv_bus}) "
            f"conns=(wye, wye) kvs=({base_kv_mt}, {base_kv_bt}) "
            f"kvas=({kva}, {kva}) %loadloss=1.0"
        )
    lines.append("")

    # Loads (clientes agregados por nodo)
    lines.append("! --- Loads (clientes) ---")
    for node, clientes in model.customer_nodes.items():
        kw = sum(c.get("energy_kwh", 0.0) for c in clientes) / (30 * 24)
        if kw <= 0:
            continue
        lines.append(
            f"New Load.L_{_id(node)} bus1={_bus(node)} phases=1 "
            f"kV={base_kv_bt} kW={kw:.4f} pf=0.92 model=1"
        )
    lines.append("")

    lines.append("Set voltagebases=[{:.3f}, {:.3f}]".format(base_kv_mt, base_kv_bt))
    lines.append("Calcvoltagebases")
    lines.append("Solve")
    lines.append("")
    return "\n".join(lines)


def write_dss(model: NetworkModel, conductors: ConductorCatalog, ruta: str, **kw) -> str:
    """Escribe el script ``.dss`` en disco y devuelve la ruta."""

    contenido = export_to_dss(model, conductors, **kw)
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(contenido)
    return ruta


def _id(s: str) -> str:
    """Normaliza un identificador para OpenDSS (sin espacios ni caracteres raros)."""

    return str(s).replace(" ", "_").replace(".", "_").replace("-", "_")


def _bus(node: str) -> str:
    return _id(node)
