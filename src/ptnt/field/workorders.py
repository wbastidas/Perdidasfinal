"""Usuarios de campo, asignación de órdenes y control del ciclo de trabajo.

Los usuarios de la aplicación móvil **se crean en el backend**, nunca en el
dispositivo: quien puede editar la red de una distribuidora es una decisión
administrativa, no algo que se resuelva instalando una app. Cada usuario recibe
un **token de dispositivo** que se emite al vincular el teléfono y se puede
revocar; si el equipo se pierde, se revoca el token y el paquete que llevaba deja
de poder sincronizar.

El ciclo de una orden es una máquina de estados deliberadamente estricta::

    ASIGNADA ──► DESCARGADA ──► EN_PROCESO ──► COMPLETADA ──► SINCRONIZADA
        │                                            │
        └────────────── RECHAZADA ◄──────────────────┘

Las transiciones inválidas se rechazan con un mensaje explícito. Sin esa
disciplina, una orden puede aparecer "completada" sin haberse descargado nunca, y
el informe de gestión deja de significar algo.

La persistencia vive en :mod:`ptnt.field.store` (SQLite transaccional). Esta capa
mantiene los objetos de dominio y la máquina de estados; el almacén garantiza que
varias cuadrillas sincronizando **al mismo tiempo** no se pisen entre sí.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

from ptnt.field.store import AlmacenCampo, ConflictoConcurrencia


class RolCampo(str, Enum):
    TECNICO = "TECNICO"            # ejecuta órdenes, edita en campo
    SUPERVISOR = "SUPERVISOR"      # asigna órdenes y revisa cambios
    LECTOR = "LECTOR"              # solo consulta


class EstadoOrden(str, Enum):
    ASIGNADA = "ASIGNADA"
    DESCARGADA = "DESCARGADA"
    EN_PROCESO = "EN_PROCESO"
    COMPLETADA = "COMPLETADA"
    SINCRONIZADA = "SINCRONIZADA"
    RECHAZADA = "RECHAZADA"


# Transiciones permitidas. Una orden no puede "completarse" sin haberse
# descargado, ni volver atrás una vez sincronizada.
_TRANSICIONES: dict[EstadoOrden, set[EstadoOrden]] = {
    EstadoOrden.ASIGNADA: {EstadoOrden.DESCARGADA, EstadoOrden.RECHAZADA},
    EstadoOrden.DESCARGADA: {EstadoOrden.EN_PROCESO, EstadoOrden.RECHAZADA},
    EstadoOrden.EN_PROCESO: {EstadoOrden.COMPLETADA, EstadoOrden.RECHAZADA},
    EstadoOrden.COMPLETADA: {EstadoOrden.SINCRONIZADA, EstadoOrden.RECHAZADA},
    EstadoOrden.SINCRONIZADA: set(),
    EstadoOrden.RECHAZADA: {EstadoOrden.ASIGNADA},
}


class TransicionInvalida(Exception):
    """Se intentó un cambio de estado que la máquina no permite."""


@dataclass
class UsuarioCampo:
    """Un usuario de la aplicación móvil, creado desde el backend."""

    usuario: str
    nombre: str
    rol: RolCampo = RolCampo.TECNICO
    unidad_negocio: str = ""
    activo: bool = True
    # Nunca se guarda la contraseña: solo su hash (ver ptnt.security.auth).
    password_hash: str = ""
    # Token del dispositivo vinculado. Revocarlo desconecta el equipo.
    token_dispositivo: str = ""
    dispositivo_id: str = ""
    vinculado_en: str = ""
    creado_en: str = ""

    def to_dict(self, *, incluir_secretos: bool = False) -> dict:
        d = {
            "usuario": self.usuario, "nombre": self.nombre, "rol": self.rol.value,
            "unidad_negocio": self.unidad_negocio, "activo": self.activo,
            "dispositivo_id": self.dispositivo_id,
            "vinculado_en": self.vinculado_en, "creado_en": self.creado_en,
        }
        if incluir_secretos:
            d["password_hash"] = self.password_hash
            d["token_dispositivo"] = self.token_dispositivo
        return d


@dataclass
class Asignacion:
    """Una orden de trabajo asignada a un usuario."""

    orden_trabajo: str
    asignado_a: str
    nivel: str
    entidad: str
    feeder_code: str = ""
    accion: str = ""
    motivo: str = ""
    clientes_a_revisar: int = 0
    recuperable_kwh_mes: float = 0.0
    x: float | None = None
    y: float | None = None
    radio_m: float = 150.0
    estado: EstadoOrden = EstadoOrden.ASIGNADA
    asignado_por: str = ""
    fecha_asignacion: str = ""
    fecha_descarga: str = ""
    fecha_inicio: str = ""
    fecha_cierre: str = ""
    resultado: str = ""
    guid: str = ""

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = str(uuid.uuid4())
        if not self.fecha_asignacion:
            self.fecha_asignacion = _ahora()

    def transicionar(self, nuevo: EstadoOrden) -> None:
        if nuevo not in _TRANSICIONES[self.estado]:
            permitidos = ", ".join(sorted(e.value for e in _TRANSICIONES[self.estado]))
            raise TransicionInvalida(
                f"La orden {self.orden_trabajo} está {self.estado.value} y no puede "
                f"pasar a {nuevo.value}. Transiciones válidas: "
                f"{permitidos or '(ninguna: estado final)'}."
            )
        self.estado = nuevo
        ahora = _ahora()
        if nuevo is EstadoOrden.DESCARGADA:
            self.fecha_descarga = ahora
        elif nuevo is EstadoOrden.EN_PROCESO:
            self.fecha_inicio = ahora
        elif nuevo in (EstadoOrden.COMPLETADA, EstadoOrden.RECHAZADA):
            self.fecha_cierre = ahora

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["estado"] = self.estado.value
        return d

    @classmethod
    def desde_fila(cls, fila: dict) -> "Asignacion":
        """Reconstruye la asignación desde una fila del almacén."""

        campos = {f for f in cls.__dataclass_fields__}
        datos = {k: v for k, v in fila.items() if k in campos}
        datos["estado"] = EstadoOrden(str(fila.get("estado", "ASIGNADA")))
        for k in ("nivel", "entidad", "feeder_code", "accion", "motivo",
                  "asignado_por", "fecha_asignacion", "fecha_descarga",
                  "fecha_inicio", "fecha_cierre", "resultado", "guid"):
            datos[k] = str(datos.get(k) or "")
        datos["clientes_a_revisar"] = int(datos.get("clientes_a_revisar") or 0)
        datos["recuperable_kwh_mes"] = float(datos.get("recuperable_kwh_mes") or 0)
        datos["radio_m"] = float(datos.get("radio_m") or 150.0)
        return cls(**datos)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _campo_fecha(nuevo: EstadoOrden) -> dict[str, str]:
    """Qué marca de tiempo corresponde a cada estado."""

    if nuevo is EstadoOrden.DESCARGADA:
        return {"fecha_descarga": _ahora()}
    if nuevo is EstadoOrden.EN_PROCESO:
        return {"fecha_inicio": _ahora()}
    if nuevo in (EstadoOrden.COMPLETADA, EstadoOrden.RECHAZADA):
        return {"fecha_cierre": _ahora()}
    return {}


class RegistroCampo:
    """Usuarios, asignaciones y ciclo de trabajo sobre un almacén transaccional.

    Se guarda en su propio archivo y no en DuckDB porque la asignación de trabajo
    debe sobrevivir a un borrado de los resultados de análisis: los cálculos se
    rehacen, un compromiso con una cuadrilla no.

    Toda escritura va directo al almacén: no hay estado en memoria que un segundo
    proceso pueda pisar. ``save()`` se conserva por compatibilidad y ya no tiene
    trabajo que hacer.
    """

    def __init__(self, ruta: str | Path):
        ruta = Path(ruta)
        # Se acepta la ruta histórica ``registro.json`` para no romper llamadas
        # existentes; el archivo real es el ``.db`` hermano.
        self.ruta_json = ruta if ruta.suffix.lower() == ".json" else None
        self.ruta = ruta if ruta.suffix.lower() == ".db" else ruta.with_suffix(".db")
        self.almacen = AlmacenCampo(self.ruta)
        self._migrar_json()

    def _migrar_json(self) -> None:
        """Importa un registro JSON anterior la primera vez, y solo esa vez."""

        if self.ruta_json is None or not self.ruta_json.exists():
            return
        if self.almacen.usuarios() or self.almacen.todas():
            return
        try:
            d = json.loads(self.ruta_json.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        for u in d.get("usuarios", []):
            self.almacen.guardar_usuario({**u, "activo": int(bool(u.get("activo", True)))})
        asigs = []
        for a in d.get("asignaciones", []):
            a = dict(a)
            a.setdefault("guid", str(uuid.uuid4()))
            asigs.append(a)
        if asigs:
            self.almacen.asignar_lote(asigs, actor="migracion",
                                      permitir_reasignar=True)

    def save(self) -> Path:
        """No-op: cada operación ya se confirmó en su propia transacción."""

        return self.ruta

    # -- lectura de conveniencia -------------------------------------------
    @property
    def usuarios(self) -> dict[str, UsuarioCampo]:
        return {u["usuario"]: _usuario_desde_fila(u)
                for u in self.almacen.usuarios()}

    @property
    def asignaciones(self) -> dict[str, Asignacion]:
        return {a["orden_trabajo"]: Asignacion.desde_fila(a)
                for a in self.almacen.todas()}

    # -- usuarios ----------------------------------------------------------
    def crear_usuario(
        self, usuario: str, nombre: str, password: str, *,
        rol: RolCampo = RolCampo.TECNICO, unidad_negocio: str = "",
    ) -> UsuarioCampo:
        """Alta de usuario móvil. La contraseña se guarda **solo** como hash."""

        if self.almacen.usuario(usuario) is not None:
            raise ValueError(f"El usuario '{usuario}' ya existe.")
        if len(password) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")

        from ptnt.security.auth import hash_password

        u = UsuarioCampo(
            usuario=usuario, nombre=nombre, rol=rol,
            unidad_negocio=unidad_negocio,
            password_hash=hash_password(password), creado_en=_ahora(),
        )
        self.almacen.guardar_usuario(_usuario_a_fila(u))
        return u

    def vincular_dispositivo(self, usuario: str, dispositivo_id: str) -> str:
        """Vincula un teléfono y emite su token. Vincular otro **revoca** el anterior.

        Un usuario, un dispositivo: si el token no fuera exclusivo, un teléfono
        extraviado seguiría sincronizando cambios a nombre del técnico."""

        u = self._usuario(usuario)
        token = secrets.token_urlsafe(32)
        u.token_dispositivo = token
        u.dispositivo_id = dispositivo_id
        u.vinculado_en = _ahora()
        self.almacen.guardar_usuario(_usuario_a_fila(u))
        return token

    def revocar_dispositivo(self, usuario: str) -> None:
        u = self._usuario(usuario)
        u.token_dispositivo = ""
        u.dispositivo_id = ""
        self.almacen.guardar_usuario(_usuario_a_fila(u))

    def autenticar_token(self, token: str) -> UsuarioCampo | None:
        if not token:
            return None
        fila = self.almacen.por_token(token)
        if fila is None:
            return None
        u = _usuario_desde_fila(fila)
        # Comparación en tiempo constante aunque la búsqueda haya sido por índice.
        if not secrets.compare_digest(u.token_dispositivo, token):
            return None
        return u

    def _usuario(self, usuario: str) -> UsuarioCampo:
        fila = self.almacen.usuario(usuario)
        if fila is None:
            raise KeyError(f"No existe el usuario '{usuario}'.")
        return _usuario_desde_fila(fila)

    # -- asignaciones ------------------------------------------------------
    def asignar(
        self, ordenes: pd.DataFrame, usuario: str, *, asignado_por: str = "",
        radio_m: float = 150.0,
    ) -> list[Asignacion]:
        """Asigna **varias** órdenes a un usuario de una vez.

        La asignación masiva es el caso normal: una cuadrilla sale con la jornada
        completa, no con una orden. Las órdenes ya asignadas a otro usuario se
        rechazan en bloque en vez de reasignarse en silencio — que dos cuadrillas
        vayan al mismo sitio es el desperdicio más común de este trabajo.

        La detección de conflictos y la inserción ocurren dentro de **la misma
        transacción**: entre comprobar y escribir no cabe otro supervisor.
        """

        self._usuario(usuario)
        nuevas = [
            Asignacion(
                orden_trabajo=str(r["orden_trabajo"]),
                asignado_a=usuario,
                nivel=str(r.get("nivel", "")),
                entidad=str(r.get("entidad", "")),
                feeder_code=str(r.get("alimentador") or ""),
                accion=str(r.get("accion", "")),
                motivo=str(r.get("motivo_principal", "")),
                clientes_a_revisar=int(r.get("clientes_a_revisar", 0) or 0),
                recuperable_kwh_mes=float(r.get("recuperable_kwh_mes", 0) or 0),
                x=_f(r.get("x")), y=_f(r.get("y")),
                radio_m=radio_m, asignado_por=asignado_por,
            )
            for _, r in ordenes.iterrows()
        ]
        self.almacen.asignar_lote([a.to_dict() for a in nuevas],
                                  actor=asignado_por, todo_o_nada=True)
        return nuevas

    def liberar(self, orden_trabajo: str) -> None:
        self.almacen.liberar([orden_trabajo])

    def de_usuario(self, usuario: str, *, estados: set[EstadoOrden] | None = None
                   ) -> list[Asignacion]:
        est = {e.value for e in estados} if estados else None
        return [Asignacion.desde_fila(f)
                for f in self.almacen.de_usuario(usuario, est)]

    def obtener(self, orden_trabajo: str) -> Asignacion | None:
        f = self.almacen.asignacion(orden_trabajo)
        return Asignacion.desde_fila(f) if f else None

    def transicionar(self, orden_trabajo: str, nuevo: EstadoOrden, *,
                     actor: str = "", resultado: str | None = None) -> Asignacion:
        """Aplica un cambio de estado con verificación de concurrencia.

        La transición se valida contra la máquina de estados y se escribe con
        ``WHERE estado = <el que se leyó>``. Si otro proceso movió la orden entre
        medias, no se sobrescribe: se levanta :class:`ConflictoConcurrencia`.
        """

        actual = self.obtener(orden_trabajo)
        if actual is None:
            raise KeyError(f"La orden '{orden_trabajo}' no está asignada.")
        desde = actual.estado
        # Valida (y lanza TransicionInvalida) sobre la copia en memoria.
        actual.transicionar(nuevo)
        ok = self.almacen.transicionar(
            orden_trabajo, desde.value, nuevo.value, actor=actor,
            campos_fecha=_campo_fecha(nuevo), resultado=resultado)
        if not ok:
            raise ConflictoConcurrencia(
                f"La orden {orden_trabajo} cambió de estado mientras se "
                f"procesaba (ya no está {desde.value}). Vuelva a consultarla.")
        if resultado is not None:
            actual.resultado = resultado
        return actual

    def marcar_descargadas(self, usuario: str, *, actor: str = "") -> int:
        """Pasa a DESCARGADA todas las órdenes ASIGNADAS del usuario, de una vez.

        Es lo que ocurre al bajar el paquete: la jornada completa cambia de estado
        junta. Hacerlo orden por orden dejaría un estado a medias si la conexión
        se corta — y con la conexión justa antes de salir a campo, se corta.
        """

        pendientes = [a.orden_trabajo
                      for a in self.de_usuario(usuario,
                                               estados={EstadoOrden.ASIGNADA})]
        return self.almacen.transicionar_lote(
            pendientes, EstadoOrden.ASIGNADA.value, EstadoOrden.DESCARGADA.value,
            actor=actor or usuario, campos_fecha={"fecha_descarga": _ahora()})

    def cerrar_orden(self, orden_trabajo: str, *, resultado: str = "",
                     actor: str = "") -> Asignacion | None:
        """Lleva una orden a COMPLETADA desde DESCARGADA o EN_PROCESO.

        Devuelve ``None`` si la orden no está en un estado que admita cierre: al
        sincronizar, una orden ya cerrada por un reenvío no es un error.
        """

        a = self.obtener(orden_trabajo)
        if a is None or a.estado not in (EstadoOrden.DESCARGADA,
                                         EstadoOrden.EN_PROCESO):
            return None
        if a.estado is EstadoOrden.DESCARGADA:
            self.transicionar(orden_trabajo, EstadoOrden.EN_PROCESO, actor=actor)
        return self.transicionar(orden_trabajo, EstadoOrden.COMPLETADA,
                                 actor=actor, resultado=resultado)

    # -- reportes ----------------------------------------------------------
    def resumen_por_usuario(self) -> pd.DataFrame:
        """Carga de trabajo por técnico: para no sobrecargar a uno y dejar
        ocioso a otro."""

        df = self.almacen.carga_por_usuario()
        if df.empty:
            return df
        df = df.rename(columns={
            "clientes": "clientes_a_revisar",
            "asignadas": EstadoOrden.ASIGNADA.value,
            "descargadas": EstadoOrden.DESCARGADA.value,
            "en_proceso": EstadoOrden.EN_PROCESO.value,
            "completadas": EstadoOrden.COMPLETADA.value,
            "sincronizadas": EstadoOrden.SINCRONIZADA.value,
        })
        df[EstadoOrden.RECHAZADA.value] = df.get(
            EstadoOrden.RECHAZADA.value, 0)
        df["activo"] = df["activo"].astype(bool)
        for e in EstadoOrden:
            df[e.value] = df[e.value].fillna(0).astype(int)
        return df

    def bitacora(self, limite: int = 200) -> pd.DataFrame:
        """Quién asignó o movió qué, y cuándo."""

        return self.almacen.bitacora(limite)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([a.to_dict() for a in self.asignaciones.values()])


def _usuario_a_fila(u: UsuarioCampo) -> dict:
    d = u.to_dict(incluir_secretos=True)
    d["unidad_negocio"] = u.unidad_negocio
    d["activo"] = int(bool(u.activo))
    return d


def _usuario_desde_fila(f: dict) -> UsuarioCampo:
    """Reconstruye el usuario tal cual está almacenado, hash y token incluidos.

    Quien serializa hacia afuera usa ``to_dict()``, que los omite salvo pedido
    explícito: el filtro está en la salida, no en la lectura interna.
    """

    return UsuarioCampo(
        usuario=str(f["usuario"]), nombre=str(f.get("nombre") or ""),
        rol=RolCampo(str(f.get("rol") or "TECNICO")),
        unidad_negocio=str(f.get("unidad_negocio") or ""),
        activo=bool(f.get("activo", True)),
        password_hash=str(f.get("password_hash") or ""),
        token_dispositivo=str(f.get("token_dispositivo") or ""),
        dispositivo_id=str(f.get("dispositivo_id") or ""),
        vinculado_en=str(f.get("vinculado_en") or ""),
        creado_en=str(f.get("creado_en") or ""),
    )


def _f(v) -> float | None:
    try:
        x = float(v)
        return None if pd.isna(x) else x
    except (TypeError, ValueError):
        return None
