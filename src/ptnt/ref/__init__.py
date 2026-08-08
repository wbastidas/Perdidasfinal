"""Catálogos de referencia: conductores y transformadores.

El FGDB entrega nombres/códigos; las magnitudes eléctricas (impedancias,
ampacidades, P0/Pk/%Z) provienen de estos catálogos YAML. Un código presente en
la red y ausente del catálogo es un error bloqueante de configuración.
"""

from ptnt.ref.catalogs import (
    Conductor,
    ConductorCatalog,
    TransformerCatalog,
    TransformerSpec,
    load_conductor_catalog,
    load_transformer_catalog,
)

__all__ = [
    "Conductor",
    "ConductorCatalog",
    "load_conductor_catalog",
    "TransformerSpec",
    "TransformerCatalog",
    "load_transformer_catalog",
]
