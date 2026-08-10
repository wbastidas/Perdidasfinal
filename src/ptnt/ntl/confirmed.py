"""Histórico de hurtos confirmados (clientes multados): validación y calibración.

La especificación asumía que **no** existía histórico de hurto confirmado y por eso
v1.0 quedaba como marco no supervisado. Si la distribuidora **sí tiene** la base de
clientes multados de los últimos años, ese dato cambia el proyecto de forma
material — es el activo de mayor valor después de la energía de cabecera:

1. **Medición honesta de precisión.** Sin etiquetas, "el detector funciona" es una
   afirmación sin respaldo. Con ellas se calculan precisión, recall, **lift** y la
   curva de aciertos por decil: cuántos hurtos reales caen en el top-N del ranking.
2. **Calibración de umbrales.** Los umbrales de las señales S1–S10 dejan de ser
   supuestos del catálogo y se ajustan al comportamiento observado en los casos
   confirmados de *esta* distribuidora.
3. **Aprendizaje PU (Positivo–No etiquetado).** Es el método correcto aquí: los
   multados son positivos confiables, pero **los no multados NO son negativos** —
   solo son "no inspeccionados o no detectados". Tratarlos como negativos es el
   error que arruina estos proyectos. Se usa el estimador de Elkan–Noto, que
   corrige la probabilidad dividiendo por la propensión ``c = P(etiquetado|positivo)``.

**Fuga temporal:** las multas tienen fecha. Una señal calculada con meses
posteriores a la multa "predice" algo que ya ocurrió. Por eso las etiquetas se
filtran por fecha de corte y se avisa si no hay fechas disponibles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd


@dataclass
class ConfirmedTheftSet:
    """Base de hurtos confirmados (clientes multados)."""

    cuentas: set[str] = field(default_factory=set)
    por_cuenta: dict[str, dict] = field(default_factory=dict)  # cuenta -> {fecha, kwh_recuperado, tipo}
    n: int = 0
    con_fecha: int = 0
    rango_fechas: tuple[str, str] | None = None
    advertencias: list[str] = field(default_factory=list)


def load_confirmed_theft(
    df: pd.DataFrame,
    *,
    cuenta_col: str = "contract_account",
    fecha_col: str | None = "fecha_multa",
    kwh_col: str | None = "kwh_recuperado",
    tipo_col: str | None = "tipo_hallazgo",
    fecha_corte: date | str | None = None,
) -> ConfirmedTheftSet:
    """Carga la base de multados, filtrando por fecha de corte para evitar fuga.

    ``fecha_corte``: solo se conservan las multas **anteriores** a esa fecha, para
    que las señales calculadas con datos previos no "predigan" el futuro.
    """

    out = ConfirmedTheftSet()
    if df is None or df.empty:
        out.advertencias.append("Base de multados vacía.")
        return out

    d = df.copy()
    d[cuenta_col] = d[cuenta_col].astype(str)

    fechas = None
    if fecha_col and fecha_col in d.columns:
        fechas = pd.to_datetime(d[fecha_col], errors="coerce")
        out.con_fecha = int(fechas.notna().sum())
        if fecha_corte is not None:
            corte = pd.Timestamp(fecha_corte)
            antes = len(d)
            d = d[fechas.isna() | (fechas < corte)]
            fechas = pd.to_datetime(d[fecha_col], errors="coerce")
            if len(d) < antes:
                out.advertencias.append(
                    f"{antes - len(d)} multas posteriores a {corte.date()} excluidas "
                    "para evitar fuga temporal."
                )
        validas = fechas.dropna()
        if not validas.empty:
            out.rango_fechas = (str(validas.min().date()), str(validas.max().date()))
    else:
        out.advertencias.append(
            "Sin fecha de multa: no se puede descartar fuga temporal. Las métricas "
            "de precisión deben leerse con cautela."
        )

    for i, (_, r) in enumerate(d.iterrows()):
        cuenta = str(r[cuenta_col])
        out.cuentas.add(cuenta)
        info = {}
        if fechas is not None and i < len(fechas):
            f = fechas.iloc[i]
            info["fecha"] = None if pd.isna(f) else str(f.date())
        if kwh_col and kwh_col in d.columns:
            info["kwh_recuperado"] = float(pd.to_numeric(r.get(kwh_col), errors="coerce") or 0)
        if tipo_col and tipo_col in d.columns:
            info["tipo"] = str(r.get(tipo_col, ""))
        out.por_cuenta[cuenta] = info

    out.n = len(out.cuentas)
    return out


# --------------------------------------------------------------------------- #
# Validación del detector contra los casos confirmados
# --------------------------------------------------------------------------- #
@dataclass
class ValidationMetrics:
    """Métricas honestas del detector medidas contra los hurtos confirmados."""

    n_confirmados_en_universo: int
    n_universo: int
    prevalencia: float                 # tasa base de hurto confirmado
    precision_at_k: dict[int, float] = field(default_factory=dict)
    recall_at_k: dict[int, float] = field(default_factory=dict)
    lift_at_k: dict[int, float] = field(default_factory=dict)
    por_decil: pd.DataFrame = field(default_factory=pd.DataFrame)
    posicion_mediana_pct: float = 0.0
    auc_aproximada: float = 0.0
    advertencias: list[str] = field(default_factory=list)

    def resumen(self) -> dict:
        return {
            "n_confirmados": self.n_confirmados_en_universo,
            "n_universo": self.n_universo,
            "prevalencia_pct": round(self.prevalencia * 100, 3),
            "precision_top_1pct": round(self.precision_at_k.get(1, 0) * 100, 2),
            "precision_top_5pct": round(self.precision_at_k.get(5, 0) * 100, 2),
            "recall_top_10pct": round(self.recall_at_k.get(10, 0) * 100, 2),
            "lift_top_5pct": round(self.lift_at_k.get(5, 0), 2),
            "posicion_mediana_pct": round(self.posicion_mediana_pct, 1),
            "auc": round(self.auc_aproximada, 3),
        }


def validate_against_confirmed(
    ranking: pd.DataFrame,
    confirmados: ConfirmedTheftSet,
    *,
    cuenta_col: str = "contract_account",
    score_col: str = "score",
    ks_pct: tuple[int, ...] = (1, 5, 10, 20),
) -> ValidationMetrics:
    """Mide el desempeño real del detector contra los clientes multados.

    El **lift** es la métrica más útil para gestión: cuántas veces más hurtos
    encuentra la cuadrilla siguiendo el ranking frente a inspeccionar al azar.
    """

    if ranking is None or ranking.empty:
        return ValidationMetrics(0, 0, 0.0, advertencias=["Ranking vacío."])

    r = ranking.copy()
    r[cuenta_col] = r[cuenta_col].astype(str)
    r = r.sort_values(score_col, ascending=False).reset_index(drop=True)
    n = len(r)
    r["_es_hurto"] = r[cuenta_col].isin(confirmados.cuentas)
    n_pos = int(r["_es_hurto"].sum())

    m = ValidationMetrics(
        n_confirmados_en_universo=n_pos, n_universo=n,
        prevalencia=(n_pos / n) if n else 0.0,
    )
    if n_pos == 0:
        m.advertencias.append(
            "Ningún cliente multado aparece en el universo analizado: no se puede "
            "medir precisión. Verifique que las cuentas contrato coincidan."
        )
        return m

    for k in ks_pct:
        top = max(1, int(round(n * k / 100.0)))
        aciertos = int(r.head(top)["_es_hurto"].sum())
        m.precision_at_k[k] = aciertos / top
        m.recall_at_k[k] = aciertos / n_pos
        m.lift_at_k[k] = (aciertos / top) / m.prevalencia if m.prevalencia > 0 else 0.0

    # Posición mediana de los confirmados en el ranking (%)
    posiciones = (r.index[r["_es_hurto"]].to_numpy() + 1) / n * 100.0
    m.posicion_mediana_pct = float(np.median(posiciones))

    # AUC aproximada (probabilidad de que un positivo tenga mayor score que un negativo)
    rangos = np.arange(1, n + 1)
    suma_rangos_pos = float(np.sum(rangos[r["_es_hurto"].to_numpy()]))
    n_neg = n - n_pos
    if n_neg > 0:
        # rangos ascendentes por score descendente -> invertir
        u = n_pos * n_neg + n_pos * (n_pos + 1) / 2 - suma_rangos_pos
        m.auc_aproximada = float(np.clip(u / (n_pos * n_neg), 0, 1))

    # Aciertos por decil (curva de ganancia)
    r["_decil"] = pd.qcut(r.index, 10, labels=False, duplicates="drop") + 1
    m.por_decil = (
        r.groupby("_decil")
        .agg(clientes=(cuenta_col, "count"), hurtos_confirmados=("_es_hurto", "sum"))
        .reset_index()
        .rename(columns={"_decil": "decil"})
    )
    m.por_decil["tasa_acierto_pct"] = (
        m.por_decil["hurtos_confirmados"] / m.por_decil["clientes"] * 100
    ).round(2)

    if n_pos < 30:
        m.advertencias.append(
            f"Solo {n_pos} casos confirmados en el universo: las métricas tienen "
            "alta incertidumbre estadística."
        )
    return m


# --------------------------------------------------------------------------- #
# Aprendizaje PU (Positivo–No etiquetado)
# --------------------------------------------------------------------------- #
@dataclass
class PUResult:
    scores: pd.Series                  # probabilidad corregida por cuenta
    c_propension: float                # P(etiquetado | positivo)
    n_positivos: int
    modelo: str
    advertencias: list[str] = field(default_factory=list)


def pu_learning(
    features: pd.DataFrame,
    confirmados: ConfirmedTheftSet,
    *,
    cuenta_col: str = "contract_account",
    feature_cols: list[str] | None = None,
    random_state: int = 20260807,
) -> PUResult | None:
    """Entrena un clasificador PU (Elkan–Noto) con los multados como positivos.

    **Los no multados no son negativos**: son no etiquetados (no inspeccionados o
    no detectados). El estimador corrige la salida dividiendo por la propensión
    ``c = P(s=1 | y=1)``, estimada sobre un conjunto de validación de positivos.

    Devuelve ``None`` si no hay scikit-learn o faltan positivos suficientes.
    """

    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import train_test_split
    except Exception:
        return None

    if features is None or features.empty or confirmados.n == 0:
        return None

    df = features.copy()
    df[cuenta_col] = df[cuenta_col].astype(str)
    cols = feature_cols or [
        c for c in df.columns
        if c != cuenta_col and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not cols:
        return None

    y = df[cuenta_col].isin(confirmados.cuentas).astype(int)   # s: etiquetado
    n_pos = int(y.sum())
    if n_pos < 10:
        return PUResult(
            scores=pd.Series(0.0, index=df[cuenta_col]), c_propension=1.0,
            n_positivos=n_pos, modelo="ninguno",
            advertencias=[f"Solo {n_pos} positivos: insuficientes para PU learning "
                          "(se recomiendan al menos 10, idealmente 100+)."],
        )

    X = df[cols].fillna(0.0).to_numpy(dtype=float)
    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y.to_numpy(), test_size=0.3, random_state=random_state, stratify=y
    )
    modelo = GradientBoostingClassifier(random_state=random_state)
    modelo.fit(X_tr, y_tr)

    # Propensión c = media de P(s=1|x) sobre los positivos de validación
    p_val = modelo.predict_proba(X_val)[:, 1]
    pos_val = p_val[y_val == 1]
    c = float(np.mean(pos_val)) if pos_val.size else 1.0
    c = float(np.clip(c, 1e-3, 1.0))

    p = modelo.predict_proba(X)[:, 1] / c     # corrección de Elkan–Noto
    scores = pd.Series(np.clip(p, 0, 1), index=df[cuenta_col].to_numpy())

    advert = []
    if c < 0.1:
        advert.append(
            f"Propensión estimada muy baja (c={c:.3f}): la base de multados cubre "
            "una fracción pequeña de los hurtos reales; los scores corregidos son "
            "muy sensibles a esta estimación."
        )
    return PUResult(scores=scores, c_propension=c, n_positivos=n_pos,
                    modelo="GradientBoosting+ElkanNoto", advertencias=advert)


# --------------------------------------------------------------------------- #
# Calibración de umbrales de señales
# --------------------------------------------------------------------------- #
def calibrate_signal_thresholds(
    señales: pd.DataFrame,
    confirmados: ConfirmedTheftSet,
    *,
    cuenta_col: str = "contract_account",
    columnas_senal: list[str] | None = None,
) -> pd.DataFrame:
    """Mide qué señales discriminan de verdad, usando los casos confirmados.

    Para cada señal calcula su tasa de activación entre confirmados vs. el resto y
    el **lift** correspondiente. Una señal con lift ≈ 1 no aporta y debería
    revisarse o desactivarse; una con lift alto merece más peso en el consenso.
    """

    if señales is None or señales.empty or confirmados.n == 0:
        return pd.DataFrame()

    s = señales.copy()
    s[cuenta_col] = s[cuenta_col].astype(str)
    cols = columnas_senal or [c for c in s.columns if c.startswith("S")]
    es_hurto = s[cuenta_col].isin(confirmados.cuentas)
    n_pos, n_neg = int(es_hurto.sum()), int((~es_hurto).sum())
    if n_pos == 0 or n_neg == 0:
        return pd.DataFrame()

    filas = []
    for c in cols:
        if c not in s.columns:
            continue
        activa = pd.to_numeric(s[c], errors="coerce").fillna(0) > 0
        a = float((activa & es_hurto).sum())      # activaciones en confirmados
        b = float((activa & ~es_hurto).sum())     # activaciones en el resto
        tasa_pos = a / n_pos
        tasa_neg = b / n_neg
        # Lift con suavizado aditivo: una señal que solo se activa en confirmados
        # daría lift infinito y rompería el orden; el suavizado lo acota y además
        # penaliza correctamente las señales con pocas observaciones.
        lift = ((a + 0.5) / (n_pos + 1.0)) / ((b + 0.5) / (n_neg + 1.0))
        filas.append({
            "senal": c,
            "activacion_en_confirmados_pct": round(tasa_pos * 100, 2),
            "activacion_en_resto_pct": round(tasa_neg * 100, 2),
            "n_activaciones_confirmados": int(a),
            "lift": round(float(lift), 2),
            "recomendacion": (
                "Aumentar peso" if lift > 2 else
                "Mantener" if lift > 1.2 else
                "Revisar umbral o desactivar (no discrimina)"
            ),
        })
    return pd.DataFrame(filas).sort_values("lift", ascending=False, na_position="last")
