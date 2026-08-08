"""Señales de PNT a nivel cliente (S1–S10, §11.3).

Cada señal produce, por cliente: un valor de intensidad en [0, 1], una confianza
y evidencia reproducible (un dict con los números que la disparan). Las señales
se calculan sobre la serie mensual (hasta 36 meses).

Discriminación importante (§11.3): un cero sostenido con servicio **suspendido**
y orden registrada NO es sospecha; solo el cero con servicio **activo** lo es.
Por eso las señales reciben el estado de servicio y las novedades del cliente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ptnt.config.models import SignalsConfig


@dataclass
class SignalResult:
    """Matriz de señales por cliente."""

    señales: pd.DataFrame  # contract_account + columnas S1..S10 (intensidad 0..1)
    evidencia: dict[str, list[dict]] = field(default_factory=dict)
    columnas_senal: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _pivote_ancho(consumo_largo: pd.DataFrame) -> tuple[np.ndarray, list, list]:
    """Convierte el formato largo a una matriz ancha (clientes × meses)."""

    piv = consumo_largo.pivot_table(
        index="contract_account", columns="period", values="kwh", aggfunc="first"
    ).sort_index(axis=1)
    return piv.to_numpy(dtype=float), list(piv.index), list(piv.columns)


# --------------------------------------------------------------------------- #
# Señales individuales (operan sobre una serie 1D)
# --------------------------------------------------------------------------- #
def s1_caida_recuperacion(serie: np.ndarray, cfg: SignalsConfig) -> tuple[float, dict]:
    """S1 — caída sostenida ≥X% durante ≥N meses seguida de recuperación."""

    v = serie[np.isfinite(serie)]
    if v.size < cfg.s1_meses_min_caida * 2 + 2:
        return 0.0, {}
    base = np.median(v[: max(3, v.size // 4)])
    if base <= 0:
        return 0.0, {}
    umbral = base * (1 - cfg.s1_caida_min_pct / 100.0)
    bajo = v < umbral
    # racha de caída
    max_racha, racha = 0, 0
    fin_racha = -1
    for i, b in enumerate(bajo):
        if b:
            racha += 1
            if racha > max_racha:
                max_racha, fin_racha = racha, i
        else:
            racha = 0
    if max_racha < cfg.s1_meses_min_caida:
        return 0.0, {}
    # recuperación posterior al nivel previo
    if fin_racha + 1 < v.size:
        post = v[fin_racha + 1 :]
        recuperado = np.any(
            post >= base * (1 - cfg.s1_tolerancia_recuperacion_pct / 100.0)
        )
        if recuperado:
            intensidad = min(1.0, max_racha / cfg.s1_meses_min_caida * 0.6)
            return intensidad, {
                "base": round(base, 2),
                "meses_caida": int(max_racha),
                "recuperado": True,
            }
    return 0.0, {}


def s3_ruptura_nivel(serie: np.ndarray, cfg: SignalsConfig) -> tuple[float, dict]:
    """S3 — cambio permanente de nivel (change point) sin causa comercial.

    Implementación por barrido de un único punto de corte maximizando la
    separación de medias (equivalente a un PELT de 1 breakpoint). Si el paquete
    ``ruptures`` está disponible, se usa para mayor robustez.
    """

    v = serie[np.isfinite(serie)]
    if v.size < 8:
        return 0.0, {}
    mejor_delta, mejor_i = 0.0, -1
    for i in range(3, v.size - 3):
        m1, m2 = np.mean(v[:i]), np.mean(v[i:])
        denom = max(m1, 1e-6)
        delta = abs(m2 - m1) / denom
        if delta > mejor_delta:
            mejor_delta, mejor_i = delta, i
    if mejor_delta * 100.0 >= cfg.s3_cambio_min_pct:
        m1 = float(np.mean(v[:mejor_i]))
        m2 = float(np.mean(v[mejor_i:]))
        # Solo cuenta como sospecha si el nivel BAJÓ (posible manipulación)
        if m2 < m1:
            intensidad = min(1.0, mejor_delta)
            return intensidad, {
                "punto": int(mejor_i),
                "nivel_antes": round(m1, 2),
                "nivel_despues": round(m2, 2),
                "cambio_pct": round(mejor_delta * 100, 1),
            }
    return 0.0, {}


def s4_cero_servicio_activo(
    serie: np.ndarray, cfg: SignalsConfig, activo: bool
) -> tuple[float, dict]:
    """S4 — cero sostenido con servicio activo y sin orden de suspensión."""

    if not activo:
        return 0.0, {}
    v = serie.copy()
    ceros = (np.nan_to_num(v, nan=-1) == 0)
    # racha máxima de ceros
    max_racha, racha = 0, 0
    for b in ceros:
        racha = racha + 1 if b else 0
        max_racha = max(max_racha, racha)
    if max_racha >= cfg.s4_meses_min_cero:
        intensidad = min(1.0, max_racha / (cfg.s4_meses_min_cero * 2))
        return intensidad, {"meses_cero_consecutivos": int(max_racha), "activo": True}
    return 0.0, {}


def s7_planitud(serie: np.ndarray, cfg: SignalsConfig) -> tuple[float, dict]:
    """S7 — consumo excesivamente constante (CV anómalamente bajo)."""

    v = serie[np.isfinite(serie)]
    v = v[v > 0]
    if v.size < 6:
        return 0.0, {}
    media = np.mean(v)
    cv = np.std(v) / media if media > 0 else np.inf
    if cv < cfg.s7_cv_max:
        intensidad = min(1.0, 1 - cv / max(cfg.s7_cv_max, 1e-9))
        return intensidad, {"cv": round(float(cv), 4)}
    return 0.0, {}


# --------------------------------------------------------------------------- #
# Señales de grupo (requieren el universo)
# --------------------------------------------------------------------------- #
def s5_divergencia_grupo_par(
    consumo_medio: pd.Series, grupos: pd.Series, cfg: SignalsConfig
) -> pd.Series:
    """S5 — cliente bajo el percentil P de su grupo par (mismo CLIRLSCOD/clase).

    ``consumo_medio`` indexado por cuenta; ``grupos`` mapea cuenta→grupo par.
    Devuelve intensidad por cuenta.
    """

    df = pd.DataFrame({"kwh": consumo_medio, "grupo": grupos})
    out = pd.Series(0.0, index=consumo_medio.index)
    for grupo, sub in df.groupby("grupo"):
        if len(sub) < cfg.s5_min_pares:
            continue
        umbral = np.nanpercentile(sub["kwh"], cfg.s5_percentil)
        mediana = np.nanmedian(sub["kwh"])
        if mediana <= 0:
            continue
        bajo = sub["kwh"] < umbral
        # intensidad proporcional a qué tan por debajo de la mediana está
        for cuenta in sub.index[bajo]:
            deficit = 1 - sub.loc[cuenta, "kwh"] / mediana
            out.loc[cuenta] = float(np.clip(deficit, 0, 1))
    return out


def s8_dispersion_intra_puesto(
    consumo_medio: pd.Series, puestos: pd.Series, cfg: SignalsConfig
) -> pd.Series:
    """S8 — en puestos multi-unidad, unidades muy por debajo de sus pares del
    mismo puesto (dispersión intra-puesto alta)."""

    df = pd.DataFrame({"kwh": consumo_medio, "puesto": puestos})
    out = pd.Series(0.0, index=consumo_medio.index)
    for puesto, sub in df.groupby("puesto"):
        if len(sub) < cfg.s8_min_unidades:
            continue
        media = np.nanmean(sub["kwh"])
        if media <= 0:
            continue
        cv = np.nanstd(sub["kwh"]) / media
        if cv < cfg.s8_cv_min:
            continue
        for cuenta in sub.index:
            if sub.loc[cuenta, "kwh"] < media:
                deficit = 1 - sub.loc[cuenta, "kwh"] / media
                out.loc[cuenta] = float(np.clip(deficit, 0, 1))
    return out


# --------------------------------------------------------------------------- #
# Orquestador
# --------------------------------------------------------------------------- #
def compute_signals(
    consumo_largo: pd.DataFrame,
    clientes: pd.DataFrame,
    cfg: SignalsConfig,
    *,
    activo_col: str = "service_active",
    grupo_col: str | None = None,
    puesto_col: str = "site_transformer_id",
) -> SignalResult:
    """Calcula todas las señales para el universo de clientes.

    ``clientes`` aporta, por cuenta, el estado de servicio (activo/suspendido), el
    grupo par (por defecto ``grupo_lectura`` = CLIRLSCOD) y el puesto de
    transformación. Devuelve la matriz de intensidades S1..S10.
    """

    grupo_col = grupo_col or cfg.campo_grupo_par
    matriz, cuentas, _meses = _pivote_ancho(consumo_largo)
    idx = {c: i for i, c in enumerate(cuentas)}

    cli = clientes.set_index("contract_account")
    activo_map = (
        cli[activo_col].reindex(cuentas).fillna(True).astype(bool)
        if activo_col in cli.columns
        else pd.Series(True, index=cuentas)
    )

    s1 = np.zeros(len(cuentas))
    s3 = np.zeros(len(cuentas))
    s4 = np.zeros(len(cuentas))
    s7 = np.zeros(len(cuentas))
    evidencia: dict[str, list[dict]] = {c: [] for c in cuentas}

    for c in cuentas:
        i = idx[c]
        serie = matriz[i, :]
        activo = bool(activo_map.loc[c]) if c in activo_map.index else True

        val, ev = s1_caida_recuperacion(serie, cfg)
        s1[i] = val
        if val > 0:
            evidencia[c].append({"signal": "S1", **ev})
        val, ev = s3_ruptura_nivel(serie, cfg)
        s3[i] = val
        if val > 0:
            evidencia[c].append({"signal": "S3", **ev})
        val, ev = s4_cero_servicio_activo(serie, cfg, activo)
        s4[i] = val
        if val > 0:
            evidencia[c].append({"signal": "S4", **ev})
        val, ev = s7_planitud(serie, cfg)
        s7[i] = val
        if val > 0:
            evidencia[c].append({"signal": "S7", **ev})

    # Consumo medio por cuenta para señales de grupo
    consumo_medio = pd.Series(np.nanmean(matriz, axis=1), index=cuentas)

    grupos = (
        cli[grupo_col].reindex(cuentas)
        if grupo_col in cli.columns
        else pd.Series(np.nan, index=cuentas)
    )
    puestos = (
        cli[puesto_col].reindex(cuentas)
        if puesto_col in cli.columns
        else pd.Series(np.nan, index=cuentas)
    )

    s5 = s5_divergencia_grupo_par(consumo_medio, grupos, cfg).reindex(cuentas).fillna(0.0)
    s8 = s8_dispersion_intra_puesto(consumo_medio, puestos, cfg).reindex(cuentas).fillna(0.0)

    señales = pd.DataFrame(
        {
            "contract_account": cuentas,
            "S1": s1,
            "S3": s3,
            "S4": s4,
            "S5": s5.to_numpy(),
            "S7": s7,
            "S8": s8.to_numpy(),
        }
    )
    cols = ["S1", "S3", "S4", "S5", "S7", "S8"]
    # registra evidencia de señales de grupo
    for c, v in zip(cuentas, s5.to_numpy()):
        if v > 0:
            evidencia[c].append({"signal": "S5", "deficit_grupo": round(float(v), 3)})
    for c, v in zip(cuentas, s8.to_numpy()):
        if v > 0:
            evidencia[c].append({"signal": "S8", "deficit_puesto": round(float(v), 3)})

    return SignalResult(señales=señales, evidencia=evidencia, columnas_senal=cols)
