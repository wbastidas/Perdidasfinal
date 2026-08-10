"""Fábrica de conectores: dado un ``FuenteConfig`` devuelve el conector adecuado."""

from __future__ import annotations

from ptnt.config.models import FuenteConfig, TipoFuente
from ptnt.io.sources.base import SourceConnector, SourceError


def build_connector(fuente: FuenteConfig) -> SourceConnector:
    """Construye el conector correspondiente al tipo de fuente."""

    tipo = fuente.tipo
    if tipo == TipoFuente.CSV:
        from ptnt.io.sources.csv_source import CsvSource

        return CsvSource(fuente)
    if tipo in {TipoFuente.DUCKDB, TipoFuente.PARQUET}:
        from ptnt.io.sources.duckdb_source import DuckdbSource

        return DuckdbSource(fuente)
    if tipo == TipoFuente.FGDB:
        from ptnt.io.sources.fgdb_source import FgdbSource

        return FgdbSource(fuente)
    if tipo in {
        TipoFuente.SQLSERVER,
        TipoFuente.POSTGRES,
        TipoFuente.ORACLE,
        TipoFuente.ORACLE_ARCSDE,
        TipoFuente.MYSQL,
    }:
        from ptnt.io.sources.sql_source import SqlSource

        return SqlSource(fuente)
    raise SourceError(f"tipo de fuente no soportado: {tipo}")
