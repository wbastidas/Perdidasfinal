"""Ejecución del proceso y tareas programadas.

Un único camino para lanzar el trabajo —botón del tablero, tarea de la madrugada
o consola— y la programación delegada al planificador del sistema operativo.
"""

from ptnt.jobs.ejecutor import (
    Bitacora,
    Ejecucion,
    ResultadoPaso,
    ejecutar_plan,
    lanzar_en_segundo_plano,
)
from ptnt.jobs.pasos import (
    PASOS,
    PLANES,
    POR_CLAVE,
    Opcion,
    Paso,
    duracion_estimada,
    resolver_plan,
    supuestos_previos,
)
from ptnt.jobs.programacion import (
    AlmacenProgramaciones,
    Programacion,
    ProgramacionError,
    comando_windows,
    instrucciones,
    linea_cron,
    orden_ptnt,
)

__all__ = [
    "Bitacora", "Ejecucion", "ResultadoPaso", "ejecutar_plan",
    "lanzar_en_segundo_plano",
    "PASOS", "PLANES", "POR_CLAVE", "Opcion", "Paso", "duracion_estimada",
    "resolver_plan", "supuestos_previos",
    "AlmacenProgramaciones", "Programacion", "ProgramacionError",
    "comando_windows", "instrucciones", "linea_cron", "orden_ptnt",
]
