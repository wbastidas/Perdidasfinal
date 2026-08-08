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


def validate_against_opendss(
    *,
    v_ll: float = 13800.0,
    load_kw: float = 500.0,
    r_ohm_km: float = 0.3,
    length_km: float = 2.0,
    tol_pct: float = 2.0,
) -> ValidationCase | None:
    """Compara el motor propio contra **OpenDSS** en un caso de un solo nivel de
    tensión (MT), donde el modelo es inequívoco: una línea con una carga.

    Ambos usan la misma carga y se comparan las pérdidas de línea. Devuelve
    ``None`` si OpenDSSDirect no está instalado.
    """

    from ptnt.powerflow.opendss_run import opendss_available

    if not opendss_available():
        return None
    import tempfile
    from pathlib import Path as _Path

    import opendssdirect as dss

    v_ln = v_ll / math.sqrt(3.0)
    cat = ConductorCatalog({"CVAL": Conductor("CVAL", "val", "AL", 50, r_ohm_km, 0.0, 1e6, "MT")})
    model = NetworkModel(
        "VALDSS", "SRC",
        [Edge("s1", "SRC", "N1", "CVAL", length_km, n_phases=3, voltage_v=v_ll, is_lv=False)],
        customer_nodes={"N1": [{"customer_id": "c1", "energy_kwh": 0}]},
    )
    g = build_radial_graph(model)
    own = run_powerflow(
        g, {"N1": load_kw}, {"N1": 1.0}, cat, FlujoConfig(max_iteraciones=200),
        base_voltage_ln=v_ln, t_op=20.0, alpha=0.00403, t_ref=20.0,
    )

    kv = v_ll / 1000.0
    # .dss inline con carga TRIFÁSICA BALANCEADA (comparable con el motor propio)
    script = f"""Clear
New Circuit.val basekv={kv} bus1=SRC phases=3 pu=1.0 X1=0.0001 R1=0.0001 X0=0.0001 R0=0.0001
New LineCode.cval nphases=3 R1={r_ohm_km} X1=0.0 R0={r_ohm_km} X0=0.0 Units=km
New Line.s1 bus1=SRC bus2=N1 phases=3 linecode=cval length={length_km} units=km
New Load.l1 bus1=N1 phases=3 kV={kv} kW={load_kw} pf=1.0 model=1
Set voltagebases=[{kv}]
Calcvoltagebases
Solve
"""
    with tempfile.TemporaryDirectory() as tmp:
        ruta = _Path(tmp) / "val.dss"
        ruta.write_text(script, encoding="utf-8")
        dss.Text.Command(f'Redirect "{ruta}"')
        dss_line_kw = float(dss.Circuit.LineLosses()[0])

    err = abs(own.loss_kw_peak - dss_line_kw) / dss_line_kw * 100.0 if dss_line_kw else 0.0
    return ValidationCase(
        name="vs_opendss_MT",
        loss_kw_expected=dss_line_kw,
        loss_kw_computed=own.loss_kw_peak,
        error_pct=err,
        passed=err <= tol_pct,
    )


def run_validation_suite(tol_pct: float = 2.0) -> list[ValidationCase]:
    """Ejecuta la batería de casos de validación disponibles.

    Incluye los casos analíticos siempre, y el caso contra OpenDSS solo si
    OpenDSSDirect está instalado.
    """

    casos = [
        validate_single_load_radial(load_kw=500.0, tol_pct=tol_pct),
        validate_single_load_radial(load_kw=300.0, r_ohm_km=0.2, length_km=3.0, tol_pct=tol_pct),
    ]
    dss_case = validate_against_opendss(tol_pct=tol_pct)
    if dss_case is not None:
        casos.append(dss_case)
    return casos
