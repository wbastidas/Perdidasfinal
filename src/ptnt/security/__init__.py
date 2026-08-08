"""Capa de seguridad: manejo de secretos, autenticación y control de acceso.

Reglas duras:
  * Ninguna credencial se guarda en YAML ni en código; todo secreto se resuelve
    desde variables de entorno (o un gestor externo) en tiempo de ejecución.
  * Las contraseñas de usuario se almacenan solo como hash (bcrypt / PBKDF2).
  * El visor web valida red de origen (CIDR) además de credenciales.
"""

from ptnt.security.auth import (
    AuthError,
    UserStore,
    hash_password,
    verify_password,
)
from ptnt.security.secrets import SecretError, get_secret, resolve_source_credentials

__all__ = [
    "AuthError",
    "UserStore",
    "hash_password",
    "verify_password",
    "SecretError",
    "get_secret",
    "resolve_source_credentials",
]
