"""Pruebas del flujo de potencia (backward-forward sweep) con casos verificables."""

import pytest

from ptnt.config.models import FlujoConfig
from ptnt.ref.catalogs import Conductor, ConductorCatalog
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph
from ptnt.powerflow.bfs import run_powerflow


def _catalogo() -> ConductorCatalog:
    return ConductorCatalog({
        "C1": Conductor("C1", "test", "AL", 50, 0.5, 0.3, 200, "BT"),
    })


def _red_una_carga() -> NetworkModel:
    # SRC - N1 (una carga en N1), tramo de 1 km
    edges = [Edge("s1", "SRC", "N1", "C1", 1.0, n_phases=3, voltage_v=400.0, is_lv=True)]
    return NetworkModel("F", "SRC", edges,
                        customer_nodes={"N1": [{"customer_id": "c1", "energy_kwh": 0}]})


@pytest.mark.unit
def test_flujo_converge_sin_carga():
    g = build_radial_graph(_red_una_carga())
    r = run_powerflow(g, {"N1": 0.0}, {"N1": 1.0}, _catalogo(), FlujoConfig())
    assert r.converged
    assert r.loss_kw_peak == pytest.approx(0.0, abs=1e-9)
    assert r.v_min_pu == pytest.approx(1.0, abs=1e-6)


@pytest.mark.unit
def test_flujo_con_carga_produce_perdida_y_caida():
    g = build_radial_graph(_red_una_carga())
    # 10 kW a fp=1 en N1
    r = run_powerflow(
        g, {"N1": 10.0}, {"N1": 1.0}, _catalogo(), FlujoConfig(),
        base_voltage_ln=230.0, t_op=20.0, alpha=0.00403, t_ref=20.0,
    )
    assert r.converged
    assert r.loss_kw_peak > 0.0
    assert r.v_min_pu < 1.0            # hay caída de tensión
    assert r.branch_current_a["s1"] > 0.0


@pytest.mark.unit
def test_flujo_mas_carga_mas_perdida():
    g = build_radial_graph(_red_una_carga())
    cat = _catalogo()
    r1 = run_powerflow(g, {"N1": 5.0}, {"N1": 0.9}, cat, FlujoConfig(), base_voltage_ln=230.0)
    r2 = run_powerflow(g, {"N1": 20.0}, {"N1": 0.9}, cat, FlujoConfig(), base_voltage_ln=230.0)
    assert r2.loss_kw_peak > r1.loss_kw_peak
