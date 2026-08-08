"""Resolución de secretos desde el entorno.

Las credenciales de las bases de origen no viven en el YAML ni en el código.
El YAML solo declara *el nombre de la variable de entorno* que las contiene
(``usuario_env``, ``password_env``, ``dsn_env``). Aquí se resuelven, con errores
explícitos si faltan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ptnt.config.models import FuenteConfig, TipoFuente


class SecretError(Exception):
    """Un secreto requerido no está disponible en el entorno."""


def get_secret(env_var: str, *, requerido: bool = True) -> str | None:
    """Devuelve el valor de una variable de entorno.

    Nunca registra el valor. Si es requerido y falta, lanza ``SecretError`` con
    el nombre de la variable (no su valor).
    """

    valor = os.environ.get(env_var)
    if valor is None or valor == "":
        if requerido:
            raise SecretError(
                f"Falta el secreto en la variable de entorno '{env_var}'. "
                "Defínala antes de ejecutar (ver docs/SEGURIDAD.md)."
            )
        return None
    return valor


@dataclass(frozen=True)
class SourceCredentials:
    usuario: str | None
    password: str | None
    dsn: str | None

    def __repr__(self) -> str:  # evita fugar la contraseña en logs/trazas
        u = self.usuario or "-"
        return f"SourceCredentials(usuario={u!r}, password=***, dsn={'***' if self.dsn else '-'})"


def resolve_source_credentials(fuente: FuenteConfig) -> SourceCredentials:
    """Resuelve las credenciales de una fuente SQL desde el entorno.

    Para fuentes de archivo (csv/parquet/duckdb) devuelve credenciales vacías.
    """

    archivo = {TipoFuente.CSV, TipoFuente.PARQUET, TipoFuente.DUCKDB}
    if fuente.tipo in archivo:
        return SourceCredentials(usuario=None, password=None, dsn=None)

    dsn = get_secret(fuente.dsn_env, requerido=False) if fuente.dsn_env else None
    if dsn:
        return SourceCredentials(usuario=None, password=None, dsn=dsn)

    if not fuente.usuario_env or not fuente.password_env:
        raise SecretError(
            f"fuente '{fuente.nombre}': debe declarar 'usuario_env' y "
            "'password_env' (o 'dsn_env')."
        )
    usuario = get_secret(fuente.usuario_env)
    password = get_secret(fuente.password_env)
    return SourceCredentials(usuario=usuario, password=password, dsn=None)
