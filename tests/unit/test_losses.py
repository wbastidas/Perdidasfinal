"""Pruebas de pérdidas técnicas (§8): factor de pérdidas, capacidad de banco y la
prueba crítica de que la pérdida en vacío NO se multiplica por el factor de
pérdidas."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ptnt.losses.conductors import segment_loss_kwh, segment_loss_peak_kw
from ptnt.losses.factors import loss_factor, resistance_at_temp
from ptnt.losses.meters import meter_losses_kwh
from ptnt.losses.transformers import (
    BankConfig,
    bank_capacity_kva,
    transformer_unit_loss_kwh,
)


# --------------------------------------------------------------------------- #
# Factor de pérdidas
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_factor_perdidas_caso_manual():
    # Fc=0.5, k=0.3 -> 0.3*0.5 + 0.7*0.25 = 0.15 + 0.175 = 0.325
    assert loss_factor(0.5, 0.3) == pytest.approx(0.325)


@pytest.mark.unit
@given(
    fc=st.floats(min_value=0.0, max_value=1.0),
    k=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=300)
def test_propiedad_fc2_leq_fp_leq_fc(fc, k):
    """Propiedad física: Fc² ≤ Fp ≤ Fc."""

    fp = loss_factor(fc, k)
    assert fc**2 - 1e-12 <= fp <= fc + 1e-12


@pytest.mark.unit
def test_correccion_temperatura():
    # R20=1.0, alpha=0.00403, T=50 -> 1*(1+0.00403*30) = 1.1209
    assert resistance_at_temp(1.0, 50, 0.00403) == pytest.approx(1.1209)


# --------------------------------------------------------------------------- #
# Capacidad de banco (tests obligatorios §8.3.2)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_banco_unidad_simple():
    assert bank_capacity_kva([50], BankConfig.UNIDAD_SIMPLE) == 50


@pytest.mark.unit
def test_banco_3_iguales():
    assert bank_capacity_kva([25, 25, 25], BankConfig.BANCO_3) == pytest.approx(75)


@pytest.mark.unit
def test_delta_abierto_es_raiz3_no_2():
    """Delta abierto (V-V) con 2 unidades: √3·kVA, NO 2·kVA."""

    cap = bank_capacity_kva([50, 50], BankConfig.DELTA_ABIERTO)
    assert cap == pytest.approx(math.sqrt(3) * 50)
    assert cap != pytest.approx(100)  # el error clásico


@pytest.mark.unit
def test_banco_desigual_limitado_por_menor():
    assert bank_capacity_kva([25, 50], BankConfig.BANCO_DESIGUAL) == pytest.approx(50)  # 25*2


@pytest.mark.unit
def test_delta_4h_con_derating():
    cap = bank_capacity_kva([50, 25, 25], BankConfig.DELTA_4H)
    assert cap == pytest.approx(100 - 0.05 * 50)


# --------------------------------------------------------------------------- #
# Pérdidas de transformador — la prueba crítica
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_perdida_vacio_no_se_multiplica_por_factor_perdidas():
    """P0 es constante; aplicarle F_p cambia (mal) el resultado. El modo correcto
    (apply_loss_factor_to_no_load=False) debe dar una pérdida en vacío mayor que
    el modo incorrecto cuando F_p < 1."""

    p0, pk = 0.1, 0.5
    s_max, s_nom, hours, fp = 40, 100, 720, 0.3

    total_ok, vacio_ok = transformer_unit_loss_kwh(
        p0, pk, s_max, s_nom, hours, fp, apply_loss_factor_to_no_load=False
    )
    total_bad, vacio_bad = transformer_unit_loss_kwh(
        p0, pk, s_max, s_nom, hours, fp, apply_loss_factor_to_no_load=True
    )
    # correcto: vacío = P0 * t
    assert vacio_ok == pytest.approx(p0 * hours)
    # incorrecto: vacío = P0 * t * fp  (subestima)
    assert vacio_bad == pytest.approx(p0 * hours * fp)
    assert vacio_ok > vacio_bad
    assert total_ok > total_bad


@pytest.mark.unit
def test_perdida_transformador_carga():
    # carga: Pk*(S/Snom)²*t*fp*fdesb ; vacío: P0*t
    total, vacio = transformer_unit_loss_kwh(
        0.1, 0.5, 100, 100, 720, 1.0, f_desbalance=1.0,
        apply_loss_factor_to_no_load=False,
    )
    # a carga nominal (S=Snom) y fp=1: carga = 0.5*720 = 360 ; vacío = 72
    assert vacio == pytest.approx(72)
    assert total == pytest.approx(72 + 360)


# --------------------------------------------------------------------------- #
# Conductores y medidores
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_perdida_conductor_i2r():
    # I=100A, R=0.5 ohm/km, L=1km, 3 fases -> 3*100²*0.5*1 = 15000 W = 15 kW
    p = segment_loss_peak_kw(100, 0.5, 1.0, n_phases=3, include_neutral=False)
    assert p == pytest.approx(15.0)


@pytest.mark.unit
def test_perdida_conductor_energia():
    assert segment_loss_kwh(15.0, 720, 0.3) == pytest.approx(3240.0)


@pytest.mark.unit
def test_perdida_medidores():
    # 3 electrónicos (0.8W) * 720h = 3*0.8*720/1000 = 1.728 kWh
    tipos = ["Electrónico", "Electrónico", "Electrónico"]
    watts = {"Electrónico": 0.8, "_default": 1.0}
    assert meter_losses_kwh(tipos, 720, watts) == pytest.approx(1.728)
