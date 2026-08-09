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


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RegistroCampo:
    """Persistencia de usuarios y asignaciones (JSON, sin base de datos).

    Se guarda como archivo y no en DuckDB porque la asignación de trabajo debe
    sobrevivir a un borrado de los resultados de análisis: los cálculos se
    rehacen, un compromiso con una cuadrilla no.
    """

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.usuarios: dict[str, UsuarioCampo] = {}
        self.asignaciones: dict[str, Asignacion] = {}
        self._cargar()

    def _cargar(self) -> None:
        if not self.ruta.exists():
            return
        d = json.loads(self.ruta.read_text(encoding="utf-8"))
        for u in d.get("usuarios", []):
            self.usuarios[u["usuario"]] = UsuarioCampo(
                usuario=u["usuario"], nombre=u.get("nombre", ""),
                rol=RolCampo(u.get("rol", "TECNICO")),
                unidad_negocio=u.get("unidad_negocio", ""),
                activo=u.get("activo", True),
                password_hash=u.get("password_hash", ""),
                token_dispositivo=u.get("token_dispositivo", ""),
                dispositivo_id=u.get("dispositivo_id", ""),
                vinculado_en=u.get("vinculado_en", ""),
                creado_en=u.get("creado_en", ""),
            )
        for a in d.get("asignaciones", []):
            a = dict(a)
            a["estado"] = EstadoOrden(a.get("estado", "ASIGNADA"))
            self.asignaciones[a["orden_trabajo"]] = Asignacion(**a)

    def save(self) -> Path:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_text(json.dumps({
            "usuarios": [u.to_dict(incluir_secretos=True)
                         for u in self.usuarios.values()],
            "asignaciones": [a.to_dict() for a in self.asignaciones.values()],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.ruta

    # -- usuarios ----------------------------------------------------------
    def crear_usuario(
        self, usuario: str, nombre: str, password: str, *,
        rol: RolCampo = RolCampo.TECNICO, unidad_negocio: str = "",
    ) -> UsuarioCampo:
        """Alta de usuario móvil. La contraseña se guarda **solo** como hash."""

        if usuario in self.usuarios:
            raise ValueError(f"El usuario '{usuario}' ya existe.")
        if len(password) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")

        from ptnt.security.auth import hash_password

        u = UsuarioCampo(
            usuario=usuario, nombre=nombre, rol=rol,
            unidad_negocio=unidad_negocio,
            password_hash=hash_password(password), creado_en=_ahora(),
        )
        self.usuarios[usuario] = u
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
        return token

    def revocar_dispositivo(self, usuario: str) -> None:
        u = self._usuario(usuario)
        u.token_dispositivo = ""
        u.dispositivo_id = ""

    def autenticar_token(self, token: str) -> UsuarioCampo | None:
        if not token:
            return None
        for u in self.usuarios.values():
            if u.token_dispositivo and secrets.compare_digest(
                    u.token_dispositivo, token) and u.activo:
                return u
        return None

    def _usuario(self, usuario: str) -> UsuarioCampo:
        if usuario not in self.usuarios:
            raise KeyError(f"No existe el usuario '{usuario}'.")
        return self.usuarios[usuario]

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
        """

        self._usuario(usuario)
        conflictos = [
            o for o in ordenes["orden_trabajo"].astype(str)
            if o in self.asignaciones
            and self.asignaciones[o].asignado_a != usuario
            and self.asignaciones[o].estado not in (
                EstadoOrden.RECHAZADA, EstadoOrden.SINCRONIZADA)
        ]
        if conflictos:
            raise ValueError(
                f"{len(conflictos)} orden(es) ya están asignadas a otro técnico: "
                f"{', '.join(conflictos[:5])}"
                + ("…" if len(conflictos) > 5 else "")
                + ". Libérelas primero o asigne otras."
            )

        nuevas = []
        for _, r in ordenes.iterrows():
            a = Asignacion(
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
            self.asignaciones[a.orden_trabajo] = a
            nuevas.append(a)
        return nuevas

    def liberar(self, orden_trabajo: str) -> None:
        self.asignaciones.pop(orden_trabajo, None)

    def de_usuario(self, usuario: str, *, estados: set[EstadoOrden] | None = None
                   ) -> list[Asignacion]:
        return [a for a in self.asignaciones.values()
                if a.asignado_a == usuario
                and (estados is None or a.estado in estados)]

    def transicionar(self, orden_trabajo: str, nuevo: EstadoOrden) -> Asignacion:
        if orden_trabajo not in self.asignaciones:
            raise KeyError(f"La orden '{orden_trabajo}' no está asignada.")
        a = self.asignaciones[orden_trabajo]
        a.transicionar(nuevo)
        return a

    # -- reportes ----------------------------------------------------------
    def resumen_por_usuario(self) -> pd.DataFrame:
        """Carga de trabajo por técnico: para no sobrecargar a uno y dejar
        ocioso a otro."""

        filas = []
        for u in self.usuarios.values():
            asigs = self.de_usuario(u.usuario)
            por_estado = {e.value: 0 for e in EstadoOrden}
            for a in asigs:
                por_estado[a.estado.value] += 1
            filas.append({
                "usuario": u.usuario, "nombre": u.nombre, "rol": u.rol.value,
                "activo": u.activo,
                "dispositivo": "vinculado" if u.token_dispositivo else "sin vincular",
                "ordenes": len(asigs),
                "clientes_a_revisar": sum(a.clientes_a_revisar for a in asigs),
                "recuperable_kwh_mes": round(
                    sum(a.recuperable_kwh_mes for a in asigs), 1),
                **por_estado,
            })
        return pd.DataFrame(filas)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([a.to_dict() for a in self.asignaciones.values()])


def _f(v) -> float | None:
    try:
        x = float(v)
        return None if pd.isna(x) else x
    except (TypeError, ValueError):
        return None
