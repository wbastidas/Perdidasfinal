"""Validación del flujo de potencia contra casos de solución conocida (§8.7).

La especificación pide reproducir IEEE 13/34/123 dentro de tolerancia. Esos casos
son trifásicos desbalanceados (con reguladores, condensadores y transformadores) y
requieren el motor trifásico completo, que es evolución del actual.

Mientras tanto, este módulo valida el motor de barrido contra un **caso radial de
solución analítica cerrada**, que es la prueba de correctitud fundamental: para una
línea con una sola carga, la caída de tensión y la pérdida tienen forma cerrada y el
motor debe reproducirlas dentro de tolerancia estrecha. La estructura permite añadir
los casos IEEE cuando esté el motor trifásico.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ptnt.config.models import FlujoConfig
from ptnt.powerflow.bfs import run_powerflow
from ptnt.ref.catalogs import Conductor, ConductorCatalog
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph


@dataclass
class ValidationCase:
    name: str
    loss_kw_expected: float
    loss_kw_computed: float
    error_pct: float
    passed: bool


def validate_single_load_radial(
    *,
    v_ll: float = 13800.0,
    load_kw: float = 500.0,
    pf: float = 1.0,
    r_ohm_km: float = 0.3,
    length_km: float = 2.0,
    tol_pct: float = 2.0,
) -> ValidationCase:
    """Valida contra el caso de una carga al final de una línea.

    Con factor de potencia unitario y carga ligera frente a la línea (régimen MT,
    caída de tensión despreciable), la pérdida trifásica tiene forma cerrada
    ``3·I²·R`` con ``I = P/(√3·V_LL)``. El motor de barrido —que además modela la
    caída de tensión y el aumento de corriente de la carga de potencia constante—
    debe reproducirla dentro de ``tol_pct``.
    """

    v_ln = v_ll / math.sqrt(3.0)
    cat = ConductorCatalog({"CVAL": Conductor("CVAL", "val", "AL", 50, r_ohm_km, 0.0, 1e6, "BT")})
    model = NetworkModel(
        "VAL", "SRC",
        [Edge("s1", "SRC", "N1", "CVAL", length_km, n_phases=3, voltage_v=v_ll, is_lv=True)],
        customer_nodes={"N1": [{"customer_id": "c1", "energy_kwh": 0}]},
    )
    g = build_radial_graph(model)
    res = run_powerflow(
        g, {"N1": load_kw}, {"N1": pf}, cat, FlujoConfig(max_iteraciones=200),
        base_voltage_ln=v_ln, t_op=20.0, alpha=0.00403, t_ref=20.0,
    )
    i_ideal = load_kw * 1000.0 / (math.sqrt(3.0) * v_ll)
    loss_ideal = 3.0 * i_ideal**2 * r_ohm_km * length_km / 1000.0
    err = abs(res.loss_kw_peak - loss_ideal) / loss_ideal * 100.0 if loss_ideal else 0.0
    return ValidationCase(
        name="radial_una_carga",
        loss_kw_expected=loss_ideal,
        loss_kw_computed=res.loss_kw_peak,
        error_pct=err,
        passed=err <= tol_pct,
    )


def run_validation_suite(tol_pct: float = 2.0) -> list[ValidationCase]:
    """Ejecuta la batería de casos de validación disponibles."""

    return [
        validate_single_load_radial(load_kw=500.0, tol_pct=tol_pct),
        validate_single_load_radial(load_kw=300.0, r_ohm_km=0.2, length_km=3.0, tol_pct=tol_pct),
    ]
