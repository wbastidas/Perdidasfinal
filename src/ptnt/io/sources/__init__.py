"""Conectores a distintas bases de origen.

El sistema está pensado para un servidor que lee de varias bases: el CSV/DB
comercial (consumos), el SIG (red), y la base local de resultados. Cada origen
implementa la misma interfaz ``SourceConnector`` para que las capas superiores
sean agnósticas al motor concreto.
"""

from ptnt.io.sources.base import SourceConnector, SourceError
from ptnt.io.sources.factory import build_connector

__all__ = ["SourceConnector", "SourceError", "build_connector"]
