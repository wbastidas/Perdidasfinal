"""Ejecución acotada por los recursos del equipo.

Tres piezas que responden a la misma pregunta —«¿cuánto aguanta este servidor?»—
en tres frentes distintos:

* :mod:`resources` — cuántos núcleos y cuánta memoria hay **ahora**, y cuántas
  tareas caben. La memoria manda: dieciséis procesos en un equipo de 16 GB no
  dan dieciséis veces la velocidad, dan *swap*.
* :mod:`pool` — procesa hasta donde el equipo aguanta y **encola el resto**, con
  aislamiento de fallos y control de memoria antes de cada tarea.
* :mod:`gate` — control de admisión de la API móvil: cuántas descargas y subidas
  simultáneas se atienden, cuántas esperan y cuándo se responde «vuelva en N
  segundos» en vez de aceptar una petición que nadie va a poder atender.
"""

from ptnt.runtime.gate import (
    MetricasPortero,
    Portero,
    PorterosServicio,
    ServicioSaturado,
)
from ptnt.runtime.pool import (
    EjecutorTareas,
    ResultadoLote,
    ResultadoTarea,
)
from ptnt.runtime.resources import (
    Presupuesto,
    Recursos,
    calcular_presupuesto,
    hay_margen,
    memoria_disponible_mb,
)

__all__ = [
    "Recursos", "Presupuesto", "calcular_presupuesto", "memoria_disponible_mb",
    "hay_margen",
    "EjecutorTareas", "ResultadoLote", "ResultadoTarea",
    "Portero", "PorterosServicio", "ServicioSaturado", "MetricasPortero",
]
