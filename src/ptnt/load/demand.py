"""Cálculo de potencia por cliente: P, Q, S, I (§6 de la especificación).

Este módulo reemplaza el cálculo del SIG, que se presume erróneo. Dos errores
concretos que corrige:

1. **Energía del último mes usada como base de potencia.** El SIG toma
   ``CLIULTCONM`` (consumo del último mes) para calcular ``POTENCIAACTIVA``. Un
   solo mes es frágil, y para clientes **no residenciales** (comerciales e
   industriales) —cuyo consumo es marcadamente estacional o variable— produce una
   potencia sistemáticamente sesgada. Aquí la energía base es el **promedio
   multi-mes robusto** (ver ``ptnt.load.averaging``).

2. **Factor √3 mal aplicado.** Aplicar √3 a un cliente monofásico subestima la
   corriente en 42 %. La fórmula de corriente se selecciona por configuración de
   fase, no por defecto trifásico.

Todas las fórmulas están cubiertas por tests unitarios con caso calculado a mano.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

import numpy as np
import pandas as pd

from ptnt.config.models import ClaseTarifaria, LoadConfig, MetodoDemanda
from ptnt.segment.classification import resolver_clave_config


class PhaseConfig(str, Enum):
    MONOFASICO = "1F"   # fase-neutro
    BIFASICO = "2F"     # fase-fase
    TRIFASICO = "3F"    # trifásico


# --------------------------------------------------------------------------- #
# Fórmulas elementales (vectorizables)
# --------------------------------------------------------------------------- #
def average_power_kw(energy_kwh, dias_periodo: float) -> float:
    """Paso 1 — energía a demanda media: ``P_media = E / (dias × 24)``."""

    horas = dias_periodo * 24.0
    if horas <= 0:
        raise ValueError("dias_periodo debe ser > 0")
    return energy_kwh / horas


def velander_max_demand(energy_kwh, a: float, b: float) -> float:
    """Paso 2B — Velander: ``P_max = a·E + b·√E`` (kW).

    Captura la no linealidad que el factor de carga fijo pierde.
    """

    e = np.maximum(energy_kwh, 0.0)
    return a * e + b * np.sqrt(e)


def max_demand_by_load_factor(p_media_kw, load_factor: float) -> float:
    """Paso 2A — factor de carga: ``P_max = P_media / LF``."""

    if load_factor <= 0:
        raise ValueError("factor de carga debe ser > 0")
    return p_media_kw / load_factor


def coincidence_factor(n: int, A: float, B: float, minimo: float = 0.0) -> float:
    """Paso 3 — factor de coincidencia ``FC(n) = A + B/√n``.

    Propiedades garantizadas: ``FC(1) = A + B = 1``, monótona decreciente en n,
    acotada inferiormente por ``minimo``.
    """

    if n <= 0:
        return 1.0
    fc = A + B / math.sqrt(n)
    return max(fc, minimo)


def reactive_power(p_kw, cos_phi: float) -> float:
    """Paso 4 — ``Q = P · tan(arccos(cosφ))``."""

    if not 0 < cos_phi <= 1:
        raise ValueError("cos_phi debe estar en (0, 1]")
    tan_phi = math.tan(math.acos(cos_phi))
    return p_kw * tan_phi


def apparent_power(p_kw, q_kvar) -> float:
    """Paso 5 — ``S = √(P² + Q²)``."""

    return np.sqrt(np.square(p_kw) + np.square(q_kvar))


def current_amperes(
    s_kva, phase: PhaseConfig, v_ln: float, v_ll: float
) -> float:
    """Paso 5 — corriente según configuración de fase.

    * Monofásico (fase-neutro): ``I = S·1000 / V_LN``
    * Bifásico (fase-fase):     ``I = S·1000 / V_LL``
    * Trifásico:                ``I = S·1000 / (√3·V_LL)``
    """

    va = s_kva * 1000.0
    if phase == PhaseConfig.MONOFASICO:
        return va / v_ln
    if phase == PhaseConfig.BIFASICO:
        return va / v_ll
    if phase == PhaseConfig.TRIFASICO:
        return va / (math.sqrt(3.0) * v_ll)
    raise ValueError(f"configuración de fase desconocida: {phase}")


def phase_from_count(cdafas: int | float | None) -> PhaseConfig:
    """Deriva la configuración de fase desde ``CDAFAS`` (nº de fases)."""

    try:
        n = int(cdafas)
    except (TypeError, ValueError):
        return PhaseConfig.MONOFASICO
    if n <= 1:
        return PhaseConfig.MONOFASICO
    if n == 2:
        return PhaseConfig.BIFASICO
    return PhaseConfig.TRIFASICO


# --------------------------------------------------------------------------- #
# Recálculo por cliente (vectorizado)
# --------------------------------------------------------------------------- #
@dataclass
class DemandResult:
    """Potencias recalculadas por cliente."""

    por_cliente: pd.DataFrame
    metodo: str


def _fase_current_vector(
    s_kva: np.ndarray, fases: np.ndarray, v_ln: float, v_ll: float
) -> np.ndarray:
    out = np.empty_like(s_kva, dtype=float)
    sqrt3 = math.sqrt(3.0)
    mono = fases <= 1
    bi = fases == 2
    tri = fases >= 3
    va = s_kva * 1000.0
    out[mono] = va[mono] / v_ln
    out[bi] = va[bi] / v_ll
    out[tri] = va[tri] / (sqrt3 * v_ll)
    return out


def recompute_customer_power(
    clientes: pd.DataFrame,
    cfg: LoadConfig,
    *,
    energia_col: str = "kwh_representativo",
    tarifa_col: str = "tariff_description",
    fases_col: str = "phases_count",
    dias_periodo: float | None = None,
) -> DemandResult:
    """Recalcula P, Q, S, I para todo el universo de clientes (vectorizado).

    ``clientes`` debe traer, por cuenta: energía representativa (promedio
    multi-mes), descripción de tarifa y número de fases (``CDAFAS``). Devuelve un
    DataFrame con ``p_avg_kw``, ``p_max_ind_kw``, ``q_kvar``, ``s_kva``,
    ``current_a``, más ``metodo`` y ``cos_phi_used``.

    Nota: ``p_max_ind_kw`` es la demanda máxima **individual** (sin diversificar);
    la diversificación por nodo (FC(n)) se aplica al agregar aguas abajo, no por
    cliente aislado.
    """

    dias = dias_periodo if dias_periodo is not None else cfg.dias_periodo_por_defecto
    df = clientes.copy()
    energia = df[energia_col].to_numpy(dtype=float)
    energia = np.where(np.isfinite(energia), energia, 0.0)

    tarifas = df[tarifa_col].astype(str).to_numpy()
    fases = (
        pd.to_numeric(df[fases_col], errors="coerce").fillna(1).to_numpy()
        if fases_col in df.columns
        else np.ones(len(df))
    )

    # Parámetros por clase (vectorizados con lookup)
    a_arr = np.empty(len(df))
    b_arr = np.empty(len(df))
    lf_arr = np.empty(len(df))
    cos_arr = np.empty(len(df))
    default = _clase_default(cfg)
    for i, t in enumerate(tarifas):
        clase = _resolver_clase(t, cfg, default)
        a_arr[i] = clase.a
        b_arr[i] = clase.b
        lf_arr[i] = clase.factor_carga
        cos_arr[i] = clase.cos_phi

    p_media = average_power_kw(energia, dias)
    if cfg.metodo_demanda_maxima == MetodoDemanda.VELANDER:
        p_max = velander_max_demand(energia, a_arr, b_arr)
    else:
        p_max = p_media / np.where(lf_arr > 0, lf_arr, np.nan)

    tan_phi = np.tan(np.arccos(np.clip(cos_arr, 1e-6, 1.0)))
    q = p_max * tan_phi
    s = np.sqrt(p_max**2 + q**2)
    current = _fase_current_vector(s, fases, cfg.voltaje_ln, cfg.voltaje_ll)

    df_out = pd.DataFrame(
        {
            "contract_account": df.get("contract_account", pd.RangeIndex(len(df))),
            "energy_kwh_used": energia,
            "p_avg_kw": p_media,
            "p_max_ind_kw": p_max,
            "q_kvar": q,
            "s_kva": s,
            "current_a": current,
            "cos_phi_used": cos_arr,
            "phase_count": fases.astype(int),
            "method": cfg.metodo_demanda_maxima.value,
        }
    )
    return DemandResult(por_cliente=df_out, metodo=cfg.metodo_demanda_maxima.value)


def _clase_default(cfg: LoadConfig) -> ClaseTarifaria:
    """Clase de respaldo cuando la tarifa no está catalogada (la primera definida)."""

    return next(iter(cfg.clases.values()))


@lru_cache(maxsize=4096)
def _clave_resuelta(tarifa: str, claves: tuple[str, ...]) -> str | None:
    """Cachea el emparejamiento tarifa→clave del catálogo (miles de filas, pocas
    descripciones distintas)."""

    return resolver_clave_config(tarifa, claves)


def _resolver_clase(
    tarifa: object, cfg: LoadConfig, default: ClaseTarifaria
) -> ClaseTarifaria:
    """Devuelve los parámetros de clase para una descripción de tarifa real.

    Primero intenta la coincidencia exacta con el catálogo; si falla —lo habitual
    con el texto de ``DESTARI``— resuelve de forma semántica por clase y nivel de
    tensión. Solo cae a ``default`` cuando la descripción no es reconocible en
    absoluto.
    """

    t = str(tarifa)
    exacta = cfg.clases.get(t)
    if exacta is not None:
        return exacta
    clave = _clave_resuelta(t, tuple(cfg.clases.keys()))
    return cfg.clases.get(clave, default) if clave else default
