"""Cuántos núcleos y cuánta memoria hay, y cuántas tareas caben.

La pregunta operativa no es «¿cuántos núcleos tiene el servidor?» sino «¿cuántos
alimentadores puedo procesar a la vez sin que el sistema operativo empiece a
paginar?». Y ahí **la memoria manda, no la CPU**.

Un alimentador urbano con 200 000 nodos ocupa cientos de megabytes mientras se
resuelve el flujo. En una máquina de 16 núcleos y 16 GB, lanzar 16 procesos a la
vez no da 16 veces la velocidad: da *swap*, y con swap el trabajo tarda más que
en secuencial. Peor aún, el sistema operativo puede matar procesos y perder
horas de cálculo sin decir por qué.

Por eso el presupuesto se calcula como::

    trabajadores = min(núcleos_utilizables, memoria_utilizable / coste_por_tarea)

y nunca baja de 1: siempre hay que poder procesar aunque sea de a uno.

**Se descuenta una reserva** para el sistema operativo y para los servicios que
comparten el equipo —la API móvil tiene que seguir respondiendo mientras se
recalcula un lote—. Un servidor al 100 % de memoria no es un servidor rápido: es
uno que está a punto de caerse.

La detección funciona sin dependencias externas. `psutil` se usa si está
instalado porque da la memoria **disponible** de verdad (contando caché
reclamable); si no está, se lee del sistema operativo directamente.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from dataclasses import dataclass

# Si no se puede averiguar la memoria, se asume un equipo modesto. Equivocarse
# por abajo cuesta velocidad; equivocarse por arriba cuesta el trabajo entero.
_RAM_DESCONOCIDA_MB = 4096


@dataclass(frozen=True)
class Recursos:
    """Foto del equipo en un instante."""

    cpus: int
    ram_total_mb: int
    ram_disponible_mb: int
    fuente: str                      # de dónde salió la medición

    def resumen(self) -> dict:
        return {
            "cpus": self.cpus,
            "ram_total_mb": self.ram_total_mb,
            "ram_disponible_mb": self.ram_disponible_mb,
            "ram_libre_pct": round(
                100.0 * self.ram_disponible_mb / max(self.ram_total_mb, 1), 1),
            "medicion": self.fuente,
        }

    @classmethod
    def detectar(cls) -> "Recursos":
        cpus = _cpus_utilizables()
        total, disponible, fuente = _memoria_mb()
        return cls(cpus=cpus, ram_total_mb=total,
                   ram_disponible_mb=disponible, fuente=fuente)


@dataclass(frozen=True)
class Presupuesto:
    """Cuántas tareas caben a la vez, y por qué ese número."""

    trabajadores: int
    limitado_por: str                # "cpu" | "memoria" | "configuracion"
    ram_utilizable_mb: int
    coste_mb_por_tarea: int
    recursos: Recursos

    def explicacion(self) -> str:
        r = self.recursos
        cabe = self.ram_utilizable_mb // max(self.coste_mb_por_tarea, 1)
        return (
            f"{self.trabajadores} tarea(s) en paralelo "
            f"(limita: {self.limitado_por}). "
            f"{r.cpus} núcleo(s) utilizables · "
            f"{self.ram_utilizable_mb:,} MB utilizables de "
            f"{r.ram_disponible_mb:,} MB disponibles · "
            f"caben {cabe} tarea(s) de {self.coste_mb_por_tarea:,} MB."
        )


def calcular_presupuesto(
    *,
    coste_mb_por_tarea: int = 512,
    cpus_maximos: int | None = None,
    ram_reservada_mb: int = 2048,
    fraccion_ram_utilizable: float = 0.75,
    tope: int | None = None,
    ligado_a_cpu: bool = True,
    recursos: Recursos | None = None,
) -> Presupuesto:
    """Cuántas tareas lanzar a la vez en este equipo, ahora mismo.

    ``coste_mb_por_tarea`` es la memoria pico estimada de una tarea. Ponerlo bajo
    hace que el sistema lance de más y acabe paginando; ponerlo alto desperdicia
    núcleos. Se mide con `medir_coste()` sobre un caso real y se ajusta en el
    YAML — no se adivina.

    ``ram_reservada_mb`` es lo que **nunca** se toca: el sistema operativo, la API
    móvil que debe seguir respondiendo, la base de datos. Sin esa reserva, un
    recálculo pesado deja a las cuadrillas sin poder descargar su trabajo.

    ``ligado_a_cpu=False`` para trabajo de **espera**: leer de once bases de datos
    se pasa el tiempo esperando a la red, no calculando. Limitar esas lecturas al
    número de núcleos sería absurdo —cuatro núcleos no impiden tener once
    conexiones abiertas—, y en un servidor modesto dejaría la ingesta a un tercio
    de su velocidad posible por un límite que no aplica.
    """

    r = recursos or Recursos.detectar()

    if not ligado_a_cpu:
        cpus = max(1, cpus_maximos or r.cpus)
    elif cpus_maximos is None:
        cpus = r.cpus
    else:
        cpus = min(r.cpus, max(1, cpus_maximos))

    utilizable = int(max(0, r.ram_disponible_mb - ram_reservada_mb)
                     * max(0.0, min(1.0, fraccion_ram_utilizable)))
    por_memoria = utilizable // max(coste_mb_por_tarea, 1)

    trabajadores = max(1, min(cpus, por_memoria))
    if por_memoria < cpus:
        limitado = "memoria"
    else:
        limitado = "cpu" if ligado_a_cpu else "concurrencia"

    if tope is not None and tope > 0 and tope < trabajadores:
        trabajadores, limitado = tope, "configuracion"

    return Presupuesto(
        trabajadores=trabajadores, limitado_por=limitado,
        ram_utilizable_mb=utilizable, coste_mb_por_tarea=coste_mb_por_tarea,
        recursos=r)


def memoria_disponible_mb() -> int:
    """Memoria disponible **ahora**, para decidir si admitir una tarea más.

    El presupuesto se calcula una vez, pero el equipo cambia mientras se trabaja:
    otro proceso arranca, alguien abre el tablero. Consultar antes de cada tarea
    evita lanzar la que rompe el equipo.
    """

    return _memoria_mb()[1]


def hay_margen(coste_mb: int, *, reserva_mb: int = 1024) -> bool:
    """¿Cabe una tarea más sin comerse la reserva del sistema?"""

    return memoria_disponible_mb() - coste_mb >= reserva_mb


# --------------------------------------------------------------------------- #
# Detección por sistema operativo
# --------------------------------------------------------------------------- #
def _cpus_utilizables() -> int:
    """Núcleos que este proceso puede usar de verdad.

    En un contenedor con `--cpuset-cpus`, `os.cpu_count()` devuelve los del
    anfitrión y sobredimensiona el pool. `sched_getaffinity` devuelve los que la
    planificación permite, que es el número correcto.
    """

    try:
        return max(1, len(os.sched_getaffinity(0)))     # Linux
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def _memoria_mb() -> tuple[int, int, str]:
    """``(total, disponible, fuente)`` en MB."""

    # psutil, si está: da la memoria disponible real, contando caché reclamable.
    try:
        import psutil                                    # type: ignore

        m = psutil.virtual_memory()
        return (m.total // (1 << 20), m.available // (1 << 20), "psutil")
    except Exception:
        pass

    sistema = platform.system()
    if sistema == "Linux":
        v = _memoria_linux()
        if v:
            return (*v, "/proc/meminfo")
    elif sistema == "Windows":
        v = _memoria_windows()
        if v:
            return (*v, "GlobalMemoryStatusEx")
    elif sistema == "Darwin":
        v = _memoria_macos()
        if v:
            return (*v, "sysctl")

    return (_RAM_DESCONOCIDA_MB, _RAM_DESCONOCIDA_MB // 2, "desconocida")


def _memoria_linux() -> tuple[int, int] | None:
    try:
        datos: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for linea in f:
                clave, _, resto = linea.partition(":")
                partes = resto.split()
                if partes:
                    datos[clave] = int(partes[0])        # kB
        total = datos.get("MemTotal", 0) // 1024
        # MemAvailable es la estimación del propio núcleo de cuánto se puede
        # pedir sin paginar. MemFree se queda corta: ignora la caché reclamable.
        disponible = datos.get("MemAvailable", datos.get("MemFree", 0)) // 1024
        if total:
            return total, disponible or total // 2
    except (OSError, ValueError):
        pass
    return None


def _memoria_windows() -> tuple[int, int] | None:
    class _Estado(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        estado = _Estado()
        estado.dwLength = ctypes.sizeof(_Estado)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
            return None
        return (int(estado.ullTotalPhys) // (1 << 20),
                int(estado.ullAvailPhys) // (1 << 20))
    except (AttributeError, OSError):
        return None


def _memoria_macos() -> tuple[int, int] | None:
    try:
        salida = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                capture_output=True, text=True, timeout=5)
        total = int(salida.stdout.strip()) // (1 << 20)
        # macOS no expone un equivalente directo de MemAvailable sin vm_stat;
        # se asume la mitad, que es conservador y no arriesga el equipo.
        return total, total // 2
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
