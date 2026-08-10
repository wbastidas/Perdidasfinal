"""Pérdidas en medidores (§8.4).

```
E_medidores = Σ_clientes  P_medidor(tipo) · t
```
Término pequeño pero sistemático; a escala de millones de clientes no es
despreciable.
"""

from __future__ import annotations


def meter_losses_kwh(
    tipos: list[str], hours: float, watts_por_tipo: dict[str, float]
) -> float:
    """Energía total perdida por los medidores del conjunto (kWh)."""

    default = watts_por_tipo.get("_default", 1.0)
    total_w = sum(watts_por_tipo.get(t, default) for t in tipos)
    return total_w * hours / 1000.0
