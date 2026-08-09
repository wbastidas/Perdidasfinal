"""Ejecución en paralelo con cola de espera y control de memoria.

El problema real: una unidad de negocio tiene cientos de alimentadores y el
servidor tiene los núcleos que tiene. Lanzarlos todos a la vez no es «más
rápido», es *swap*, y con swap el lote tarda más que en secuencial —cuando no
termina con procesos muertos por el sistema operativo y horas de cálculo
perdidas sin explicación—.

Este módulo procesa **hasta donde el equipo aguanta** y **encola el resto**.
Cuatro decisiones que vienen de eso:

1. **Solo hay N tareas vivas a la vez.** Nunca se crean 500 futuros para 500
   alimentadores: el planificador mantiene la ventana llena y va tomando de la
   cola. Materializar todo consumiría memoria antes de calcular nada.

2. **Se vuelve a mirar la memoria antes de admitir cada tarea.** El presupuesto
   se calcula al empezar, pero el equipo cambia mientras se trabaja: alguien
   abre el tablero, arranca un respaldo. Si el margen se agotó, la tarea espera
   en vez de tumbar el servidor.

3. **Un fallo no cancela el lote.** Un alimentador con datos corruptos no puede
   arruinar las otras 499 horas de cálculo. Se registra, se sigue, y al final se
   reporta qué falló y por qué.

4. **Procesos para cálculo, hilos para espera.** El flujo de potencia es Python
   puro y el GIL lo serializaría: van procesos. Leer de once bases de datos es
   esperar a la red: van hilos, que no pagan el coste de arrancar un intérprete.

Las funciones que se ejecutan en procesos deben ser **importables por nombre**
(definidas al nivel del módulo, no closures ni lambdas): es como
`ProcessPoolExecutor` las envía al trabajador.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ptnt.runtime.resources import Presupuesto, calcular_presupuesto, memoria_disponible_mb


@dataclass
class ResultadoTarea:
    """Qué pasó con una tarea. El fallo es un resultado, no una excepción suelta."""

    clave: str
    ok: bool
    valor: Any = None
    error: str = ""
    segundos: float = 0.0
    espera_s: float = 0.0            # cuánto estuvo en cola antes de arrancar

    @property
    def fallo(self) -> bool:
        return not self.ok


@dataclass
class ResultadoLote:
    """El lote completo, con lo que salió bien y lo que no."""

    resultados: list[ResultadoTarea] = field(default_factory=list)
    presupuesto: Presupuesto | None = None
    segundos: float = 0.0
    esperas_por_memoria: int = 0

    @property
    def ok(self) -> list[ResultadoTarea]:
        return [r for r in self.resultados if r.ok]

    @property
    def fallidos(self) -> list[ResultadoTarea]:
        return [r for r in self.resultados if r.fallo]

    def valores(self) -> dict[str, Any]:
        return {r.clave: r.valor for r in self.resultados if r.ok}

    def resumen(self) -> dict:
        return {
            "tareas": len(self.resultados),
            "ok": len(self.ok),
            "fallidas": len(self.fallidos),
            "segundos": round(self.segundos, 1),
            "trabajadores": self.presupuesto.trabajadores if self.presupuesto else 1,
            "limitado_por": self.presupuesto.limitado_por if self.presupuesto else "-",
            "esperas_por_memoria": self.esperas_por_memoria,
        }

    def informe(self) -> str:
        r = self.resumen()
        base = (f"{r['ok']}/{r['tareas']} tarea(s) en {r['segundos']} s con "
                f"{r['trabajadores']} trabajador(es) (limita: {r['limitado_por']})")
        if r["esperas_por_memoria"]:
            base += f" · {r['esperas_por_memoria']} espera(s) por memoria"
        if self.fallidos:
            base += "\nFallaron:\n" + "\n".join(
                f"  · {f.clave}: {f.error}" for f in self.fallidos[:10])
            if len(self.fallidos) > 10:
                base += f"\n  … y {len(self.fallidos) - 10} más"
        return base


class EjecutorTareas:
    """Pool acotado por CPU **y** memoria, con cola de espera.

    Uso típico::

        ejecutor = EjecutorTareas.desde_config(cfg.recursos, tipo="cpu")
        lote = ejecutor.ejecutar(analizar_alimentador, tareas)

    donde ``tareas`` es un iterable de ``(clave, argumentos)``. Se acepta un
    iterable perezoso a propósito: con 5 000 alimentadores, construir la lista
    entera en memoria antes de empezar ya es parte del problema.
    """

    def __init__(
        self,
        *,
        presupuesto: Presupuesto | None = None,
        tipo: str = "cpu",
        max_en_cola: int = 0,
        reserva_memoria_mb: int = 1024,
        espera_memoria_s: float = 2.0,
        espera_maxima_s: float = 300.0,
        nombre: str = "lote",
    ):
        self.presupuesto = presupuesto or calcular_presupuesto()
        self.tipo = tipo
        self.max_en_cola = max_en_cola
        self.reserva_memoria_mb = reserva_memoria_mb
        self.espera_memoria_s = espera_memoria_s
        self.espera_maxima_s = espera_maxima_s
        self.nombre = nombre

    @classmethod
    def desde_config(cls, cfg, *, tipo: str = "cpu", nombre: str = "lote",
                     coste_mb: int | None = None) -> "EjecutorTareas":
        """Construye el ejecutor desde la sección ``recursos`` del YAML."""

        coste = coste_mb if coste_mb is not None else cfg.coste_mb_por_tarea
        if tipo == "io":
            # Leer de una base no consume memoria de cálculo ni núcleos: se pasa
            # el tiempo esperando a la red. Lo que limita son las sesiones que el
            # servidor de origen tolera. Atarlo al número de núcleos dejaría la
            # ingesta de once unidades de negocio a un tercio de su velocidad en
            # un servidor modesto.
            presupuesto = calcular_presupuesto(
                coste_mb_por_tarea=max(1, coste // 8),
                cpus_maximos=cfg.lecturas_simultaneas,
                ram_reservada_mb=cfg.ram_reservada_mb,
                fraccion_ram_utilizable=cfg.fraccion_ram_utilizable,
                tope=cfg.lecturas_simultaneas, ligado_a_cpu=False)
        else:
            presupuesto = calcular_presupuesto(
                coste_mb_por_tarea=coste,
                cpus_maximos=cfg.cpus,
                ram_reservada_mb=cfg.ram_reservada_mb,
                fraccion_ram_utilizable=cfg.fraccion_ram_utilizable,
                tope=cfg.max_trabajadores or None)
        return cls(presupuesto=presupuesto, tipo=tipo,
                   max_en_cola=cfg.max_en_cola,
                   reserva_memoria_mb=cfg.ram_reservada_mb // 2,
                   espera_maxima_s=cfg.espera_maxima_s,
                   nombre=nombre)

    # -- ejecución ---------------------------------------------------------
    def ejecutar(
        self,
        funcion: Callable[..., Any],
        tareas: Iterable[tuple[str, tuple]],
        *,
        al_terminar: Callable[[ResultadoTarea], None] | None = None,
    ) -> ResultadoLote:
        """Ejecuta ``funcion(*args)`` por cada tarea, respetando el presupuesto.

        ``al_terminar`` se llama en el hilo principal según van llegando
        resultados: sirve para mostrar progreso sin esperar al lote completo, que
        en un recálculo de una unidad de negocio son minutos.
        """

        n = self.presupuesto.trabajadores
        inicio = time.monotonic()
        lote = ResultadoLote(presupuesto=self.presupuesto)
        pendientes: Iterator[tuple[str, tuple]] = iter(tareas)

        logger.info("{}: {}", self.nombre, self.presupuesto.explicacion())

        if n <= 1:
            # Sin paralelismo no se paga el coste de arrancar procesos ni de
            # serializar argumentos, que en un lote pequeño domina el tiempo.
            for clave, args in pendientes:
                # La espera se mide igual que en la ruta paralela: en secuencial
                # la tarea número 40 espera a las 39 anteriores, y esa espera es
                # justamente el argumento para pedir más máquina.
                espera = time.monotonic() - inicio
                r = _ejecutar_una(funcion, clave, args)
                r.espera_s = round(espera, 3)
                lote.resultados.append(r)
                if al_terminar:
                    al_terminar(r)
            lote.segundos = time.monotonic() - inicio
            return lote

        fabrica = ProcessPoolExecutor if self.tipo == "cpu" else ThreadPoolExecutor
        # Los procesos hijos no deben abrir a su vez pools de BLAS: N procesos ×
        # N hilos de OpenBLAS es sobresuscripción, y el equipo se pasa el tiempo
        # cambiando de contexto en vez de calculando.
        entorno = _entorno_hijo() if self.tipo == "cpu" else None

        with fabrica(max_workers=n, **(entorno or {})) as pool:
            vivos: dict[Future, tuple[str, float]] = {}

            def _lanzar() -> bool:
                """Toma la siguiente de la cola si hay hueco y memoria."""
                try:
                    clave, args = next(pendientes)
                except StopIteration:
                    return False
                self._esperar_memoria(lote, clave)
                fut = pool.submit(_ejecutar_una, funcion, clave, args)
                # Cuánto esperó esta tarea desde que arrancó el lote hasta que
                # hubo un hueco libre: es la métrica que dice si el equipo se
                # quedó corto, y la que justifica pedir más máquina.
                vivos[fut] = (clave, time.monotonic() - inicio)
                return True

            while len(vivos) < n and _lanzar():
                pass

            while vivos:
                hechos, _ = wait(list(vivos), return_when=FIRST_COMPLETED)
                for fut in hechos:
                    clave, espera = vivos.pop(fut)
                    try:
                        r = fut.result()
                    except Exception as exc:                      # noqa: BLE001
                        # Un proceso muerto (por memoria, por ejemplo) no entrega
                        # ResultadoTarea: llega como excepción del futuro.
                        r = ResultadoTarea(clave=clave, ok=False,
                                           error=f"{type(exc).__name__}: {exc}")
                    r.espera_s = round(max(0.0, espera), 3)
                    lote.resultados.append(r)
                    if al_terminar:
                        al_terminar(r)
                    _lanzar()

        lote.segundos = time.monotonic() - inicio
        return lote

    def _esperar_memoria(self, lote: ResultadoLote, clave: str) -> None:
        """Retiene la tarea mientras no haya margen de memoria.

        Es la diferencia entre encolar y reventar: si el equipo está al límite —
        porque alguien abrió el tablero o arrancó un respaldo—, la tarea espera.
        Al agotarse la espera máxima se lanza igual: bloquear el lote para
        siempre sería peor que arriesgarse, y el tope existe para que un error de
        medición no cuelgue el proceso.
        """

        coste = self.presupuesto.coste_mb_por_tarea
        limite = time.monotonic() + self.espera_maxima_s
        aviso = False
        while memoria_disponible_mb() - coste < self.reserva_memoria_mb:
            if time.monotonic() >= limite:
                logger.warning(
                    "{}: '{}' arranca sin margen de memoria tras esperar {:.0f} s",
                    self.nombre, clave, self.espera_maxima_s)
                return
            if not aviso:
                logger.info("{}: '{}' en cola, esperando memoria libre",
                            self.nombre, clave)
                aviso = True
                lote.esperas_por_memoria += 1
            time.sleep(self.espera_memoria_s)


def _ejecutar_una(funcion: Callable[..., Any], clave: str,
                  args: tuple) -> ResultadoTarea:
    """Envoltorio que corre en el trabajador y **nunca** propaga la excepción.

    Que un alimentador con datos corruptos cancele el lote entero significaría
    perder el cálculo de los otros 499. El error viaja como dato.
    """

    t0 = time.monotonic()
    try:
        valor = funcion(*args)
        return ResultadoTarea(clave=clave, ok=True, valor=valor,
                              segundos=round(time.monotonic() - t0, 3))
    except Exception as exc:                                      # noqa: BLE001
        return ResultadoTarea(
            clave=clave, ok=False, error=f"{type(exc).__name__}: {exc}",
            segundos=round(time.monotonic() - t0, 3))


def _entorno_hijo() -> dict:
    """Un hilo de BLAS por proceso: evita la sobresuscripción."""

    variables = {
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
    }
    return {"initializer": _fijar_hilos, "initargs": (variables,)}


def _fijar_hilos(variables: dict) -> None:
    for k, v in variables.items():
        os.environ.setdefault(k, v)
