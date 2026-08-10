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
    load_conductor_catalog_full,
    load_transformer_catalog,
)
from ptnt.ref.conductor_derive import (
    derive_conductor,
    derive_from_structure_catalog,
)
from ptnt.ref.structure_catalog import (
    ElementCategory,
    StructureCatalog,
    StructureItem,
    classify_structure,
    load_structure_catalog,
)

__all__ = [
    "Conductor",
    "ConductorCatalog",
    "load_conductor_catalog",
    "load_conductor_catalog_full",
    "TransformerSpec",
    "TransformerCatalog",
    "load_transformer_catalog",
    "StructureCatalog",
    "StructureItem",
    "ElementCategory",
    "classify_structure",
    "load_structure_catalog",
    "derive_conductor",
    "derive_from_structure_catalog",
]
