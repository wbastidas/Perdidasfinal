"""Gestor de la base local DuckDB.

Responsabilidades:
  * Aplicar el DDL (``schema.sql``) de forma idempotente.
  * Registrar corridas (``meta.run``) con su snapshot de configuración y hash.
  * Persistir resultados (rankings de PNT, reconciliación, promedios) en tablas
    de resultado y en Parquet cuando corresponde.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pandas as pd


class StoreError(Exception):
    """Error de la capa de almacenamiento."""


class Database:
    def __init__(self, ruta: str | Path):
        try:
            import duckdb  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depende de instalación
            raise StoreError(
                "duckdb no está instalado. Instale: pip install 'ptnt-bal[store]'"
            ) from exc
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        import duckdb

        self._con = duckdb.connect(str(self.ruta))

    # -- ciclo de vida -------------------------------------------------------
    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- esquema -------------------------------------------------------------
    def apply_schema(self) -> None:
        """Aplica el DDL completo del proyecto de forma idempotente."""

        sql = resources.files("ptnt.store").joinpath("schema.sql").read_text(
            encoding="utf-8"
        )
        try:
            self._con.execute(sql)
        except Exception as exc:
            raise StoreError(f"Error aplicando el esquema: {exc}") from exc

    # -- corridas ------------------------------------------------------------
    def start_run(
        self, stage: str, *, config_hash: str, config_snapshot: dict, feeder_code: str | None = None
    ) -> str:
        run_id = str(uuid.uuid4())
        self._con.execute(
            """
            INSERT INTO meta.run
              (run_id, stage, feeder_code, started_at, status, config_hash, config_snapshot)
            VALUES (?, ?, ?, ?, 'PARTIAL', ?, ?)
            """,
            [run_id, stage, feeder_code, datetime.now(timezone.utc), config_hash,
             _to_json(config_snapshot)],
        )
        return run_id

    def finish_run(self, run_id: str, *, status: str, metrics: dict | None = None,
                   error: str | None = None) -> None:
        self._con.execute(
            """
            UPDATE meta.run
               SET finished_at = ?, status = ?, metrics = ?, error_detail = ?
             WHERE run_id = ?
            """,
            [datetime.now(timezone.utc), status, _to_json(metrics or {}), error, run_id],
        )

    # -- persistencia de resultados -----------------------------------------
    def write_dataframe(self, df: pd.DataFrame, tabla: str, *, replace: bool = True) -> None:
        """Materializa un DataFrame como tabla DuckDB (esquema ``resultados``)."""

        self._con.execute("CREATE SCHEMA IF NOT EXISTS resultados")
        destino = f"resultados.{tabla}"
        self._con.register("_tmp_df", df)
        try:
            if replace:
                self._con.execute(f"CREATE OR REPLACE TABLE {destino} AS SELECT * FROM _tmp_df")
            else:
                self._con.execute(f"INSERT INTO {destino} SELECT * FROM _tmp_df")
        finally:
            self._con.unregister("_tmp_df")

    def read_sql(self, query: str) -> pd.DataFrame:
        return self._con.execute(query).df()

    def table_exists(self, schema: str, tabla: str) -> bool:
        r = self._con.execute(
            """
            SELECT count(*) FROM information_schema.tables
             WHERE table_schema = ? AND table_name = ?
            """,
            [schema, tabla],
        ).fetchone()
        return bool(r and r[0])


def _to_json(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
