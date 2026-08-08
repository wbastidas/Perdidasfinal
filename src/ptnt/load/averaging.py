"""Mecanismo de promedio de consumo sobre varios meses.

Motivación (requisito explícito): el consumo es una serie de hasta 36 meses. El
SIG calcula la potencia a partir del **último mes** (``CLIULTCONM``), lo que es
frágil: un mes atípico (vacaciones, lectura estimada, cero por corte) distorsiona
toda la potencia. Este módulo produce un **consumo representativo** por cliente a
partir de la ventana histórica, con métodos robustos configurables.

Métodos:
  * ``media``            — media aritmética simple de la ventana.
  * ``media_recortada``  — descarta un porcentaje de las colas (robusto a picos y ceros atípicos).
  * ``mediana``          — muy robusto, insensible a outliers.
  * ``media_ponderada``  — pondera más los meses recientes (decaimiento exponencial).
  * ``estacional``       — media por mes-calendario, corrige estacionalidad.

Reglas de tratamiento (§6.6):
  * Los ceros con servicio suspendido pueden excluirse de la ventana.
  * Las lecturas estimadas pueden excluirse (no se imputan en silencio).
  * Si quedan menos de ``min_meses_validos`` observaciones válidas, el promedio se
    marca de baja confiabilidad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ptnt.config.models import AveragingConfig, MetodoPromedio


@dataclass
class AveragingResult:
    """Consumo representativo por cliente."""

    por_cliente: pd.DataFrame  # contract_account, kwh_representativo, n_meses_validos, confiable, cv
    metodo: str
    ventana_meses: int


def _trimmed_mean(values: np.ndarray, recorte_pct: float) -> float:
    """Media recortada simétrica descartando ``recorte_pct``% de cada cola."""

    v = np.sort(values[np.isfinite(values)])
    n = v.size
    if n == 0:
        return float("nan")
    k = int(np.floor(n * (recorte_pct / 100.0)))
    if 2 * k >= n:
        # recorte demasiado agresivo para la muestra: cae a mediana
        return float(np.median(v))
    return float(np.mean(v[k : n - k]))


def _weighted_mean_recency(values: np.ndarray, half_life: float) -> float:
    """Media ponderada con más peso a los meses recientes.

    ``values`` va de más antiguo (índice 0) a más reciente (último). El peso del
    mes ``i`` desde el más reciente es ``0.5 ** (edad/half_life)``.
    """

    finite = np.isfinite(values)
    v = values[finite]
    if v.size == 0:
        return float("nan")
    idx = np.arange(values.size)[finite]
    edad = (values.size - 1) - idx  # 0 = más reciente
    pesos = 0.5 ** (edad / max(half_life, 1e-9))
    return float(np.sum(v * pesos) / np.sum(pesos))


def average_series(
    values: list[float] | np.ndarray,
    cfg: AveragingConfig,
    *,
    meses: list[date] | None = None,
    estimadas: np.ndarray | None = None,
    suspendido: bool = False,
) -> tuple[float, int, float]:
    """Promedia una serie de consumo mensual de un cliente.

    Devuelve ``(kwh_representativo, n_meses_validos, coef_variacion)``.

    * Toma la ventana de los últimos ``ventana_meses`` (asume ``values`` ordenado
      de más antiguo a más reciente).
    * Aplica las exclusiones de §6.6 según configuración.
    """

    arr = np.asarray(values, dtype=float)
    n_total = arr.size
    if n_total == 0:
        return float("nan"), 0, float("nan")

    # Ventana: últimos N meses
    w = min(cfg.ventana_meses, n_total)
    ventana = arr[-w:].copy()
    est_ventana = None
    if estimadas is not None:
        est_ventana = np.asarray(estimadas, dtype=bool)[-w:]
    meses_ventana = meses[-w:] if meses is not None else None

    # Máscara de validez
    valido = np.isfinite(ventana)
    if cfg.excluir_ceros_suspendidos and suspendido:
        valido &= ventana > 0
    if cfg.excluir_estimadas and est_ventana is not None:
        valido &= ~est_ventana

    v = ventana[valido]
    n_validos = int(v.size)
    if n_validos == 0:
        return 0.0, 0, float("nan")

    cv = float(np.std(v) / np.mean(v)) if np.mean(v) > 0 else float("nan")

    metodo = cfg.metodo
    if metodo == MetodoPromedio.MEDIA:
        rep = float(np.mean(v))
    elif metodo == MetodoPromedio.MEDIANA:
        rep = float(np.median(v))
    elif metodo == MetodoPromedio.MEDIA_RECORTADA:
        rep = _trimmed_mean(v, cfg.recorte_pct)
    elif metodo == MetodoPromedio.MEDIA_PONDERADA:
        # reconstruye la serie válida en orden temporal para ponderar por recencia
        serie_valida = ventana.copy()
        serie_valida[~valido] = np.nan
        rep = _weighted_mean_recency(serie_valida, cfg.half_life_meses)
    elif metodo == MetodoPromedio.ESTACIONAL:
        rep = _seasonal_mean(ventana, valido, meses_ventana)
    else:  # pragma: no cover - enum cerrado
        rep = float(np.mean(v))

    return rep, n_validos, cv


def _seasonal_mean(
    ventana: np.ndarray, valido: np.ndarray, meses: list[date] | None
) -> float:
    """Media que corrige estacionalidad: promedia primero por mes-calendario y
    luego entre meses. Si no hay fechas, cae a media simple."""

    v = ventana[valido]
    if meses is None:
        return float(np.mean(v)) if v.size else float("nan")
    por_mes: dict[int, list[float]] = {}
    for i, ok in enumerate(valido):
        if ok:
            por_mes.setdefault(meses[i].month, []).append(float(ventana[i]))
    medias = [np.mean(vs) for vs in por_mes.values() if vs]
    return float(np.mean(medias)) if medias else float("nan")


def average_consumption(
    consumo_largo: pd.DataFrame,
    cfg: AveragingConfig,
    *,
    suspendidos: set[str] | None = None,
) -> AveragingResult:
    """Aplica el promedio a todo el universo (formato largo -> por cliente).

    ``consumo_largo`` debe tener columnas ``contract_account``, ``period``,
    ``kwh`` y opcionalmente ``is_estimated``.
    """

    suspendidos = suspendidos or set()
    df = consumo_largo.sort_values(["contract_account", "period"])
    tiene_est = "is_estimated" in df.columns

    registros = []
    for cuenta, grupo in df.groupby("contract_account", sort=False):
        vals = grupo["kwh"].to_numpy(dtype=float)
        meses = list(grupo["period"])
        est = grupo["is_estimated"].to_numpy(dtype=bool) if tiene_est else None
        rep, n_val, cv = average_series(
            vals,
            cfg,
            meses=meses,
            estimadas=est,
            suspendido=cuenta in suspendidos,
        )
        registros.append(
            {
                "contract_account": cuenta,
                "kwh_representativo": rep,
                "n_meses_validos": n_val,
                "cv": cv,
                "confiable": n_val >= cfg.min_meses_validos,
            }
        )

    por_cliente = pd.DataFrame.from_records(registros)
    return AveragingResult(
        por_cliente=por_cliente,
        metodo=cfg.metodo.value,
        ventana_meses=cfg.ventana_meses,
    )
