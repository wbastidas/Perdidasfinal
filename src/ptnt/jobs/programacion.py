"""Tareas programadas: que los números se actualicen solos.

**Aquí no hay un servicio que se quede corriendo.** La programación se delega al
planificador del sistema operativo —el Programador de tareas de Windows, o cron
en Linux— y este módulo se limita a guardar qué hay que hacer y a generar la
orden que hay que registrar allí.

Es a propósito. Un demonio propio significaría vigilar que siga vivo, arrancarlo
con la máquina, y explicar por qué un martes no se ejecutó. El Programador de
Windows ya hace eso, lleva veinte años haciéndolo, sobrevive a los reinicios y
tiene su propio registro de errores. Escribir uno peor no le sirve a nadie.

Lo que sí aporta este módulo:

- **Guardar la definición** en un sitio legible y editable.
- **Generar la orden exacta** que hay que pegar en el Programador, para que nadie
  tenga que inventarse rutas ni comillas.
- **Saber si una tarea se saltó.** El planificador sabe si lanzó el proceso; no
  sabe si el proceso hizo lo que debía. Eso lo dice la bitácora, y aquí se cruza:
  una tarea que debía correr anoche y no dejó rastro **se reporta**.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

FRECUENCIAS = ("DIARIA", "SEMANAL", "MENSUAL")
DIAS = ("LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM")
_CRON_DIA = {"LUN": 1, "MAR": 2, "MIE": 3, "JUE": 4, "VIE": 5, "SAB": 6, "DOM": 0}
_SCHTASKS_DIA = {"LUN": "MON", "MAR": "TUE", "MIE": "WED", "JUE": "THU",
                 "VIE": "FRI", "SAB": "SAT", "DOM": "SUN"}


class ProgramacionError(Exception):
    """Definición de tarea que no se puede cumplir."""


@dataclass
class Programacion:
    """Una tarea que debe repetirse sola."""

    nombre: str
    plan: list[str] = field(default_factory=list)
    frecuencia: str = "DIARIA"
    hora: str = "03:00"
    dia_semana: str = "LUN"          # solo si es SEMANAL
    dia_mes: int = 1                 # solo si es MENSUAL
    activa: bool = True
    opciones: dict = field(default_factory=dict)
    descripcion: str = ""
    creada: str = ""
    creada_por: str = ""

    def __post_init__(self):
        if self.frecuencia not in FRECUENCIAS:
            raise ProgramacionError(
                f"Frecuencia '{self.frecuencia}' desconocida. Use una de: "
                f"{', '.join(FRECUENCIAS)}.")
        if self.frecuencia == "SEMANAL" and self.dia_semana not in DIAS:
            raise ProgramacionError(
                f"Día '{self.dia_semana}' desconocido. Use uno de: "
                f"{', '.join(DIAS)}.")
        if self.frecuencia == "MENSUAL" and not 1 <= int(self.dia_mes) <= 28:
            # Se corta en 28 a propósito: el 30 no existe en febrero y la tarea
            # simplemente no se ejecutaría ese mes, en silencio.
            raise ProgramacionError(
                "El día del mes debe estar entre 1 y 28. Del 29 al 31 hay meses "
                "que no lo tienen y la tarea se saltaría sin avisar.")
        try:
            h, m = self.hora.split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise ProgramacionError(
                f"La hora '{self.hora}' no es válida. Use el formato HH:MM, "
                "por ejemplo 03:00.")
        if not self.plan:
            raise ProgramacionError(
                "La tarea no hace nada: indique al menos un paso.")
        if not self.creada:
            self.creada = datetime.now().isoformat(timespec="seconds")

    # -- Cuándo toca --------------------------------------------------------- #
    def cuando(self) -> str:
        """En castellano, para que se lea en una pantalla."""

        if self.frecuencia == "DIARIA":
            return f"todos los días a las {self.hora}"
        if self.frecuencia == "SEMANAL":
            return f"cada {self.dia_semana.lower()} a las {self.hora}"
        return f"el día {self.dia_mes} de cada mes a las {self.hora}"

    def proxima(self, desde: datetime | None = None) -> datetime:
        """La próxima vez que debería ejecutarse."""

        ahora = desde or datetime.now()
        h, m = (int(x) for x in self.hora.split(":"))
        cand = ahora.replace(hour=h, minute=m, second=0, microsecond=0)

        if self.frecuencia == "DIARIA":
            return cand if cand > ahora else cand + timedelta(days=1)

        if self.frecuencia == "SEMANAL":
            objetivo = DIAS.index(self.dia_semana)          # lunes = 0
            delta = (objetivo - cand.weekday()) % 7
            cand = cand + timedelta(days=delta)
            return cand if cand > ahora else cand + timedelta(days=7)

        dia = int(self.dia_mes)
        cand = cand.replace(day=dia)
        if cand > ahora:
            return cand
        mes, anio = (1, cand.year + 1) if cand.month == 12 else (cand.month + 1, cand.year)
        return cand.replace(year=anio, month=mes, day=dia)

    def vencida(self, ultima_corrida: datetime | None,
                ahora: datetime | None = None,
                tolerancia_horas: int = 6) -> bool:
        """¿Debía haber corrido y no hay rastro de ello?

        La tolerancia evita gritar por un retraso de minutos: un servidor
        ocupado que arranca la tarea a las 03:07 no tiene ningún problema.
        """

        ahora = ahora or datetime.now()
        # La ocasión anterior a la próxima es la que ya debería haber ocurrido.
        anterior = self.proxima(ahora) - self._periodo()

        # Una tarea no puede haberse saltado una cita anterior a su propia
        # creación. Sin esto, cualquier tarea nueva nace marcada en rojo y la
        # señal deja de significar nada.
        creada = self._creada_dt()
        if creada and anterior < creada:
            return False

        if ahora < anterior + timedelta(hours=tolerancia_horas):
            return False
        return ultima_corrida is None or ultima_corrida < anterior

    def _creada_dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.creada) if self.creada else None
        except ValueError:
            return None

    def _periodo(self) -> timedelta:
        return {"DIARIA": timedelta(days=1),
                "SEMANAL": timedelta(days=7)}.get(self.frecuencia,
                                                  timedelta(days=30))

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Almacén
# --------------------------------------------------------------------------- #
class AlmacenProgramaciones:
    """Las tareas, en un JSON que se puede abrir y leer sin herramientas."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self._tareas: dict[str, Programacion] = {}
        if self.ruta.exists():
            self._cargar()

    def _cargar(self) -> None:
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for d in datos.get("tareas", []):
            try:
                p = Programacion(**{k: v for k, v in d.items()
                                    if k in Programacion.__dataclass_fields__})
            except ProgramacionError:
                # Una tarea corrupta no puede impedir leer las demás.
                continue
            self._tareas[p.nombre] = p

    def _guardar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"tareas": [t.to_dict() for t in self._tareas.values()]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def agregar(self, tarea: Programacion, *, reemplazar: bool = False) -> None:
        if tarea.nombre in self._tareas and not reemplazar:
            raise ProgramacionError(
                f"Ya existe una tarea llamada '{tarea.nombre}'. Use otro nombre "
                "o pida reemplazarla.")
        self._tareas[tarea.nombre] = tarea
        self._guardar()

    def quitar(self, nombre: str) -> bool:
        if nombre not in self._tareas:
            return False
        del self._tareas[nombre]
        self._guardar()
        return True

    def activar(self, nombre: str, activa: bool) -> None:
        if nombre not in self._tareas:
            raise ProgramacionError(f"No existe la tarea '{nombre}'.")
        self._tareas[nombre].activa = activa
        self._guardar()

    def obtener(self, nombre: str) -> Programacion | None:
        return self._tareas.get(nombre)

    def listar(self) -> list[Programacion]:
        return sorted(self._tareas.values(), key=lambda t: t.nombre)


# --------------------------------------------------------------------------- #
# Registrar en el planificador del sistema
# --------------------------------------------------------------------------- #
def _ejecutable() -> str:
    return sys.executable or "python"


def orden_ptnt(tarea: Programacion, ruta_config: str) -> list[str]:
    """La orden que el planificador tiene que lanzar.

    La configuración va en **ruta absoluta**: el planificador no arranca la tarea
    desde la carpeta del proyecto, y una ruta relativa haría que no encontrara el
    archivo a las tres de la mañana, sin nadie delante.
    """

    return [_ejecutable(), "-m", "ptnt.cli", "tarea-ejecutar", tarea.nombre,
            "--config", str(Path(ruta_config).resolve())]


def comando_windows(tarea: Programacion, ruta_config: str, *,
                    carpeta_trabajo: str | None = None,
                    usuario: str | None = None) -> str:
    """La línea de ``schtasks`` para registrarla en el Programador de Windows.

    Se devuelve como texto para que quien la ejecute **la vea antes**: registrar
    una tarea que corre sola en un servidor de producción no es algo que deba
    pasar sin que nadie lo lea.

    Va envuelta en ``cmd /c cd /d`` porque ``schtasks`` no tiene forma de fijar
    la carpeta de trabajo, y las rutas de salida de la configuración son
    relativas a ella: sin el cambio de carpeta, los resultados aparecerían en
    C:\\Windows\\System32.
    """

    orden = orden_ptnt(tarea, ruta_config)
    carpeta = str(Path(carpeta_trabajo).resolve()) if carpeta_trabajo else None
    # schtasks quiere la orden entera entrecomillada, y las rutas de Windows
    # llevan espacios con toda naturalidad: las internas se escapan.
    interna = " ".join(f'\\"{a}\\"' if " " in str(a) else str(a) for a in orden)
    if carpeta:
        interna = f'cmd /c cd /d \\"{carpeta}\\" && {interna}'

    partes = ["schtasks", "/Create", "/TN", f'"PTNT-BAL\\{tarea.nombre}"',
              "/TR", f'"{interna}"']
    if tarea.frecuencia == "DIARIA":
        partes += ["/SC", "DAILY"]
    elif tarea.frecuencia == "SEMANAL":
        partes += ["/SC", "WEEKLY", "/D", _SCHTASKS_DIA[tarea.dia_semana]]
    else:
        partes += ["/SC", "MONTHLY", "/D", str(tarea.dia_mes)]
    partes += ["/ST", tarea.hora, "/RL", "HIGHEST", "/F"]
    if usuario:
        partes += ["/RU", f'"{usuario}"']
    return " ".join(partes)


def linea_cron(tarea: Programacion, ruta_config: str, *,
               carpeta_trabajo: str | None = None) -> str:
    """La línea de crontab equivalente, para instalaciones sobre Linux."""

    h, m = (int(x) for x in tarea.hora.split(":"))
    if tarea.frecuencia == "DIARIA":
        cuando = f"{m} {h} * * *"
    elif tarea.frecuencia == "SEMANAL":
        cuando = f"{m} {h} * * {_CRON_DIA[tarea.dia_semana]}"
    else:
        cuando = f"{m} {h} {tarea.dia_mes} * *"
    orden = " ".join(shlex.quote(str(a)) for a in orden_ptnt(tarea, ruta_config))
    if carpeta_trabajo:
        orden = f"cd {shlex.quote(str(Path(carpeta_trabajo).resolve()))} && {orden}"
    return f"{cuando} {orden}"


def instrucciones(tarea: Programacion, ruta_config: str, *,
                  carpeta_trabajo: str | None = None) -> str:
    """El paso a paso para dejarla registrada, para quien nunca lo ha hecho."""

    win = comando_windows(tarea, ruta_config, carpeta_trabajo=carpeta_trabajo)
    return f"""Para que «{tarea.nombre}» se ejecute sola ({tarea.cuando()}):

WINDOWS SERVER
 1. Pulse Inicio, escriba «cmd», haga clic derecho en «Símbolo del sistema» y
    elija «Ejecutar como administrador».
 2. Copie y pegue esta línea completa, y pulse Intro:

    {win}

 3. Debe responder «CORRECTO: se ha creado la tarea programada». Si pide una
    contraseña, es la del usuario con el que se ejecutará.
 4. Para comprobarlo: abra «Programador de tareas», carpeta PTNT-BAL.

LINUX
 1. Ejecute: crontab -e
 2. Añada esta línea al final:

    {linea_cron(tarea, ruta_config, carpeta_trabajo=carpeta_trabajo)}

COMPROBAR QUE FUNCIONA, SIN ESPERAR
    Ejecute la tarea a mano una vez y mire el resultado:

    {' '.join(orden_ptnt(tarea, ruta_config))}
"""
