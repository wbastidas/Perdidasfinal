"""Pruebas de las capacidades avanzadas: Monte Carlo, reglas de calidad,
exportador OpenDSS, validación del flujo, señales de red y exportadores."""

import pandas as pd
import pytest

from ptnt.config.loader import load_config
from ptnt.losses.montecarlo import montecarlo_losses, montecarlo_ntl
from ptnt.ntl.network_signals import (
    n1_zone_balance_residual,
    n3_totalizer_balance,
    n4_loading_incoherence,
)
from ptnt.powerflow.opendss_export import export_to_dss
from ptnt.powerflow.validation import run_validation_suite
from ptnt.quality.rules import run_quality_rules
from ptnt.ref.catalogs import Conductor, ConductorCatalog, load_conductor_catalog
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph


# --------------------------------------------------------------------------- #
# Monte Carlo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_montecarlo_percentiles_ordenados():
    d = montecarlo_losses(100, 500, 400, 20, iterations=500, seed=1)
    assert d.p10 <= d.p50 <= d.p90
    assert d.mean > 0


@pytest.mark.unit
def test_montecarlo_ntl_refleja_incertidumbre_tecnica():
    d = montecarlo_losses(100, 500, 400, 20, iterations=500, seed=2)
    ntl = montecarlo_ntl(10000, 8500, 200, 10, 0, d, iterations=500, seed=3)
    assert ntl.p10 <= ntl.p90
    # PNT = total - técnica; a más técnica, menos PNT -> banda invertida coherente
    assert ntl.p50 == pytest.approx(10000 - 8500 - 200 - 10 - d.p50, abs=d.std * 4)


# --------------------------------------------------------------------------- #
# Validación del flujo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_validacion_flujo_pasa():
    casos = run_validation_suite(tol_pct=2.0)
    assert casos
    for c in casos:
        assert c.passed, f"{c.name} error {c.error_pct:.2f}%"


# --------------------------------------------------------------------------- #
# Reglas de calidad
# --------------------------------------------------------------------------- #
def _red_con_errores() -> NetworkModel:
    edges = [
        Edge("s1", "SRC", "A", "COO0004", 0.3),        # ok
        Edge("s2", "A", "B", "NOEXISTE", 0.3),          # R05: conductor ausente
        Edge("s3", "B", "C", "", 0.3),                  # R12: sin conductor
        Edge("s4", "A", "D", "COO0004", 0.0),           # R11: longitud nula
        Edge("s5", "X", "Y", "COO0004", 0.3),           # R22: isla
    ]
    return NetworkModel("F", "SRC", edges,
                        transformer_sites={"B": {"site_id": "TSx", "kvas": [],
                                                 "config": "UNIDAD_SIMPLE"}})


@pytest.mark.unit
def test_reglas_calidad_detectan_errores():
    cfg = load_config("config/base.yaml")
    cond = load_conductor_catalog(cfg.catalogos.conductores)
    g = build_radial_graph(_red_con_errores())
    rep = run_quality_rules(g, cond, cfg)
    reglas = rep.by_rule()
    assert reglas.get("R05", 0) >= 1     # conductor ausente
    assert reglas.get("R12", 0) >= 1     # sin conductor
    assert reglas.get("R11", 0) >= 1     # longitud nula
    assert reglas.get("R22", 0) >= 1     # isla
    assert reglas.get("R24", 0) >= 1     # puesto sin unidades


@pytest.mark.unit
def test_reglas_calidad_red_limpia_sin_hallazgos():
    from ptnt.synth.network import generate_radial_network

    cfg = load_config("config/base.yaml")
    cond = load_conductor_catalog(cfg.catalogos.conductores)
    net = generate_radial_network(n_transformers=4, customers_per_tx=8)
    g = build_radial_graph(net.model)
    rep = run_quality_rules(g, cond, cfg)
    # la red sintética limpia no debe disparar R05/R12/R22
    reglas = rep.by_rule()
    assert reglas.get("R05", 0) == 0
    assert reglas.get("R12", 0) == 0
    assert reglas.get("R22", 0) == 0


@pytest.mark.unit
def test_regla_r15_discrepancia_asignacion():
    cfg = load_config("config/base.yaml")
    cond = load_conductor_catalog(cfg.catalogos.conductores)
    edges = [
        Edge("s1", "SRC", "TXA", "COO0004", 0.3),
        Edge("s2", "TXA", "cnode", "COO0050", 0.05, is_lv=True),
    ]
    model = NetworkModel("F", "SRC", edges,
                         transformer_sites={"TXA": {"site_id": "TS_A", "kvas": [50],
                                                    "config": "UNIDAD_SIMPLE"}},
                         customer_nodes={"cnode": [{"customer_id": "c1", "energy_kwh": 100}]})
    g = build_radial_graph(model)
    # declarado dice TS_OTRO pero la traza da TS_A
    rep = run_quality_rules(g, cond, cfg, declared_assignment={"c1": "TS_OTRO"})
    assert rep.by_rule().get("R15", 0) == 1


# --------------------------------------------------------------------------- #
# OpenDSS export
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_opendss_export_estructura():
    from ptnt.synth.network import generate_radial_network

    cond = ConductorCatalog({
        "COO0004": Conductor("COO0004", "x", "AL", 100, 0.174, 0.34, 340, "MT"),
        "COO0050": Conductor("COO0050", "y", "AL", 33, 0.856, 0.10, 130, "BT"),
    })
    net = generate_radial_network(n_transformers=2, customers_per_tx=3)
    dss = export_to_dss(net.model, cond)
    assert "New Circuit." in dss
    assert "New LineCode." in dss
    assert "New Line." in dss
    assert "New Transformer." in dss
    assert "Solve" in dss


# --------------------------------------------------------------------------- #
# Señales de red
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_n1_residuo_zona():
    s = n1_zone_balance_residual("Z1", 1000, 700, 100)
    # residuo = 1000 - 700 - 100 = 200 ; intensidad = 200/1000 = 0.2
    assert s.value == pytest.approx(0.2)


@pytest.mark.unit
def test_n3_totalizador_es_la_mas_limpia():
    s = n3_totalizer_balance("P1", 1000, 800)
    assert s.value == pytest.approx(0.2)
    assert s.confidence >= 0.9  # la señal más limpia


@pytest.mark.unit
def test_n4_cargabilidad_incoherente():
    s = n4_loading_incoherence("P1", 0.05)  # muy subutilizado
    assert s.value > 0
    s2 = n4_loading_incoherence("P2", 0.5)  # adecuado
    assert s2.value == 0.0


# --------------------------------------------------------------------------- #
# Exportadores
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_reporte_ejecutivo_html(tmp_path):
    from ptnt.io.exporters import write_executive_report

    balance = {
        "balance_type": "MEDIDO", "e_input_kwh": 1000, "e_billed_kwh": 850,
        "loss_technical_kwh": 100, "ntl_kwh": 50, "ntl_pct": 5.0,
        "loss_components": {"RED_CONDUCTORES": 30, "TRANSF_DIST_TOTAL": 70},
        "metrics": {"ntl_p10": 30, "ntl_p90": 70},
    }
    ruta = write_executive_report("F001", balance, str(tmp_path / "rep.html"))
    html = open(ruta, encoding="utf-8").read()
    assert "F001" in html
    assert "PNT" in html
    assert "MEDIDO" in html
