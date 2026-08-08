"""Conector genérico a bases de datos SQL vía SQLAlchemy.

Cubre SQL Server, PostgreSQL, Oracle y MySQL/MariaDB con un solo código,
cambiando únicamente el dialecto/driver de la URL. Las credenciales se resuelven
desde variables de entorno (nunca del YAML). SQLAlchemy es un extra opcional
(``pip install ptnt-bal[sources]``); si no está instalado, el conector lanza un
error claro al construirse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

from ptnt.config.models import FuenteConfig, TipoFuente
from ptnt.io.sources.base import SourceConnector, SourceError
from ptnt.security.secrets import resolve_source_credentials

_DIALECTOS = {
    TipoFuente.SQLSERVER: "mssql+pyodbc",
    TipoFuente.POSTGRES: "postgresql+psycopg2",
    TipoFuente.ORACLE: "oracle+oracledb",
    TipoFuente.MYSQL: "mysql+pymysql",
}


class SqlSource(SourceConnector):
    def __init__(self, fuente: FuenteConfig):
        super().__init__(fuente)
        try:
            import sqlalchemy  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depende de instalación
            raise SourceError(
                "SQLAlchemy no está instalado. Instale el extra: "
                "pip install 'ptnt-bal[sources]'"
            ) from exc
        self._engine = None

    # -- construcción de URL -------------------------------------------------
    def _build_url(self) -> str:
        from sqlalchemy import URL

        creds = resolve_source_credentials(self.fuente)
        if creds.dsn:
            return creds.dsn
        dialecto = _DIALECTOS.get(self.fuente.tipo)
        if dialecto is None:
            raise SourceError(f"tipo de fuente SQL no soportado: {self.fuente.tipo}")
        query = dict(self.fuente.opciones)
        if self.fuente.tipo == TipoFuente.SQLSERVER:
            query.setdefault("driver", self.fuente.driver or "ODBC Driver 18 for SQL Server")
            # Cifrado obligatorio salvo que se desactive explícitamente
            query.setdefault("Encrypt", "yes" if self.fuente.requiere_ssl else "no")
        elif self.fuente.tipo == TipoFuente.POSTGRES and self.fuente.requiere_ssl:
            query.setdefault("sslmode", "require")
        elif self.fuente.tipo == TipoFuente.MYSQL and self.fuente.requiere_ssl:
            query.setdefault("ssl", "true")
        return URL.create(
            dialecto,
            username=creds.usuario,
            password=creds.password,
            host=self.fuente.host,
            port=self.fuente.puerto,
            database=self.fuente.base_datos,
            query=query,
        ).render_as_string(hide_password=False)

    def _get_engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine

            try:
                self._engine = create_engine(
                    self._build_url(),
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": int(
                        self.fuente.opciones.get("connect_timeout", "10")
                    )} if self.fuente.tipo != TipoFuente.SQLSERVER else {},
                )
            except Exception as exc:
                raise SourceError(
                    f"No se pudo crear el engine para '{self.nombre}': {exc}"
                ) from exc
        return self._engine

    def _qualified(self, tabla: str) -> str:
        if self.fuente.esquema:
            return f"{self.fuente.esquema}.{tabla}"
        return tabla

    # -- interfaz ------------------------------------------------------------
    def test_connection(self) -> bool:
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise SourceError(
                f"Conexión fallida a '{self.nombre}' ({self.fuente.tipo.value}): {exc}"
            ) from exc
        return True

    def read_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                return pd.read_sql(text(query), conn, params=params or {})
        except Exception as exc:
            raise SourceError(f"Error en consulta a '{self.nombre}': {exc}") from exc

    def read_table(
        self, tabla: str, columnas: list[str] | None = None, limite: int | None = None
    ) -> pd.DataFrame:
        cols = ", ".join(columnas) if columnas else "*"
        base = f"SELECT {cols} FROM {self._qualified(tabla)}"
        if limite:
            # TOP para SQL Server, LIMIT para el resto
            if self.fuente.tipo == TipoFuente.SQLSERVER:
                base = f"SELECT TOP {int(limite)} {cols} FROM {self._qualified(tabla)}"
            elif self.fuente.tipo == TipoFuente.ORACLE:
                base = f"{base} FETCH FIRST {int(limite)} ROWS ONLY"
            else:
                base = f"{base} LIMIT {int(limite)}"
        return self.read_query(base)

    def iter_batches(
        self, tabla: str, batch_size: int = 50_000
    ) -> Iterator[pd.DataFrame]:
        from sqlalchemy import text

        query = text(f"SELECT * FROM {self._qualified(tabla)}")
        try:
            with self._get_engine().connect().execution_options(
                stream_results=True
            ) as conn:
                for chunk in pd.read_sql(query, conn, chunksize=batch_size):
                    yield chunk
        except Exception as exc:
            raise SourceError(
                f"Error en lectura por lotes de '{self.nombre}': {exc}"
            ) from exc

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
