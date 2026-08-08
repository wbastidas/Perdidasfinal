"""Cargabilidad de transformadores (§11.7) y desbalance de fases (§11.6)."""

from __future__ import annotations

from enum import Enum

from ptnt.config.models import CargabilidadConfig


class LoadabilityClass(str, Enum):
    SOBRECARGADO_CRITICO = "SOBRECARGADO_CRITICO"
    SOBRECARGADO = "SOBRECARGADO"
    ALTA_CARGA = "ALTA_CARGA"
    ADECUADO = "ADECUADO"
    SUBUTILIZADO = "SUBUTILIZADO"
    MUY_SUBUTILIZADO = "MUY_SUBUTILIZADO"


def classify_loadability(ratio: float, cfg: CargabilidadConfig) -> LoadabilityClass:
    """Clasifica ``S_max_diversificada / S_capacidad_puesto`` (§11.7)."""

    if ratio > cfg.sobrecargado_critico:
        return LoadabilityClass.SOBRECARGADO_CRITICO
    if ratio >= cfg.sobrecargado:
        return LoadabilityClass.SOBRECARGADO
    if ratio >= cfg.alta_carga:
        return LoadabilityClass.ALTA_CARGA
    if ratio >= cfg.adecuado_min:
        return LoadabilityClass.ADECUADO
    if ratio >= cfg.subutilizado_min:
        return LoadabilityClass.SUBUTILIZADO
    return LoadabilityClass.MUY_SUBUTILIZADO


def phase_imbalance_pct(i_a: float, i_b: float, i_c: float) -> float:
    """Desbalance de fases: ``máx|I_fase − I_prom| / I_prom · 100`` (§11.6)."""

    corrientes = [i_a, i_b, i_c]
    i_prom = sum(corrientes) / 3.0
    if i_prom <= 0:
        return 0.0
    return max(abs(i - i_prom) for i in corrientes) / i_prom * 100.0


def rebalance_benefit_kwh(
    i_a: float, i_b: float, i_c: float, r_neutral_ohm: float, hours: float,
    loss_factor_value: float,
) -> float:
    """Beneficio energético de rebalancear (§11.6).

    Estima la pérdida en el neutro por desbalance (``I_n² · R_n``) que se eliminaría
    si las fases quedaran equilibradas. ``I_n = |I_A + I_B + I_C|`` (aproximación de
    corrientes en fase; el caso fasorial exacto requiere ángulos).
    """

    i_n = abs(i_a + i_b + i_c - 3 * (min(i_a, i_b, i_c)))  # componente de desbalance
    perdida_pico_w = (i_n ** 2) * r_neutral_ohm
    return perdida_pico_w * hours * loss_factor_value / 1000.0
