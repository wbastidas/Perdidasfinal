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
]
