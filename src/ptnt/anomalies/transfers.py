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
class PisoDeteccion:
    """Qué magnitud de transferencia se puede encontrar con estos datos.

    Una detección sin piso declarado no se puede leer: «no se detectó nada» puede
    significar que no hubo maniobras o que las hubo y eran más pequeñas que el
    ruido. Solo el segundo caso justifica pedir más meses de cabecera.
    """

    ruido_pct: float            # dispersión típica del residuo mes a mes
    umbral_pct: float           # el que se exigió en esta corrida
    detectable_pct: float       # a partir de aquí se encuentra de forma fiable
    detectable_kwh: float       # lo mismo, en energía del alimentador mediano

    def explicacion(self) -> str:
        return (
            f"Con estos datos, el residuo mes a mes tiene una dispersión típica de "
            f"{self.ruido_pct:.1f} %. Una transferencia se distingue del ruido a "
            f"partir de ~{self.detectable_pct:.1f} % de la cabecera "
            f"({self.detectable_kwh:,.0f} kWh en un alimentador mediano). "
            f"El umbral exigido fue {self.umbral_pct:.1f} %: por debajo de esa "
            f"magnitud, **una maniobra real pasaría inadvertida**."
        )


@dataclass
class TransferReport:
    status: TransferDetectionStatus
    candidates: list[TransferCandidate] = field(default_factory=list)
    feeders_afectados: set[str] = field(default_factory=set)
    detail: str = ""
    n_periodos: int = 0
    piso: PisoDeteccion | None = None

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
    descontar_movimiento_comun: bool = True,
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

    # --- Descuento del movimiento común del sistema --------------------------
    # En una misma zona todos los alimentadores suben y bajan JUNTOS por
    # estacionalidad (en la Costa, climatización de enero a abril). Buscando
    # pares sobre la variación bruta, cualquier alimentador que baje aparece
    # emparejado con todos los que suben, y el par real se pierde entre falsos
    # positivos: medido sobre el escenario de 12 alimentadores, la variación
    # bruta producía 3 pares espurios y NO encontraba la transferencia inyectada.
    #
    # Se descuenta el movimiento común usando la **mediana** de las variaciones
    # relativas del período (robusta: si un par transfiere, son 2 de N series y
    # no mueven la mediana). Lo que queda es la variación idiosincrática de cada
    # alimentador, que es donde vive la maniobra.
    if descontar_movimiento_comun and piv.shape[1] >= 3:
        rel = piv.pct_change()
        comun = rel.median(axis=1)
        deltas = rel.sub(comun, axis=0) * piv.shift(1)
    else:
        deltas = piv.diff()           # variación bruta mes a mes
    alimentadores = list(piv.columns)
    candidatos: list[TransferCandidate] = []

    # Ruido propio de cada alimentador, en kWh. Un alimentador de 2,8 GWh se
    # desvía del patrón común en cientos de miles de kWh por su cuenta; uno de
    # 1 GWh, en decenas de miles. Sin esto, la simetría en kWh absolutos rechaza
    # las transferencias entre un alimentador grande y uno chico —que son
    # justamente las más habituales, porque se descarga el saturado sobre el que
    # tiene margen—.
    ruido = {a: _ruido_kwh(deltas[a]) for a in alimentadores}

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
                # …pero medida contra el ruido de cada uno, no en crudo. Dos
                # residuos que difieren menos de lo que el mayor se mueve solo
                # son compatibles con una misma transferencia: exigirles
                # proporcionalidad estricta descarta el par real y deja el caso
                # sin explicación.
                holgura = 2.0 * max(ruido.get(fa, 0.0), ruido.get(fb, 0.0))
                compatible = abs(abs(da) - abs(db)) <= holgura
                if simetria < simetria_min and not compatible:
                    continue
                # La magnitud se estima **ponderando cada alimentador por su
                # propio ruido**: los dos miden lo mismo —la energía que cambió
                # de manos— pero el grande la mide peor, porque su desviación
                # estacional propia es de cientos de miles de kWh. Promediarlos
                # a partes iguales le da el mismo voto a la medición mala.
                # Medido sobre el escenario de 20 000 clientes, con 62 600 kWh
                # reales: la media daba 89 200, el mínimo 56 100, y esto 59 000.
                magnitud_par = _magnitud_ponderada(
                    abs(da), ruido.get(fa, 0.0), abs(db), ruido.get(fb, 0.0))
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

                # El discriminador que de verdad separa la maniobra del ruido:
                # una transferencia deja un **escalón permanente en la relación
                # entre los dos alimentadores** —B pasa a valer más respecto de A
                # y se queda ahí—, mientras que el ruido hace fluctuar esa
                # relación sin cambiarle el nivel. Sin esto, la holgura por ruido
                # que hace falta para emparejar un alimentador grande con uno
                # chico deja pasar cualquier par que se mueva en sentidos
                # opuestos un mes cualquiera.
                paso = _escalon_en_la_razon(piv, fa, fb, i)
                if exigir_sostenido and paso is False:
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
    piso = _piso_deteccion(piv, deltas, cambio_min_pct)
    detalle = (
        f"{len(candidatos)} transferencia(s) probable(s) en {n_periodos} períodos. "
        f"Alimentadores afectados: {', '.join(sorted(afectados)) or 'ninguno'}. "
        "Se recomienda excluirlos del ranking de sospecha hasta aclarar la maniobra."
        if candidatos else
        f"Sin transferencias detectadas en {n_periodos} períodos."
    )
    if not candidatos and piso is not None:
        # «No se detectó nada» es ambiguo sin esto: puede ser que no hubo
        # maniobras, o que las hubo y eran más chicas que el ruido. Solo lo
        # segundo justifica pedir más meses de cabecera.
        detalle += " " + piso.explicacion()
    return TransferReport(
        status=TransferDetectionStatus.OK, candidates=candidatos,
        feeders_afectados=afectados, detail=detalle, n_periodos=n_periodos,
        piso=piso,
    )


def _magnitud_ponderada(da: float, ruido_a: float,
                        db: float, ruido_b: float) -> float:
    """Combina las dos mediciones de la misma transferencia, por su precisión.

    Cada alimentador «mide» la energía que cambió de manos con un error del
    tamaño de su propia variabilidad. Ponderar por el inverso de la varianza es
    darle más voto al que mide mejor, que casi siempre es el chico.
    """

    wa = 1.0 / max(ruido_a, 1.0) ** 2
    wb = 1.0 / max(ruido_b, 1.0) ** 2
    if wa + wb <= 0:
        return (da + db) / 2.0
    return float((da * wa + db * wb) / (wa + wb))


def _escalon_en_la_razon(piv: pd.DataFrame, fa: str, fb: str,
                         i: int) -> bool | None:
    """¿La relación B/A cambió de nivel en el mes ``i`` y se quedó ahí?

    La razón entre dos alimentadores **cancela la estacionalidad**: los dos suben
    y bajan juntos con el clima, así que su cociente es plano salvo que alguien
    mueva carga de uno al otro. Un escalón sostenido en esa razón es la firma de
    la maniobra; el ruido la hace oscilar sin desplazarle el nivel.

    Devuelve ``None`` cuando no hay meses suficientes a un lado para opinar: con
    la maniobra en el primer o el último mes, la falta de evidencia no es
    evidencia de que no la haya.
    """

    try:
        r = (piv[fb] / piv[fa]).to_numpy(dtype=float)
    except Exception:
        return None
    antes, despues = r[:i], r[i:]
    antes = antes[np.isfinite(antes)]
    despues = despues[np.isfinite(despues)]
    if antes.size < 2 or despues.size < 2:
        return None

    salto = float(np.median(despues) - np.median(antes))
    if salto <= 0:
        return False        # B no ganó respecto de A: no es esta transferencia
    disp = float(_mad(antes) + _mad(despues))
    # Dos desviaciones robustas: por debajo, el «escalón» cabe dentro de lo que
    # la razón se mueve sola de un mes a otro.
    return salto > 2.0 * disp if disp > 0 else True


def _mad(v: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def _ruido_kwh(serie: pd.Series) -> float:
    """Cuánto se mueve solo un alimentador, en kWh, tras descontar lo común.

    Se usa la desviación absoluta mediana: si la serie contiene la maniobra que
    se está buscando, la desviación típica se infla con ella y el ruido saldría
    tan alto que la propia maniobra quedaría dentro de lo «normal».
    """

    v = serie.dropna().to_numpy(dtype=float)
    if v.size < 3:
        return 0.0
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def _piso_deteccion(piv: pd.DataFrame, deltas: pd.DataFrame,
                    umbral_pct: float) -> PisoDeteccion | None:
    """Estima a partir de qué magnitud una transferencia se distingue del ruido.

    Se mide sobre el **residuo** —lo que queda tras descontar el movimiento
    común—, porque es ahí donde el detector busca. Se usa la desviación absoluta
    mediana y no la desviación típica: si en la serie hay una maniobra real, la
    típica se infla con ella y el piso saldría más alto de lo que es.
    """

    try:
        rel = (deltas / piv.shift(1)).abs().to_numpy(dtype=float) * 100.0
    except Exception:
        return None
    rel = rel[np.isfinite(rel)]
    if rel.size < 4:
        return None

    mediana = float(np.median(rel))
    mad = float(np.median(np.abs(rel - mediana)))
    # 3 desviaciones robustas sobre la mediana: por debajo de eso, el candidato
    # es indistinguible de la variación normal de un mes cualquiera.
    ruido = mediana + 1.4826 * mad
    detectable = max(3.0 * ruido, umbral_pct)
    mediano_kwh = float(np.nanmedian(piv.to_numpy(dtype=float)))
    return PisoDeteccion(
        ruido_pct=round(ruido, 2), umbral_pct=float(umbral_pct),
        detectable_pct=round(detectable, 2),
        detectable_kwh=round(mediano_kwh * detectable / 100.0, 1),
    )
