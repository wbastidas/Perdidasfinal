"""Conector para orígenes de archivo CSV.

Para el CSV comercial con formato especial (separadores por columna) se usa el
parser dedicado en ``ptnt.io.commercial_parser``; este conector cubre la lectura
genérica de CSV auxiliares.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ptnt.io.sources.base import SourceConnector, SourceError


class CsvSource(SourceConnector):
    @property
    def ruta(self) -> Path:
        if not self.fuente.ruta:
            raise SourceError(f"fuente '{self.nombre}' sin 'ruta'")
        return Path(self.fuente.ruta)

    def test_connection(self) -> bool:
        if not self.ruta.exists():
            raise SourceError(f"CSV no encontrado: '{self.ruta}'")
        return True

    def read_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        raise SourceError("El conector CSV no soporta consultas SQL arbitrarias.")

    def read_table(
        self, tabla: str, columnas: list[str] | None = None, limite: int | None = None
    ) -> pd.DataFrame:
        sep = self.fuente.opciones.get("separador", ";")
        enc = self.fuente.opciones.get("encoding", "utf-8")
        try:
            df = pd.read_csv(
                self.ruta, sep=sep, encoding=enc, nrows=limite, dtype=str
            )
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            raise SourceError(f"Error leyendo CSV '{self.ruta}': {exc}") from exc
        if columnas:
            faltan = [c for c in columnas if c not in df.columns]
            if faltan:
                raise SourceError(f"CSV '{self.ruta}' sin columnas {faltan}")
            df = df[columnas]
        return df
