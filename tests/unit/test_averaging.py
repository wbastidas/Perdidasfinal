"""Pruebas del mecanismo de promedio multi-mes."""

import numpy as np
import pytest

from ptnt.config.models import AveragingConfig, MetodoPromedio
from ptnt.load.averaging import average_series


@pytest.mark.unit
def test_media_simple():
    cfg = AveragingConfig(metodo=MetodoPromedio.MEDIA, ventana_meses=12)
    rep, n, cv = average_series([10.0] * 12, cfg)
    assert rep == pytest.approx(10.0)
    assert n == 12


@pytest.mark.unit
def test_media_recortada_ignora_outliers():
    """La media recortada es robusta frente a un pico atípico (el último mes)."""

    serie = [100.0] * 11 + [10_000.0]  # último mes atípico
    cfg = AveragingConfig(
        metodo=MetodoPromedio.MEDIA_RECORTADA, ventana_meses=12, recorte_pct=10
    )
    rep, _, _ = average_series(serie, cfg)
    media_simple = np.mean(serie)
    # el promedio robusto debe estar mucho más cerca de 100 que la media simple
    assert rep < media_simple
    assert rep == pytest.approx(100.0, rel=0.2)


@pytest.mark.unit
def test_mediana_muy_robusta():
    serie = [100.0] * 11 + [10_000.0]
    cfg = AveragingConfig(metodo=MetodoPromedio.MEDIANA, ventana_meses=12)
    rep, _, _ = average_series(serie, cfg)
    assert rep == pytest.approx(100.0)


@pytest.mark.unit
def test_media_ponderada_favorece_reciente():
    # serie creciente: la ponderada por recencia debe superar a la media simple
    serie = list(range(1, 13))  # 1..12, reciente = 12
    cfg = AveragingConfig(
        metodo=MetodoPromedio.MEDIA_PONDERADA, ventana_meses=12, half_life_meses=3
    )
    rep, _, _ = average_series(serie, cfg)
    assert rep > np.mean(serie)


@pytest.mark.unit
def test_ventana_limita_a_ultimos_n():
    serie = [1.0] * 24 + [100.0] * 12  # últimos 12 = 100
    cfg = AveragingConfig(metodo=MetodoPromedio.MEDIA, ventana_meses=12)
    rep, n, _ = average_series(serie, cfg)
    assert rep == pytest.approx(100.0)
    assert n == 12


@pytest.mark.unit
def test_excluir_ceros_suspendidos():
    serie = [50.0] * 9 + [0.0, 0.0, 0.0]
    cfg = AveragingConfig(
        metodo=MetodoPromedio.MEDIA, ventana_meses=12, excluir_ceros_suspendidos=True
    )
    rep, n, _ = average_series(serie, cfg, suspendido=True)
    assert rep == pytest.approx(50.0)  # los ceros del suspendido se excluyen
    assert n == 9


@pytest.mark.unit
def test_min_meses_validos_marca_no_confiable():
    from ptnt.load.averaging import average_consumption
    import pandas as pd

    # cuenta con solo 2 meses válidos
    df = pd.DataFrame(
        {
            "contract_account": ["A"] * 12,
            "period": pd.date_range("2025-01-01", periods=12, freq="MS").date,
            "kwh": [np.nan] * 10 + [5.0, 6.0],
        }
    )
    cfg = AveragingConfig(metodo=MetodoPromedio.MEDIA, min_meses_validos=3)
    res = average_consumption(df, cfg)
    fila = res.por_cliente.iloc[0]
    assert fila["n_meses_validos"] == 2
    assert bool(fila["confiable"]) is False
