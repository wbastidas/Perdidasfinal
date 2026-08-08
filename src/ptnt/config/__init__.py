"""Capa de configuración: modelos pydantic + carga de YAML con validación estricta.

Principio rector (§13 de la especificación): *ningún valor de volumetría, costo,
factor, tarifa o umbral está escrito en el código*. Todo se carga desde YAML y se
valida. Los parámetros físicos obligatorios no tienen valor por defecto: si
faltan, el arranque falla con un error explícito que nombra el parámetro.
"""

from ptnt.config.loader import ConfigError, load_config
from ptnt.config.models import (
    AlumbradoConfig,
    AppConfig,
    AveragingConfig,
    BalanceConfig,
    CargabilidadConfig,
    CatalogosConfig,
    ClaseTarifaria,
    ComercialConfig,
    FlujoConfig,
    FuenteConfig,
    LoadConfig,
    PerdidasConfig,
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
    "CatalogosConfig",
    "PerdidasConfig",
    "AlumbradoConfig",
    "BalanceConfig",
    "CargabilidadConfig",
    "FlujoConfig",
    "load_config",
    "ConfigError",
]
