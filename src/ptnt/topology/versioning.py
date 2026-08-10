"""Versionado de la red por alimentador (§E1.3).

Implementa la regla del proyecto: **alimentador nuevo → alta; alimentador
existente → actualización**, con detección de qué cambió y qué hay que recalcular.

Tres hashes independientes, para no recalcularlo todo cuando cambia poco:

| Hash | Qué cubre | Qué invalida al cambiar |
|---|---|---|
| ``topology_hash`` | conectividad y longitudes | topología, flujo, pérdidas, balance |
| ``attribute_hash`` | conductor, fases, tensión, kVA | calidad, flujo, pérdidas, balance |
| ``switch_state_hash`` | posiciones de maniobra | topología dinámica, balance |

**Lo que se conserva siempre:** las ubicaciones del registro geográfico
(``survey.locations``) y el histórico de resultados en `gold`. Una orden de trabajo
emitida antes del cambio sigue apuntando al mismo sitio físico, porque su
identificador viene de la coordenada y no de la topología.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from ptnt.topology.graph import NetworkModel


class VersionAction(str, Enum):
    ALTA = "ALTA"
    ACTUALIZACION = "ACTUALIZACION"
    SIN_CAMBIO = "SIN_CAMBIO"


# Qué etapas invalida cada hash (§E1.3)
INVALIDACIONES = {
    "topology_hash": ["topologia", "flujo", "perdidas", "balance", "focalizacion"],
    "attribute_hash": ["calidad", "flujo", "perdidas", "balance"],
    "switch_state_hash": ["topologia_dinamica", "balance"],
}


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:16]


def topology_hash(model: NetworkModel) -> str:
    """Hash de conectividad y longitudes (qué está conectado con qué)."""

    datos = sorted(
        (e.segment_id, e.from_node, e.to_node, round(e.length_km, 4))
        for e in model.edges
    )
    return _sha({"source": model.source_node, "edges": datos})


def attribute_hash(model: NetworkModel) -> str:
    """Hash de atributos eléctricos (conductor, fases, tensión, kVA del puesto)."""

    tramos = sorted(
        (e.segment_id, e.conductor_code, e.n_phases, e.voltage_v)
        for e in model.edges
    )
    puestos = sorted(
        (ts.get("site_id", n), ts.get("kva"), ts.get("config"), ts.get("fases"))
        for n, ts in model.transformer_sites.items()
    )
    return _sha({"tramos": tramos, "puestos": puestos})


def switch_state_hash(model: NetworkModel) -> str:
    """Hash del estado de los dispositivos de maniobra."""

    sw = sorted(
        (n, s.get("switch_id"), s.get("normal_pos"), s.get("current_pos"))
        for n, s in model.switch_nodes.items()
    )
    return _sha(sw)


@dataclass
class FeederVersion:
    feeder_code: str
    version_id: int
    loaded_at: str
    topology_hash: str
    attribute_hash: str
    switch_state_hash: str
    element_counts: dict = field(default_factory=dict)
    is_current: bool = True


@dataclass
class VersionResult:
    """Resultado de versionar un alimentador."""

    action: VersionAction
    feeder_code: str
    version_id: int
    hashes_cambiados: list[str] = field(default_factory=list)
    etapas_a_recalcular: list[str] = field(default_factory=list)
    change_summary: dict = field(default_factory=dict)
    ubicaciones_conservadas: bool = True
    detail: str = ""


def element_counts(model: NetworkModel) -> dict:
    return {
        "tramos": len(model.edges),
        "puestos_transformacion": len(model.transformer_sites),
        "nodos_cliente": len(model.customer_nodes),
        "clientes": sum(len(v) for v in model.customer_nodes.values()),
        "luminarias": sum(len(v) for v in model.streetlight_nodes.values()),
        "semaforos_camaras": sum(len(v) for v in model.traffic_light_nodes.values()),
        "seccionadores": len(model.switch_nodes),
        "capacitores": len(model.capacitor_nodes),
        "postes": len(model.pole_nodes),
    }


class VersionStore:
    """Almacén de versiones de red por alimentador (JSON local)."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self._versiones: dict[str, list[FeederVersion]] = {}
        if self.ruta.exists():
            data = json.loads(self.ruta.read_text(encoding="utf-8"))
            for code, lst in data.get("alimentadores", {}).items():
                self._versiones[code] = [FeederVersion(**v) for v in lst]

    def save(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        payload = {"alimentadores": {
            code: [vars(v) for v in lst] for code, lst in self._versiones.items()
        }}
        self.ruta.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def current(self, feeder_code: str) -> FeederVersion | None:
        for v in reversed(self._versiones.get(feeder_code, [])):
            if v.is_current:
                return v
        return None

    def history(self, feeder_code: str) -> list[FeederVersion]:
        return list(self._versiones.get(feeder_code, []))

    def register(self, model: NetworkModel) -> VersionResult:
        """Registra la red: alta si es nueva, actualización si cambió.

        No borra la versión anterior: la marca ``is_current = False`` y conserva su
        histórico, de modo que se pueda comparar y auditar qué cambió y cuándo.
        """

        code = model.feeder_code
        th, ah, sh = (topology_hash(model), attribute_hash(model),
                      switch_state_hash(model))
        counts = element_counts(model)
        ahora = datetime.now().isoformat(timespec="seconds")
        actual = self.current(code)

        if actual is None:
            v = FeederVersion(code, 1, ahora, th, ah, sh, counts, True)
            self._versiones.setdefault(code, []).append(v)
            return VersionResult(
                VersionAction.ALTA, code, 1,
                etapas_a_recalcular=sorted({e for lst in INVALIDACIONES.values()
                                            for e in lst}),
                change_summary={"altas": counts},
                detail=f"Alimentador {code} dado de alta (versión 1): "
                       f"{counts['tramos']} tramos, {counts['clientes']} clientes.",
            )

        cambiados = []
        if actual.topology_hash != th:
            cambiados.append("topology_hash")
        if actual.attribute_hash != ah:
            cambiados.append("attribute_hash")
        if actual.switch_state_hash != sh:
            cambiados.append("switch_state_hash")

        if not cambiados:
            return VersionResult(
                VersionAction.SIN_CAMBIO, code, actual.version_id,
                detail=f"Alimentador {code} sin cambios (versión {actual.version_id}): "
                       "no se reprocesa nada.",
            )

        # Actualización: cerrar la anterior y abrir una nueva
        actual.is_current = False
        nueva_id = actual.version_id + 1
        v = FeederVersion(code, nueva_id, ahora, th, ah, sh, counts, True)
        self._versiones[code].append(v)

        etapas = sorted({e for h in cambiados for e in INVALIDACIONES[h]})
        delta = {
            k: counts.get(k, 0) - actual.element_counts.get(k, 0)
            for k in counts
            if counts.get(k, 0) != actual.element_counts.get(k, 0)
        }
        return VersionResult(
            VersionAction.ACTUALIZACION, code, nueva_id,
            hashes_cambiados=cambiados, etapas_a_recalcular=etapas,
            change_summary={"antes": actual.element_counts, "despues": counts,
                            "delta": delta},
            detail=(
                f"Alimentador {code} actualizado a la versión {nueva_id}. "
                f"Cambió: {', '.join(cambiados)}. "
                f"Se recalcula: {', '.join(etapas)}. "
                "Las ubicaciones del registro geográfico y el histórico de "
                "resultados se conservan."
            ),
        )
