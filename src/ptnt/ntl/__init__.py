"""Detección de pérdidas no técnicas (PNT / hurto).

Extrae señales de comportamiento sobre la serie multi-mes de cada cliente
(§11.3), las combina por consenso y produce un ranking de sospecha con las
razones activas y su evidencia. Ninguna señal individual es concluyente; el valor
predictivo está en la coincidencia de varias.
"""

from ptnt.ntl.network_signals import (
    NetworkSignal,
    n1_zone_balance_residual,
    n3_totalizer_balance,
    n4_loading_incoherence,
)
from ptnt.ntl.confirmed import (
    ConfirmedTheftSet,
    ValidationMetrics,
    calibrate_signal_thresholds,
    load_confirmed_theft,
    pu_learning,
    validate_against_confirmed,
)
from ptnt.ntl.scoring import NtlRanking, score_customers
from ptnt.ntl.signals import (
    SignalResult,
    compute_signals,
)

__all__ = [
    "compute_signals",
    "SignalResult",
    "score_customers",
    "NtlRanking",
    "NetworkSignal",
    "n1_zone_balance_residual",
    "n3_totalizer_balance",
    "n4_loading_incoherence",
    "load_confirmed_theft",
    "ConfirmedTheftSet",
    "validate_against_confirmed",
    "ValidationMetrics",
    "pu_learning",
    "calibrate_signal_thresholds",
]
