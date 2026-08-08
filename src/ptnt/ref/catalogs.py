"""Carga de catálogos de conductores y transformadores desde YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class CatalogError(Exception):
    """Error de catálogo (código ausente, YAML inválido)."""


# --------------------------------------------------------------------------- #
# Conductores
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Conductor:
    code: str
    nombre: str
    material: str
    seccion_mm2: float
    r_ohm_km_20c: float
    x_ohm_km: float
    ampacidad_a: float
    voltaje_clase: str

    def r_at_temp(self, t_op: float, t_ref: float, alpha: float) -> float:
        """Resistencia corregida por temperatura: R_T = R_20·[1+α·(T−20)]."""

        return self.r_ohm_km_20c * (1.0 + alpha * (t_op - t_ref))


class ConductorCatalog:
    def __init__(self, conductores: dict[str, Conductor]):
        self._c = conductores

    def __contains__(self, code: str) -> bool:
        return code in self._c

    def __len__(self) -> int:
        return len(self._c)

    def get(self, code: str) -> Conductor:
        try:
            return self._c[code]
        except KeyError as exc:
            raise CatalogError(
                f"Conductor '{code}' ausente del catálogo (regla R05, bloqueante)."
            ) from exc

    def get_or_none(self, code: str) -> Conductor | None:
        return self._c.get(code)


def load_conductor_catalog(ruta: str | Path) -> ConductorCatalog:
    data = _load_yaml(ruta)
    conductores: dict[str, Conductor] = {}
    for code, c in (data.get("conductores") or {}).items():
        try:
            conductores[code] = Conductor(
                code=code,
                nombre=c.get("nombre", code),
                material=c.get("material", "AL"),
                seccion_mm2=float(c.get("seccion_mm2", 0)),
                r_ohm_km_20c=float(c["r_ohm_km_20c"]),
                x_ohm_km=float(c.get("x_ohm_km", 0)),
                ampacidad_a=float(c["ampacidad_a"]),
                voltaje_clase=c.get("voltaje_clase", "BT"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise CatalogError(f"Conductor '{code}' mal definido: {exc}") from exc
    if not conductores:
        raise CatalogError(f"Catálogo de conductores vacío: '{ruta}'")
    return ConductorCatalog(conductores)


# --------------------------------------------------------------------------- #
# Transformadores
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TransformerSpec:
    kva: float
    fases: int
    clase: str
    p0_kw: float   # pérdida en vacío (constante)
    pk_kw: float   # pérdida con carga nominal
    z_pct: float


class TransformerCatalog:
    def __init__(self, specs: list[TransformerSpec]):
        self._specs = specs

    def __len__(self) -> int:
        return len(self._specs)

    def nearest(self, kva: float, fases: int, clase: str = "BT") -> TransformerSpec:
        """Devuelve la especificación del kVA más cercano para fases/clase dadas.

        Si no hay ninguna con esas fases/clase, cae a cualquiera por kVA cercano.
        """

        candidatos = [s for s in self._specs if s.fases == fases and s.clase == clase]
        if not candidatos:
            candidatos = [s for s in self._specs if s.fases == fases]
        if not candidatos:
            candidatos = list(self._specs)
        if not candidatos:
            raise CatalogError("Catálogo de transformadores vacío")
        return min(candidatos, key=lambda s: abs(s.kva - kva))


def load_transformer_catalog(ruta: str | Path) -> TransformerCatalog:
    data = _load_yaml(ruta)
    specs: list[TransformerSpec] = []
    for t in data.get("transformadores") or []:
        try:
            specs.append(
                TransformerSpec(
                    kva=float(t["kva"]),
                    fases=int(t["fases"]),
                    clase=t.get("clase", "BT"),
                    p0_kw=float(t["p0_kw"]),
                    pk_kw=float(t["pk_kw"]),
                    z_pct=float(t.get("z_pct", 0)),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise CatalogError(f"Transformador mal definido {t}: {exc}") from exc
    if not specs:
        raise CatalogError(f"Catálogo de transformadores vacío: '{ruta}'")
    return TransformerCatalog(specs)


def _load_yaml(ruta: str | Path) -> dict:
    p = Path(ruta)
    if not p.exists():
        raise CatalogError(f"Catálogo no encontrado: '{p}'")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise CatalogError(f"Catálogo con formato inválido: '{p}'")
    return data
