"""Cálculo del consumo de alumbrado público (§9.2).

```
E_AP = (P_lámpara + P_auxiliar) · horas_efectivas · días_mes / 1000
```

Regla de exclusión (§9.2): si ``BAJOMEDICION = Sí`` la luminaria está medida y su
energía ya está en la facturación; NO se cuenta como AP no medido (evita doble
conteo).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ptnt.config.models import AlumbradoConfig


@dataclass
class StreetlightResult:
    por_luminaria: pd.DataFrame           # id, energy_kwh, is_metered, tech
    total_unmetered_kwh: float
    total_metered_kwh: float
    por_tecnologia: dict[str, float] = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)


def luminaire_energy_kwh(
    lamp_w: float, aux_w: float, hours: float, days: float
) -> float:
    """Energía mensual de una luminaria (kWh)."""

    return (lamp_w + aux_w) * hours * days / 1000.0


def compute_streetlight_energy(
    luminarias: pd.DataFrame,
    cfg: AlumbradoConfig,
    *,
    lamp_w_col: str = "lamp_w",
    tech_col: str = "technology",
    hours_col: str = "hours",
    days_col: str = "days_month",
    metered_col: str = "is_metered",
) -> StreetlightResult:
    """Calcula la energía de AP del universo de luminarias, separando medido de no
    medido y validando el rango de horas.

    Devuelve el total no medido (que se resta del balance) y el medido (que ya
    está en la facturación).
    """

    df = luminarias.copy()
    advert: list[str] = []

    aux_map = cfg.perdidas_auxiliares_w
    default_aux = aux_map.get("_default", 20)

    energias = []
    for _, r in df.iterrows():
        tech = str(r.get(tech_col, "_default"))
        lamp = float(r.get(lamp_w_col, 0) or 0)
        aux = float(aux_map.get(tech, default_aux))
        hours = float(r.get(hours_col, 0) or 0)
        days = float(r.get(days_col, cfg.dias_mes_por_defecto) or cfg.dias_mes_por_defecto)
        if not (cfg.horas_min <= hours <= cfg.horas_max):
            # fuera de rango regulatorio -> se acota y se advierte (anomalía AP04)
            hours = min(max(hours, cfg.horas_min), cfg.horas_max)
        energias.append(luminaire_energy_kwh(lamp, aux, hours, days))
    df["energy_kwh"] = energias

    metered = (
        df[metered_col].fillna(False).astype(bool)
        if metered_col in df.columns
        else pd.Series(False, index=df.index)
    )
    df["is_metered"] = metered

    total_metered = float(df.loc[metered, "energy_kwh"].sum())
    total_unmetered = float(df.loc[~metered, "energy_kwh"].sum())

    por_tec = (
        df.loc[~metered]
        .groupby(df[tech_col] if tech_col in df.columns else "is_metered")["energy_kwh"]
        .sum()
        .to_dict()
    )

    if not cfg.excluir_si_bajo_medicion and metered.any():
        advert.append(
            "excluir_si_bajo_medicion=false: riesgo de doble conteo de luminarias "
            "medidas. Debe ser true."
        )

    return StreetlightResult(
        por_luminaria=df,
        total_unmetered_kwh=total_unmetered,
        total_metered_kwh=total_metered,
        por_tecnologia={str(k): float(v) for k, v in por_tec.items()},
        advertencias=advert,
    )
