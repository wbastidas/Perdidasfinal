"""Informes visuales por etapa: HTML autocontenido y PDF."""

from ptnt.report.charts import (
    barra_apilada_balance,
    barras_horizontales,
    lineas_temporales,
    mapa_puntos,
)
from ptnt.report.pages import Informe, compendio, html_a_pdf, unir_pdfs

__all__ = [
    "Informe", "compendio", "html_a_pdf", "unir_pdfs",
    "barras_horizontales", "lineas_temporales", "mapa_puntos",
    "barra_apilada_balance",
]
