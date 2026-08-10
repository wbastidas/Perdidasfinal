"""Segmentación de clientes para el análisis de pérdidas no técnicas.

Separa el padrón por clase tarifaria, nivel de tensión y estrato de consumo, y
construye grupos par jerárquicos, de modo que cada cliente se compare únicamente
contra clientes equivalentes. Ver `classification.py` para el porqué.
"""

from ptnt.segment.classification import (
    ClaseConsumo,
    NivelTension,
    ResumenSegmentacion,
    clasificar_tarifa,
    clasificar_tension,
    consumo_base,
    etiquetar_estrato,
    segmentar_clientes,
    tiene_demanda_facturada,
)
from ptnt.segment.peers import (
    NIVELES_GRUPO_PAR,
    ResultadoGrupoPar,
    asignar_grupo_par,
    consumo_esperado_por_grupo,
    energia_recuperable,
)

__all__ = [
    "ClaseConsumo",
    "NivelTension",
    "ResumenSegmentacion",
    "clasificar_tarifa",
    "clasificar_tension",
    "consumo_base",
    "etiquetar_estrato",
    "segmentar_clientes",
    "tiene_demanda_facturada",
    "NIVELES_GRUPO_PAR",
    "ResultadoGrupoPar",
    "asignar_grupo_par",
    "consumo_esperado_por_grupo",
    "energia_recuperable",
]
