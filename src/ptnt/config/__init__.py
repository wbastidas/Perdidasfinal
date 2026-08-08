"""Capa de configuración: modelos pydantic + carga de YAML con validación estricta.

Principio rector (§13 de la especificación): *ningún valor de volumetría, costo,
factor, tarifa o umbral está escrito en el código*. Todo se carga desde YAML y se
valida. Los parámetros físicos obligatorios no tienen valor por defecto: si
faltan, el arranque falla con un error explícito que nombra el parámetro.
"""

from ptnt.config.loader import ConfigError, load_config
from ptnt.config.models import (
    AppConfig,
    AveragingConfig,
    ClaseTarifaria,
    ComercialConfig,
    FuenteConfig,
    LoadConfig,
    SecurityConfig,
    SignalsConfig,
)

__all__ = [
    "AppConfig",
    "AveragingConfig",
    "ClaseTarifaria",
    "ComercialConfig",
    "FuenteConfig",
    "LoadConfig",
    "SecurityConfig",
    "SignalsConfig",
    "load_config",
    "ConfigError",
]
