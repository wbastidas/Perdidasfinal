"""Pruebas de balance (§10), alumbrado (§9) y cargabilidad/desbalance (§11)."""

import math

import pandas as pd
import pytest

from ptnt.config.models import AlumbradoConfig, BalanceConfig, CargabilidadConfig
from ptnt.balance.energy_balance import BalanceType, compute_balance
from ptnt.grid.loadability import (
    LoadabilityClass,
    classify_loadability,
    phase_imbalance_pct,
)
from ptnt.lighting.streetlight import compute_streetlight_energy, luminaire_energy_kwh


# --------------------------------------------------------------------------- #
# Balance
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_balance_medido_cierra():
    cfg = BalanceConfig()
    # entrada 1000, facturado 850, AP 20, propios 10, ENS 0, técnicas 100
    r = compute_balance(
        "F001", cfg=cfg, e_billed_kwh=850, loss_technical_kwh=100,
        e_input_kwh=1000, e_streetlight_unmetered_kwh=20, e_own_use_kwh=10,
        e_not_supplied_kwh=0,
    )
    assert r.balance_type == BalanceType.MEDIDO
    # pérdidas totales = 1000 - 850 - 20 - 10 = 120 ; PNT = 120 - 100 = 20
    assert r.loss_total_kwh == pytest.approx(120)
    assert r.ntl_kwh == pytest.approx(20)


@pytest.mark.unit
def test_balance_indicativo_sin_cabecera():
    cfg = BalanceConfig()
    r = compute_balance(
        "F001", cfg=cfg, e_billed_kwh=850, loss_technical_kwh=100,
        e_input_kwh=None, indicative_loss_rate=0.12,
    )
    assert r.balance_type == BalanceType.INDICATIVO
    assert any("INDICATIVO" in w for w in r.warnings)


@pytest.mark.unit
def test_control_c01_pnt_negativa():
    cfg = BalanceConfig()
    # técnicas mayores que pérdidas totales -> PNT negativa
    r = compute_balance(
        "F001", cfg=cfg, e_billed_kwh=900, loss_technical_kwh=200,
        e_input_kwh=1000, e_own_use_kwh=0, e_not_supplied_kwh=0,
    )
    c01 = next(c for c in r.controls if c.code == "C01")
    assert c01.triggered is True
    assert r.ntl_kwh < 0


@pytest.mark.unit
def test_control_c03_facturado_mayor_entrada():
    cfg = BalanceConfig()
    r = compute_balance(
        "F001", cfg=cfg, e_billed_kwh=1100, loss_technical_kwh=50,
        e_input_kwh=1000, e_own_use_kwh=0, e_not_supplied_kwh=0,
    )
    c03 = next(c for c in r.controls if c.code == "C03")
    assert c03.triggered is True


# --------------------------------------------------------------------------- #
# Alumbrado público
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_energia_luminaria_manual():
    # (100+4)*12*30/1000 = 37.44 kWh
    assert luminaire_energy_kwh(100, 4, 12, 30) == pytest.approx(37.44)


@pytest.mark.unit
def test_bajo_medicion_no_cuenta_como_no_medido():
    cfg = AlumbradoConfig()
    df = pd.DataFrame([
        {"lamp_w": 100, "technology": "LED", "hours": 12, "days_month": 30, "is_metered": False},
        {"lamp_w": 100, "technology": "LED", "hours": 12, "days_month": 30, "is_metered": True},
    ])
    r = compute_streetlight_energy(df, cfg)
    # una medida, una no medida: el no medido es solo la primera
    assert r.total_unmetered_kwh == pytest.approx(37.44)
    assert r.total_metered_kwh == pytest.approx(37.44)


# --------------------------------------------------------------------------- #
# Cargabilidad y desbalance
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("ratio,esperado", [
    (1.30, LoadabilityClass.SOBRECARGADO_CRITICO),
    (1.05, LoadabilityClass.SOBRECARGADO),
    (0.90, LoadabilityClass.ALTA_CARGA),
    (0.50, LoadabilityClass.ADECUADO),
    (0.20, LoadabilityClass.SUBUTILIZADO),
    (0.05, LoadabilityClass.MUY_SUBUTILIZADO),
])
def test_clasificacion_cargabilidad(ratio, esperado):
    assert classify_loadability(ratio, CargabilidadConfig()) == esperado


@pytest.mark.unit
def test_desbalance_fases():
    # I = [100, 100, 100] -> 0% desbalance
    assert phase_imbalance_pct(100, 100, 100) == pytest.approx(0.0)
    # I = [120, 90, 90] -> prom=100, max|dev|=20 -> 20%
    assert phase_imbalance_pct(120, 90, 90) == pytest.approx(20.0)
