"""Pruebas del motor trifásico con neutro, la migración de datos y los decodificadores."""

import numpy as np
import pytest

from ptnt.canonical.decode import (
    decode_phase_designation,
    length_cascade,
    parse_transformer_kva,
    phase_count,
)
from ptnt.config.models import FlujoConfig
from ptnt.powerflow.bfs3ph import run_powerflow_3ph
from ptnt.ref.catalogs import Conductor, ConductorCatalog
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph


# --------------------------------------------------------------------------- #
# Decodificadores de dominio
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("val,esperado", [
    (4, "A"), (2, "B"), (1, "C"),
    (3, "BC"), (5, "AC"), (6, "AB"), (7, "ABC"),
])
def test_decode_phase_bitmask(val, esperado):
    assert decode_phase_designation(val) == esperado


@pytest.mark.unit
def test_phase_count():
    assert phase_count(7) == 3
    assert phase_count(4) == 1
    assert phase_count(6) == 2


@pytest.mark.unit
@pytest.mark.parametrize("raw,esperado", [
    ("15KVA", 15.0), ("37.5KVA", 37.5), ("112.5 KVA", 112.5), ("50", 50.0),
])
def test_parse_transformer_kva(raw, esperado):
    assert parse_transformer_kva(raw) == pytest.approx(esperado)


@pytest.mark.unit
def test_parse_transformer_kva_dominio_cercano():
    # 40 no está en el dominio -> devuelve el más cercano (37.5)
    assert parse_transformer_kva("40KVA", dominio=[25, 37.5, 50]) == 37.5


@pytest.mark.unit
def test_length_cascade():
    # LONGITUDCAMPO dentro de tolerancia -> MEDIDO
    v, origen = length_cascade(1.05, None, 1.0)
    assert origen == "MEDIDO" and v == 1.05
    # fuera de tolerancia -> SHAPE_Length / INFERIDO
    v, origen = length_cascade(5.0, None, 1.0)
    assert origen == "INFERIDO_TOPOLOGIA" and v == 1.0
    # sin campo pero sistema válido -> CATALOGO
    v, origen = length_cascade(None, 0.98, 1.0)
    assert origen == "CATALOGO"


# --------------------------------------------------------------------------- #
# Motor trifásico con neutro
# --------------------------------------------------------------------------- #
def _red_1tramo() -> NetworkModel:
    return NetworkModel(
        "F", "SRC",
        [Edge("s1", "SRC", "N1", "C1", 1.0, n_phases=3, voltage_v=13800.0)],
        customer_nodes={"N1": [{"customer_id": "c1", "energy_kwh": 0}]},
    )


def _cat() -> ConductorCatalog:
    return ConductorCatalog({"C1": Conductor("C1", "x", "AL", 50, 0.3, 0.3, 1e6, "MT")})


@pytest.mark.unit
def test_3ph_balanceado_neutro_cero():
    g = build_radial_graph(_red_1tramo())
    vln = 13800.0 / 3**0.5
    r = run_powerflow_3ph(
        g, {"N1": np.array([50., 50., 50.])}, {"N1": 1.0}, _cat(),
        FlujoConfig(max_iteraciones=100), base_voltage_ln=vln, t_op=20.0,
    )
    assert r.converged
    assert r.neutral_current_a["s1"] < 1e-6      # neutro ~0 en balanceado
    assert r.loss_neutral_kw == pytest.approx(0.0, abs=1e-6)
    assert r.imbalance_pct_max == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_3ph_desbalanceado_produce_neutro():
    g = build_radial_graph(_red_1tramo())
    vln = 13800.0 / 3**0.5
    r = run_powerflow_3ph(
        g, {"N1": np.array([60., 30., 10.])}, {"N1": 1.0}, _cat(),
        FlujoConfig(max_iteraciones=100), base_voltage_ln=vln, t_op=20.0,
    )
    assert r.converged
    assert r.neutral_current_a["s1"] > 0.0       # neutro conduce
    assert r.loss_neutral_kw > 0.0
    assert r.imbalance_pct_max > 0.0


@pytest.mark.unit
def test_3ph_balanceado_similar_a_monofasico_equivalente():
    """Con carga balanceada, la pérdida del motor 3F (fases) debe aproximar la del
    monofásico equivalente con la misma potencia total."""

    from ptnt.powerflow.bfs import run_powerflow

    g = build_radial_graph(_red_1tramo())
    vln = 13800.0 / 3**0.5
    total_kw = 150.0
    r3 = run_powerflow_3ph(
        g, {"N1": np.array([50., 50., 50.])}, {"N1": 1.0}, _cat(),
        FlujoConfig(max_iteraciones=100), base_voltage_ln=vln, t_op=20.0,
    )
    r1 = run_powerflow(
        g, {"N1": total_kw}, {"N1": 1.0}, _cat(),
        FlujoConfig(max_iteraciones=100), base_voltage_ln=vln, t_op=20.0,
    )
    assert r3.metrics["loss_phases_kw"] == pytest.approx(r1.loss_kw_peak, rel=0.02)


# --------------------------------------------------------------------------- #
# Migración de datos (round-trip)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_migracion_round_trip():
    from ptnt.io.migration import migrate_network, network_to_tables
    from ptnt.synth.network import generate_radial_network

    net = generate_radial_network(n_transformers=4, customers_per_tx=6)
    tablas = network_to_tables(net.model)
    assert set(tablas) == {"segments", "transformer_sites", "customer_nodes", "streetlights"}
    assert len(tablas["segments"]) == len(net.model.edges)
    # la tabla de clientes tiene una fila por cliente
    n_cli = sum(len(v) for v in net.model.customer_nodes.values())
    assert len(tablas["customer_nodes"]) == n_cli


@pytest.mark.unit
def test_migracion_desde_duckdb(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from ptnt.config.loader import load_config
    from ptnt.io.migration import migrate_network, persist_network
    from ptnt.store.database import Database
    from ptnt.synth.network import generate_radial_network

    cfg = load_config("config/base.yaml")
    cfg.rutas.duckdb = str(tmp_path / "ptnt.duckdb")
    # apuntar la migración a la base local recién creada
    for f in cfg.fuentes:
        if f.nombre == "resultados_local":
            f.ruta = cfg.rutas.duckdb

    net = generate_radial_network(n_transformers=5, customers_per_tx=8)
    with Database(cfg.rutas.duckdb) as db:
        persist_network(net.model, db)

    migrado = migrate_network(cfg)
    assert len(migrado.edges) == len(net.model.edges)
    assert len(migrado.transformer_sites) == len(net.model.transformer_sites)
    assert migrado.source_node == net.model.source_node
