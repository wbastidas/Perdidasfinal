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
    "S9": "Consumo muy por debajo de su propio nivel histórico habitual",
    "UNSUP": "Patrón atípico detectado por análisis no supervisado",
}


@dataclass
class NtlRanking:
    """Ranking de clientes por sospecha de PNT."""

    ranking: pd.DataFrame  # contract_account, score, rank, razones, recuperable_kwh
    n_clientes: int
    n_sospechosos: int
    # "segmentado" (contra el grupo par del cliente) o "global" (mediana única).
    # Se reporta para que nadie interprete un recuperable global como si fuera fino.
    metodo_recuperable: str = "global"

    def por_clase(self) -> pd.DataFrame:
        """Resumen del ranking por clase de consumo (si el padrón venía segmentado)."""

        if "clase_consumo" not in self.ranking.columns:
            return pd.DataFrame()
        return (
            self.ranking.groupby("clase_consumo")
            .agg(
                clientes=("contract_account", "count"),
                sospechosos=("n_senales_activas", lambda s: int((s > 0).sum())),
                recuperable_kwh_mes=("recuperable_kwh_mes", "sum"),
                score_medio=("score", "mean"),
            )
            .reset_index()
            .sort_values("recuperable_kwh_mes", ascending=False)
        )


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
    clientes: pd.DataFrame | None = None,
) -> NtlRanking:
    """Produce el ranking de sospecha por consenso de rangos.

    * Cada señal aporta su intensidad [0,1].
    * El score de consenso es el promedio de los rangos percentiles de las señales
      activas más el score no supervisado (si está disponible).
    * ``recuperable_kwh`` estima la energía mensual recuperable **dentro del
      segmento** del cliente cuando ``clientes`` trae la segmentación
      (`ptnt.segment`); si no, cae al método global, mucho más grueso.

    ``clientes`` es el padrón segmentado (columnas ``grupo_par_id``,
    ``consumo_base_kwh``, ``clase_consumo``, ``es_gran_cliente``). Aportarlo cambia
    el resultado de forma importante: sin él, el recuperable se mide contra una
    **mediana global** que mezcla residenciales con industriales, lo que subestima
    gravemente a los grandes consumidores —donde está la energía— y le inventa
    recuperable a los pequeños.
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

    # --- Energía recuperable estimada ---------------------------------------
    recuperable = np.zeros(n)
    metodo_recuperable = "no_calculado"
    if consumo_medio is not None:
        cm_serie = consumo_medio.reindex(cuentas)
        cm = cm_serie.to_numpy(dtype=float)
        deficit_signals = np.isin(columnas_senal, ["S4", "S5", "S8", "S9"])
        tiene_deficit = (
            (S[:, deficit_signals] > 0).any(axis=1)
            if deficit_signals.any() else np.zeros(n, bool)
        )

        segmentado = (
            clientes is not None
            and "grupo_par_id" in clientes.columns
            and "consumo_base_kwh" in clientes.columns
        )
        if segmentado:
            from ptnt.segment.peers import (
                consumo_esperado_por_grupo,
                energia_recuperable,
            )

            cli = clientes.set_index("contract_account")
            grupos = cli["grupo_par_id"].reindex(cuentas)
            grupos.index = cm_serie.index
            base = cli["consumo_base_kwh"].reindex(cuentas)
            base.index = cm_serie.index
            esperado = consumo_esperado_por_grupo(cm_serie, grupos)
            rec = energia_recuperable(cm_serie, esperado, base).to_numpy(dtype=float)
            recuperable = np.where(tiene_deficit, rec, 0.0)
            metodo_recuperable = "segmentado"
        else:
            # Método global: una única mediana para todo el padrón. Se conserva
            # como respaldo cuando no hay segmentación, pero es notoriamente
            # sesgado — ver el docstring.
            mediana_global = np.nanmedian(cm[cm > 0]) if np.any(cm > 0) else 0.0
            recuperable = np.where(
                tiene_deficit, np.maximum(mediana_global - np.nan_to_num(cm), 0.0), 0.0
            )
            metodo_recuperable = "global"

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

    # Arrastra el segmento al ranking: sin esto el resultado no se puede leer por
    # clase ni entregar a dos cuadrillas distintas (masiva vs. grandes clientes).
    if clientes is not None:
        cols_seg = [
            c for c in (
                "clase_consumo", "nivel_tension", "estrato_consumo", "segmento",
                "grupo_par_nivel", "es_gran_cliente", "consumo_base_kwh",
            )
            if c in clientes.columns
        ]
        if cols_seg:
            df = df.merge(
                clientes[["contract_account", *cols_seg]],
                on="contract_account", how="left",
            )

    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, n + 1)

    umbral = np.nanpercentile(score, 100 * (1 - cfg.contaminacion)) if n else 0.0
    n_sosp = int(np.sum(score >= umbral)) if n else 0

    return NtlRanking(
        ranking=df, n_clientes=n, n_sospechosos=n_sosp,
        metodo_recuperable=metodo_recuperable,
    )
