"""Decodificadores de dominios del modelo de datos (§4.4).

Se implementan como funciones puras y probadas: la decodificación bitwise de fases,
el parseo de ``POTENCIANOMINAL`` (String → Double) y la cascada de longitud.
"""

from __future__ import annotations

import re

# Dominio 'Phase Designation' es un bitmask: C=1, B=2, A=4.
_PHASE_BITS = {"A": 4, "B": 2, "C": 1}


def decode_phase_designation(value: int) -> str:
    """Decodifica el bitmask de fase a la cadena de fases presentes.

    C=1, B=2, A=4 ⇒ BC=3, AC=5, AB=6, ABC=7. Devuelve p.ej. ``"ABC"``, ``"A"``.
    """

    try:
        v = int(value)
    except (TypeError, ValueError):
        return ""
    fases = [nombre for nombre, bit in _PHASE_BITS.items() if v & bit]
    # orden canónico A, B, C
    return "".join(f for f in ("A", "B", "C") if f in fases)


def phase_count(value: int) -> int:
    """Número de fases presentes según el bitmask ``Phase Designation``."""

    return len(decode_phase_designation(value))


_KVA_RE = re.compile(r"(\d+[.,]?\d*)")


def parse_transformer_kva(value: str, dominio: list[float] | None = None) -> float | None:
    """Parsea ``POTENCIANOMINAL`` (String, p.ej. ``"15KVA"``) a numérico.

    Si se pasa ``dominio`` (valores válidos) y el parseado no pertenece, devuelve el
    valor del dominio más cercano (marca ``ESTIMADO_MODELO`` en la capa que lo use).
    """

    if value is None:
        return None
    # Normaliza: quita 'KVA'/espacios; trata la coma como separador decimal.
    s = str(value).upper().replace("KVA", "").replace("KV", "").strip()
    s = s.replace(",", ".")
    m = _KVA_RE.search(s)
    if not m:
        return None
    try:
        kva = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    if dominio:
        if kva in dominio:
            return kva
        return min(dominio, key=lambda d: abs(d - kva))
    return kva


def length_cascade(
    longitud_campo: float | None,
    longitud_sistema: float | None,
    shape_length: float,
    *,
    tolerancia_pct: float = 30.0,
) -> tuple[float, str]:
    """Cascada de preferencia de longitud (§4.4).

    Devuelve ``(longitud, origen_valor)``:
      1. LONGITUDCAMPO   si > 0 y dentro de ±tol de SHAPE_Length  → MEDIDO
      2. LONGITUDSISTEMA si > 0 y dentro de ±tol de SHAPE_Length  → CATALOGO
      3. SHAPE_Length                                             → INFERIDO_TOPOLOGIA
    """

    tol = tolerancia_pct / 100.0

    def _dentro(v: float | None) -> bool:
        return (
            v is not None and v > 0 and shape_length > 0
            and abs(v - shape_length) / shape_length <= tol
        )

    if _dentro(longitud_campo):
        return float(longitud_campo), "MEDIDO"
    if _dentro(longitud_sistema):
        return float(longitud_sistema), "CATALOGO"
    return float(shape_length), "INFERIDO_TOPOLOGIA"
