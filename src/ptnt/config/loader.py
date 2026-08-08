"""Carga y validación de la configuración YAML.

El objetivo de esta capa es que un parámetro obligatorio ausente produzca un
error explícito y accionable (nombre del parámetro + ruta en el YAML), en lugar
de un valor por defecto silencioso que corrompería un cálculo físico.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ptnt.config.models import AppConfig


class ConfigError(Exception):
    """Error de configuración con mensaje orientado al operador."""


def _formatear_errores(exc: ValidationError, archivo: Path) -> str:
    lineas = [f"Configuración inválida en '{archivo}':"]
    for err in exc.errors():
        ruta = ".".join(str(p) for p in err["loc"]) or "(raíz)"
        tipo = err["type"]
        msg = err["msg"]
        if tipo == "missing":
            lineas.append(f"  • FALTA parámetro obligatorio: '{ruta}'")
        else:
            lineas.append(f"  • '{ruta}': {msg}")
    return "\n".join(lineas)


def load_config(ruta: str | Path) -> AppConfig:
    """Carga un YAML y devuelve un ``AppConfig`` validado.

    Lanza ``ConfigError`` con detalle legible si el archivo no existe, no es YAML
    válido o falta cualquier parámetro obligatorio.
    """

    archivo = Path(ruta)
    if not archivo.exists():
        raise ConfigError(f"Archivo de configuración no encontrado: '{archivo}'")

    try:
        with archivo.open("r", encoding="utf-8") as fh:
            crudo: Any = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # pragma: no cover - depende de YAML malformado
        raise ConfigError(f"YAML malformado en '{archivo}': {exc}") from exc

    if not isinstance(crudo, dict):
        raise ConfigError(f"El YAML raíz de '{archivo}' debe ser un mapeo (dict).")

    try:
        return AppConfig.model_validate(crudo)
    except ValidationError as exc:
        raise ConfigError(_formatear_errores(exc, archivo)) from exc


def config_hash(cfg: AppConfig) -> str:
    """Hash determinístico de la configuración efectiva.

    Se usa para invalidación de checkpoints y para el snapshot de cada corrida
    (``meta.run.config_hash``). Es estable ante reordenamientos de claves.
    """

    data = cfg.model_dump(mode="json")
    serial = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()
