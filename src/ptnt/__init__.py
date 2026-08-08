"""PTNT-BAL — Plataforma de Pérdidas Técnicas, No Técnicas y Balance Energético.

Núcleo de análisis de consumo eléctrico multi-mes, recálculo de potencia por
cliente y detección de hurto (pérdidas no técnicas) sobre el modelo de datos
homologado CNEL EP.

El paquete está diseñado para ejecutarse en un servidor único, leyendo desde
distintas bases de origen (CSV comercial, SQL Server, PostgreSQL, Oracle,
MySQL o Parquet/DuckDB) y publicando resultados en dos interfaces web: un
tablero de análisis para escritorio (Streamlit) y un visor de solo lectura
para consulta por terceros (FastAPI).
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
