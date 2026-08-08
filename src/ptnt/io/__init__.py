"""Adaptadores de entrada/salida: conectores a bases de origen y parser comercial."""

from ptnt.io.commercial_parser import (
    CommercialParseError,
    ConsumoLargo,
    parse_commercial_csv,
)
from ptnt.io.exporters import (
    executive_report_html,
    export_tables_xlsx,
    write_executive_report,
)
from ptnt.io.migration import (
    MigrationError,
    migrate_network,
    network_to_tables,
    persist_network,
)
from ptnt.io.sources import SourceConnector, SourceError, build_connector

__all__ = [
    "SourceConnector",
    "SourceError",
    "build_connector",
    "parse_commercial_csv",
    "ConsumoLargo",
    "CommercialParseError",
    "export_tables_xlsx",
    "executive_report_html",
    "write_executive_report",
    "migrate_network",
    "network_to_tables",
    "persist_network",
    "MigrationError",
]
