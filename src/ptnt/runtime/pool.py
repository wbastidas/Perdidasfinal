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

5. **La cola no es solo FIFO: admite prioridad.** Cuando se recalcula una unidad
   de negocio entera, el analista está esperando por unos pocos alimentadores
   —el de la campaña en curso, el de la PNT más alta— y los resultados van
   saliendo según terminan. Que esos salgan al final por haber entrado al final
   de la lista es tiempo perdido de una persona.

6. **Se reintenta lo transitorio, nunca lo corrupto.** Un corte de red en una de
   once bases pierde la unidad de negocio entera por algo que se arregla solo en
   dos segundos. Pero reintentar un alimentador con datos malos solo consume el
   lote dos veces, así que esos no se reintentan.

Las funciones que se ejecutan en procesos deben ser **importables por nombre**
(definidas al nivel del módulo, no closures ni lambdas): es como
`ProcessPoolExecutor` las envía al trabajador.
"""

from __future__ import annotations

import errno
import heapq
import os
import time
from collections.abc import Callable, Iterable
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
    intentos: int = 1                # 1 = salió al primer intento
    transitorio: bool = False        # ¿el fallo era de red/sesión, no de datos?

    @property
    def fallo(self) -> bool:
        return not self.ok


# --------------------------------------------------------------------------- #
# Qué se reintenta y qué no
# --------------------------------------------------------------------------- #
# Errores del sistema de ficheros que **no** mejoran por reintentar: si la
# geodatabase no está o no hay permiso, estará igual de ausente dentro de dos
# segundos. Son subclases de OSError, así que hay que excluirlas a mano.
_PERMANENTES = (FileNotFoundError, PermissionError, IsADirectoryError,
                NotADirectoryError, FileExistsError)

# Nombres de clase de los conectores de base de datos. Se comparan por nombre
# porque importar cx_Oracle o psycopg aquí obligaría a instalarlos para poder
# clasificar un error — y el conector que falla vive en el proceso hijo.
# `DatabaseError` queda fuera a propósito: en Oracle cubre también «la tabla no
# existe», que reintentar no arregla.
_NOMBRES_TRANSITORIOS = frozenset({
    "OperationalError",      # sesión caída, base ocupada, listener sin atender
    "InterfaceError",        # conexión perdida a media consulta
    "PoolError", "PoolTimeout", "OperationalTimeout",
})

# Errores de red que sí se resuelven solos: el cable, el cortafuegos que reinicia,
# la base que se está recuperando.
_ERRNOS_TRANSITORIOS = frozenset({
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.EPIPE,
    errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN,
    errno.EAGAIN, errno.EBUSY,
})


def es_transitorio(exc: BaseException) -> bool:
    """¿Merece la pena volver a intentarlo?

    La distinción es la que separa recuperar una unidad de negocio de gastar el
    doble de tiempo para fallar igual. Ante la duda se responde que **no**: un
    reintento de más cuesta el lote dos veces; un reintento de menos cuesta una
    lectura que se puede repetir a mano.
    """

    if isinstance(exc, _PERMANENTES):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _ERRNOS_TRANSITORIOS:
        return True
    return type(exc).__name__ in _NOMBRES_TRANSITORIOS


# Cuántas tareas se mantienen a la vista para elegir la más prioritaria.
#
# Lo que la cola guarda es la **clave y los argumentos** —un código de
# alimentador y una ruta de YAML—, jamás el modelo de red: eso es precisamente lo
# que este módulo no envía a los trabajadores. Mil de esos pares son unos cientos
# de kilobytes, así que la ventana puede ser holgada y cubrir de sobra cualquier
# lote real; el tope existe para que una fuente infinita no llene la memoria de
# argumentos pendientes.
_VENTANA_PRIORIDAD = 1024


class _ColaAdmision:
    """Va sirviendo tareas por prioridad sin materializar la fuente entera.

    Se ordena dentro de una **ventana de admisión**: se mantienen ``ventana``
    tareas a la vista y se sirve la de mayor prioridad de entre ellas. Con
    ``ventana=0`` se ordena globalmente, a cambio de agotar la fuente antes de
    empezar.

    El compromiso solo se nota con una fuente perezosa más larga que la ventana:
    ahí una tarea urgente que aparezca en la posición 5 000 no puede adelantar a
    las 1 000 primeras, porque todavía no se la ha visto. Para lotes normales
    —una unidad de negocio son cientos de alimentadores, no miles— el orden es el
    global.
    """

    def __init__(self, tareas: Iterable, ventana: int):
        self._fuente = iter(tareas)
        self._ventana = max(0, ventana)
        self._monton: list[tuple[int, int, str, tuple]] = []
        self._orden = 0
        self._agotada = False

    def _rellenar(self) -> None:
        while not self._agotada and (self._ventana == 0
                                     or len(self._monton) < self._ventana):
            try:
                clave, args, prioridad = _desempaquetar(next(self._fuente))
            except StopIteration:
                self._agotada = True
                break
            # Prioridad negada porque heapq es un montón de mínimos; `_orden`
            # desempata en FIFO, que es lo que espera quien no usa prioridades:
            # sin él, dos tareas de igual prioridad saldrían en orden arbitrario.
            heapq.heappush(self._monton, (-prioridad, self._orden, clave, args))
            self._orden += 1

    def siguiente(self) -> tuple[str, tuple]:
        """La siguiente tarea, o ``StopIteration`` si ya no queda ninguna."""

        self._rellenar()
        if not self._monton:
            raise StopIteration
        _, _, clave, args = heapq.heappop(self._monton)
        return clave, args


def _desempaquetar(item) -> tuple[str, tuple, int]:
    """Acepta ``(clave, args)`` y ``(clave, args, prioridad)``.

    Sin prioridad se asume 0, con lo que el comportamiento de quien no la use es
    exactamente el de antes: FIFO.
    """

    if len(item) == 3:
        clave, args, prioridad = item
        return clave, args, int(prioridad)
    clave, args = item
    return clave, args, 0


@dataclass
class ResultadoLote:
    """El lote completo, con lo que salió bien y lo que no."""

    resultados: list[ResultadoTarea] = field(default_factory=list)
    presupuesto: Presupuesto | None = None
    segundos: float = 0.0
    esperas_por_memoria: int = 0
    reintentos: int = 0              # cuántas veces se reintentó algo pasajero

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
            "reintentos": self.reintentos,
        }

    @property
    def recuperados(self) -> list[ResultadoTarea]:
        """Las que salieron bien pero no a la primera.

        Se listan aparte porque son la señal de que una base o un enlace está
        inestable: el lote termina en verde y el problema queda invisible.
        """

        return [r for r in self.resultados if r.ok and r.intentos > 1]

    def informe(self) -> str:
        r = self.resumen()
        base = (f"{r['ok']}/{r['tareas']} tarea(s) en {r['segundos']} s con "
                f"{r['trabajadores']} trabajador(es) (limita: {r['limitado_por']})")
        if r["esperas_por_memoria"]:
            base += f" · {r['esperas_por_memoria']} espera(s) por memoria"
        if r["reintentos"]:
            base += (f" · {r['reintentos']} reintento(s) por fallos pasajeros"
                     f" ({len(self.recuperados)} recuperada/s)")
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
        ventana_prioridad: int | None = None,
        reintentos: int = 0,
        espera_reintento_s: float = 2.0,
    ):
        self.presupuesto = presupuesto or calcular_presupuesto()
        self.tipo = tipo
        self.max_en_cola = max_en_cola
        self.reserva_memoria_mb = reserva_memoria_mb
        self.espera_memoria_s = espera_memoria_s
        self.espera_maxima_s = espera_maxima_s
        self.nombre = nombre
        self.reintentos = max(0, reintentos)
        self.espera_reintento_s = espera_reintento_s
        # Cuántas tareas se miran a la vez para elegir la más prioritaria.
        self.ventana_prioridad = (_VENTANA_PRIORIDAD if ventana_prioridad is None
                                  else max(0, ventana_prioridad))

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
                   ventana_prioridad=cfg.ventana_prioridad or None,
                   # Solo la lectura de bases reintenta por defecto: ahí el fallo
                   # típico es la red. En cálculo, un fallo es casi siempre el
                   # dato, y repetirlo gasta el lote dos veces para nada.
                   reintentos=cfg.reintentos_lectura if tipo == "io" else 0,
                   espera_reintento_s=cfg.espera_reintento_s,
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

        Cada tarea es ``(clave, argumentos)`` o ``(clave, argumentos, prioridad)``.
        A mayor prioridad, antes se atiende; sin prioridad, FIFO como siempre.

        ``al_terminar`` se llama en el hilo principal según van llegando
        resultados: sirve para mostrar progreso sin esperar al lote completo, que
        en un recálculo de una unidad de negocio son minutos.
        """

        n = self.presupuesto.trabajadores
        inicio = time.monotonic()
        lote = ResultadoLote(presupuesto=self.presupuesto)
        cola = _ColaAdmision(tareas, self.ventana_prioridad)
        # (cuándo toca, orden, clave, args, intentos ya gastados)
        aplazadas: list[tuple[float, int, str, tuple, int]] = []
        contador = 0

        logger.info("{}: {}", self.nombre, self.presupuesto.explicacion())

        def _asentar(r: ResultadoTarea, args: tuple) -> None:
            """Da el resultado por definitivo, o lo devuelve a la cola."""

            nonlocal contador
            if self._reintentable(r):
                demora = self.espera_reintento_s * (2 ** (r.intentos - 1))
                logger.info("{}: '{}' falló por algo pasajero ({}); reintento "
                            "{} de {} en {:.1f} s", self.nombre, r.clave,
                            r.error, r.intentos, self.reintentos, demora)
                heapq.heappush(aplazadas, (time.monotonic() + demora, contador,
                                           r.clave, args, r.intentos))
                contador += 1
                lote.reintentos += 1
                return
            lote.resultados.append(r)
            if al_terminar:
                al_terminar(r)

        if n <= 1:
            # Sin paralelismo no se paga el coste de arrancar procesos ni de
            # serializar argumentos, que en un lote pequeño domina el tiempo.
            while True:
                ahora = time.monotonic()
                if aplazadas and aplazadas[0][0] <= ahora:
                    _, _, clave, args, gastados = heapq.heappop(aplazadas)
                else:
                    try:
                        clave, args = cola.siguiente()
                    except StopIteration:
                        if not aplazadas:
                            break
                        # Solo quedan reintentos y aún no toca ninguno.
                        time.sleep(max(0.0, min(aplazadas[0][0] - ahora, 0.5)))
                        continue
                    gastados = 0
                # La espera se mide igual que en la ruta paralela: en secuencial
                # la tarea número 40 espera a las 39 anteriores, y esa espera es
                # justamente el argumento para pedir más máquina.
                espera = time.monotonic() - inicio
                r = _ejecutar_una(funcion, clave, args, gastados + 1)
                r.espera_s = round(espera, 3)
                _asentar(r, args)
            lote.segundos = time.monotonic() - inicio
            return lote

        fabrica = ProcessPoolExecutor if self.tipo == "cpu" else ThreadPoolExecutor
        # Los procesos hijos no deben abrir a su vez pools de BLAS: N procesos ×
        # N hilos de OpenBLAS es sobresuscripción, y el equipo se pasa el tiempo
        # cambiando de contexto en vez de calculando.
        entorno = _entorno_hijo() if self.tipo == "cpu" else None

        with fabrica(max_workers=n, **(entorno or {})) as pool:
            vivos: dict[Future, tuple[str, tuple, float]] = {}

            def _lanzar() -> bool:
                """Toma la siguiente —reintento vencido o tarea nueva— si cabe."""

                ahora = time.monotonic()
                if aplazadas and aplazadas[0][0] <= ahora:
                    # Los reintentos vencidos se adelantan: esa lectura ya tiene
                    # a alguien esperándola desde hace rato.
                    _, _, clave, args, gastados = heapq.heappop(aplazadas)
                else:
                    try:
                        clave, args = cola.siguiente()
                    except StopIteration:
                        return False
                    gastados = 0
                self._esperar_memoria(lote, clave)
                fut = pool.submit(_ejecutar_una, funcion, clave, args,
                                  gastados + 1)
                # Cuánto esperó esta tarea desde que arrancó el lote hasta que
                # hubo un hueco libre: es la métrica que dice si el equipo se
                # quedó corto, y la que justifica pedir más máquina.
                vivos[fut] = (clave, args, time.monotonic() - inicio)
                return True

            while len(vivos) < n and _lanzar():
                pass

            while vivos or aplazadas:
                if not vivos:
                    # Nada en vuelo y solo quedan reintentos por vencer.
                    time.sleep(max(0.0, min(aplazadas[0][0] - time.monotonic(),
                                            0.5)))
                    _lanzar()
                    continue

                hechos, _ = wait(list(vivos), return_when=FIRST_COMPLETED)
                for fut in hechos:
                    clave, args, espera = vivos.pop(fut)
                    try:
                        r = fut.result()
                    except Exception as exc:                      # noqa: BLE001
                        # Un proceso muerto (por memoria, por ejemplo) no entrega
                        # ResultadoTarea: llega como excepción del futuro. No se
                        # reintenta: si el pool se rompió, reenviar solo produce
                        # el mismo error tantas veces como tareas queden.
                        r = ResultadoTarea(clave=clave, ok=False,
                                           error=f"{type(exc).__name__}: {exc}")
                    r.espera_s = round(max(0.0, espera), 3)
                    _asentar(r, args)
                while len(vivos) < n and _lanzar():
                    pass

        lote.segundos = time.monotonic() - inicio
        return lote

    def _reintentable(self, r: ResultadoTarea) -> bool:
        """Solo lo pasajero, y solo mientras queden intentos."""

        return (r.fallo and r.transitorio and self.reintentos > 0
                and r.intentos <= self.reintentos)

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


def _ejecutar_una(funcion: Callable[..., Any], clave: str, args: tuple,
                  intento: int = 1) -> ResultadoTarea:
    """Envoltorio que corre en el trabajador y **nunca** propaga la excepción.

    Que un alimentador con datos corruptos cancele el lote entero significaría
    perder el cálculo de los otros 499. El error viaja como dato.

    La excepción se clasifica **aquí**, donde todavía es un objeto: al padre solo
    llega el texto, y decidir por el texto si merece reintento sería adivinar.
    """

    t0 = time.monotonic()
    try:
        valor = funcion(*args)
        return ResultadoTarea(clave=clave, ok=True, valor=valor, intentos=intento,
                              segundos=round(time.monotonic() - t0, 3))
    except Exception as exc:                                      # noqa: BLE001
        return ResultadoTarea(
            clave=clave, ok=False, error=f"{type(exc).__name__}: {exc}",
            intentos=intento, transitorio=es_transitorio(exc),
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
