"""Ejecutar el proceso y dejar constancia de lo que pasó.

Un mismo camino para las tres formas de lanzar el trabajo: el botón del tablero,
la tarea programada de la madrugada y la consola. Se invoca la CLI real como
proceso aparte, y eso trae tres cosas que importan:

- **No hay dos implementaciones que se separen con el tiempo.** Lo que calcula el
  tablero es lo que calcula la tarea nocturna, porque es el mismo comando.
- **Un paso que revienta no se lleva por delante al proceso que lo lanzó.** El
  tablero sigue en pie y muestra el error.
- **La salida ya está escrita para que la lea una persona**, porque es la misma
  que ve quien ejecuta a mano.

La bitácora se escribe **mientras** corre, no al final: si el proceso se corta a
la mitad —se reinicia el servidor, alguien cierra la sesión— tiene que quedar
constancia de hasta dónde se llegó. Una bitácora que solo se escribe al terminar
es la que falta justo el día que hace falta.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from ptnt.jobs.pasos import Paso, resolver_plan

ESTADOS = ("PENDIENTE", "EN_CURSO", "CORRECTO", "FALLO", "OMITIDO")


@dataclass
class ResultadoPaso:
    clave: str
    titulo: str
    estado: str = "PENDIENTE"
    inicio: str = ""
    fin: str = ""
    segundos: float = 0.0
    codigo: int | None = None
    salida: str = ""
    resumen: str = ""

    @property
    def ok(self) -> bool:
        return self.estado == "CORRECTO"


@dataclass
class Ejecucion:
    """Una corrida del proceso, con todo lo necesario para explicarla después."""

    ejecucion_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    plan: str = ""
    origen: str = "MANUAL"          # MANUAL | PROGRAMADA
    usuario: str = ""
    inicio: str = ""
    fin: str = ""
    estado: str = "EN_CURSO"
    pasos: list[ResultadoPaso] = field(default_factory=list)

    @property
    def correcto(self) -> bool:
        return self.estado == "CORRECTO"

    def lectura(self) -> str:
        """Una frase que se pueda pegar en un correo."""

        hechos = sum(1 for p in self.pasos if p.ok)
        fallidos = [p for p in self.pasos if p.estado == "FALLO"]
        if self.estado == "EN_CURSO":
            actual = next((p.titulo for p in self.pasos if p.estado == "EN_CURSO"),
                          "arrancando")
            return f"En curso: {actual} ({hechos} de {len(self.pasos)} listos)."
        if not fallidos:
            return (f"Correcto: {hechos} de {len(self.pasos)} pasos, "
                    f"{self.minutos():.0f} min.")
        return (f"Falló en «{fallidos[0].titulo}». Se completaron {hechos} de "
                f"{len(self.pasos)} pasos. Los siguientes no se ejecutaron para "
                "no calcular sobre datos a medias.")

    def minutos(self) -> float:
        return sum(p.segundos for p in self.pasos) / 60.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lectura"] = self.lectura()
        return d


class Bitacora:
    """Las corridas, en disco, para que el tablero las lea y el jefe las audite."""

    def __init__(self, directorio: str | Path):
        self.dir = Path(directorio)
        self.dir.mkdir(parents=True, exist_ok=True)

    def ruta(self, ejecucion_id: str) -> Path:
        return self.dir / f"{ejecucion_id}.json"

    def guardar(self, ej: Ejecucion) -> None:
        # Escritura atómica: si el servidor se apaga a media escritura, un JSON
        # truncado dejaría la bitácora ilegible justo cuando hay que explicar qué
        # pasó.
        tmp = self.ruta(ej.ejecucion_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(ej.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.ruta(ej.ejecucion_id))

    def leer(self, ejecucion_id: str) -> dict | None:
        r = self.ruta(ejecucion_id)
        if not r.exists():
            return None
        try:
            return json.loads(r.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def ultimas(self, n: int = 20) -> list[dict]:
        archivos = sorted(self.dir.glob("*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        salida = []
        for a in archivos[:n]:
            try:
                salida.append(json.loads(a.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return salida

    def en_curso(self) -> dict | None:
        for e in self.ultimas(5):
            if e.get("estado") == "EN_CURSO":
                return e
        return None


# --------------------------------------------------------------------------- #
# La ejecución
# --------------------------------------------------------------------------- #
def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


_RUIDO = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[.,]\d+\s*\|"   # traza de loguru
    r"|^\s*[│┃┏┡└┗┌├╭╰─━┈╌]"                              # bordes de tabla
    r"|^\s*(Traceback|File \"|\s+at )"                     # traza de excepción
)


def _resumir(texto: str, limite: int = 400) -> str:
    """La última parte de la salida, que es donde está el resultado o el error.

    Se descartan las trazas de registro y los bordes de las tablas: quien lee
    esto en el tablero quiere saber cómo acabó el paso, no ver el diario técnico
    de lo que hizo por dentro. La salida completa queda igualmente guardada.
    """

    limpio = [l.rstrip() for l in texto.splitlines()
              if l.strip() and not _RUIDO.match(l)]
    if not limpio:
        # Un paso puede no imprimir nada salvo registro; mejor decirlo que
        # devolver una cadena vacía que se lee como «no pasó nada».
        return "Terminó sin mensajes en pantalla."
    return " · ".join(limpio[-3:])[:limite]


def ejecutar_plan(claves: list[str], ruta_config: str, *,
                  opciones: dict | None = None,
                  bitacora: Bitacora | None = None,
                  origen: str = "MANUAL",
                  usuario: str = "",
                  nombre_plan: str = "",
                  ejecucion_id: str = "",
                  al_avanzar=None,
                  tiempo_maximo_min: int = 120) -> Ejecucion:
    """Corre los pasos en orden y devuelve lo que pasó en cada uno.

    ``al_avanzar(ejecucion)`` se llama después de cada cambio de estado para que
    la interfaz pueda refrescarse sin esperar al final.
    """

    pasos = resolver_plan(list(claves))
    opciones = opciones or {}

    ej = Ejecucion(plan=nombre_plan or ",".join(claves), origen=origen,
                   usuario=usuario, inicio=_ahora())
    if ejecucion_id:
        # Quien lanza en segundo plano necesita saber dónde mirar antes de que
        # el proceso arranque; si el identificador se generara aquí, la pantalla
        # no tendría forma de seguirlo.
        ej.ejecucion_id = ejecucion_id
    ej.pasos = [ResultadoPaso(clave=p.clave, titulo=p.titulo) for p in pasos]

    def latir():
        if bitacora:
            bitacora.guardar(ej)
        if al_avanzar:
            try:
                al_avanzar(ej)
            except Exception:                                   # noqa: BLE001
                # Que falle el refresco de una pantalla no puede tumbar el
                # cálculo que lleva veinte minutos corriendo.
                logger.warning("el aviso de avance falló; el proceso sigue")

    latir()

    for paso, res in zip(pasos, ej.pasos):
        res.estado = "EN_CURSO"
        res.inicio = _ahora()
        latir()

        try:
            args = paso.argumentos(opciones.get(paso.clave, {}))
        except ValueError as exc:
            res.estado = "FALLO"
            res.fin = _ahora()
            res.resumen = str(exc)
            res.salida = str(exc)
            break

        comando = [sys.executable, "-m", "ptnt.cli", *args,
                   "--config", str(ruta_config)]
        arranque = datetime.now()
        try:
            proc = subprocess.run(
                comando, capture_output=True, text=True,
                timeout=tiempo_maximo_min * 60,
                env={**os.environ, "COLUMNS": "140"},
            )
            res.codigo = proc.returncode
            res.salida = (proc.stdout or "") + (proc.stderr or "")
            res.estado = "CORRECTO" if proc.returncode == 0 else "FALLO"
        except subprocess.TimeoutExpired:
            res.codigo = -1
            res.estado = "FALLO"
            res.salida = (f"El paso pasó de {tiempo_maximo_min} minutos y se "
                          "detuvo. Suele ser una base que no responde.")
        except Exception as exc:                                # noqa: BLE001
            res.codigo = -1
            res.estado = "FALLO"
            res.salida = f"No se pudo lanzar el paso: {type(exc).__name__}: {exc}"

        res.segundos = round((datetime.now() - arranque).total_seconds(), 1)
        res.fin = _ahora()
        res.resumen = _resumir(res.salida)
        latir()

        if res.estado == "FALLO":
            # Se para. Seguir calcularía el balance sobre datos que no se
            # llegaron a traer, y el número saldría con toda naturalidad.
            logger.error("paso «{}» falló; no se ejecutan los siguientes",
                         paso.titulo)
            break

    for res in ej.pasos:
        if res.estado == "PENDIENTE":
            res.estado = "OMITIDO"
            res.resumen = "No se ejecutó porque un paso anterior falló."

    ej.fin = _ahora()
    ej.estado = "CORRECTO" if all(p.ok for p in ej.pasos) else "FALLO"
    latir()
    return ej


def lanzar_en_segundo_plano(claves: list[str], ruta_config: str, *,
                            usuario: str = "", nombre_plan: str = "",
                            csv: str = "", feeder: str = "",
                            cabecera: str = "", multados: str = "") -> str:
    """Arranca el proceso aparte y devuelve el identificador para seguirlo.

    El cálculo de un mes entero puede tardar media hora. Ejecutarlo dentro de la
    página web ataría el resultado a que nadie cierre la pestaña ni pierda la
    conexión; así, la pantalla solo consulta la bitácora y el trabajo sigue su
    curso aunque quien lo lanzó se vaya a comer.
    """

    ident = uuid.uuid4().hex
    comando = [sys.executable, "-m", "ptnt.cli", "ejecutar",
               "--config", str(ruta_config), "--id", ident]
    for clave in claves:
        comando += ["--paso", clave]
    for bandera, valor in (("--csv", csv), ("--feeder", feeder),
                           ("--cabecera", cabecera), ("--multados", multados),
                           ("--usuario", usuario), ("--nombre", nombre_plan)):
        if valor:
            comando += [bandera, str(valor)]

    creacion = {}
    if os.name == "nt":                                     # pragma: no cover
        # Sin esto, cerrar la consola desde la que arrancó el tablero se llevaría
        # por delante el cálculo en curso.
        creacion["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        creacion["start_new_session"] = True

    subprocess.Popen(comando, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **creacion)
    logger.info("proceso lanzado en segundo plano: {}", ident)
    return ident
