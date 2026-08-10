"""Identidad geográfica estable de las ubicaciones de levantamiento.

**Problema que resuelve.** Los sectores nacen de un agrupamiento (HDBSCAN o
rejilla), y sus etiquetas dependen del orden y del conjunto de datos: al cargar un
mes nuevo, el sector que ayer era ``SEC-0003`` puede pasar a ser otro sitio. Una
orden de trabajo emitida a campo quedaría apuntando a un lugar equivocado.

**Solución.** El identificador se deriva de **la coordenada del sitio**, no del
orden de cálculo: mismo lugar ⇒ mismo identificador, siempre, sin importar cuántas
veces se recalcule ni qué datos nuevos entren.

```
SEC-E620.1-N9755.4      (UTM 17S, 100 m de resolución)
```

Además se mantiene un **registro persistente de ubicaciones** con la primera y la
última vez que cada sitio fue priorizado y cuántas veces lo fue. Eso permite:

* Reemitir una orden sabiendo que apunta al mismo predio.
* Ver la **reincidencia**: un sector priorizado seis meses seguidos y nunca
  inspeccionado no es lo mismo que uno que aparece por primera vez.
* Conservar el histórico aunque cambie la topología o el algoritmo de agrupamiento.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

# Resolución del código geográfico: 100 m es suficiente para que una cuadrilla
# ubique el sitio y lo bastante grueso para que el mismo lugar no cambie de código
# por variaciones menores del centroide entre corridas.
RESOLUCION_M = 100.0


def geo_code(x: float, y: float, *, prefijo: str = "SEC",
             resolucion_m: float = RESOLUCION_M) -> str:
    """Código estable derivado de la coordenada UTM.

    ``SEC-E620.1-N9755.4`` = 620 100 m Este, 9 755 400 m Norte. Dos corridas
    distintas sobre el mismo lugar producen el mismo código.
    """

    if x is None or y is None or not (pd.notna(x) and pd.notna(y)):
        return f"{prefijo}-SIN-COORD"
    xr = round(float(x) / resolucion_m) * resolucion_m
    yr = round(float(y) / resolucion_m) * resolucion_m
    return f"{prefijo}-E{xr/1000:.1f}-N{yr/1000:.1f}"


@dataclass
class LocationRecord:
    """Una ubicación física con su historia de priorización."""

    location_id: str
    nivel: str
    x: float | None
    y: float | None
    primera_deteccion: str
    ultima_deteccion: str
    veces_priorizado: int = 1
    prioridad_max: float = 0.0
    prioridad_ultima: float = 0.0
    entidades: list[str] = field(default_factory=list)   # ids algorítmicos vistos
    inspeccionado: bool = False
    fecha_inspeccion: str | None = None
    resultado_inspeccion: str | None = None

    @property
    def reincidente(self) -> bool:
        """Priorizado en 3 o más corridas sin haber sido inspeccionado."""
        return self.veces_priorizado >= 3 and not self.inspeccionado


class LocationRegistry:
    """Registro persistente de ubicaciones priorizadas.

    Se guarda en JSON junto a las salidas. Sobrevive a recálculos, a cargas de
    datos nuevos y a cambios de topología: la clave es la **coordenada**, no el
    identificador que produjo el algoritmo.
    """

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self._reg: dict[str, LocationRecord] = {}
        if self.ruta.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.ruta.read_text(encoding="utf-8"))
        for r in data.get("ubicaciones", []):
            self._reg[r["location_id"]] = LocationRecord(**r)

    def save(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ubicaciones": [vars(r) for r in self._reg.values()]}
        self.ruta.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._reg)

    def get(self, location_id: str) -> LocationRecord | None:
        return self._reg.get(location_id)

    def register(
        self, location_id: str, nivel: str, x: float | None, y: float | None,
        prioridad: float, entidad: str | None = None,
        fecha: str | None = None,
    ) -> LocationRecord:
        """Registra (o actualiza) una ubicación priorizada en esta corrida."""

        hoy = fecha or date.today().isoformat()
        rec = self._reg.get(location_id)
        if rec is None:
            rec = LocationRecord(
                location_id=location_id, nivel=nivel, x=x, y=y,
                primera_deteccion=hoy, ultima_deteccion=hoy,
                veces_priorizado=1, prioridad_max=prioridad,
                prioridad_ultima=prioridad,
                entidades=[entidad] if entidad else [],
            )
            self._reg[location_id] = rec
        else:
            # No se re-cuenta si ya se registró hoy (permite reejecutar sin inflar)
            if rec.ultima_deteccion != hoy:
                rec.veces_priorizado += 1
            rec.ultima_deteccion = hoy
            rec.prioridad_ultima = prioridad
            rec.prioridad_max = max(rec.prioridad_max, prioridad)
            if entidad and entidad not in rec.entidades:
                rec.entidades.append(entidad)
            if x is not None and pd.notna(x):
                rec.x, rec.y = x, y
        return rec

    def marcar_inspeccionado(self, location_id: str, resultado: str,
                             fecha: str | None = None) -> bool:
        """Cierra el ciclo: registra que la cuadrilla ya fue y qué encontró."""

        rec = self._reg.get(location_id)
        if rec is None:
            return False
        rec.inspeccionado = True
        rec.fecha_inspeccion = fecha or date.today().isoformat()
        rec.resultado_inspeccion = resultado
        return True

    def reincidentes(self) -> list[LocationRecord]:
        """Ubicaciones priorizadas repetidamente y aún sin inspeccionar."""

        return sorted(
            (r for r in self._reg.values() if r.reincidente),
            key=lambda r: (r.veces_priorizado, r.prioridad_max), reverse=True,
        )

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "location_id": r.location_id, "nivel": r.nivel,
                "x": r.x, "y": r.y,
                "primera_deteccion": r.primera_deteccion,
                "ultima_deteccion": r.ultima_deteccion,
                "veces_priorizado": r.veces_priorizado,
                "prioridad_max": round(r.prioridad_max, 4),
                "prioridad_ultima": round(r.prioridad_ultima, 4),
                "reincidente": r.reincidente,
                "inspeccionado": r.inspeccionado,
                "fecha_inspeccion": r.fecha_inspeccion,
                "resultado_inspeccion": r.resultado_inspeccion,
            }
            for r in self._reg.values()
        ])


def register_plan(plan, registry: LocationRegistry, *, fecha: str | None = None) -> int:
    """Registra todas las ubicaciones con coordenada de un plan de levantamientos.

    Devuelve cuántas ubicaciones se registraron. Los objetivos sin coordenada
    (p. ej. un alimentador completo) no forman parte del registro geográfico.
    """

    n = 0
    for t in plan.targets:
        if t.centroid_x is None or not pd.notna(t.centroid_x):
            continue
        prefijo = {"SECTOR": "SEC", "PUESTO_TRANSFORMACION": "PTR",
                   "RAMAL": "RAM", "CLIENTE": "CLI"}.get(t.level.value, "UBI")
        lid = geo_code(t.centroid_x, t.centroid_y, prefijo=prefijo)
        registry.register(lid, t.level.value, t.centroid_x, t.centroid_y,
                          t.priority_score, entidad=t.entity_id, fecha=fecha)
        n += 1
    return n
