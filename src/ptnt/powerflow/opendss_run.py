"""Ejecución de OpenDSS y comparación de motores (§8.7).

Ejecuta el script ``.dss`` generado por el exportador usando **OpenDSSDirect.py**
(si está instalado), extrae las pérdidas y tensiones, y las compara contra el
motor propio. Discrepancia mayor que la tolerancia bloquea la publicación del
resultado de ese alimentador (regla de comparación automática obligatoria).

OpenDSSDirect es un extra opcional; si no está instalado, ``run_opendss`` devuelve
``None`` y la comparación se marca como no disponible, sin romper el pipeline.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ptnt.powerflow.opendss_export import export_to_dss
from ptnt.ref.catalogs import ConductorCatalog
from ptnt.topology.graph import NetworkModel


@dataclass
class OpenDSSResult:
    total_loss_kw: float
    line_loss_kw: float
    v_min_pu: float
    converged: bool


@dataclass
class EngineComparison:
    available: bool
    own_loss_kw: float
    opendss_loss_kw: float | None
    diff_pct: float | None
    within_tolerance: bool | None
    tolerance_pct: float
    detail: str


def opendss_available() -> bool:
    try:
        import opendssdirect  # noqa: F401

        return True
    except Exception:
        return False


def run_opendss(
    model: NetworkModel, conductors: ConductorCatalog, **export_kw
) -> OpenDSSResult | None:
    """Ejecuta el modelo en OpenDSS y devuelve pérdidas/tensión. ``None`` si no está
    instalado OpenDSSDirect."""

    try:
        import opendssdirect as dss
    except Exception:  # pragma: no cover - depende de instalación
        return None

    contenido = export_to_dss(model, conductors, **export_kw)
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "circuito.dss"
        ruta.write_text(contenido, encoding="utf-8")
        dss.Text.Command(f'Redirect "{ruta}"')
        # Pérdidas totales (incluye transformadores) y de línea (solo conductores).
        total_kw = float(dss.Circuit.Losses()[0]) / 1000.0
        line_kw = float(dss.Circuit.LineLosses()[0])  # ya en kW
        vmags = dss.Circuit.AllBusMagPu()
        v_min = float(min(vmags)) if vmags else 1.0
        converged = bool(dss.Solution.Converged())
    return OpenDSSResult(
        total_loss_kw=total_kw, line_loss_kw=line_kw, v_min_pu=v_min, converged=converged
    )


def compare_engines(
    own_line_loss_kw: float,
    model: NetworkModel,
    conductors: ConductorCatalog,
    *,
    node_kw: dict[str, float] | None = None,
    tolerance_pct: float = 2.0,
    **export_kw,
) -> EngineComparison:
    """Compara la **pérdida de línea** del motor propio contra OpenDSS.

    Se comparan pérdidas de conductores (no totales, que incluirían transformadores)
    y ambos motores usan **las mismas cargas** (``node_kw``) para que la comparación
    sea legítima. Si OpenDSS no está disponible, ``available=False`` y no se bloquea.
    """

    dss_res = run_opendss(model, conductors, node_kw=node_kw, **export_kw)
    if dss_res is None:
        return EngineComparison(
            available=False, own_loss_kw=own_line_loss_kw, opendss_loss_kw=None,
            diff_pct=None, within_tolerance=None, tolerance_pct=tolerance_pct,
            detail="OpenDSSDirect no instalado (pip install 'ptnt-bal[opendss]').",
        )
    ref = dss_res.line_loss_kw
    if ref == 0:
        diff = 0.0 if own_line_loss_kw == 0 else 100.0
    else:
        diff = abs(own_line_loss_kw - ref) / abs(ref) * 100.0
    ok = diff <= tolerance_pct
    return EngineComparison(
        available=True, own_loss_kw=own_line_loss_kw, opendss_loss_kw=ref,
        diff_pct=diff, within_tolerance=ok, tolerance_pct=tolerance_pct,
        detail=("OK" if ok else
                f"Discrepancia {diff:.1f}% > {tolerance_pct}% en pérdidas de línea: "
                "bloquea publicación."),
    )
