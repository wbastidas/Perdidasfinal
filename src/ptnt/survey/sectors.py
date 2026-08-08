"""Sectores de sospecha: agrupamiento geográfico de clientes priorizados (§11.5).

Agrupa espacialmente los clientes de mayor score para delimitar **sectores** que
una cuadrilla puede recorrer en una salida, en lugar de direcciones dispersas.

Usa HDBSCAN si está disponible (``scikit-learn>=1.3`` lo incluye) y, si no, cae a
un agrupamiento por rejilla espacial (grid) equivalente en intención: agrupar lo
que está cerca. La rejilla no requiere dependencias y es determinista.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Sector:
    """Sector geográfico de sospecha, recorrible por una cuadrilla."""

    sector_id: str
    customers: list[str] = field(default_factory=list)
    n_customers: int = 0
    suspect_energy_kwh: float = 0.0     # energía bajo sospecha del sector
    mean_score: float = 0.0
    max_score: float = 0.0
    centroid_x: float = 0.0
    centroid_y: float = 0.0
    radius_m: float = 0.0               # radio que cubre el sector
    feeder_code: str | None = None
    algorithm: str = "grid"


def _grid_labels(x: np.ndarray, y: np.ndarray, cell_m: float) -> np.ndarray:
    """Etiquetas de agrupamiento por rejilla: celdas de ``cell_m`` metros."""

    gx = np.floor(x / cell_m).astype(int)
    gy = np.floor(y / cell_m).astype(int)
    claves = {}
    labels = np.empty(x.size, dtype=int)
    for i, (a, b) in enumerate(zip(gx, gy)):
        k = (a, b)
        if k not in claves:
            claves[k] = len(claves)
        labels[i] = claves[k]
    return labels


def _hdbscan_labels(x: np.ndarray, y: np.ndarray, min_cluster_size: int) -> np.ndarray | None:
    try:
        from sklearn.cluster import HDBSCAN
    except Exception:
        return None
    if x.size < max(min_cluster_size, 3):
        return None
    try:
        datos = np.column_stack([x, y])
        try:
            modelo = HDBSCAN(min_cluster_size=max(2, min_cluster_size), copy=True)
        except TypeError:  # scikit-learn < 1.5 no acepta 'copy'
            modelo = HDBSCAN(min_cluster_size=max(2, min_cluster_size))
        return modelo.fit_predict(datos)
    except Exception:
        return None


def cluster_sectors(
    priorizados: pd.DataFrame,
    *,
    x_col: str = "x",
    y_col: str = "y",
    score_col: str = "score",
    energy_col: str = "recuperable_kwh_mes",
    id_col: str = "contract_account",
    feeder_col: str | None = "feeder_code",
    min_cluster_size: int = 5,
    cell_m: float = 300.0,
    usar_hdbscan: bool = True,
) -> list[Sector]:
    """Agrupa los clientes priorizados en sectores geográficos de sospecha.

    ``priorizados`` debe traer coordenadas (x, y en metros, EPSG:32717), score y
    energía recuperable. Devuelve los sectores ordenados por energía bajo sospecha
    (el criterio operativo: primero donde más se recupera).
    """

    df = priorizados.dropna(subset=[x_col, y_col]).copy()
    if df.empty:
        return []
    x = df[x_col].to_numpy(dtype=float)
    y = df[y_col].to_numpy(dtype=float)

    labels = _hdbscan_labels(x, y, min_cluster_size) if usar_hdbscan else None
    algoritmo = "HDBSCAN"
    # HDBSCAN puede etiquetar TODO como ruido (-1) con densidad uniforme, lo que
    # dejaría al plan sin sectores. En ese caso se cae a la rejilla, que siempre
    # agrupa: perder los sectores sería perder el nivel más accionable del plan.
    if labels is None or not (labels >= 0).any():
        labels = _grid_labels(x, y, cell_m)
        algoritmo = "grid"

    df["_label"] = labels
    sectores: list[Sector] = []
    for label, sub in df.groupby("_label"):
        if label == -1:  # ruido de HDBSCAN: no forma sector
            continue
        cx = float(sub[x_col].mean())
        cy = float(sub[y_col].mean())
        # radio = distancia máxima al centroide
        dist = np.sqrt((sub[x_col] - cx) ** 2 + (sub[y_col] - cy) ** 2)
        feeder = None
        if feeder_col and feeder_col in sub.columns:
            vals = sub[feeder_col].dropna()
            feeder = str(vals.iloc[0]) if not vals.empty else None
        # Identificador anclado a la COORDENADA, no a la etiqueta del algoritmo:
        # el mismo lugar conserva su identificador entre corridas y cargas nuevas,
        # que es lo que necesita una orden de trabajo ya emitida a campo.
        from ptnt.survey.locations import geo_code

        sectores.append(Sector(
            sector_id=geo_code(cx, cy, prefijo="SEC"),
            customers=[str(v) for v in sub[id_col].tolist()],
            n_customers=int(len(sub)),
            suspect_energy_kwh=float(sub[energy_col].sum()) if energy_col in sub.columns else 0.0,
            mean_score=float(sub[score_col].mean()) if score_col in sub.columns else 0.0,
            max_score=float(sub[score_col].max()) if score_col in sub.columns else 0.0,
            centroid_x=cx, centroid_y=cy,
            radius_m=float(dist.max()) if len(dist) else 0.0,
            feeder_code=feeder,
            algorithm=algoritmo,
        ))

    sectores.sort(key=lambda s: (s.suspect_energy_kwh, s.mean_score), reverse=True)
    return sectores


def sectors_to_dataframe(sectores: list[Sector]) -> pd.DataFrame:
    """Convierte los sectores a DataFrame para exportar/visualizar."""

    return pd.DataFrame([
        {
            "sector_id": s.sector_id, "n_clientes": s.n_customers,
            "energia_sospecha_kwh": round(s.suspect_energy_kwh, 1),
            "score_medio": round(s.mean_score, 3), "score_max": round(s.max_score, 3),
            "centroide_x": round(s.centroid_x, 2), "centroide_y": round(s.centroid_y, 2),
            "radio_m": round(s.radius_m, 1), "alimentador": s.feeder_code,
            "algoritmo": s.algorithm,
        }
        for s in sectores
    ])
