"""Almacén transaccional del trabajo de campo.

Reemplaza la persistencia en JSON por SQLite. El motivo no es estético: con JSON,
cada operación carga el archivo entero, lo modifica y lo vuelve a escribir
completo. Si tres técnicos sincronizan al mismo tiempo —el caso normal cuando la
cuadrilla vuelve a la base— **el último en guardar pisa lo que escribieron los
otros**. Medido sobre el escenario de prueba: de 9 actualizaciones concurrentes
sobrevivían 3.

SQLite resuelve eso con transacciones reales. Tres decisiones concretas:

1. **``BEGIN IMMEDIATE``** en toda escritura: toma el bloqueo de escritura al
   empezar la transacción, no al primer ``INSERT``. Sin eso, dos transacciones
   que leen y luego escriben pueden entrelazarse y una recibe ``SQLITE_BUSY`` a
   mitad de camino, con parte del trabajo hecho.

2. **Actualización condicional (*compare-and-set*)**: los cambios de estado se
   escriben con ``UPDATE … WHERE estado = <esperado>``. Si otro proceso ya cambió
   el estado, el ``UPDATE`` afecta cero filas y la operación falla explícitamente
   en vez de sobrescribir una transición ajena.

3. **``busy_timeout``**: en vez de fallar de inmediato ante un bloqueo, se espera.
   Una sincronización que tarda 200 ms extra es invisible; una que falla obliga
   al técnico a reintentar y a veces a rehacer el trabajo.

El archivo sigue siendo uno solo, sin servidor de base de datos, coherente con el
resto del sistema: se puede copiar, respaldar y versionar.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuario (
    usuario           TEXT PRIMARY KEY,
    nombre            TEXT NOT NULL DEFAULT '',
    rol               TEXT NOT NULL DEFAULT 'TECNICO',
    unidad_negocio    TEXT NOT NULL DEFAULT '',
    activo            INTEGER NOT NULL DEFAULT 1,
    password_hash     TEXT NOT NULL DEFAULT '',
    token_dispositivo TEXT,
    dispositivo_id    TEXT NOT NULL DEFAULT '',
    vinculado_en      TEXT NOT NULL DEFAULT '',
    creado_en         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS asignacion (
    orden_trabajo       TEXT PRIMARY KEY,
    guid                TEXT NOT NULL,
    asignado_a          TEXT NOT NULL,
    nivel               TEXT NOT NULL DEFAULT '',
    entidad             TEXT NOT NULL DEFAULT '',
    feeder_code         TEXT NOT NULL DEFAULT '',
    accion              TEXT NOT NULL DEFAULT '',
    motivo              TEXT NOT NULL DEFAULT '',
    clientes_a_revisar  INTEGER NOT NULL DEFAULT 0,
    recuperable_kwh_mes REAL NOT NULL DEFAULT 0,
    x                   REAL,
    y                   REAL,
    radio_m             REAL NOT NULL DEFAULT 150,
    estado              TEXT NOT NULL DEFAULT 'ASIGNADA',
    asignado_por        TEXT NOT NULL DEFAULT '',
    fecha_asignacion    TEXT NOT NULL DEFAULT '',
    fecha_descarga      TEXT NOT NULL DEFAULT '',
    fecha_inicio        TEXT NOT NULL DEFAULT '',
    fecha_cierre        TEXT NOT NULL DEFAULT '',
    resultado           TEXT NOT NULL DEFAULT '',
    tipo_trabajo        TEXT NOT NULL DEFAULT 'INSPECCION_PNT',
    -- Una revisión de campo puede llevar varios días. Estas dos columnas son
    -- las que distinguen «va por la mitad» de «lleva una semana parada»: sin
    -- ellas, una orden EN_PROCESO se ve igual el día 1 que el día 12.
    visitas             INTEGER NOT NULL DEFAULT 0,
    fecha_ultimo_avance TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (asignado_a) REFERENCES usuario(usuario)
);

CREATE INDEX IF NOT EXISTS ix_asig_usuario ON asignacion(asignado_a, estado);

-- Bitácora de operaciones: quién asignó qué y cuándo. Sin esto, una orden que
-- cambió de técnico es imposible de explicar tres semanas después.
CREATE TABLE IF NOT EXISTS bitacora (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ocurrido_en   TEXT NOT NULL,
    operacion     TEXT NOT NULL,
    orden_trabajo TEXT,
    usuario       TEXT,
    actor         TEXT,
    detalle       TEXT
);
"""


class ConflictoConcurrencia(Exception):
    """Otro proceso modificó el registro entre la lectura y la escritura."""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AlmacenCampo:
    """Persistencia transaccional de usuarios y asignaciones."""

    def __init__(self, ruta: str | Path, *, timeout_s: float = 10.0):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout_s
        with self._conectar() as con:
            con.executescript(_ESQUEMA)

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ruta, timeout=self._timeout,
                              isolation_level=None)
        con.row_factory = sqlite3.Row
        # WAL: los lectores no bloquean al escritor. Un técnico consultando sus
        # órdenes no debe frenar la sincronización de otro.
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        return con

    @contextmanager
    def escritura(self):
        """Transacción de escritura con bloqueo tomado desde el inicio.

        ``BEGIN IMMEDIATE`` reserva el bloqueo de escritura antes de leer nada.
        Con el ``BEGIN`` diferido por defecto, dos transacciones pueden leer el
        mismo estado y chocar al escribir, dejando una a medio camino.
        """

        con = self._conectar()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()

    @contextmanager
    def lectura(self):
        con = self._conectar()
        try:
            yield con
        finally:
            con.close()

    # -- bitácora ----------------------------------------------------------
    def _anotar(self, con: sqlite3.Connection, operacion: str, *,
                orden: str | None = None, usuario: str | None = None,
                actor: str = "", detalle: dict | None = None) -> None:
        con.execute(
            "INSERT INTO bitacora (ocurrido_en, operacion, orden_trabajo, "
            "usuario, actor, detalle) VALUES (?,?,?,?,?,?)",
            (_ahora(), operacion, orden, usuario, actor,
             json.dumps(detalle or {}, ensure_ascii=False, default=str)))

    # -- usuarios ----------------------------------------------------------
    def guardar_usuario(self, datos: dict) -> None:
        campos = ("usuario", "nombre", "rol", "unidad_negocio", "activo",
                  "password_hash", "token_dispositivo", "dispositivo_id",
                  "vinculado_en", "creado_en")
        with self.escritura() as con:
            con.execute(
                f"INSERT OR REPLACE INTO usuario ({','.join(campos)}) "
                f"VALUES ({','.join('?' * len(campos))})",
                [datos.get(c) for c in campos])

    def usuario(self, usuario: str) -> dict | None:
        with self.lectura() as con:
            r = con.execute("SELECT * FROM usuario WHERE usuario = ?",
                            (usuario,)).fetchone()
        return dict(r) if r else None

    def usuarios(self) -> list[dict]:
        with self.lectura() as con:
            return [dict(r) for r in
                    con.execute("SELECT * FROM usuario ORDER BY usuario")]

    def por_token(self, token: str) -> dict | None:
        """Resuelve el usuario desde el token del dispositivo."""

        if not token:
            return None
        with self.lectura() as con:
            r = con.execute(
                "SELECT * FROM usuario WHERE token_dispositivo = ? AND activo = 1",
                (token,)).fetchone()
        return dict(r) if r else None

    # -- asignaciones ------------------------------------------------------
    def asignar_lote(self, asignaciones: list[dict], *, actor: str = "",
                     permitir_reasignar: bool = False,
                     todo_o_nada: bool = False) -> tuple[int, list[str]]:
        """Asigna varias órdenes **en una sola transacción**.

        La comprobación de conflictos y la inserción ocurren dentro del mismo
        ``BEGIN IMMEDIATE``: entre "está libre" y "queda asignada" no cabe otro
        supervisor.

        Devuelve ``(insertadas, conflictos)``. Los conflictos son órdenes que ya
        pertenecen a otro técnico y no se tocan: que dos cuadrillas vayan al mismo
        sitio es el desperdicio más común de este trabajo.

        Con ``todo_o_nada`` un solo conflicto aborta el lote completo y no se
        escribe nada. Es lo correcto cuando el supervisor asigna una jornada: media
        jornada asignada es peor que ninguna, porque nadie sabe qué falta.
        """

        if not asignaciones:
            return 0, []

        campos = ("orden_trabajo", "guid", "asignado_a", "nivel", "entidad",
                  "feeder_code", "accion", "motivo", "clientes_a_revisar",
                  "recuperable_kwh_mes", "x", "y", "radio_m", "estado",
                  "asignado_por", "fecha_asignacion", "fecha_descarga",
                  "fecha_inicio", "fecha_cierre", "resultado", "tipo_trabajo",
                  "visitas", "fecha_ultimo_avance")

        conflictos: list[str] = []
        with self.escritura() as con:
            ordenes = [a["orden_trabajo"] for a in asignaciones]
            marcas = ",".join("?" * len(ordenes))
            existentes = {
                r["orden_trabajo"]: r for r in con.execute(
                    f"SELECT orden_trabajo, asignado_a, estado FROM asignacion "
                    f"WHERE orden_trabajo IN ({marcas})", ordenes)
            }
            a_insertar = []
            for a in asignaciones:
                prev = existentes.get(a["orden_trabajo"])
                if prev is not None and not permitir_reasignar:
                    ocupada = (prev["asignado_a"] != a["asignado_a"]
                               and prev["estado"] not in ("RECHAZADA",
                                                          "SINCRONIZADA"))
                    if ocupada:
                        conflictos.append(a["orden_trabajo"])
                        continue
                a_insertar.append([a.get(c) for c in campos])

            if conflictos and todo_o_nada:
                # La excepción sale del ``with`` y dispara el ROLLBACK: nada de
                # este lote queda escrito.
                raise ValueError(
                    f"{len(conflictos)} orden(es) ya están asignadas a otro "
                    f"técnico: {', '.join(conflictos[:5])}"
                    + ("…" if len(conflictos) > 5 else "")
                    + ". Libérelas primero o asigne otras.")

            if a_insertar:
                con.executemany(
                    f"INSERT OR REPLACE INTO asignacion ({','.join(campos)}) "
                    f"VALUES ({','.join('?' * len(campos))})", a_insertar)
                self._anotar(con, "ASIGNAR", actor=actor,
                             detalle={"ordenes": len(a_insertar),
                                      "conflictos": len(conflictos)})
        return len(a_insertar), conflictos

    def de_usuario(self, usuario: str, estados: set[str] | None = None
                   ) -> list[dict]:
        sql = "SELECT * FROM asignacion WHERE asignado_a = ?"
        args: list = [usuario]
        if estados:
            sql += f" AND estado IN ({','.join('?' * len(estados))})"
            args += sorted(estados)
        sql += " ORDER BY recuperable_kwh_mes DESC"
        with self.lectura() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def asignacion(self, orden: str) -> dict | None:
        with self.lectura() as con:
            r = con.execute("SELECT * FROM asignacion WHERE orden_trabajo = ?",
                            (orden,)).fetchone()
        return dict(r) if r else None

    def transicionar(self, orden: str, desde: str, hacia: str, *,
                     actor: str = "", campos_fecha: dict | None = None,
                     resultado: str | None = None) -> bool:
        """Cambia el estado **solo si** sigue en el estado esperado.

        Es la operación que hace segura la concurrencia: el ``WHERE estado = ?``
        garantiza que dos procesos no apliquen la misma transición dos veces ni
        pisen la de otro. Devuelve ``False`` si otro ya lo movió — el llamador
        decide si eso es un error o algo esperable.
        """

        sets = ["estado = ?"]
        args: list = [hacia]
        for k, v in (campos_fecha or {}).items():
            sets.append(f"{k} = ?")
            args.append(v)
        if resultado is not None:
            sets.append("resultado = ?")
            args.append(resultado)
        args += [orden, desde]

        with self.escritura() as con:
            cur = con.execute(
                f"UPDATE asignacion SET {', '.join(sets)} "
                f"WHERE orden_trabajo = ? AND estado = ?", args)
            ok = cur.rowcount > 0
            if ok:
                self._anotar(con, "TRANSICION", orden=orden, actor=actor,
                             detalle={"desde": desde, "hacia": hacia})
        return ok

    def transicionar_lote(self, ordenes: list[str], desde: str, hacia: str, *,
                          actor: str = "", campos_fecha: dict | None = None
                          ) -> int:
        """Transiciona varias órdenes en una sola transacción.

        Es lo que ocurre al descargar un paquete: todas las órdenes del técnico
        pasan a DESCARGADA juntas. Hacerlo una por una dejaría un estado
        inconsistente si la conexión se corta a mitad.
        """

        if not ordenes:
            return 0
        sets = ["estado = ?"]
        args: list = [hacia]
        for k, v in (campos_fecha or {}).items():
            sets.append(f"{k} = ?")
            args.append(v)
        marcas = ",".join("?" * len(ordenes))
        args += list(ordenes) + [desde]

        with self.escritura() as con:
            cur = con.execute(
                f"UPDATE asignacion SET {', '.join(sets)} "
                f"WHERE orden_trabajo IN ({marcas}) AND estado = ?", args)
            n = cur.rowcount
            if n:
                self._anotar(con, "TRANSICION_LOTE", actor=actor,
                             detalle={"desde": desde, "hacia": hacia, "n": n})
        return n

    def anotar_avance(self, orden: str, *, actor: str = "") -> bool:
        """Suma una jornada trabajada y deja la orden **abierta y en proceso**.

        Es lo que ocurre cuando un trabajo largo sincroniza al final del día: hay
        avance real que registrar, pero la orden sigue abierta. Sin esto, un
        trabajo de una semana es indistinguible de uno abandonado el primer día.

        El estado pasa a EN_PROCESO si aún estaba DESCARGADA. Es corrección, no
        adorno: el técnico abrió la orden en el dispositivo y el backend seguía
        diciendo «descargada», así que el tablero del supervisor mostraba trabajo
        sin empezar donde había una cuadrilla trabajando.
        """

        ahora = _ahora()
        with self.escritura() as con:
            cur = con.execute(
                "UPDATE asignacion SET "
                "  visitas = visitas + 1, "
                "  fecha_ultimo_avance = ?, "
                "  estado = CASE WHEN estado = 'DESCARGADA' THEN 'EN_PROCESO' "
                "                ELSE estado END, "
                "  fecha_inicio = CASE WHEN fecha_inicio = '' THEN ? "
                "                      ELSE fecha_inicio END "
                "WHERE orden_trabajo = ? AND estado IN "
                "('DESCARGADA','EN_PROCESO')",
                (ahora, ahora, orden))
            ok = cur.rowcount > 0
            if ok:
                fila = con.execute(
                    "SELECT estado, visitas FROM asignacion "
                    "WHERE orden_trabajo = ?", (orden,)).fetchone()
                self._anotar(con, "AVANCE", orden=orden, actor=actor,
                             detalle={"estado": fila["estado"],
                                      "jornada": fila["visitas"]})
        return ok

    def estancadas(self, dias: int = 5) -> list[dict]:
        """Órdenes abiertas sin avance en N días: dónde se atascó la campaña."""

        from datetime import timedelta

        corte = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(
            timespec="seconds")
        with self.lectura() as con:
            filas = con.execute(
                "SELECT * FROM asignacion WHERE estado IN "
                "('ASIGNADA','DESCARGADA','EN_PROCESO') "
                "AND COALESCE(NULLIF(fecha_ultimo_avance,''), fecha_asignacion) < ? "
                "ORDER BY recuperable_kwh_mes DESC", (corte,)).fetchall()
        return [dict(f) for f in filas]

    def liberar(self, ordenes: list[str], *, actor: str = "") -> int:
        if not ordenes:
            return 0
        with self.escritura() as con:
            cur = con.execute(
                f"DELETE FROM asignacion WHERE orden_trabajo IN "
                f"({','.join('?' * len(ordenes))})", ordenes)
            if cur.rowcount:
                self._anotar(con, "LIBERAR", actor=actor,
                             detalle={"n": cur.rowcount})
            return cur.rowcount

    def todas(self) -> list[dict]:
        with self.lectura() as con:
            return [dict(r) for r in con.execute("SELECT * FROM asignacion")]

    # -- reportes ----------------------------------------------------------
    def carga_por_usuario(self) -> pd.DataFrame:
        """Carga de trabajo por técnico, en una sola consulta."""

        with self.lectura() as con:
            filas = con.execute("""
                SELECT u.usuario, u.nombre, u.rol, u.activo,
                       CASE WHEN u.token_dispositivo IS NOT NULL
                            AND u.token_dispositivo <> ''
                            THEN 'vinculado' ELSE 'sin vincular' END AS dispositivo,
                       COUNT(a.orden_trabajo)                      AS ordenes,
                       COALESCE(SUM(a.clientes_a_revisar), 0)      AS clientes,
                       COALESCE(SUM(a.recuperable_kwh_mes), 0)     AS recuperable_kwh_mes,
                       SUM(a.estado = 'ASIGNADA')                  AS asignadas,
                       SUM(a.estado = 'DESCARGADA')                AS descargadas,
                       SUM(a.estado = 'EN_PROCESO')                AS en_proceso,
                       SUM(a.estado = 'COMPLETADA')                AS completadas,
                       SUM(a.estado = 'SINCRONIZADA')              AS sincronizadas,
                       COALESCE(SUM(a.visitas), 0)                 AS jornadas
                FROM usuario u
                LEFT JOIN asignacion a ON a.asignado_a = u.usuario
                GROUP BY u.usuario ORDER BY u.usuario
            """).fetchall()
        df = pd.DataFrame([dict(r) for r in filas])
        if not df.empty:
            df["recuperable_kwh_mes"] = df["recuperable_kwh_mes"].round(1)
        return df

    def bitacora(self, limite: int = 200) -> pd.DataFrame:
        with self.lectura() as con:
            filas = con.execute(
                "SELECT * FROM bitacora ORDER BY id DESC LIMIT ?",
                (limite,)).fetchall()
        return pd.DataFrame([dict(r) for r in filas])
