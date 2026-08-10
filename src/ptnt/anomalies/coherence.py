"""Consolidado de alimentadores con valores incoherentes.

Reúne en una sola vista todo lo que hace **no creíble** el resultado de un
alimentador, con su causa probable ordenada por sensibilidad (§10.6):

* Controles de balance C01–C06 disparados (PNT negativa, PNT excesiva, facturado
  mayor que la entrada, pérdidas técnicas excesivas, cobertura insuficiente).
* Transferencias probables no reportadas (§10.4).
* Energía sin vincular por encima del umbral (§E3.3).
* Baja confiabilidad del modelo por densidad de hallazgos de calidad (§10.7).

Un alimentador incoherente **no se reporta como hurto**: se manda a corregir datos
y se **excluye o degrada** del ranking de sospecha, para no gastar cuadrillas en
artefactos de información.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Severidad(str, Enum):
    BLOQUEANTE = "BLOQUEANTE"   # el resultado no debe publicarse
    ALTA = "ALTA"
    MEDIA = "MEDIA"


@dataclass
class Incoherencia:
    codigo: str
    severidad: Severidad
    descripcion: str
    causa_probable: str
    valor: float | None = None
    umbral: float | None = None


@dataclass
class FeederCoherence:
    """Diagnóstico de coherencia de un alimentador."""

    feeder_code: str
    incoherencias: list[Incoherencia] = field(default_factory=list)
    excluir_de_ranking: bool = False
    publicable: bool = True
    indice_confiabilidad: float = 100.0

    @property
    def es_incoherente(self) -> bool:
        return bool(self.incoherencias)

    @property
    def severidad_maxima(self) -> str:
        if any(i.severidad == Severidad.BLOQUEANTE for i in self.incoherencias):
            return Severidad.BLOQUEANTE.value
        if any(i.severidad == Severidad.ALTA for i in self.incoherencias):
            return Severidad.ALTA.value
        return Severidad.MEDIA.value if self.incoherencias else "OK"


@dataclass
class IncoherenceReport:
    feeders: list[FeederCoherence] = field(default_factory=list)

    @property
    def incoherentes(self) -> list[FeederCoherence]:
        return [f for f in self.feeders if f.es_incoherente]

    def to_dataframe(self) -> pd.DataFrame:
        filas = []
        for f in self.feeders:
            if not f.es_incoherente:
                continue
            for inc in f.incoherencias:
                filas.append({
                    "alimentador": f.feeder_code,
                    "codigo": inc.codigo,
                    "severidad": inc.severidad.value,
                    "descripcion": inc.descripcion,
                    "causa_probable": inc.causa_probable,
                    "valor": inc.valor,
                    "umbral": inc.umbral,
                    "excluir_de_ranking": f.excluir_de_ranking,
                    "publicable": f.publicable,
                })
        return pd.DataFrame(filas)

    def resumen(self) -> dict:
        inc = self.incoherentes
        return {
            "n_alimentadores": len(self.feeders),
            "n_incoherentes": len(inc),
            "n_bloqueantes": sum(1 for f in inc
                                 if f.severidad_maxima == Severidad.BLOQUEANTE.value),
            "n_excluidos_del_ranking": sum(1 for f in inc if f.excluir_de_ranking),
        }


# Causas ordenadas por sensibilidad para PNT negativa (§10.6 C01)
_CAUSAS_PNT_NEGATIVA = (
    "1) transferencia entre alimentadores no registrada; "
    "2) sobreestimación del alumbrado público; "
    "3) error de asignación de clientes al alimentador"
)


def analyze_feeder_coherence(
    balances: list,
    *,
    transfer_report=None,
    unmatched_report=None,
    confiabilidad_por_alimentador: dict[str, float] | None = None,
    umbral_confiabilidad: float = 50.0,
) -> IncoherenceReport:
    """Consolida las incoherencias de cada alimentador.

    ``balances`` es una lista de ``BalanceResult`` (o dicts equivalentes con
    ``feeder_code``, ``ntl_kwh``, ``ntl_pct``, ``controls``).
    """

    confiabilidad_por_alimentador = confiabilidad_por_alimentador or {}
    afectados_transfer = (
        set(transfer_report.feeders_afectados) if transfer_report is not None else set()
    )

    out: list[FeederCoherence] = []
    for b in balances:
        code = getattr(b, "feeder_code", None) or (b.get("feeder_code") if isinstance(b, dict) else "")
        controles = getattr(b, "controls", None) or (b.get("controls") if isinstance(b, dict) else []) or []
        conf = float(confiabilidad_por_alimentador.get(code, 100.0))
        fc = FeederCoherence(feeder_code=str(code), indice_confiabilidad=conf)

        for c in controles:
            disparado = getattr(c, "triggered", None)
            if disparado is None and isinstance(c, dict):
                disparado = c.get("triggered")
            if not disparado:
                continue
            codigo = getattr(c, "code", None) or (c.get("code") if isinstance(c, dict) else "")
            observado = getattr(c, "observed", None) or (c.get("observed") if isinstance(c, dict) else None)
            umbral = getattr(c, "threshold", None) or (c.get("threshold") if isinstance(c, dict) else None)
            detalle = getattr(c, "detail", "") or (c.get("detail", "") if isinstance(c, dict) else "")

            if codigo == "C01":
                fc.incoherencias.append(Incoherencia(
                    "C01", Severidad.BLOQUEANTE,
                    "PNT negativa: el alimentador entrega menos de lo que factura + pierde",
                    _CAUSAS_PNT_NEGATIVA, observado, umbral))
                fc.publicable = False
                fc.excluir_de_ranking = True
            elif codigo == "C02":
                fc.incoherencias.append(Incoherencia(
                    "C02", Severidad.ALTA,
                    "PNT improbablemente alta para un alimentador completo",
                    "Revisar cobertura de facturación y asignación de clientes antes "
                    "de reportar; puede ser energía sin vincular, no hurto",
                    observado, umbral))
                fc.excluir_de_ranking = True
            elif codigo == "C03":
                fc.incoherencias.append(Incoherencia(
                    "C03", Severidad.BLOQUEANTE,
                    "Energía facturada mayor que la energía de entrada",
                    "Error de datos o de período: desalineación de ciclos de "
                    "facturación, o clientes de otro alimentador asignados aquí",
                    observado, umbral))
                fc.publicable = False
                fc.excluir_de_ranking = True
            elif codigo == "C04":
                fc.incoherencias.append(Incoherencia(
                    "C04", Severidad.ALTA,
                    "Pérdidas técnicas excesivas respecto de la energía de entrada",
                    "Revisar atributos de conductor (calibre/longitud) y la "
                    "asignación de carga antes de aceptar el resultado",
                    observado, umbral))
            elif codigo == "C05":
                fc.incoherencias.append(Incoherencia(
                    "C05", Severidad.MEDIA,
                    "Clientes trazados difieren de los declarados",
                    "Cobertura incompleta de la traza topológica", observado, umbral))
            elif codigo == "C06":
                fc.incoherencias.append(Incoherencia(
                    "C06", Severidad.ALTA,
                    "Cobertura de energía insuficiente para balance medido",
                    "Degradar a INDICATIVO: la PNT sería indistinguible del hueco "
                    "de energía sin vincular", observado, umbral))
                fc.excluir_de_ranking = True
            elif disparado:
                fc.incoherencias.append(Incoherencia(
                    str(codigo), Severidad.MEDIA, str(detalle), "Revisar", observado, umbral))

        # Transferencia probable no reportada
        if code in afectados_transfer:
            fc.incoherencias.append(Incoherencia(
                "TRANSFER", Severidad.ALTA,
                "Transferencia de carga probable no reportada con un alimentador vecino",
                "Maniobra no registrada en el log de conmutación: produce PNT alta "
                "en uno y negativa en el vecino. Excluir del ranking hasta aclarar"))
            fc.excluir_de_ranking = True

        # Energía sin vincular
        if unmatched_report is not None and not unmatched_report.apto_balance_medido:
            fc.incoherencias.append(Incoherencia(
                "VINCULACION", Severidad.ALTA,
                f"Solo {unmatched_report.pct_energia_vinculada:.1f}% de la energía "
                "facturada está vinculada al SIG",
                "Clientes que facturan sin estar en la red: su energía no se asigna "
                "y se confunde con PNT",
                unmatched_report.pct_energia_vinculada, 95.0))
            fc.excluir_de_ranking = True

        # Baja confiabilidad del modelo
        if conf < umbral_confiabilidad:
            fc.incoherencias.append(Incoherencia(
                "CONFIABILIDAD", Severidad.ALTA,
                f"Índice de confiabilidad del modelo {conf:.0f}/100",
                "Alta densidad de hallazgos de calidad: primero es un problema de "
                "datos, no de hurto", conf, umbral_confiabilidad))
            fc.excluir_de_ranking = True

        out.append(fc)

    return IncoherenceReport(feeders=out)
