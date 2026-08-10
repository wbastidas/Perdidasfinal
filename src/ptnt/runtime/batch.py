"""Lotes de trabajo real: varios alimentadores, varias bases, varios paquetes.

Aquí se conectan el presupuesto de recursos y el ejecutor con las tres cosas que
en la operación llegan de a muchas:

* **Alimentadores.** Una unidad de negocio tiene cientos. Es cálculo puro, así
  que van en procesos.
* **Bases de origen.** Once unidades de negocio son once conexiones que pasan la
  mayor parte del tiempo esperando a la red. Van en hilos, y con un tope **por
  base**: pasarse de sesiones no acelera, hace que el DBA corte el acceso.
* **Paquetes de campo.** Armar el `.gpkg` de cada cuadrilla es CPU y disco.

Un detalle que decide si esto funciona o no: **al trabajador no se le manda el
modelo, se le manda dónde encontrarlo**. Serializar una red de 200 000 nodos para
enviarla a un proceso hijo cuesta más que calcularla, y además duplica la memoria
justo cuando se está intentando ahorrarla. Cada proceso abre lo suyo.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ptnt.runtime.pool import EjecutorTareas, ResultadoLote, ResultadoTarea


@dataclass
class TareaAlimentador:
    """Lo que necesita un trabajador para procesar un alimentador **por su cuenta**.

    Se le pasa la **ruta del YAML**, no la configuración cargada, y el código del
    alimentador, no la red. El trabajador migra su alimentador desde la fuente
    configurada y calcula. Enviar el modelo ya construido costaría más
    serializarlo que calcularlo, y duplicaría en el padre la memoria que se
    intenta repartir.
    """

    feeder_code: str
    ruta_config: str
    head_energy_kwh: float | None = None
    trifasico: bool = True
    opciones: dict[str, Any] = field(default_factory=dict)
    # A mayor número, antes se atiende. Sirve para que el alimentador con una
    # campaña de campo en curso, o el de mayor PNT, salga primero: los resultados
    # se van entregando según terminan y hay alguien esperando por esos, no por
    # los 400 rutinarios.
    prioridad: int = 0

    def argumentos(self) -> tuple:
        return (self.feeder_code, self.ruta_config, self.head_energy_kwh,
                self.trifasico, self.opciones)


def analizar_alimentadores(
    tareas: Iterable[TareaAlimentador],
    cfg,
    *,
    funcion: Callable[..., Any] | None = None,
    al_terminar: Callable[[ResultadoTarea], None] | None = None,
) -> ResultadoLote:
    """Procesa alimentadores en paralelo, hasta donde el equipo aguante.

    ``funcion`` recibe los argumentos de :meth:`TareaAlimentador.argumentos` y
    debe estar definida **al nivel de un módulo importable**: es como el pool la
    envía al proceso hijo. Por defecto se usa :func:`_analizar_uno`.
    """

    ejecutor = EjecutorTareas.desde_config(cfg.recursos, tipo="cpu",
                                           nombre="alimentadores")
    trabajo = ((t.feeder_code, t.argumentos(), t.prioridad) for t in tareas)
    return ejecutor.ejecutar(funcion or _analizar_uno, trabajo,
                             al_terminar=al_terminar)


def _analizar_uno(feeder_code: str, ruta_config: str,
                  head_energy_kwh: float | None, trifasico: bool,
                  opciones: dict) -> dict:
    """Se ejecuta **en el proceso hijo**: abre lo suyo y devuelve solo números.

    Devuelve un diccionario y no el objeto de resultado completo a propósito: lo
    que vuelve por la tubería del proceso hay que serializarlo, y devolver el
    modelo entero de cada alimentador reconstruiría en el padre la memoria que se
    intentaba repartir.
    """

    from ptnt.config.loader import load_config
    from ptnt.grid_pipeline import run_grid_analysis
    from ptnt.io.migration import migrate_network

    cfg = load_config(ruta_config)
    red = migrate_network(cfg, feeder_code=feeder_code)
    r = run_grid_analysis(red, cfg, head_energy_kwh=head_energy_kwh,
                          trifasico=trifasico, **opciones)
    b = r.balance
    return {
        "feeder_code": feeder_code,
        "engine": r.engine,
        "converge": r.powerflow_converged,
        "v_min_pu": r.v_min_pu,
        "e_input_kwh": b.e_input_kwh,
        "e_billed_kwh": b.e_billed_kwh,
        "loss_technical_kwh": b.loss_technical_kwh,
        "ntl_kwh": b.ntl_kwh,
        "ntl_pct": b.ntl_pct,
        "balance_type": b.balance_type.value,
    }


# --------------------------------------------------------------------------- #
# Lectura de varias bases de origen
# --------------------------------------------------------------------------- #
@dataclass
class TareaLectura:
    """Una lectura de una base de origen."""

    clave: str                       # p. ej. "CNEL-GYE/padron"
    fuente: str                      # nombre de la fuente en el YAML
    consulta: str = ""
    ruta_config: str = ""
    opciones: dict[str, Any] = field(default_factory=dict)
    prioridad: int = 0               # a mayor número, antes se lee


class LimitadorPorFuente:
    """Tope de conexiones simultáneas **por base**, no solo en total.

    Once unidades de negocio pueden apuntar a la misma instancia. Un límite
    global de seis lecturas permitiría seis contra el mismo Oracle, que es como
    se agotan las sesiones y el DBA corta el acceso a media mañana.
    """

    def __init__(self, max_por_fuente: int = 2):
        self.max_por_fuente = max(1, max_por_fuente)
        self._semaforos: dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()

    def semaforo(self, fuente: str) -> threading.Semaphore:
        with self._lock:
            if fuente not in self._semaforos:
                self._semaforos[fuente] = threading.Semaphore(self.max_por_fuente)
            return self._semaforos[fuente]


_LIMITADOR: LimitadorPorFuente | None = None


def leer_fuentes(
    tareas: Iterable[TareaLectura],
    cfg,
    *,
    funcion: Callable[..., Any] | None = None,
    al_terminar: Callable[[ResultadoTarea], None] | None = None,
) -> ResultadoLote:
    """Lee de varias bases a la vez: hilos, porque el tiempo se va esperando.

    Con once unidades de negocio, leer en serie es once veces la latencia de red
    sumada. En paralelo, el tiempo total es el de la base más lenta.
    """

    global _LIMITADOR
    _LIMITADOR = LimitadorPorFuente(cfg.recursos.max_por_fuente)

    ejecutor = EjecutorTareas.desde_config(cfg.recursos, tipo="io",
                                           nombre="lecturas")
    trabajo = ((t.clave, (t.clave, t.fuente, t.consulta,
                          t.ruta_config or "", t.opciones), t.prioridad)
               for t in tareas)
    lote = ejecutor.ejecutar(funcion or _leer_una, trabajo,
                             al_terminar=al_terminar)
    if lote.recuperados:
        # El lote termina en verde y el enlace inestable quedaría invisible. Es
        # la primera pista de que una unidad de negocio va a empezar a fallar.
        logger.warning(
            "lecturas: {} fuente(s) solo respondieron tras reintentar ({}). "
            "Revise el enlace antes de que deje de responder del todo.",
            len(lote.recuperados),
            ", ".join(r.clave for r in lote.recuperados[:5]))
    return lote


def _leer_una(clave: str, fuente: str, consulta: str, ruta_config: str,
              opciones: dict) -> dict:
    """Lectura de una fuente, respetando el tope de sesiones **de esa base**."""

    from ptnt.config.loader import load_config
    from ptnt.io.sources.factory import build_connector

    limitador = _LIMITADOR or LimitadorPorFuente()
    # El semáforo se toma alrededor de la conexión entera, no solo de la
    # consulta: lo que agota una base son las sesiones abiertas, no las
    # sentencias en vuelo.
    with limitador.semaforo(fuente):
        cfg = load_config(ruta_config)
        conector = build_connector(cfg.fuente(fuente))
        tabla = opciones.get("tabla")
        if consulta:
            df = conector.read_query(consulta, opciones.get("params"))
        elif tabla:
            df = conector.read_table(tabla, columnas=opciones.get("columnas"),
                                     limite=opciones.get("limite"))
        else:
            raise ValueError(
                f"'{clave}': indique 'consulta' o la opción 'tabla'.")
        return {"clave": clave, "fuente": fuente, "filas": len(df),
                "columnas": list(df.columns)[:20], "datos": df}


# --------------------------------------------------------------------------- #
# Paquetes de campo en lote
# --------------------------------------------------------------------------- #
def construir_paquetes_en_paralelo(
    directorio,
    *,
    registro,
    red: dict,
    cfg,
    usuarios: list[str] | None = None,
    **kwargs,
) -> dict:
    """Arma el paquete de cada técnico en paralelo, dentro del presupuesto.

    Se usan **hilos** y no procesos: la red ya está cargada en memoria y enviarla
    a un proceso hijo por cada técnico la duplicaría tantas veces como cuadrillas
    haya — exactamente lo contrario de lo que se busca. La escritura del
    GeoPackage es disco, que los hilos aprovechan bien.
    """

    from pathlib import Path

    from ptnt.field.package import construir_paquete
    from ptnt.field.workorders import EstadoOrden

    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)

    if usuarios is None:
        usuarios = sorted({
            a.asignado_a for a in registro.asignaciones.values()
            if a.estado in (EstadoOrden.ASIGNADA, EstadoOrden.DESCARGADA,
                            EstadoOrden.EN_PROCESO)
        })

    pendientes = {
        "estados": {EstadoOrden.ASIGNADA, EstadoOrden.DESCARGADA,
                    EstadoOrden.EN_PROCESO},
    }
    trabajo = []
    resultados: dict[str, Any] = {}
    for usuario in usuarios:
        asigs = registro.de_usuario(usuario, **pendientes)
        if not asigs:
            resultados[usuario] = "Sin órdenes pendientes: no se generó paquete."
            continue
        trabajo.append((usuario, (directorio / f"{usuario}.gpkg", usuario,
                                  asigs, red, kwargs)))

    if not trabajo:
        return resultados

    ejecutor = EjecutorTareas.desde_config(
        cfg.recursos, tipo="io", nombre="paquetes",
        coste_mb=max(64, cfg.recursos.coste_mb_por_tarea // 4))
    logger.info("paquetes: {}", ejecutor.presupuesto.explicacion())

    # Un cierre vale porque estos van en hilos: `ProcessPoolExecutor` exigiría
    # una función importable por nombre, pero aquí no hay procesos hijos.
    def _armar(destino, usuario, asignaciones, red_local, extra):
        return construir_paquete(destino, usuario=usuario,
                                 asignaciones=asignaciones, red=red_local,
                                 **extra)

    lote = ejecutor.ejecutar(_armar, trabajo)
    for r in lote.resultados:
        resultados[r.clave] = r.valor if r.ok else f"Error: {r.error}"
    return resultados
