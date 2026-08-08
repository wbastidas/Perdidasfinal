"""Flujo de potencia radial (§8.7).

Motor propio de barrido hacia atrás/adelante (backward-forward sweep) para redes
radiales. Produce corrientes por tramo y tensiones nodales, base del cálculo de
pérdidas en conductores. Formulación monofásica equivalente balanceada (la
extensión trifásica desbalanceada con neutro queda como evolución del motor).
"""

from ptnt.powerflow.bfs import PowerFlowResult, run_powerflow

__all__ = ["run_powerflow", "PowerFlowResult"]
