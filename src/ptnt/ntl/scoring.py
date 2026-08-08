"""Consenso de señales y ranking de sospecha de hurto (§11.3–11.4).

Combina las señales S1–S10 con una detección no supervisada complementaria
(IsolationForest / LOF, opcional vía ``pyod``/``scikit-learn``) por **consenso de
rangos**, produciendo un score por cliente y un ranking con las tres razones
principales en lenguaje operativo y la energía recuperable estimada.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ptnt.config.models import SignalsConfig

_RAZONES = {
    "S1": "Caída y recuperación de consumo (patrón de manipulación temporal)",
    "S3": "Ruptura de nivel de consumo sin causa comercial",
    "S4": "Consumo en cero con servicio activo",
    "S5": "Consumo muy por debajo de su grupo par (misma ruta/clase)",
    "S7": "Consumo anómalamente plano (posible consumo fijo declarado)",
    "S8": "Consumo muy por debajo de sus vecinos del mismo transformador",
    "UNSUP": "Patrón atípico detectado por análisis no supervisado",
}


@dataclass
class NtlRanking:
    """Ranking de clientes por sospecha de PNT."""

    ranking: pd.DataFrame  # contract_account, score, rank, razones, recuperable_kwh
    n_clientes: int
    n_sospechosos: int


def _unsupervised_scores(features: np.ndarray, contaminacion: float) -> np.ndarray | None:
    """Score no supervisado normalizado en [0,1] o None si no hay sklearn."""

    try:
        from sklearn.ensemble import IsolationForest
    except Exception:  # pragma: no cover - sklearn opcional
        return None
    if features.shape[0] < 20:
        return None
    x = np.nan_to_num(features, nan=0.0)
    modelo = IsolationForest(
        contamination=min(max(contaminacion, 0.001), 0.5),
        random_state=20260807,
        n_estimators=150,
    )
    modelo.fit(x)
    # score_samples: mayor = más normal; se invierte y normaliza
    raw = -modelo.score_samples(x)
    lo, hi = np.min(raw), np.max(raw)
    if hi - lo < 1e-12:
        return np.zeros(features.shape[0])
    return (raw - lo) / (hi - lo)


def score_customers(
    señales: pd.DataFrame,
    cfg: SignalsConfig,
    *,
    columnas_senal: list[str],
    consumo_medio: pd.Series | None = None,
    evidencia: dict[str, list[dict]] | None = None,
) -> NtlRanking:
    """Produce el ranking de sospecha por consenso de rangos.

    * Cada señal aporta su intensidad [0,1].
    * El score de consenso es el promedio de los rangos percentiles de las señales
      activas más el score no supervisado (si está disponible).
    * ``recuperable_kwh`` estima la energía mensual recuperable como la diferencia
      entre el consumo esperado del grupo par y el observado, cuando aplica.
    """

    cuentas = señales["contract_account"].to_numpy()
    S = señales[columnas_senal].to_numpy(dtype=float)
    n = len(cuentas)

    # Rango percentil por señal (consenso de rangos)
    rangos = np.zeros_like(S)
    for j in range(S.shape[1]):
        col = S[:, j]
        orden = np.argsort(np.argsort(col))
        rangos[:, j] = orden / max(n - 1, 1)
    # Solo cuentan los rangos de señales activas (>0); si ninguna activa, 0.
    activa = S > 0
    with np.errstate(invalid="ignore"):
        consenso_senales = np.where(
            activa.any(axis=1),
            np.sum(rangos * activa, axis=1) / np.maximum(activa.sum(axis=1), 1),
            0.0,
        )

    score = consenso_senales.copy()
    unsup = None
    if cfg.usar_no_supervisado:
        unsup = _unsupervised_scores(S, cfg.contaminacion)
        if unsup is not None:
            score = 0.7 * consenso_senales + 0.3 * unsup

    # Recuperable estimado
    recuperable = np.zeros(n)
    if consumo_medio is not None:
        cm = consumo_medio.reindex(cuentas).to_numpy(dtype=float)
        mediana_global = np.nanmedian(cm[cm > 0]) if np.any(cm > 0) else 0.0
        # Para clientes con señal de déficit, el hueco frente a la mediana
        deficit_signals = np.isin(columnas_senal, ["S4", "S5", "S8"])
        tiene_deficit = (S[:, deficit_signals] > 0).any(axis=1) if deficit_signals.any() else np.zeros(n, bool)
        recuperable = np.where(
            tiene_deficit, np.maximum(mediana_global - np.nan_to_num(cm), 0.0), 0.0
        )

    # Razones principales (top-3 señales por intensidad)
    razones_list = []
    señal_labels = np.array(columnas_senal)
    for i in range(n):
        fila = S[i, :]
        activos = np.argsort(fila)[::-1]
        top = [señal_labels[j] for j in activos if fila[j] > 0][:3]
        if unsup is not None and unsup[i] > 0.8:
            top = (top + ["UNSUP"])[:3]
        razones_list.append([_RAZONES.get(s, s) for s in top])

    df = pd.DataFrame(
        {
            "contract_account": cuentas,
            "score": score,
            "recuperable_kwh_mes": recuperable,
            "razones": razones_list,
            "n_senales_activas": activa.sum(axis=1),
        }
    )
    if unsup is not None:
        df["score_no_supervisado"] = unsup
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, n + 1)

    umbral = np.nanpercentile(score, 100 * (1 - cfg.contaminacion)) if n else 0.0
    n_sosp = int(np.sum(score >= umbral)) if n else 0

    return NtlRanking(ranking=df, n_clientes=n, n_sospechosos=n_sosp)
