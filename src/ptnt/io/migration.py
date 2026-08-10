"""Migración de datos: origen → modelo canónico (§4, §E1–E2).

Lee las tablas de red desde una **base de origen** (vía ``SourceConnector``:
DuckDB/Parquet, SQL Server, PostgreSQL, Oracle, MySQL o CSV) y construye el
``NetworkModel`` canónico, decodificando dominios (fase bitmask, potencia nominal)
y resolviendo la cascada de longitud. Es la puerta de entrada de los datos reales
al pipeline de red.

Para poder validar la migración de punta a punta sin la FGDB real, se incluye la
operación inversa ``network_to_tables`` (canónico → tablas) y ``persist_network``
(escribe esas tablas a la base local), de modo que el ciclo
*red → tablas → migración → red* sea verificable (round-trip).
"""

from __future__ import annotations

import json

import pandas as pd

from ptnt.canonical.decode import parse_transformer_kva, phase_count
from ptnt.config.models import AppConfig, MigracionConfig
from ptnt.io.sources import build_connector
from ptnt.topology.graph import Edge, NetworkModel


class MigrationError(Exception):
    """Error durante la migración de datos."""


# --------------------------------------------------------------------------- #
# Canónico → tablas (para persistencia y round-trip)
# --------------------------------------------------------------------------- #
def network_to_tables(model: NetworkModel) -> dict[str, pd.DataFrame]:
    """Descompone un ``NetworkModel`` en tablas planas (silver)."""

    seg = pd.DataFrame([{
        "segment_id": e.segment_id, "from_node": e.from_node, "to_node": e.to_node,
        "conductor_code": e.conductor_code, "length_km": e.length_km,
        "n_phases": e.n_phases, "voltage_v": e.voltage_v, "is_lv": e.is_lv,
        "feeder_code": model.feeder_code,
    } for e in model.edges])

    puestos = pd.DataFrame([{
        "node": node, "site_id": ts["site_id"],
        "kva": ts.get("kva", (ts.get("kvas") or [None])[0]),
        "kvas": json.dumps(ts.get("kvas", [])),
        "config": ts.get("config", "UNIDAD_SIMPLE"),
        "fases": ts.get("fases", 3),
    } for node, ts in model.transformer_sites.items()])

    clientes = pd.DataFrame([{
        "node": node, "customer_id": c["customer_id"],
        "energy_kwh": c.get("energy_kwh", 0.0), "phase": c.get("phase", 1),
        "tariff": c.get("tariff", ""), "meter_type": c.get("meter_type", "_default"),
    } for node, cls in model.customer_nodes.items() for c in cls])

    lums = pd.DataFrame([{
        "node": node, "streetlight_id": l.get("streetlight_id", ""),
        "lamp_w": l.get("lamp_w", 0.0), "technology": l.get("technology", "_default"),
        "hours": l.get("hours", 12.0), "days_month": l.get("days_month", 30),
        "is_metered": l.get("is_metered", False),
    } for node, cls in model.streetlight_nodes.items() for l in cls])

    return {
        "segments": seg, "transformer_sites": puestos,
        "customer_nodes": clientes, "streetlights": lums,
    }


def persist_network(model: NetworkModel, db) -> None:
    """Escribe las tablas silver del modelo en la base local DuckDB."""

    tablas = network_to_tables(model)
    db._con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    for nombre, df in tablas.items():
        db._con.register("_mig_tmp", df)
        db._con.execute(f"CREATE OR REPLACE TABLE silver.{nombre} AS SELECT * FROM _mig_tmp")
        db._con.unregister("_mig_tmp")


# --------------------------------------------------------------------------- #
# Origen → canónico
# --------------------------------------------------------------------------- #
def _read(connector, tabla: str) -> pd.DataFrame:
    # soporta 'esquema.tabla' o 'tabla'
    if "." in tabla:
        esquema, nombre = tabla.split(".", 1)
        return connector.read_query(f"SELECT * FROM {esquema}.{nombre}")
    return connector.read_table(tabla)


def migrate_network(cfg: AppConfig, feeder_code: str | None = None) -> NetworkModel:
    """Construye el ``NetworkModel`` leyendo desde la fuente configurada en
    ``migracion``. Decodifica dominios y arma la estructura Puesto→Unidad→Cliente."""

    mig: MigracionConfig = cfg.migracion
    fuente = cfg.fuente(mig.fuente)
    connector = build_connector(fuente)
    connector.test_connection()

    try:
        seg = _read(connector, mig.tabla_segmentos)
        puestos = _read(connector, mig.tabla_puestos)
        clientes = _read(connector, mig.tabla_clientes)
        try:
            lums = _read(connector, mig.tabla_luminarias)
        except Exception:
            lums = pd.DataFrame()
    except Exception as exc:
        raise MigrationError(f"Error leyendo tablas de red de '{mig.fuente}': {exc}") from exc
    finally:
        connector.close()

    if seg.empty:
        raise MigrationError("La tabla de segmentos está vacía; no hay red que migrar.")

    fc = feeder_code or (seg["feeder_code"].iloc[0] if "feeder_code" in seg.columns else "F001")
    if "feeder_code" in seg.columns and feeder_code:
        seg = seg[seg["feeder_code"] == feeder_code]

    # Fuente: nodo que no aparece como destino de ningún tramo
    froms = set(seg["from_node"]); tos = set(seg["to_node"])
    raices = froms - tos
    source = sorted(raices)[0] if raices else seg["from_node"].iloc[0]

    edges = [
        Edge(
            segment_id=str(r["segment_id"]), from_node=str(r["from_node"]),
            to_node=str(r["to_node"]), conductor_code=str(r.get("conductor_code", "") or ""),
            length_km=float(r.get("length_km", 0) or 0),
            n_phases=int(r.get("n_phases", 3) or 3),
            voltage_v=float(r.get("voltage_v", 13800) or 13800),
            is_lv=bool(r.get("is_lv", False)),
        )
        for _, r in seg.iterrows()
    ]

    transformer_sites: dict[str, dict] = {}
    for _, r in puestos.iterrows():
        kvas = _parse_kvas(r.get("kvas"))
        kva = r.get("kva")
        kva = float(kva) if kva is not None and pd.notna(kva) else (kvas[0] if kvas else None)
        transformer_sites[str(r["node"])] = {
            "site_id": str(r["site_id"]),
            "kvas": kvas or ([kva] if kva else []),
            "kva": kva,
            "config": str(r.get("config", "UNIDAD_SIMPLE")),
            "fases": int(r.get("fases", 3) or 3),
        }

    customer_nodes: dict[str, list[dict]] = {}
    for _, r in clientes.iterrows():
        node = str(r["node"])
        customer_nodes.setdefault(node, []).append({
            "customer_id": str(r["customer_id"]),
            "energy_kwh": float(r.get("energy_kwh", 0) or 0),
            "phase": int(r.get("phase", 1) or 1),
            "tariff": str(r.get("tariff", "")),
            "meter_type": str(r.get("meter_type", "_default")),
        })

    streetlight_nodes: dict[str, list[dict]] = {}
    if not lums.empty:
        for _, r in lums.iterrows():
            node = str(r["node"])
            streetlight_nodes.setdefault(node, []).append({
                "streetlight_id": str(r.get("streetlight_id", "")),
                "lamp_w": float(r.get("lamp_w", 0) or 0),
                "technology": str(r.get("technology", "_default")),
                "hours": float(r.get("hours", 12) or 12),
                "days_month": int(r.get("days_month", 30) or 30),
                "is_metered": bool(r.get("is_metered", False)),
            })

    return NetworkModel(
        feeder_code=str(fc), source_node=str(source), edges=edges,
        transformer_sites=transformer_sites, customer_nodes=customer_nodes,
        streetlight_nodes=streetlight_nodes,
    )


def _parse_kvas(value) -> list[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [float(v) for v in value]
    try:
        data = json.loads(value)
        return [float(v) for v in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        # puede venir como "15KVA" (dominio) — parsear
        kva = parse_transformer_kva(str(value))
        return [kva] if kva else []
