"""Agregación multinivel del riesgo de PNT (§11.4).

```
Riesgo_zona   = f(residuo de balance de zona, densidad de anomalías, confiabilidad)
Riesgo_puesto = g(riesgo_zona, residuo del puesto, cargabilidad incoherente, ...)
Riesgo_unidad = h(consenso de señales de cliente, riesgo_puesto)
```

La coincidencia de una unidad sospechosa en un puesto con residuo alto dentro de
una zona con residuo elevado es la señal de mayor valor predictivo, y domina el
ranking. Una unidad con score alto en una zona cuyo balance cierra es más probable
un problema de datos: se penaliza con el índice de confiabilidad.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate_risk(
    ranking_unidad: pd.DataFrame,
    *,
    riesgo_puesto: dict[str, float] | None = None,
    puesto_por_cliente: dict[str, str] | None = None,
    confiabilidad_zona: dict[str, float] | None = None,
    zona_por_cliente: dict[str, str] | None = None,
    peso_puesto: float = 0.35,
    umbral_confiabilidad_penalizacion: float = 50.0,
) -> pd.DataFrame:
    """Combina el score de consenso de señales de cliente con el riesgo del puesto
    y penaliza por baja confiabilidad de la zona.

    ``ranking_unidad`` debe tener ``contract_account`` y ``score`` (0–1). Devuelve
    el mismo DataFrame con ``risk_score`` ajustado, ``data_problem_flag`` y el
    riesgo del puesto/confiabilidad usados.
    """

    df = ranking_unidad.copy()
    cuentas = df["contract_account"].astype(str)

    rp = np.zeros(len(df))
    if riesgo_puesto and puesto_por_cliente:
        rp = cuentas.map(lambda c: riesgo_puesto.get(puesto_por_cliente.get(c, ""), 0.0)).to_numpy()

    consenso = df["score"].to_numpy(dtype=float)
    risk = (1 - peso_puesto) * consenso + peso_puesto * rp

    # Penalización por baja confiabilidad de zona
    data_problem = np.zeros(len(df), dtype=bool)
    if confiabilidad_zona and zona_por_cliente:
        conf = cuentas.map(lambda c: confiabilidad_zona.get(zona_por_cliente.get(c, ""), 100.0)).to_numpy()
        baja = conf < umbral_confiabilidad_penalizacion
        # score alto en zona de baja confiabilidad => probable problema de datos
        data_problem = baja & (consenso > np.nanpercentile(consenso, 80) if len(consenso) else False)
        factor = np.where(baja, conf / 100.0, 1.0)
        risk = risk * factor
        df["zone_confidence"] = conf

    df["risk_score"] = risk
    df["riesgo_puesto"] = rp
    df["data_problem_flag"] = data_problem
    df = df.sort_values("risk_score", ascending=False).reset_index(drop=True)
    df["risk_rank"] = np.arange(1, len(df) + 1)
    return df
