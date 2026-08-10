"""Propagación de incertidumbre por Monte Carlo (§8.6).

Las pérdidas técnicas se reportan con P10/P50/P90, propagando la incertidumbre de:
  * P0/Pk de catálogo vs. placa real (mayor fuente en transformación).
  * Factor de carga supuesto.
  * Atributos de conductor (índice de confiabilidad).
  * Longitud de tramo.

Se ejecuta sobre el motor propio (rápido). El número de iteraciones es
configurable; por defecto 200 (tamizaje) / 1000 (detalle).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class LossDistribution:
    p10: float
    p50: float
    p90: float
    mean: float
    std: float
    samples: int


def _percentiles(vals: np.ndarray) -> LossDistribution:
    return LossDistribution(
        p10=float(np.percentile(vals, 10)),
        p50=float(np.percentile(vals, 50)),
        p90=float(np.percentile(vals, 90)),
        mean=float(np.mean(vals)),
        std=float(np.std(vals)),
        samples=int(vals.size),
    )


def montecarlo_losses(
    base_loss_conductors_kwh: float,
    base_loss_transformers_kwh: float,
    base_loss_noload_kwh: float,
    base_loss_meters_kwh: float,
    *,
    iterations: int = 200,
    seed: int = 20260807,
    unc_p0_pk_pct: float = 15.0,
    unc_load_factor_pct: float = 20.0,
    unc_conductor_pct: float = 10.0,
    unc_length_pct: float = 5.0,
) -> LossDistribution:
    """Propaga la incertidumbre y devuelve la distribución de la pérdida técnica total.

    Cada componente se perturba con un multiplicador normal centrado en 1 y
    desviación derivada del porcentaje de incertidumbre correspondiente. La pérdida
    en vacío (parte de la de transformación) se perturba solo por P0/Pk; la de
    carga combina P0/Pk y factor de carga.
    """

    rng = np.random.default_rng(seed)
    n = max(1, iterations)

    def mult(pct: float) -> np.ndarray:
        # desviación estándar = pct/100 tratado como ~1σ (acotado a >0)
        return np.clip(rng.normal(1.0, pct / 100.0, n), 0.05, None)

    # Conductores: atributo de conductor + longitud
    cond = base_loss_conductors_kwh * mult(unc_conductor_pct) * mult(unc_length_pct)
    # Transformadores carga: P0/Pk + factor de carga
    tx_load_base = max(base_loss_transformers_kwh - base_loss_noload_kwh, 0.0)
    tx_load = tx_load_base * mult(unc_p0_pk_pct) * mult(unc_load_factor_pct)
    # Transformadores vacío: solo P0/Pk (NO factor de carga)
    tx_noload = base_loss_noload_kwh * mult(unc_p0_pk_pct)
    # Medidores: bajo, sin incertidumbre relevante
    meters = np.full(n, base_loss_meters_kwh)

    total = cond + tx_load + tx_noload + meters
    return _percentiles(total)


def montecarlo_ntl(
    e_input_kwh: float,
    e_billed_kwh: float,
    e_streetlight_unmetered_kwh: float,
    e_own_use_kwh: float,
    e_not_supplied_kwh: float,
    loss_tech_dist: LossDistribution,
    *,
    iterations: int = 200,
    seed: int = 20260808,
) -> LossDistribution:
    """Distribución de la PNT propagando la incertidumbre de las pérdidas técnicas.

    ``PNT = pérdidas_totales − pérdidas_técnicas``. La pérdida total es un número
    duro (viene del balance con cabecera); la técnica es la incierta, así que la
    banda de PNT es el reflejo de la banda técnica.
    """

    rng = np.random.default_rng(seed)
    n = max(1, iterations)
    loss_total = (
        e_input_kwh - e_billed_kwh - e_streetlight_unmetered_kwh
        - e_own_use_kwh - e_not_supplied_kwh
    )
    # muestrear la técnica como normal(mean, std) acotada a >=0
    tech = np.clip(
        rng.normal(loss_tech_dist.mean, max(loss_tech_dist.std, 1e-9), n), 0.0, None
    )
    ntl = loss_total - tech
    return _percentiles(ntl)
