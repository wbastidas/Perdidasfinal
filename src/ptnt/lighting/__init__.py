"""Alumbrado público (§9).

El AP no medido es *consumo conocido no facturado*, no pérdida no técnica.
Restarlo mal es la causa más común de inflar artificialmente la PNT. La regla de
exclusión ``BAJOMEDICION`` es la más importante: una luminaria medida ya está en
la energía facturada y NO debe restarse otra vez.
"""

from ptnt.lighting.streetlight import (
    StreetlightResult,
    compute_streetlight_energy,
    luminaire_energy_kwh,
)

__all__ = [
    "luminaire_energy_kwh",
    "compute_streetlight_energy",
    "StreetlightResult",
]
