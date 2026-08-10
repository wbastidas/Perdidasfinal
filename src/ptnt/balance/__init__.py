"""Balance jerárquico y PNT (§10).

Cierra el balance de energía por alimentador donde hay medición de cabecera y
separa pérdidas técnicas de no técnicas (PNT). Distingue explícitamente el
balance MEDIDO (con cabecera) del INDICATIVO (sin ella), y aplica los controles de
coherencia C01–C06.
"""

from ptnt.balance.energy_balance import (
    BalanceResult,
    ControlResult,
    compute_balance,
)

__all__ = ["compute_balance", "BalanceResult", "ControlResult"]
