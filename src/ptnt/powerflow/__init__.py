"""Flujo de potencia radial (§8.7).

Motor propio de barrido hacia atrás/adelante (backward-forward sweep) para redes
radiales. Produce corrientes por tramo y tensiones nodales, base del cálculo de
pérdidas en conductores. Formulación monofásica equivalente balanceada (la
extensión trifásica desbalanceada con neutro queda como evolución del motor).
"""

from ptnt.powerflow.bfs import PowerFlowResult, run_powerflow
from ptnt.powerflow.bfs3ph import (
    PowerFlow3phResult,
    customer_loads_by_phase,
    run_powerflow_3ph,
)
from ptnt.powerflow.opendss_export import export_to_dss, write_dss
from ptnt.powerflow.opendss_run import (
    EngineComparison,
    compare_engines,
    run_opendss,
)
from ptnt.powerflow.validation import ValidationCase, run_validation_suite

__all__ = [
    "run_powerflow",
    "PowerFlowResult",
    "run_powerflow_3ph",
    "PowerFlow3phResult",
    "customer_loads_by_phase",
    "export_to_dss",
    "write_dss",
    "run_opendss",
    "compare_engines",
    "EngineComparison",
    "run_validation_suite",
    "ValidationCase",
]
