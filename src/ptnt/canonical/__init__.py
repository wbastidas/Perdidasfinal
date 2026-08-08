"""Modelo canónico: decodificación de dominios y mapeo de campos FGDB → canónico.

La normalización de nombres FGDB (clases, campos, dominios) a nombres canónicos
ocurre exclusivamente aquí (capa Silver), documentada en ``field_map.yaml``, que es
la única fuente de verdad de la traducción (§4.3).
"""

from ptnt.canonical.decode import (
    decode_phase_designation,
    length_cascade,
    parse_transformer_kva,
    phase_count,
)

__all__ = [
    "decode_phase_designation",
    "phase_count",
    "parse_transformer_kva",
    "length_cascade",
]
