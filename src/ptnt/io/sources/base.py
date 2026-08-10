"""Interfaz común de los conectores de origen."""

from __future__ import annotations

import abc
from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd

from ptnt.config.models import FuenteConfig


class SourceError(Exception):
    """Error al conectar o leer de una base de origen."""


class SourceConnector(abc.ABC):
    """Contrato mínimo que toda base de origen debe cumplir.

    Las capas de negocio solo dependen de esta interfaz, de modo que agregar un
    nuevo motor (otra base) no toca el resto del sistema.
    """

    def __init__(self, fuente: FuenteConfig):
        self.fuente = fuente

    @property
    def nombre(self) -> str:
        return self.fuente.nombre

    @abc.abstractmethod
    def test_connection(self) -> bool:
        """Verifica conectividad/lectura sin traer datos. Lanza SourceError si falla."""

    @abc.abstractmethod
    def read_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        """Ejecuta una consulta (SQL o nombre lógico) y devuelve un DataFrame."""

    @abc.abstractmethod
    def read_table(
        self, tabla: str, columnas: list[str] | None = None, limite: int | None = None
    ) -> pd.DataFrame:
        """Lee una tabla/entidad completa o un subconjunto de columnas."""

    def iter_batches(
        self, tabla: str, batch_size: int = 50_000
    ) -> Iterator[pd.DataFrame]:
        """Lectura por lotes (por defecto: una sola tanda). Los conectores SQL
        la sobreescriben para no cargar millones de filas en memoria."""
        yield self.read_table(tabla)

    @contextmanager
    def session(self):
        """Contexto de conexión; los conectores con estado lo sobreescriben."""
        try:
            yield self
        finally:
            self.close()

    def close(self) -> None:  # pragma: no cover - por defecto no-op
        """Libera recursos. Sin estado por defecto."""
