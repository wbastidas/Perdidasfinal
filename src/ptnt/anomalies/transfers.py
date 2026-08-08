"""Detección de transferencias de carga entre alimentadores no reportadas (§10.4).

Es la **causa número uno de balances que no cierran**: una maniobra que pasa carga
del alimentador A al B (sin registrarse en el log de conmutación) produce PNT
falsamente alta en A y negativa en B. Reportar eso como hurto y enviar cuadrillas
es el error más caro del proyecto.

Método (sin log de conmutación disponible):

1. Se calcula la **variación mes a mes** de la energía de cabecera de cada alimentador.
2. Se buscan pares con cambios **abruptos, de signo opuesto y magnitud similar** en
   el mismo mes: lo que un alimentador pierde, el vecino lo gana.
3. Se puntúa cada par por la similitud de magnitud y la abruptez del cambio, y se
   pide que el cambio sea **sostenido** (no un pico de un solo mes).
4. Los alimentadores implicados se marcan para **excluirlos del ranking de sospecha**
   hasta aclarar la maniobra.

Restricción honesta (§10.4): con **un solo mes** de energía de cabecera la detección
por serie temporal **no es posible**. En ese caso se devuelve el estado
``NO_APLICABLE_POR_DATOS`` en vez de inventar candidatos — y esa es justamente la
razón de peso para conseguir los 8 meses de cabecera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class TransferDetectionStatus(str, Enum):
    OK = "OK"
    NO_APLICABLE_POR_DATOS = "NO_APLICABLE_POR_DATOS"


@dataclass
class TransferCandidate:
    """Par de alimentadores con una transferencia probable no reportada."""

    feeder_a: str
    feeder_b: str
    period: str
    delta_a_kwh: float          # variación de A en ese mes (negativa si pierde)
    delta_b_kwh: float          # variación de B (signo opuesto)
    magnitude_kwh: float        # magnitud transferida estimada
    similarity: float           # 0–1: qué tan simétrico es el intercambio
    sustained: bool             # el cambio se mantiene los meses siguientes
    confidence: float           # 0–1
    evidence: dict = field(default_factory=dict)

    @property
    def descripcion(self) -> str:
        return (
            f"{self.feeder_a} pierde {abs(self.delta_a_kwh):,.0f} kWh y "
            f"{self.feeder_b} gana {abs(self.delta_b_kwh):,.0f} kWh en {self.period}: "
            f"transferencia probable de {self.magnitude_kwh:,.0f} kWh"
        )


@dataclass
class TransferReport:
    status: TransferDetectionStatus
    candidates: list[TransferCandidate] = field(default_factory=list)
    feeders_afectados: set[str] = field(default_factory=set)
    detail: str = ""
    n_periodos: int = 0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "alimentador_a": c.feeder_a, "alimentador_b": c.feeder_b,
                "periodo": c.period,
                "delta_a_kwh": round(c.delta_a_kwh, 1),
                "delta_b_kwh": round(c.delta_b_kwh, 1),
                "magnitud_kwh": round(c.magnitude_kwh, 1),
                "simetria": round(c.similarity, 3),
                "sostenido": c.sustained,
                "confianza": round(c.confidence, 3),
                "descripcion": c.descripcion,
            }
            for c in self.candidates
        ])


def detect_transfers(
    head_energy: pd.DataFrame,
    *,
    feeder_col: str = "feeder_code",
    period_col: str = "period",
    kwh_col: str = "kwh_delivered",
    cambio_min_pct: float = 10.0,
    magnitud_min_kwh: float = 0.0,
    simetria_min: float = 0.60,
    exigir_sostenido: bool = True,
    vecinos: dict[str, set[str]] | None = None,
) -> TransferReport:
    """Detecta transferencias probables entre alimentadores.

    ``head_energy`` es la energía de cabecera en formato largo (alimentador,
    período, kWh). ``vecinos`` restringe los pares a alimentadores eléctricamente
    vecinos (si se conoce la topología de enlaces); sin él se consideran todos los
    pares, lo que puede producir coincidencias espurias que el operador debe filtrar.

    El umbral relativo se exige a **al menos uno** de los dos alimentadores, no a
    ambos: la misma transferencia en kWh representa un porcentaje distinto según el
    tamaño de cada alimentador, y exigirlo a los dos deja escapar transferencias
    reales entre un alimentador chico y uno grande. La simetría ya garantiza que
    ambos se movieron en magnitudes comparables.
    """

    if head_energy is None or head_energy.empty:
        return TransferReport(
            status=TransferDetectionStatus.NO_APLICABLE_POR_DATOS,
            detail="Sin energía de cabecera: la detección de transferencias no es posible.",
        )

    piv = head_energy.pivot_table(
        index=period_col, columns=feeder_col, values=kwh_col, aggfunc="sum"
    ).sort_index()
    n_periodos = int(piv.shape[0])

    if n_periodos < 3:
        return TransferReport(
            status=TransferDetectionStatus.NO_APLICABLE_POR_DATOS,
            n_periodos=n_periodos,
            detail=(
                f"Solo {n_periodos} período(s) de cabecera: la detección de "
                "transferencias por serie temporal requiere al menos 3. "
                "Se marca NO_APLICABLE_POR_DATOS y se eleva la incertidumbre del "
                "balance (§10.4); conseguir más meses de cabecera lo desbloquea."
            ),
        )

    deltas = piv.diff()               # variación mes a mes
    alimentadores = list(piv.columns)
    candidatos: list[TransferCandidate] = []

    for i, per in enumerate(deltas.index):
        fila = deltas.loc[per]
        if fila.isna().all():
            continue
        for ia, fa in enumerate(alimentadores):
            da = fila.get(fa)
            if da is None or not np.isfinite(da) or da >= 0:
                continue   # A debe PERDER energía
            base_a = piv[fa].iloc[max(0, i - 1)]
            if not np.isfinite(base_a) or base_a <= 0:
                continue
            pct_a = abs(da) / base_a * 100.0
            for fb in alimentadores[ia + 1:]:
                if vecinos is not None and fb not in vecinos.get(fa, set()):
                    continue
                db = fila.get(fb)
                if db is None or not np.isfinite(db) or db <= 0:
                    continue   # B debe GANAR energía
                base_b = piv[fb].iloc[max(0, i - 1)]
                if not np.isfinite(base_b) or base_b <= 0:
                    continue
                pct_b = abs(db) / base_b * 100.0
                # Basta con que el cambio sea abrupto para UNO de los dos: la
                # misma transferencia pesa distinto en un alimentador chico que
                # en uno grande.
                if max(pct_a, pct_b) < cambio_min_pct:
                    continue
                # simetría: lo que uno pierde, el otro gana
                simetria = min(abs(da), abs(db)) / max(abs(da), abs(db))
                if simetria < simetria_min:
                    continue
                magnitud_par = (abs(da) + abs(db)) / 2.0
                if magnitud_par < magnitud_min_kwh:
                    continue
                # sostenido: el nivel se mantiene el mes siguiente
                sostenido = True
                if i + 1 < len(deltas.index):
                    sig = deltas.iloc[i + 1]
                    da2, db2 = sig.get(fa), sig.get(fb)
                    if np.isfinite(da2) and np.isfinite(db2):
                        # si revierte casi por completo, fue un pico, no transferencia
                        revierte = (da2 > 0 and abs(da2) > 0.7 * abs(da)) and \
                                   (db2 < 0 and abs(db2) > 0.7 * abs(db))
                        sostenido = not revierte
                if exigir_sostenido and not sostenido:
                    continue
                magnitud = magnitud_par
                confianza = float(np.clip(
                    0.5 * simetria + 0.3 * (1.0 if sostenido else 0.0) +
                    0.2 * min(max(pct_a, pct_b) / 100.0, 1.0), 0, 1
                ))
                candidatos.append(TransferCandidate(
                    feeder_a=str(fa), feeder_b=str(fb), period=str(per),
                    delta_a_kwh=float(da), delta_b_kwh=float(db),
                    magnitude_kwh=float(magnitud), similarity=float(simetria),
                    sustained=bool(sostenido), confidence=confianza,
                    evidence={"base_a_kwh": float(base_a), "base_b_kwh": float(base_b)},
                ))

    candidatos.sort(key=lambda c: c.confidence, reverse=True)
    afectados = {c.feeder_a for c in candidatos} | {c.feeder_b for c in candidatos}
    detalle = (
        f"{len(candidatos)} transferencia(s) probable(s) en {n_periodos} períodos. "
        f"Alimentadores afectados: {', '.join(sorted(afectados)) or 'ninguno'}. "
        "Se recomienda excluirlos del ranking de sospecha hasta aclarar la maniobra."
        if candidatos else
        f"Sin transferencias detectadas en {n_periodos} períodos."
    )
    return TransferReport(
        status=TransferDetectionStatus.OK, candidates=candidatos,
        feeders_afectados=afectados, detail=detalle, n_periodos=n_periodos,
    )
