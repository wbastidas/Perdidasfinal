"""Pruebas unitarias de las fórmulas de demanda (§6) con casos calculados a mano
y pruebas de propiedad (hypothesis)."""

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ptnt.load.demand import (
    PhaseConfig,
    apparent_power,
    average_power_kw,
    coincidence_factor,
    current_amperes,
    reactive_power,
    velander_max_demand,
)


@pytest.mark.unit
def test_potencia_media_calculo_manual():
    # 720 kWh en 30 días => 720 / (30*24) = 1.0 kW
    assert average_power_kw(720.0, 30) == pytest.approx(1.0)


@pytest.mark.unit
def test_velander_calculo_manual():
    # a=0.0002, b=0.03, E=10000 -> 0.0002*10000 + 0.03*100 = 2 + 3 = 5 kW
    assert velander_max_demand(10000.0, 0.0002, 0.03) == pytest.approx(5.0)


@pytest.mark.unit
def test_coincidencia_fc1_es_1():
    # FC(1) = A + B = 1 por construcción
    assert coincidence_factor(1, 0.2, 0.8) == pytest.approx(1.0)


@pytest.mark.unit
def test_coincidencia_monotona_decreciente():
    A, B = 0.2, 0.8
    vals = [coincidence_factor(n, A, B) for n in range(1, 50)]
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))


@pytest.mark.unit
def test_reactiva_cero_si_cosphi_1():
    assert reactive_power(10.0, 1.0) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_reactiva_calculo_manual():
    # cosφ=0.8 -> tanφ = 0.75 ; Q = 10 * 0.75 = 7.5
    assert reactive_power(10.0, 0.8) == pytest.approx(7.5, rel=1e-6)


@pytest.mark.unit
def test_corriente_monofasica_vs_trifasica_factor_sqrt3():
    """El error del √3: aplicar trifásico a un monofásico subestima ~42%."""

    s = 10.0  # kVA
    i_mono = current_amperes(s, PhaseConfig.MONOFASICO, v_ln=127, v_ll=220)
    i_tri = current_amperes(s, PhaseConfig.TRIFASICO, v_ln=127, v_ll=220)
    # I_mono = 10000/127 = 78.7 A ; I_tri = 10000/(sqrt3*220) = 26.24 A
    assert i_mono == pytest.approx(10000 / 127, rel=1e-6)
    assert i_tri == pytest.approx(10000 / (math.sqrt(3) * 220), rel=1e-6)
    assert i_tri < i_mono


@pytest.mark.unit
@given(
    p=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    cosphi=st.floats(min_value=0.5, max_value=1.0),
)
@settings(max_examples=200)
def test_propiedad_S_mayor_igual_P(p, cosphi):
    """Propiedad: S >= P siempre."""

    q = reactive_power(p, cosphi)
    s = apparent_power(p, q)
    assert s >= p - 1e-6


@pytest.mark.unit
@given(n=st.integers(min_value=1, max_value=10_000))
def test_propiedad_fc_acotada(n):
    A, B = 0.3, 0.7
    fc = coincidence_factor(n, A, B, minimo=0.15)
    assert 0.15 <= fc <= 1.0 + 1e-9
