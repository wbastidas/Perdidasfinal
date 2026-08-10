"""Pérdidas técnicas por componente (§8).

Componentes:
  * ``factors``      — factor de pérdidas F_p y corrección por temperatura.
  * ``conductors``   — pérdidas I²R por tramo (MT, BT, acometidas) + neutro.
  * ``transformers`` — pérdidas de transformador (vacío + carga) y capacidad de
                       banco según su configuración (delta abierto, banco 3, etc.).
  * ``meters``       — pérdidas de medidores por tipo.
"""

from ptnt.losses.conductors import segment_loss_kwh, segment_loss_peak_kw
from ptnt.losses.factors import loss_factor, resistance_at_temp
from ptnt.losses.meters import meter_losses_kwh
from ptnt.losses.montecarlo import (
    LossDistribution,
    montecarlo_losses,
    montecarlo_ntl,
)
from ptnt.losses.transformers import (
    BankConfig,
    bank_capacity_kva,
    transformer_unit_loss_kwh,
)

__all__ = [
    "loss_factor",
    "resistance_at_temp",
    "segment_loss_peak_kw",
    "segment_loss_kwh",
    "transformer_unit_loss_kwh",
    "bank_capacity_kva",
    "BankConfig",
    "meter_losses_kwh",
    "montecarlo_losses",
    "montecarlo_ntl",
    "LossDistribution",
]
