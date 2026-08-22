"""Espacio de trabajo del analista: escenarios, iteraciones y evaluación.

Permite probar cambios sobre un alimentador o una subestación **sin publicarlos**,
ver el balance al momento, y conservar cada evaluación como un punto en la
evolución de esa entidad.
"""

from ptnt.workspace.escenarios import (
    AlmacenEscenarios,
    CambioPropuesto,
    Comparacion,
    Escenario,
    EscenarioError,
    EstadoEscenario,
    Iteracion,
    comparar,
)
from ptnt.workspace.evaluacion import (
    CambioNoAplicado,
    ResultadoEvaluacion,
    aplicar_cambios,
    energia_cabecera,
    evaluar_escenario,
)

__all__ = [
    "AlmacenEscenarios", "CambioPropuesto", "Comparacion", "Escenario",
    "EscenarioError", "EstadoEscenario", "Iteracion", "comparar",
    "CambioNoAplicado", "ResultadoEvaluacion", "aplicar_cambios",
    "energia_cabecera", "evaluar_escenario",
]
