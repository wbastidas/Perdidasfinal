"""Derivación de impedancias y ampacidad de conductores desde su descripción.

El catálogo `CATALOGOESTRUCTURA` entrega 415 códigos de conductor (`COO*`) con su
**nombre descriptivo** (p. ej. ``"CONDUCTOR AAAC 6201 #397.5 MCM"``,
``"Conductor TW Cu # 3/0 AWG"``, ``"Conductor Clase 15kV Al 4/0 AWG"``) pero **no**
sus parámetros eléctricos. Este módulo los deriva de forma física y trazable, en
lugar de transcribir a mano unas pocas entradas:

1. **Parseo** de material (Cu / Al / AAAC / ACSR) y calibre (AWG, kcmil/MCM, mm²).
2. **Área**: AWG por la fórmula normalizada; kcmil por conversión exacta.
3. **Resistencia a 20 °C**: ``R = ρ / A`` con la resistividad del material
   (IACS), más un recargo por cableado (los hilos trenzados son ~2 % más largos
   que el eje del conductor).
4. **Reactancia**: ``X = 2πf · 2·10⁻⁴ · ln(GMD/GMR)`` Ω/km, con GMD según el
   nivel de tensión (espaciamiento típico) y GMR = 0,7788·r para conductor
   sólido equivalente.
5. **Ampacidad**: correlación con el área calibrada contra tablas publicadas.

Constantes de resistividad (Ω·mm²/m a 20 °C) y coeficientes de temperatura:

| Material | ρ₂₀ | α | Referencia |
|---|---|---|---|
| Cobre duro (hard-drawn) | 0,017774 | 0,00381 | 97 % IACS (IACS 100 % = 0,017241) |
| Aluminio 1350 (AAC/EC) | 0,028264 | 0,00403 | 61 % IACS |
| ACSR (aluminio exterior) | 0,028264 | 0,00403 | área de aluminio |
| AAAC 6201-T81 | 0,032600 | 0,00360 | 52,5 % IACS |

Todo valor derivado se marca con ``origen_valor = "ESTIMADO_MODELO"`` para que el
balance reporte qué fracción del resultado descansa en derivación y no en catálogo
de fabricante (§2.3).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ptnt.ref.catalogs import Conductor

# --- Resistividad (Ω·mm²/m a 20 °C) y coeficiente térmico por material ------
RESISTIVIDAD = {
    "CU": 0.017774,    # cobre duro, 97 % IACS
    "AL": 0.028264,    # aluminio 1350, 61 % IACS
    "ACSR": 0.028264,  # se calcula sobre el área de aluminio
    "AAAC": 0.032600,  # aleación 6201-T81, 52,5 % IACS
}
ALPHA = {"CU": 0.00381, "AL": 0.00403, "ACSR": 0.00403, "AAAC": 0.00360}

# Factor de cableado: los hilos helicoidales son ~2 % más largos que el eje
FACTOR_CABLEADO = 1.02

# Espaciamiento geométrico medio típico por nivel de tensión (m)
GMD_POR_CLASE = {"BT": 0.30, "MT": 1.00, "AT": 3.00}

FRECUENCIA_HZ = 60.0

# Ampacidad: ley de potencia ``I = k · A^0,72`` (la disipación crece con la
# superficie, no con el área, por eso el exponente < 1). Calibrada contra tablas
# de fabricante para conductor desnudo a 75 °C / ambiente 25 °C:
#   4/0 ACSR (107 mm²) ≈ 340 A · 336,4 kcmil (170 mm²) ≈ 530 A · 795 kcmil ≈ 907 A
_AMP_K = {"CU": 15.9, "AL": 12.6, "ACSR": 12.6, "AAAC": 12.4}
_AMP_EXP = 0.72


@dataclass(frozen=True)
class ParsedConductor:
    """Resultado del parseo de la descripción de un conductor."""

    material: str          # CU | AL | ACSR | AAAC
    area_mm2: float
    calibre: str           # texto normalizado del calibre
    voltage_class: str     # BT | MT | AT
    is_underground: bool


# --------------------------------------------------------------------------- #
# Conversión de calibres
# --------------------------------------------------------------------------- #
def awg_to_mm2(awg: str) -> float | None:
    """Área en mm² de un calibre AWG.

    Fórmula normalizada: ``d(mm) = 0,127 · 92^((36−n)/39)``, con n = 0 para 1/0,
    −1 para 2/0, −2 para 3/0 y −3 para 4/0.
    """

    s = str(awg).strip().upper().replace(" ", "")
    ceros = {"1/0": 0, "2/0": -1, "3/0": -2, "4/0": -3,
             "1_0": 0, "2_0": -1, "3_0": -2, "4_0": -3,
             "0": 0, "00": -1, "000": -2, "0000": -3}
    if s in ceros:
        n = ceros[s]
    else:
        try:
            n = int(float(s))
        except ValueError:
            return None
        if not -3 <= n <= 40:
            return None
    d_mm = 0.127 * (92.0 ** ((36 - n) / 39.0))
    return math.pi * (d_mm / 2.0) ** 2


def kcmil_to_mm2(kcmil: float) -> float:
    """Área en mm² de un calibre en kcmil/MCM (1 kcmil = 0,5067 mm²)."""

    return float(kcmil) * 0.5067074


# --------------------------------------------------------------------------- #
# Parseo de la descripción
# --------------------------------------------------------------------------- #
_RE_KCMIL = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:MCM|KCMIL)", re.I)
# Hasta 4 dígitos: hay descripciones que etiquetan kcmil como AWG (p. ej. "#266 AWG")
_RE_AWG = re.compile(r"#?\s*(\d{1,2}/0|\d{1,4})\s*AWG", re.I)
_RE_MM2 = re.compile(r"(\d+(?:[.,]\d+)?)\s*MM2", re.I)
_RE_CLASE_KV = re.compile(r"(\d+(?:[.,]\d+)?)\s*KV", re.I)
# Multiconductor / concéntrico: "4x6", "2X4", "2x6+10"
_RE_MULTI = re.compile(r"(\d+)\s*X\s*(\d+(?:/0)?)", re.I)


def _detect_material(desc: str) -> str:
    d = desc.upper()
    if "ACSR" in d:
        return "ACSR"
    if "AAAC" in d or "6201" in d or "ALEACION" in d or "ALEACIÓN" in d:
        return "AAAC"
    if re.search(r"\bCU\b|COBRE", d):
        return "CU"
    if re.search(r"\bAL\b|ALUMINIO", d):
        return "AL"
    return "AL"  # por defecto en distribución


def _detect_voltage_class(desc: str) -> str:
    m = _RE_CLASE_KV.search(desc)
    if m:
        try:
            kv = float(m.group(1).replace(",", "."))
        except ValueError:
            kv = 0.0
        if kv >= 46:
            return "AT"
        if kv >= 1:
            return "MT"
    d = desc.upper()
    if any(k in d for k in ("TW", "THHN", "TTU", "CONCENTRIC", "CONCÉNTRIC",
                            "MULTIPLEX", "MULTICONDUCTOR", "PREENSAMBLAD")):
        return "BT"
    return "MT"


def _detect_underground(desc: str) -> bool:
    d = desc.upper()
    return any(k in d for k in ("TTU", "SUBTERR", "XLPE", "CLASE"))


def parse_conductor_description(desc: str) -> ParsedConductor | None:
    """Extrae material, área y clase de tensión de la descripción del conductor."""

    if not desc:
        return None
    texto = desc.replace(",", ".")
    material = _detect_material(texto)
    vclass = _detect_voltage_class(texto)
    subter = _detect_underground(texto)

    area = None
    calibre = ""
    # Preensamblado / multiplex métrico: "3x50 + Nx70 mm²" -> fase de 50 mm²
    es_metrico = bool(re.search(r"MM\s*2|MM²|\bMM\b", texto, re.I))

    m = _RE_KCMIL.search(texto)
    if m:
        area = kcmil_to_mm2(float(m.group(1)))
        calibre = f"{m.group(1)} kcmil"
    if area is None:
        m = _RE_AWG.search(texto)
        if m:
            crudo = m.group(1)
            area = awg_to_mm2(crudo)
            calibre = f"{crudo} AWG"
            if area is None:
                # "#266 AWG" no existe: es un calibre kcmil mal etiquetado
                try:
                    n = float(crudo)
                except ValueError:
                    n = 0.0
                if n > 40:
                    area = kcmil_to_mm2(n)
                    calibre = f"{crudo} kcmil (etiquetado AWG)"
    if area is None:
        m = _RE_MULTI.search(texto)
        if m:
            # En descripciones métricas el número es mm²; si no, es AWG
            valor = m.group(2)
            if es_metrico:
                try:
                    area = float(valor)
                    calibre = f"{m.group(1)}x{valor} mm2"
                except ValueError:
                    area = None
            else:
                area = awg_to_mm2(valor)
                calibre = f"{m.group(1)}x{valor} AWG"
    if area is None:
        m = _RE_MM2.search(texto)
        if m:
            area = float(m.group(1))
            calibre = f"{m.group(1)} mm2"
    if area is None or area <= 0:
        return None
    return ParsedConductor(material, area, calibre, vclass, subter)


# --------------------------------------------------------------------------- #
# Derivación de parámetros eléctricos
# --------------------------------------------------------------------------- #
def resistance_ohm_km(area_mm2: float, material: str) -> float:
    """Resistencia en Ω/km a 20 °C: ``R = ρ·1000/A`` con recargo por cableado."""

    rho = RESISTIVIDAD.get(material, RESISTIVIDAD["AL"])
    return rho * 1000.0 / area_mm2 * FACTOR_CABLEADO


def reactance_ohm_km(area_mm2: float, voltage_class: str, underground: bool = False) -> float:
    """Reactancia inductiva en Ω/km.

    ``X = 2πf · 2·10⁻⁴ · ln(GMD/GMR)`` con GMR = 0,7788·r (conductor sólido
    equivalente). En subterráneo el GMD es mucho menor (cables contiguos).
    """

    r_m = math.sqrt(area_mm2 / math.pi) / 1000.0   # radio en metros
    gmr = 0.7788 * r_m
    gmd = 0.15 if underground else GMD_POR_CLASE.get(voltage_class, 1.0)
    if gmr <= 0 or gmd <= gmr:
        return 0.05
    return 2 * math.pi * FRECUENCIA_HZ * 2e-4 * math.log(gmd / gmr)


def ampacity_a(area_mm2: float, material: str, underground: bool = False) -> float:
    """Ampacidad estimada (A) por ley de potencia ``I = k · A^0,72``.

    El exponente < 1 refleja que la disipación térmica crece con la superficie y
    no con el área de cobre. Los conductores aislados/subterráneos disipan peor:
    se aplica un derating del 25 %.
    """

    k = _AMP_K.get(material, _AMP_K["AL"])
    amp = k * (area_mm2 ** _AMP_EXP)
    if underground:
        amp *= 0.75
    return amp


def derive_conductor(code: str, description: str) -> Conductor | None:
    """Deriva un ``Conductor`` completo desde el código y su descripción.

    Devuelve ``None`` si la descripción no permite identificar el calibre (esos
    códigos se reportan como hallazgo de configuración, no se inventan).
    """

    p = parse_conductor_description(description)
    if p is None:
        return None
    return Conductor(
        code=code,
        nombre=description.strip()[:80],
        material=p.material,
        seccion_mm2=round(p.area_mm2, 3),
        r_ohm_km_20c=round(resistance_ohm_km(p.area_mm2, p.material), 5),
        x_ohm_km=round(reactance_ohm_km(p.area_mm2, p.voltage_class, p.is_underground), 5),
        ampacidad_a=round(ampacity_a(p.area_mm2, p.material, p.is_underground), 1),
        voltaje_clase=p.voltage_class,
    )


def derive_from_structure_catalog(struct_catalog) -> dict[str, Conductor]:
    """Deriva todos los conductores (``COO*``) del catálogo de estructuras."""

    from ptnt.ref.structure_catalog import ElementCategory

    out: dict[str, Conductor] = {}
    for item in struct_catalog.items_by_category(ElementCategory.CONDUCTOR):
        cond = derive_conductor(item.code, item.description)
        if cond is not None:
            out[item.code] = cond
    return out
