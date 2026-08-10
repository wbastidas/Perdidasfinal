"""Pérdidas en conductores por tramo (§8.2).

```
P_perdida_tramo_max = Σ_fases  I_fase_max² · R_fase · L    (kW pico)
E_perdida_tramo     = P_perdida_tramo_max · t · F_p        (kWh)
```

En sistemas desbalanceados el neutro conduce (``I_n = |I_A+I_B+I_C|``); su pérdida
se suma cuando ``incluir_neutro`` está activo.
"""

from __future__ import annotations


def segment_loss_peak_kw(
    current_a: float,
    r_ohm_km: float,
    length_km: float,
    *,
    n_phases: int = 1,
    neutral_current_a: float = 0.0,
    r_neutral_ohm_km: float = 0.0,
    include_neutral: bool = True,
) -> float:
    """Pérdida de potencia pico en un tramo (kW).

    Para un tramo balanceado se aproxima ``Σ_fases I²R`` como ``n_phases · I²R``.
    La pérdida del neutro se añade con su propia corriente y resistencia.
    """

    p_w = n_phases * (current_a**2) * r_ohm_km * length_km
    if include_neutral and neutral_current_a > 0 and r_neutral_ohm_km > 0:
        p_w += (neutral_current_a**2) * r_neutral_ohm_km * length_km
    return p_w / 1000.0  # W -> kW


def segment_loss_kwh(
    peak_kw: float, hours: float, loss_factor_value: float
) -> float:
    """Energía perdida en el período: ``E = P_pico · t · F_p``."""

    return peak_kw * hours * loss_factor_value
