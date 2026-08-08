"""Pérdidas y capacidad de transformadores (§8.3).

Dos piezas críticas y explícitamente cubiertas por tests obligatorios:

1. **La pérdida en vacío (P0) es constante y NO se afecta por el factor de
   pérdidas.** Aplicarle F_p subestima severamente las pérdidas en redes con
   muchos transformadores subutilizados.

2. **La capacidad de un puesto no es la suma ingenua de kVA de sus unidades.**
   Depende de la configuración del banco (delta abierto V-V da √3·kVA_u, etc.).
"""

from __future__ import annotations

import math
from enum import Enum


class BankConfig(str, Enum):
    UNIDAD_SIMPLE = "UNIDAD_SIMPLE"          # 1 unidad
    BANCO_3 = "BANCO_3"                      # 3 unidades iguales (estrella/delta)
    DELTA_ABIERTO = "DELTA_ABIERTO"          # 2 unidades (V-V)
    BANCO_DESIGUAL = "BANCO_DESIGUAL"        # 2-3 unidades desiguales
    DELTA_4H = "DELTA_4H"                    # delta 4 hilos (una mayor)


def bank_capacity_kva(kvas: list[float], config: BankConfig) -> float:
    """Capacidad trifásica del puesto según la configuración del banco (§8.3.2).

    | Configuración        | Capacidad                         |
    |----------------------|-----------------------------------|
    | Unidad simple        | kVA_1                             |
    | Banco 3 iguales      | 3 · kVA_u                         |
    | Delta abierto (V-V)  | √3 · kVA_u ≈ 1.732 · kVA_u        |
    | Banco desigual       | limitada por la unidad menor      |
    | Delta 4 hilos        | suma con derating de la mayor     |
    """

    if not kvas:
        raise ValueError("se requiere al menos una unidad")
    kvas = [float(k) for k in kvas]

    if config == BankConfig.UNIDAD_SIMPLE:
        return kvas[0]
    if config == BankConfig.BANCO_3:
        # capacidad = 3 × unidad (usa el promedio si difieren levemente)
        return 3.0 * (sum(kvas) / len(kvas))
    if config == BankConfig.DELTA_ABIERTO:
        # V-V con 2 unidades: √3 × unidad (la menor manda si difieren)
        return math.sqrt(3.0) * min(kvas)
    if config == BankConfig.BANCO_DESIGUAL:
        # limitada por la unidad menor × nº de unidades
        return min(kvas) * len(kvas)
    if config == BankConfig.DELTA_4H:
        # suma con derating (~5 %) de la unidad de mayor carga
        total = sum(kvas)
        return total - 0.05 * max(kvas)
    raise ValueError(f"configuración de banco desconocida: {config}")


def infer_bank_config(n_units: int, kvas: list[float]) -> tuple[BankConfig, float]:
    """Infiere la configuración del banco y una confianza [0,1] a partir del
    número de unidades y sus kVA. Heurística de respaldo cuando el
    ``CODIGOESTRUCTURA`` no resuelve la configuración."""

    if n_units == 1:
        return BankConfig.UNIDAD_SIMPLE, 1.0
    if n_units == 2:
        return BankConfig.DELTA_ABIERTO, 0.7
    if n_units == 3:
        if kvas and (max(kvas) - min(kvas)) / max(kvas) < 0.05:
            return BankConfig.BANCO_3, 0.9
        return BankConfig.DELTA_4H, 0.6
    return BankConfig.BANCO_DESIGUAL, 0.5


def transformer_unit_loss_kwh(
    p0_kw: float,
    pk_kw: float,
    s_max_kva: float,
    s_nom_kva: float,
    hours: float,
    loss_factor_value: float,
    *,
    f_desbalance: float = 1.02,
    apply_loss_factor_to_no_load: bool = False,
) -> tuple[float, float]:
    """Energía perdida por una unidad de transformador (§8.3.1).

    ```
    E = P0 · t                                   (vacío, constante)
      + Pk · (S_max/S_nom)² · t · F_p · F_desb   (carga)
    ```

    Devuelve ``(E_total_kwh, E_vacio_kwh)``. ``apply_loss_factor_to_no_load``
    DEBE ser ``False``; existe solo para poder demostrar en un test que ponerlo en
    ``True`` cambia (mal) el resultado.
    """

    if s_nom_kva <= 0:
        raise ValueError("S_nom debe ser > 0")
    # Vacío: constante, NO se multiplica por F_p
    if apply_loss_factor_to_no_load:
        e_vacio = p0_kw * hours * loss_factor_value  # (incorrecto, solo para test)
    else:
        e_vacio = p0_kw * hours
    carga_ratio = (s_max_kva / s_nom_kva) ** 2
    e_carga = pk_kw * carga_ratio * hours * loss_factor_value * f_desbalance
    return e_vacio + e_carga, e_vacio
