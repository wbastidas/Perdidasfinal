"""Adaptadores de entrada/salida: conectores a bases de origen y parser comercial."""

from ptnt.io.commercial_parser import (
    CommercialParseError,
    ConsumoLargo,
    parse_commercial_csv,
)
from ptnt.io.sources import SourceConnector, SourceError, build_connector

__all__ = [
    "SourceConnector",
    "SourceError",
    "build_connector",
    "parse_commercial_csv",
    "ConsumoLargo",
    "CommercialParseError",
]
