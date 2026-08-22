"""Escenarios de trabajo: acumular cambios y ver el balance **antes** de publicar.

El caso real: un analista corrige la configuración de tres bancos y reconecta
un cliente en un alimentador, y quiere saber **en ese momento** cómo queda el
balance. Hoy tendría que aplicar los cambios al modelo oficial y recalcular —es
decir, publicar algo que todavía no sabe si está bien— o no probarlo.

Un escenario es una **caja aparte**:

* Se abre sobre un alcance que el usuario define: un alimentador o una
  subestación.
* Acumula cambios sin tocar el modelo oficial. Pueden venir a mano, del campo o
  de una carga.
* Se **evalúa cuando el usuario quiere**, y cada evaluación es una *iteración*
  que queda guardada con sus números.
* Las iteraciones **no se borran nunca**: son la evolución del alimentador en el
  tiempo, que es lo que permite responder «¿esto está mejorando?».

**Cuatro decisiones que sostienen el módulo.**

1. **Evaluar no publica.** Es la razón de existir: probar sin comprometer. Solo
   `aplicar()` toca el modelo oficial, y pasa por la revisión de siempre.

2. **El escenario pertenece a una unidad de negocio, no solo a un usuario.** Un
   compañero de la misma unidad debe poder ver el trabajo —las vacaciones
   existen—, pero nadie de otra unidad, y la matriz sí. El alcance se comprueba
   al abrir y se guarda, para que un cambio de asignación posterior no
   reinterprete escenarios viejos.

3. **Una iteración guarda de qué versión de la red partió.** Sin eso, comparar
   la iteración 1 con la 7 puede estar comparando dos redes distintas y
   atribuir a los cambios lo que hizo una carga del SIG. Se guarda el hash de
   topología y se avisa al comparar.

4. **Un cambio que no se pudo aplicar se reporta, no se ignora.** Si el elemento
   ya no existe en la red, el usuario tiene que enterarse: si no, cree que probó
   algo que en realidad no se probó.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EstadoEscenario(str, Enum):
    ABIERTO = "ABIERTO"          # acumulando cambios
    EVALUADO = "EVALUADO"        # tiene al menos una iteración
    APLICADO = "APLICADO"        # sus cambios se llevaron al modelo oficial
    DESCARTADO = "DESCARTADO"    # se decidió no seguir


class EscenarioError(Exception):
    pass


@dataclass
class Escenario:
    escenario_id: str
    nombre: str
    usuario: str
    unidad_negocio: str
    nivel: str                   # ALIMENTADOR | SUBESTACION
    entidad: str
    alimentadores: list[str] = field(default_factory=list)
    estado: str = EstadoEscenario.ABIERTO.value
    creado_en: str = ""
    actualizado_en: str = ""
    comentario: str = ""

    @property
    def abierto(self) -> bool:
        return self.estado in (EstadoEscenario.ABIERTO.value,
                               EstadoEscenario.EVALUADO.value)

    def resumen(self) -> dict:
        return {
            "escenario": self.escenario_id[:8], "nombre": self.nombre,
            "usuario": self.usuario, "unidad": self.unidad_negocio,
            "alcance": f"{self.nivel} {self.entidad}",
            "alimentadores": len(self.alimentadores),
            "estado": self.estado, "creado_en": self.creado_en,
        }


@dataclass
class CambioPropuesto:
    """Un cambio que todavía no está en el modelo oficial."""

    capa: str
    elemento_guid: str
    campo: str
    valor_despues: object
    valor_antes: object = None
    operacion: str = "MODIFICAR"
    origen: str = "MANUAL"        # MANUAL | CAMPO | CARGA
    autor: str = ""
    motivo: str = ""
    secuencia: int = 0
    ocurrido_en: str = ""

    def to_dict(self) -> dict:
        return {
            "secuencia": self.secuencia, "capa": self.capa,
            "elemento_guid": self.elemento_guid, "operacion": self.operacion,
            "campo": self.campo, "valor_antes": self.valor_antes,
            "valor_despues": self.valor_despues, "origen": self.origen,
            "autor": self.autor, "motivo": self.motivo,
            "ocurrido_en": self.ocurrido_en,
        }


@dataclass
class Iteracion:
    """Una evaluación del escenario, con sus números y de qué partió."""

    escenario_id: str
    n: int
    evaluado_en: str
    n_cambios: int
    metricas: dict = field(default_factory=dict)
    hash_topologia: str = ""
    version_red: str = ""
    cambios_no_aplicados: list = field(default_factory=list)
    comentario: str = ""

    def to_dict(self) -> dict:
        d = {"iteracion": self.n, "evaluado_en": self.evaluado_en,
             "cambios": self.n_cambios, **self.metricas}
        if self.cambios_no_aplicados:
            d["cambios_no_aplicados"] = len(self.cambios_no_aplicados)
        if self.comentario:
            d["comentario"] = self.comentario
        return d


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS escenario (
    escenario_id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT NOT NULL,
    unidad_negocio TEXT NOT NULL,
    nivel TEXT NOT NULL,
    entidad TEXT NOT NULL,
    alimentadores TEXT NOT NULL,
    estado TEXT NOT NULL,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    comentario TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cambio (
    escenario_id TEXT NOT NULL,
    secuencia INTEGER NOT NULL,
    capa TEXT NOT NULL,
    elemento_guid TEXT NOT NULL,
    operacion TEXT NOT NULL,
    campo TEXT,
    valor_antes TEXT,
    valor_despues TEXT,
    origen TEXT,
    autor TEXT,
    motivo TEXT,
    ocurrido_en TEXT,
    PRIMARY KEY (escenario_id, secuencia)
);

CREATE TABLE IF NOT EXISTS iteracion (
    escenario_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    evaluado_en TEXT NOT NULL,
    n_cambios INTEGER NOT NULL,
    metricas TEXT NOT NULL,
    hash_topologia TEXT,
    version_red TEXT,
    cambios_no_aplicados TEXT,
    comentario TEXT DEFAULT '',
    PRIMARY KEY (escenario_id, n)
);

CREATE INDEX IF NOT EXISTS ix_esc_unidad ON escenario(unidad_negocio);
CREATE INDEX IF NOT EXISTS ix_esc_entidad ON escenario(entidad);
"""


class AlmacenEscenarios:
    """Escenarios, sus cambios y sus iteraciones, en SQLite transaccional.

    Se usa la misma disciplina que el almacén de campo —WAL y transacciones
    inmediatas— porque aquí también hay varios usuarios a la vez: dos analistas
    de la misma unidad evaluando escenarios distintos no pueden pisarse el
    contador de iteraciones.
    """

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.ruta, timeout=30.0)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode = WAL")
        self.con.execute("PRAGMA busy_timeout = 30000")
        self.con.executescript(_ESQUEMA)
        self.con.commit()

    def cerrar(self) -> None:
        self.con.commit()
        self.con.close()

    def __enter__(self) -> "AlmacenEscenarios":
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()

    @contextmanager
    def _tx(self):
        # BEGIN IMMEDIATE toma el bloqueo de escritura al empezar: sin él, dos
        # evaluaciones simultáneas leen el mismo «última iteración» y una de las
        # dos se pierde al escribir.
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    # -- ciclo de vida ------------------------------------------------------
    def abrir(self, *, nombre: str, usuario: str, unidad_negocio: str,
              nivel: str, entidad: str, alimentadores: list[str],
              comentario: str = "") -> Escenario:
        """Crea un escenario. El alcance ya viene comprobado por el llamador."""

        esc = Escenario(
            escenario_id=str(uuid.uuid4()), nombre=nombre, usuario=usuario,
            unidad_negocio=unidad_negocio, nivel=nivel.upper(), entidad=entidad,
            alimentadores=list(alimentadores),
            estado=EstadoEscenario.ABIERTO.value,
            creado_en=_ahora(), actualizado_en=_ahora(), comentario=comentario)
        with self._tx() as c:
            c.execute(
                "INSERT INTO escenario VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (esc.escenario_id, esc.nombre, esc.usuario, esc.unidad_negocio,
                 esc.nivel, esc.entidad, json.dumps(esc.alimentadores),
                 esc.estado, esc.creado_en, esc.actualizado_en, esc.comentario))
        return esc

    def obtener(self, escenario_id: str) -> Escenario | None:
        f = self.con.execute(
            "SELECT * FROM escenario WHERE escenario_id = ? OR "
            "escenario_id LIKE ? || '%'", (escenario_id, escenario_id)).fetchone()
        return self._a_escenario(f) if f else None

    @staticmethod
    def _a_escenario(f: sqlite3.Row) -> Escenario:
        return Escenario(
            escenario_id=f["escenario_id"], nombre=f["nombre"],
            usuario=f["usuario"], unidad_negocio=f["unidad_negocio"],
            nivel=f["nivel"], entidad=f["entidad"],
            alimentadores=json.loads(f["alimentadores"] or "[]"),
            estado=f["estado"], creado_en=f["creado_en"],
            actualizado_en=f["actualizado_en"], comentario=f["comentario"] or "")

    def listar(self, *, alcance=None, usuario: str = "",
               entidad: str = "", incluir_cerrados: bool = False
               ) -> list[Escenario]:
        """Escenarios que ``alcance`` puede ver.

        El filtro por unidad va en la consulta y no después: traer todo y
        recortar en memoria es la clase de atajo que se olvida el día que
        alguien añade una vista nueva.
        """

        sql = "SELECT * FROM escenario WHERE 1=1"
        args: list = []
        if alcance is not None and not getattr(alcance, "matriz", False):
            unidades = sorted(getattr(alcance, "unidades", ()) or ())
            if not unidades:
                return []
            sql += f" AND unidad_negocio IN ({','.join('?' * len(unidades))})"
            args += unidades
        if usuario:
            sql += " AND usuario = ?"
            args.append(usuario)
        if entidad:
            sql += " AND entidad = ?"
            args.append(entidad)
        if not incluir_cerrados:
            sql += " AND estado IN ('ABIERTO','EVALUADO')"
        sql += " ORDER BY actualizado_en DESC"
        return [self._a_escenario(f) for f in self.con.execute(sql, args)]

    def cambiar_estado(self, escenario_id: str, estado: EstadoEscenario) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE escenario SET estado = ?, actualizado_en = ? "
                "WHERE escenario_id = ?",
                (estado.value, _ahora(), escenario_id))

    # -- cambios ------------------------------------------------------------
    def acumular(self, escenario_id: str,
                 cambios: list[CambioPropuesto]) -> int:
        """Añade cambios al escenario. Devuelve cuántos quedaron registrados."""

        esc = self.obtener(escenario_id)
        if esc is None:
            raise EscenarioError(f"No existe el escenario '{escenario_id}'.")
        if not esc.abierto:
            raise EscenarioError(
                f"El escenario '{esc.nombre}' está {esc.estado} y ya no admite "
                "cambios. Abra uno nuevo — modificar uno aplicado dejaría el "
                "histórico contando una evolución que no ocurrió así.")
        if not cambios:
            return 0

        with self._tx() as c:
            fila = c.execute(
                "SELECT COALESCE(MAX(secuencia), 0) AS s FROM cambio "
                "WHERE escenario_id = ?", (esc.escenario_id,)).fetchone()
            sec = int(fila["s"])
            for cam in cambios:
                sec += 1
                cam.secuencia = sec
                cam.ocurrido_en = cam.ocurrido_en or _ahora()
                c.execute(
                    "INSERT INTO cambio VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (esc.escenario_id, sec, cam.capa, cam.elemento_guid,
                     cam.operacion, cam.campo,
                     json.dumps(cam.valor_antes, default=str),
                     json.dumps(cam.valor_despues, default=str),
                     cam.origen, cam.autor, cam.motivo, cam.ocurrido_en))
            c.execute("UPDATE escenario SET actualizado_en = ? "
                      "WHERE escenario_id = ?", (_ahora(), esc.escenario_id))
        return len(cambios)

    def cambios(self, escenario_id: str) -> list[CambioPropuesto]:
        esc = self.obtener(escenario_id)
        if esc is None:
            return []
        out = []
        for f in self.con.execute(
                "SELECT * FROM cambio WHERE escenario_id = ? ORDER BY secuencia",
                (esc.escenario_id,)):
            out.append(CambioPropuesto(
                capa=f["capa"], elemento_guid=f["elemento_guid"],
                campo=f["campo"] or "", operacion=f["operacion"],
                valor_antes=_desjson(f["valor_antes"]),
                valor_despues=_desjson(f["valor_despues"]),
                origen=f["origen"] or "", autor=f["autor"] or "",
                motivo=f["motivo"] or "", secuencia=int(f["secuencia"]),
                ocurrido_en=f["ocurrido_en"] or ""))
        return out

    # -- iteraciones --------------------------------------------------------
    def registrar_iteracion(self, escenario_id: str, *, metricas: dict,
                            n_cambios: int, hash_topologia: str = "",
                            version_red: str = "",
                            cambios_no_aplicados: list | None = None,
                            comentario: str = "") -> Iteracion:
        """Guarda una evaluación. **Nunca se sobrescribe una anterior.**

        Cada evaluación es un punto de la evolución del alimentador. Reemplazar
        la última «porque salió mal» borraría justamente la información que
        explica por qué se cambió de rumbo.
        """

        esc = self.obtener(escenario_id)
        if esc is None:
            raise EscenarioError(f"No existe el escenario '{escenario_id}'.")

        with self._tx() as c:
            fila = c.execute(
                "SELECT COALESCE(MAX(n), 0) AS n FROM iteracion "
                "WHERE escenario_id = ?", (esc.escenario_id,)).fetchone()
            n = int(fila["n"]) + 1
            it = Iteracion(
                escenario_id=esc.escenario_id, n=n, evaluado_en=_ahora(),
                n_cambios=n_cambios, metricas=metricas,
                hash_topologia=hash_topologia, version_red=version_red,
                cambios_no_aplicados=list(cambios_no_aplicados or []),
                comentario=comentario)
            c.execute(
                "INSERT INTO iteracion VALUES (?,?,?,?,?,?,?,?,?)",
                (esc.escenario_id, n, it.evaluado_en, n_cambios,
                 json.dumps(metricas, default=str), hash_topologia, version_red,
                 json.dumps(it.cambios_no_aplicados, default=str), comentario))
            c.execute(
                "UPDATE escenario SET estado = ?, actualizado_en = ? "
                "WHERE escenario_id = ?",
                (EstadoEscenario.EVALUADO.value, _ahora(), esc.escenario_id))
        return it

    def iteraciones(self, escenario_id: str) -> list[Iteracion]:
        esc = self.obtener(escenario_id)
        if esc is None:
            return []
        out = []
        for f in self.con.execute(
                "SELECT * FROM iteracion WHERE escenario_id = ? ORDER BY n",
                (esc.escenario_id,)):
            out.append(Iteracion(
                escenario_id=esc.escenario_id, n=int(f["n"]),
                evaluado_en=f["evaluado_en"], n_cambios=int(f["n_cambios"]),
                metricas=json.loads(f["metricas"] or "{}"),
                hash_topologia=f["hash_topologia"] or "",
                version_red=f["version_red"] or "",
                cambios_no_aplicados=json.loads(f["cambios_no_aplicados"] or "[]"),
                comentario=f["comentario"] or ""))
        return out

    def evolucion(self, escenario_id: str) -> pd.DataFrame:
        """La serie de iteraciones: cómo fue cambiando el balance."""

        its = self.iteraciones(escenario_id)
        if not its:
            return pd.DataFrame()
        return pd.DataFrame([it.to_dict() for it in its])

    def evolucion_de_entidad(self, entidad: str, *, alcance=None) -> pd.DataFrame:
        """Todas las iteraciones de una entidad, **de todos sus escenarios**.

        Es la vista que responde «¿este alimentador está mejorando?»: la historia
        de un alimentador no cabe en un solo escenario, porque se abre uno nuevo
        cada vez que se aplica el anterior.
        """

        escenarios = self.listar(alcance=alcance, entidad=entidad,
                                 incluir_cerrados=True)
        filas = []
        for esc in escenarios:
            for it in self.iteraciones(esc.escenario_id):
                filas.append({
                    "entidad": entidad, "escenario": esc.nombre,
                    "estado_escenario": esc.estado, "usuario": esc.usuario,
                    **it.to_dict()})
        if not filas:
            return pd.DataFrame()
        return pd.DataFrame(filas).sort_values("evaluado_en").reset_index(drop=True)


def _desjson(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


# --------------------------------------------------------------------------- #
# Comparación entre iteraciones
# --------------------------------------------------------------------------- #
@dataclass
class Comparacion:
    desde: int
    hasta: int
    diferencias: dict = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"metrica": k, "desde": v[0], "hasta": v[1],
             "diferencia": v[2], "variacion_pct": v[3]}
            for k, v in self.diferencias.items()
        ])


def comparar(almacen: AlmacenEscenarios, escenario_id: str,
             desde: int, hasta: int) -> Comparacion:
    """Qué cambió entre dos iteraciones del mismo escenario."""

    its = {it.n: it for it in almacen.iteraciones(escenario_id)}
    if desde not in its or hasta not in its:
        raise EscenarioError(
            f"El escenario no tiene las iteraciones {desde} y {hasta}. "
            f"Tiene: {sorted(its) or 'ninguna'}.")

    a, b = its[desde], its[hasta]
    comp = Comparacion(desde=desde, hasta=hasta)

    if a.hash_topologia and b.hash_topologia and a.hash_topologia != b.hash_topologia:
        # Sin este aviso, una carga del SIG entre las dos evaluaciones se
        # atribuiría a los cambios del analista.
        comp.advertencias.append(
            "Las dos iteraciones parten de versiones distintas de la red: entre "
            "ellas hubo una actualización del SIG. La diferencia que ve NO es "
            "solo efecto de sus cambios.")

    tipo_a = str(a.metricas.get("tipo_balance", "") or "")
    tipo_b = str(b.metricas.get("tipo_balance", "") or "")
    if tipo_a and tipo_b and tipo_a != tipo_b:
        # Una PNT medida y una estimada no son magnitudes del mismo orden de
        # garantía: restarlas da un número, no una conclusión.
        comp.advertencias.append(
            f"El tipo de balance cambió entre iteraciones ({tipo_a} → {tipo_b}): "
            "la diferencia de PNT mezcla un número verificable con uno estimado.")

    for k in sorted(set(a.metricas) | set(b.metricas)):
        va, vb = a.metricas.get(k), b.metricas.get(k)
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            continue
        dif = vb - va
        pct = (100.0 * dif / abs(va)) if va else None
        comp.diferencias[k] = (round(va, 3), round(vb, 3), round(dif, 3),
                               round(pct, 2) if pct is not None else None)

    if not comp.diferencias:
        comp.advertencias.append(
            "Las dos iteraciones no comparten ninguna métrica numérica "
            "comparable.")
    return comp
