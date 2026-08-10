"""Capa de almacenamiento local: DuckDB + Parquet.

DuckDB es el motor analítico local: aloja catálogos, resultados agregados,
estado de corridas y rankings, y consulta Parquet directamente. Es un extra
opcional (``pip install ptnt-bal[store]``); las funciones de escritura verifican
su disponibilidad y dan un error claro si falta.
"""

from ptnt.store.database import Database, StoreError

__all__ = ["Database", "StoreError"]
