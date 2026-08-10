"""Prueba de integración del pipeline de red (E4–E10) sobre red sintética."""

import pytest

from ptnt.config.loader import load_config
from ptnt.grid_pipeline import run_grid_analysis
from ptnt.synth.network import generate_radial_network


@pytest.mark.integration
def test_grid_pipeline_extremo_a_extremo():
    cfg = load_config("config/base.yaml")
    net = generate_radial_network(n_transformers=6, customers_per_tx=15, ntl_fraction=0.10)
    res = run_grid_analysis(net.model, cfg, head_energy_kwh=net.head_energy_kwh)

    # el flujo converge
    assert res.powerflow_converged
    assert 0.9 <= res.v_min_pu <= 1.0

    # el balance es MEDIDO (hay cabecera) y cierra la ecuación
    assert res.balance.balance_type.value == "MEDIDO"
    # pérdidas totales = entrada - facturado - AP - propios - ENS
    b = res.balance
    esperado = (
        b.e_input_kwh - b.e_billed_kwh - b.e_streetlight_unmetered_kwh
        - b.e_own_use_kwh - b.e_not_supplied_kwh
    )
    assert b.loss_total_kwh == pytest.approx(esperado, rel=1e-6)
    # PNT = pérdidas totales - técnicas
    assert b.ntl_kwh == pytest.approx(b.loss_total_kwh - b.loss_technical_kwh, rel=1e-6)


@pytest.mark.integration
def test_grid_perdida_vacio_presente_en_subutilizados():
    """En una red de transformadores subutilizados, la pérdida en vacío es una
    fracción dominante de la pérdida de transformación (P0 constante)."""

    cfg = load_config("config/base.yaml")
    net = generate_radial_network(n_transformers=6, customers_per_tx=12)
    res = run_grid_analysis(net.model, cfg, head_energy_kwh=net.head_energy_kwh)
    comp = res.loss_components_kwh
    assert comp["TRANSF_DIST_VACIO"] > 0
    # vacío es una parte grande del total de transformación en red subutilizada
    assert comp["TRANSF_DIST_VACIO"] <= comp["TRANSF_DIST_TOTAL"]
    assert comp["TRANSF_DIST_VACIO"] / comp["TRANSF_DIST_TOTAL"] > 0.5


@pytest.mark.integration
def test_grid_sin_cabecera_es_indicativo():
    cfg = load_config("config/base.yaml")
    net = generate_radial_network(n_transformers=4, customers_per_tx=10)
    res = run_grid_analysis(net.model, cfg, head_energy_kwh=None)
    assert res.balance.balance_type.value == "INDICATIVO"
