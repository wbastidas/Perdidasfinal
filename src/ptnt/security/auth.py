"""Autenticación local: hashing de contraseñas y almacén de usuarios.

Diseñado para servidor interno. Usa bcrypt si está disponible (extra
``security``); si no, cae a PBKDF2-HMAC-SHA256 de la librería estándar, de modo
que la autenticación funciona incluso en una instalación mínima. En ningún caso
se almacena la contraseña en claro.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _secrets
from dataclasses import dataclass, field, fields
from pathlib import Path

try:  # bcrypt es preferible; opcional
    from passlib.hash import bcrypt as _bcrypt  # type: ignore

    _HAS_BCRYPT = True
except Exception:  # pragma: no cover - depende de instalación
    _HAS_BCRYPT = False

_PBKDF2_ROUNDS = 240_000
_PBKDF2_PREFIX = "pbkdf2_sha256"


class AuthError(Exception):
    """Fallo de autenticación o de gestión de usuarios."""


def hash_password(password: str) -> str:
    """Devuelve un hash verificable de la contraseña.

    Nunca devuelve ni registra la contraseña. Formato:
      * ``bcrypt`` -> cadena passlib estándar.
      * fallback   -> ``pbkdf2_sha256$rounds$salt_hex$hash_hex``.
    """

    if not password or len(password) < 8:
        raise AuthError("La contraseña debe tener al menos 8 caracteres.")
    if _HAS_BCRYPT:
        return _bcrypt.hash(password)
    salt = _secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifica una contraseña contra su hash, en tiempo constante."""

    if not hashed:
        return False
    if hashed.startswith(_PBKDF2_PREFIX + "$"):
        try:
            _, rounds_s, salt_hex, hash_hex = hashed.split("$")
            rounds = int(rounds_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except (ValueError, TypeError):
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
        return hmac.compare_digest(dk, expected)
    if _HAS_BCRYPT:
        try:
            return bool(_bcrypt.verify(password, hashed))
        except Exception:
            return False
    return False


@dataclass
class User:
    username: str
    password_hash: str
    role: str = "viewer"  # viewer | analyst | admin
    disabled: bool = False
    # Unidades de negocio que puede ver. Vacío **no** significa «todas»: un
    # usuario sin unidad asignada no ve nada, y eso es deliberado. Lo contrario
    # convierte un alta a medias en una fuga del padrón de otra unidad.
    unidades: list[str] = field(default_factory=list)
    # La oficina central ve todas las unidades y elige cuál analizar.
    matriz: bool = False


class UserStore:
    """Almacén de usuarios en JSON (solo hashes).

    El archivo se crea con permisos restrictivos (0600 en POSIX). En Windows los
    permisos se gestionan por ACL del directorio de configuración.
    """

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self._users: dict[str, User] = {}
        if self.ruta.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.ruta.read_text(encoding="utf-8"))
        # Se filtran las claves desconocidas en vez de reventar: un archivo de
        # una versión anterior no puede dejar a nadie sin poder entrar, y uno de
        # una versión posterior tampoco.
        campos = {f.name for f in fields(User)}
        self._users = {}
        for u in data.get("usuarios", []):
            datos = {k: v for k, v in u.items() if k in campos}
            self._users[datos["username"]] = User(**datos)

    def _save(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "usuarios": [
                {
                    "username": u.username,
                    "password_hash": u.password_hash,
                    "role": u.role,
                    "disabled": u.disabled,
                    "unidades": list(u.unidades),
                    "matriz": u.matriz,
                }
                for u in self._users.values()
            ]
        }
        self.ruta.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        try:
            os.chmod(self.ruta, 0o600)
        except (OSError, NotImplementedError):  # pragma: no cover - Windows
            pass

    def add_user(self, username: str, password: str, role: str = "viewer",
                 *, unidades: list[str] | None = None,
                 matriz: bool = False) -> None:
        if role not in {"viewer", "analyst", "admin"}:
            raise AuthError(f"rol inválido: {role}")
        if username in self._users:
            raise AuthError(f"el usuario '{username}' ya existe")
        # Un administrador ve todas las unidades por definición: puede crear
        # usuarios, así que podría asignarse cualquiera. Negarle los datos sería
        # teatro, y obligar a declararlo cada vez, ruido.
        if role == "admin" and not unidades:
            matriz = True
        # No se rechaza el alta sin unidad: crear primero y asignar después es
        # un flujo legítimo, y el control de verdad está donde se leen los datos
        # —un usuario sin alcance no ve nada—. Quien crea desde la CLI recibe el
        # aviso ahí, que es donde se puede corregir en el momento.
        self._users[username] = User(
            username, hash_password(password), role,
            unidades=list(unidades or []), matriz=matriz)
        self._save()

    def set_unidades(self, username: str, unidades: list[str], *,
                     matriz: bool | None = None) -> None:
        """Cambia el alcance de un usuario ya creado."""

        u = self._users.get(username)
        if u is None:
            raise AuthError(f"usuario '{username}' inexistente")
        if matriz is not None:
            u.matriz = matriz
        u.unidades = list(unidades)
        self._save()

    def usuarios(self) -> list[User]:
        return sorted(self._users.values(), key=lambda u: u.username)

    def set_password(self, username: str, password: str) -> None:
        if username not in self._users:
            raise AuthError(f"usuario '{username}' inexistente")
        self._users[username].password_hash = hash_password(password)
        self._save()

    def authenticate(self, username: str, password: str) -> User | None:
        user = self._users.get(username)
        if user is None:
            # Verificación dummy para no filtrar existencia por tiempo de respuesta
            verify_password(password, hash_password("x" * 12))
            return None
        if user.disabled:
            return None
        if verify_password(password, user.password_hash):
            return user
        return None

    def __contains__(self, username: str) -> bool:
        return username in self._users

    def __len__(self) -> int:
        return len(self._users)
