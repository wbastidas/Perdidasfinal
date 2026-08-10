"""Catálogo de estructuras (``CATALOGOESTRUCTURA``).

Es la tabla maestra del modelo CNEL EP que relaciona el ``CODIGOESTRUCTURA`` de
cada elemento con su potencia y pérdidas. Un mismo catálogo cubre transformadores
(kVA), luminarias/proyectores (W lámpara + W balastro), semáforos y cámaras (W),
capacitores (kVAR), medidores, seccionadores, postes, etc.

La categoría del elemento se deduce del **prefijo** de 3 letras del código:

  APO/AOD/AOC → alumbrado público (luminaria/proyector/ornamental)
  CSP         → semáforo / cámara  (consumo no medido, por regulación)
  TRT/TRR/TRV/TRS/TUT/TUV/TUS → transformador (POTENCIA = kVA; descr. = config de banco)
  COO         → conductor
  ECT/ECR     → capacitor (POTENCIA = kVAR)
  ECS/ECV     → regulador de tensión
  SP*/SS*     → seccionador / puesto de protección
  MED/MEC/MET/MEV/MEU → medidor
  POO/TOO     → poste / torre (estructura soporte)
  SD*         → generación distribuida (SGDA fotovoltaica)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path

from ptnt.losses.transformers import BankConfig


class ElementCategory(str, Enum):
    ALUMBRADO = "ALUMBRADO"
    SEMAFORO_CAMARA = "SEMAFORO_CAMARA"
    TRANSFORMADOR = "TRANSFORMADOR"
    CONDUCTOR = "CONDUCTOR"
    CAPACITOR = "CAPACITOR"
    REGULADOR = "REGULADOR"
    SECCIONADOR = "SECCIONADOR"
    MEDIDOR = "MEDIDOR"
    POSTE = "POSTE"
    GENERACION = "GENERACION"
    ESTRUCTURA = "ESTRUCTURA"
    OTRO = "OTRO"


_PREFIX_CATEGORY = {
    "APO": ElementCategory.ALUMBRADO, "AOD": ElementCategory.ALUMBRADO,
    "AOC": ElementCategory.ALUMBRADO,
    "CSP": ElementCategory.SEMAFORO_CAMARA,
    "TRT": ElementCategory.TRANSFORMADOR, "TRR": ElementCategory.TRANSFORMADOR,
    "TRV": ElementCategory.TRANSFORMADOR, "TRS": ElementCategory.TRANSFORMADOR,
    "TUT": ElementCategory.TRANSFORMADOR, "TUV": ElementCategory.TRANSFORMADOR,
    "TUS": ElementCategory.TRANSFORMADOR,
    "COO": ElementCategory.CONDUCTOR,
    "ECT": ElementCategory.CAPACITOR, "ECR": ElementCategory.CAPACITOR,
    "ECS": ElementCategory.REGULADOR, "ECV": ElementCategory.REGULADOR,
    "MED": ElementCategory.MEDIDOR, "MEC": ElementCategory.MEDIDOR,
    "MET": ElementCategory.MEDIDOR, "MEV": ElementCategory.MEDIDOR,
    "MEU": ElementCategory.MEDIDOR,
    "POO": ElementCategory.POSTE, "TOO": ElementCategory.POSTE,
}
_SECCIONADOR_PREFIXES = {"SPT", "SPV", "SPR", "SPS", "SPO", "SPD", "SPE",
                         "SST", "SSV", "SSS", "SSD"}
_GENERACION_PREFIX = "SD"       # SDD, SDT, SDV, ...
_ESTRUCTURA_PREFIX = ("EST", "ESV", "ESR", "ESS", "ESD", "ESN", "ESE", "ESC", "EU0")


def classify_structure(code: str) -> ElementCategory:
    """Clasifica un código de estructura por su prefijo."""

    if not code:
        return ElementCategory.OTRO
    p = code[:3].upper()
    if p in _PREFIX_CATEGORY:
        return _PREFIX_CATEGORY[p]
    if p in _SECCIONADOR_PREFIXES:
        return ElementCategory.SECCIONADOR
    if p.startswith(_GENERACION_PREFIX):
        return ElementCategory.GENERACION
    if p in _ESTRUCTURA_PREFIX:
        return ElementCategory.ESTRUCTURA
    return ElementCategory.OTRO


@dataclass(frozen=True)
class StructureItem:
    code: str
    category: ElementCategory
    power: float           # kVA (transf), W (AP/semáforo), kVAR (capacitor)
    power2: float          # segundo régimen (doble nivel AP)
    loss_w: float          # pérdida balastro/driver régimen 1 (W)
    loss2_w: float         # pérdida balastro régimen 2 (W)
    double_level: bool
    description: str

    @property
    def is_double_level(self) -> bool:
        return self.double_level or self.power2 > 0 or self.loss2_w > 0

    def bank_config(self) -> BankConfig:
        """Infiere la configuración de banco de un transformador desde la descripción."""

        d = self.description.lower()
        if "banco de 3" in d or "3 transformadores" in d:
            return BankConfig.BANCO_3
        if "banco de 2" in d or "2 transformadores" in d:
            return BankConfig.DELTA_ABIERTO
        return BankConfig.UNIDAD_SIMPLE


def _to_float(s: str) -> float:
    if not s:
        return 0.0
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return 0.0


class StructureCatalog:
    def __init__(self, items: dict[str, StructureItem]):
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, code: str) -> bool:
        return code in self._items

    def get(self, code: str) -> StructureItem | None:
        return self._items.get(code)

    def category(self, code: str) -> ElementCategory:
        item = self._items.get(code)
        return item.category if item else classify_structure(code)

    # -- helpers de dominio -------------------------------------------------
    def lamp_power_w(self, code: str) -> float:
        """Potencia de lámpara (W) para AP/semáforo/cámara."""
        item = self._items.get(code)
        return item.power if item else 0.0

    def ballast_loss_w(self, code: str) -> float:
        """Pérdida de balastro/driver (W)."""
        item = self._items.get(code)
        return item.loss_w if item else 0.0

    def transformer_kva(self, code: str) -> float | None:
        item = self._items.get(code)
        if item and item.category == ElementCategory.TRANSFORMADOR and item.power > 0:
            return item.power
        return None

    def capacitor_kvar(self, code: str) -> float | None:
        item = self._items.get(code)
        if item and item.category == ElementCategory.CAPACITOR and item.power > 0:
            return item.power
        return None

    def items_by_category(self, cat: ElementCategory) -> list[StructureItem]:
        return [i for i in self._items.values() if i.category == cat]


def load_structure_catalog(ruta: str | Path | None = None) -> StructureCatalog:
    """Carga el catálogo de estructuras desde CSV (bundled o ruta dada)."""

    if ruta is None:
        # bundled dentro del paquete si existe; si no, config/
        cand = Path("config/catalogo_estructura.csv")
        ruta = cand if cand.exists() else None
    if ruta is None:
        try:
            text = resources.files("ptnt.ref").joinpath("catalogo_estructura.csv").read_text(
                encoding="utf-8"
            )
            return _parse_csv_text(text)
        except (FileNotFoundError, ModuleNotFoundError):
            raise FileNotFoundError("Catálogo de estructuras no encontrado")
    return _parse_csv_text(Path(ruta).read_text(encoding="utf-8"))


def _parse_csv_text(text: str) -> StructureCatalog:
    items: dict[str, StructureItem] = {}
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        code = (row.get("codigo_estructura") or "").strip()
        if not code:
            continue
        items[code] = StructureItem(
            code=code,
            category=classify_structure(code),
            power=_to_float(row.get("potencia", "")),
            power2=_to_float(row.get("potencia2", "")),
            loss_w=_to_float(row.get("perdidas_w", "")),
            loss2_w=_to_float(row.get("perdidas2_w", "")),
            double_level=str(row.get("doblenivel", "")).strip().upper() in {"S", "SI", "1", "TRUE"},
            description=(row.get("descripcion_larga") or "").strip(),
        )
    return StructureCatalog(items)
