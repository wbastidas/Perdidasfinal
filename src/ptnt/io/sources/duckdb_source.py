"""Conector para DuckDB local y para Parquet (vía DuckDB).

DuckDB es el motor analítico local del sistema: consulta Parquet directamente y
aloja catálogos y resultados. Es un extra opcional (``pip install
ptnt-bal[store]``).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ptnt.config.models import TipoFuente
from ptnt.io.sources.base import SourceConnector, SourceError


class DuckdbSource(SourceConnector):
    def __init__(self, fuente):
        super().__init__(fuente)
        try:
            import duckdb  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depende de instalación
            raise SourceError(
                "duckdb no está instalado. Instale el extra: "
                "pip install 'ptnt-bal[store]'"
            ) from exc
        self._con = None

    def _connect(self):
        import duckdb

        if self._con is None:
            if self.fuente.tipo == TipoFuente.PARQUET:
                self._con = duckdb.connect(database=":memory:")
            else:
                ruta = self.fuente.ruta
                if not ruta:
                    raise SourceError(f"fuente '{self.nombre}' sin 'ruta'")
                Path(ruta).parent.mkdir(parents=True, exist_ok=True)
                self._con = duckdb.connect(database=ruta)
        return self._con

    def test_connection(self) -> bool:
        try:
            self._connect().execute("SELECT 1")
        except Exception as exc:
            raise SourceError(f"DuckDB '{self.nombre}' no accesible: {exc}") from exc
        return True

    def read_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        try:
            con = self._connect()
            if params:
                return con.execute(query, params).df()
            return con.execute(query).df()
        except Exception as exc:
            raise SourceError(f"Error en consulta DuckDB '{self.nombre}': {exc}") from exc

    def read_table(
        self, tabla: str, columnas: list[str] | None = None, limite: int | None = None
    ) -> pd.DataFrame:
        cols = ", ".join(columnas) if columnas else "*"
        if self.fuente.tipo == TipoFuente.PARQUET:
            origen = f"read_parquet('{self.fuente.ruta}')"
        else:
            origen = tabla
        q = f"SELECT {cols} FROM {origen}"
        if limite:
            q += f" LIMIT {int(limite)}"
        return self.read_query(q)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
