"""Ecuación de balance de energía y controles de coherencia (§10.1, §10.6).

```
E_cabecera
  ± E_transferida
  − E_facturada_clientes
  − E_alumbrado_público_no_medido
  − E_consumos_propios
  − E_no_suministrada (ENS)
  = Pérdidas_totales

PNT = Pérdidas_totales − Pérdidas_técnicas
```

**MEDIDO vs INDICATIVO** (§2.2): sin medición de cabecera no hay balance, hay
estimación. El balance INDICATIVO estima la entrada aplicando la tasa de pérdidas
del mes de referencia a la energía facturada, y **nunca** se reporta como PNT
verificada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ptnt.config.models import BalanceConfig


class BalanceType(str, Enum):
    MEDIDO = "MEDIDO"
    INDICATIVO = "INDICATIVO"


@dataclass
class ControlResult:
    code: str
    triggered: bool
    observed: float | None
    threshold: float | None
    detail: str


@dataclass
class BalanceResult:
    feeder_code: str
    balance_type: BalanceType
    e_input_kwh: float
    e_billed_kwh: float
    e_streetlight_unmetered_kwh: float
    e_own_use_kwh: float
    e_not_supplied_kwh: float
    e_transferred_kwh: float
    loss_total_kwh: float
    loss_technical_kwh: float
    ntl_kwh: float
    ntl_pct: float
    closure_residual_pct: float
    controls: list[ControlResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compute_balance(
    feeder_code: str,
    *,
    cfg: BalanceConfig,
    e_billed_kwh: float,
    loss_technical_kwh: float,
    e_input_kwh: float | None = None,
    e_streetlight_unmetered_kwh: float = 0.0,
    e_own_use_kwh: float | None = None,
    e_not_supplied_kwh: float | None = None,
    e_transferred_kwh: float = 0.0,
    customers_traced: int | None = None,
    customers_declared: int | None = None,
    energy_coverage_pct: float | None = None,
    indicative_loss_rate: float | None = None,
) -> BalanceResult:
    """Ensambla el balance de un alimentador y aplica C01–C06.

    Si ``e_input_kwh`` (cabecera) está disponible, el balance es MEDIDO. Si no, se
    produce un balance INDICATIVO estimando la entrada con ``indicative_loss_rate``
    (tasa de pérdidas del mes de referencia) aplicada a la energía facturada.
    """

    warnings: list[str] = []

    if e_own_use_kwh is None:
        e_own_use_kwh = e_billed_kwh * cfg.consumos_propios_pct_defecto / 100.0
        warnings.append("Consumos propios asumidos por defecto (origen SUPUESTO_DEFECTO).")
    if e_not_supplied_kwh is None:
        e_not_supplied_kwh = cfg.ens_por_defecto_kwh

    if e_input_kwh is not None:
        balance_type = BalanceType.MEDIDO
    else:
        balance_type = BalanceType.INDICATIVO
        rate = indicative_loss_rate if indicative_loss_rate is not None else 0.0
        # entrada estimada: facturado + AP + propios, inflado por la tasa de pérdidas
        base = e_billed_kwh + e_streetlight_unmetered_kwh + e_own_use_kwh
        e_input_kwh = base / max(1e-9, (1.0 - rate))
        warnings.append(
            "Balance INDICATIVO: sin medición de cabecera. La PNT NO es verificable."
        )

    # Ecuación de balance
    loss_total = (
        e_input_kwh
        + e_transferred_kwh
        - e_billed_kwh
        - e_streetlight_unmetered_kwh
        - e_own_use_kwh
        - e_not_supplied_kwh
    )
    ntl = loss_total - loss_technical_kwh
    ntl_pct = (ntl / e_input_kwh * 100.0) if e_input_kwh > 0 else 0.0
    loss_total_pct = (loss_total / e_input_kwh * 100.0) if e_input_kwh > 0 else 0.0
    tech_pct = (loss_technical_kwh / e_input_kwh * 100.0) if e_input_kwh > 0 else 0.0
    closure_residual_pct = 0.0  # cerrado por construcción salvo transferencias

    controls = _apply_controls(
        cfg,
        ntl=ntl,
        ntl_pct=ntl_pct,
        e_billed=e_billed_kwh,
        e_input=e_input_kwh,
        tech_pct=tech_pct,
        customers_traced=customers_traced,
        customers_declared=customers_declared,
        energy_coverage_pct=energy_coverage_pct,
    )

    return BalanceResult(
        feeder_code=feeder_code,
        balance_type=balance_type,
        e_input_kwh=e_input_kwh,
        e_billed_kwh=e_billed_kwh,
        e_streetlight_unmetered_kwh=e_streetlight_unmetered_kwh,
        e_own_use_kwh=e_own_use_kwh,
        e_not_supplied_kwh=e_not_supplied_kwh,
        e_transferred_kwh=e_transferred_kwh,
        loss_total_kwh=loss_total,
        loss_technical_kwh=loss_technical_kwh,
        ntl_kwh=ntl,
        ntl_pct=ntl_pct,
        closure_residual_pct=closure_residual_pct,
        controls=controls,
        warnings=warnings,
    )


def _apply_controls(
    cfg: BalanceConfig,
    *,
    ntl: float,
    ntl_pct: float,
    e_billed: float,
    e_input: float,
    tech_pct: float,
    customers_traced: int | None,
    customers_declared: int | None,
    energy_coverage_pct: float | None,
) -> list[ControlResult]:
    """Aplica los controles de coherencia C01–C06 (§10.6)."""

    ctrl: list[ControlResult] = []

    # C01 — PNT negativa
    ctrl.append(ControlResult(
        "C01", ntl < 0, ntl, 0.0,
        "PNT negativa: probable transferencia entre alimentadores, sobreestimación "
        "de AP, o error de asignación de clientes." if ntl < 0 else "OK",
    ))
    # C02 — PNT > máximo
    ctrl.append(ControlResult(
        "C02", ntl_pct > cfg.controles.c02_pnt_maxima_pct, ntl_pct,
        cfg.controles.c02_pnt_maxima_pct,
        "PNT improbablemente alta: revisar cobertura de facturación y asignación."
        if ntl_pct > cfg.controles.c02_pnt_maxima_pct else "OK",
    ))
    # C03 — facturado > entrada
    ctrl.append(ControlResult(
        "C03", e_billed > e_input, e_billed, e_input,
        "Facturado mayor que la entrada: error de datos o de período (bloquea)."
        if e_billed > e_input else "OK",
    ))
    # C04 — pérdidas técnicas excesivas
    ctrl.append(ControlResult(
        "C04", tech_pct > cfg.controles.c04_perdidas_tecnicas_max_pct, tech_pct,
        cfg.controles.c04_perdidas_tecnicas_max_pct,
        "Pérdidas técnicas altas: revisar atributos de conductor y asignación."
        if tech_pct > cfg.controles.c04_perdidas_tecnicas_max_pct else "OK",
    ))
    # C05 — cobertura de clientes
    if customers_traced is not None and customers_declared:
        desv = abs(customers_traced - customers_declared) / customers_declared * 100.0
        ctrl.append(ControlResult(
            "C05", desv > cfg.controles.c05_cobertura_clientes_pct, desv,
            cfg.controles.c05_cobertura_clientes_pct,
            "Clientes trazados difieren de declarados más del umbral."
            if desv > cfg.controles.c05_cobertura_clientes_pct else "OK",
        ))
    # C06 — cobertura de energía
    if energy_coverage_pct is not None:
        ctrl.append(ControlResult(
            "C06", energy_coverage_pct < cfg.controles.c06_cobertura_energia_min_pct,
            energy_coverage_pct, cfg.controles.c06_cobertura_energia_min_pct,
            "Cobertura de energía insuficiente: degradar a INDICATIVO."
            if energy_coverage_pct < cfg.controles.c06_cobertura_energia_min_pct else "OK",
        ))
    return ctrl
