"""Factor de pérdidas y corrección de resistencia por temperatura (§8.1–8.2)."""

from __future__ import annotations


def loss_factor(load_factor: float, k: float) -> float:
    """Factor de pérdidas ``F_p = k·F_c + (1−k)·F_c²`` (§8.1).

    Propiedad física garantizada: ``F_c² ≤ F_p ≤ F_c`` para ``k ∈ [0, 1]`` y
    ``F_c ∈ [0, 1]``. Casos límite:
      * carga plana todo el tiempo: ``F_p = F_c``  (k=1)
      * pico agudo + constante:     ``F_p = F_c²`` (k=0)
    """

    if not 0.0 <= load_factor <= 1.0:
        raise ValueError("F_c (factor de carga) debe estar en [0, 1]")
    if not 0.0 <= k <= 1.0:
        raise ValueError("k debe estar en [0, 1]")
    return k * load_factor + (1.0 - k) * load_factor**2


def resistance_at_temp(r_20: float, t_op: float, alpha: float, t_ref: float = 20.0) -> float:
    """Resistencia corregida por temperatura: ``R_T = R_20·[1 + α·(T_op − T_ref)]``."""

    return r_20 * (1.0 + alpha * (t_op - t_ref))
