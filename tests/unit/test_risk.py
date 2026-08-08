"""Pruebas de agregación multinivel del riesgo (§11.4) e inferencia de banco."""

import pandas as pd
import pytest

from ptnt.grid.risk import aggregate_risk
from ptnt.losses.transformers import BankConfig, infer_bank_config


@pytest.mark.unit
def test_agregacion_riesgo_combina_puesto():
    ranking = pd.DataFrame({
        "contract_account": ["a", "b", "c"],
        "score": [0.9, 0.5, 0.1],
    })
    riesgo_puesto = {"P1": 0.8, "P2": 0.0}
    puesto_por_cliente = {"a": "P1", "b": "P2", "c": "P1"}
    out = aggregate_risk(
        ranking, riesgo_puesto=riesgo_puesto, puesto_por_cliente=puesto_por_cliente,
        peso_puesto=0.5,
    )
    # 'a': 0.5*0.9 + 0.5*0.8 = 0.85 ; 'c': 0.5*0.1 + 0.5*0.8 = 0.45 ; 'b': 0.25
    fila_a = out[out["contract_account"] == "a"].iloc[0]
    assert fila_a["risk_score"] == pytest.approx(0.85)
    assert "risk_rank" in out.columns
    assert out.iloc[0]["contract_account"] == "a"  # 'a' domina


@pytest.mark.unit
def test_penalizacion_baja_confiabilidad_marca_problema_datos():
    ranking = pd.DataFrame({
        "contract_account": ["a", "b", "c", "d", "e"],
        "score": [0.95, 0.9, 0.4, 0.3, 0.2],
    })
    zona_por_cliente = {c: "Z1" for c in ["a", "b", "c", "d", "e"]}
    confiabilidad = {"Z1": 30.0}  # zona de baja confiabilidad
    out = aggregate_risk(
        ranking, confiabilidad_zona=confiabilidad, zona_por_cliente=zona_por_cliente,
        umbral_confiabilidad_penalizacion=50.0,
    )
    # el score alto en zona de baja confiabilidad se marca como problema de datos
    assert out["data_problem_flag"].any()
    # el riesgo se reduce por el factor de confiabilidad
    fila_a = out[out["contract_account"] == "a"].iloc[0]
    assert fila_a["risk_score"] < 0.95


@pytest.mark.unit
@pytest.mark.parametrize("n,kvas,esperado", [
    (1, [50], BankConfig.UNIDAD_SIMPLE),
    (2, [50, 50], BankConfig.DELTA_ABIERTO),
    (3, [25, 25, 25], BankConfig.BANCO_3),
    (3, [50, 25, 25], BankConfig.DELTA_4H),
    (4, [50, 50, 50, 50], BankConfig.BANCO_DESIGUAL),
])
def test_inferencia_config_banco(n, kvas, esperado):
    config, conf = infer_bank_config(n, kvas)
    assert config == esperado
    assert 0.0 <= conf <= 1.0
