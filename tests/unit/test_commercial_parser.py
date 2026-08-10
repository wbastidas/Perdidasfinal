"""Pruebas del parser comercial (§6.4).

Cubre el caso normativo del registro de ejemplo: '1.302' -> 1302 y
'621077.988000000' -> 621077.988, y el aborto ante orientación temporal invertida.
"""

import numpy as np
import pytest

from ptnt.io.commercial_parser import (
    CommercialParseError,
    build_month_axis,
    parse_coord_value,
    parse_kwh_value,
    verificar_orientacion,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("1.302", 1302.0),   # punto = separador de miles
        ("963", 963.0),
        ("0", 0.0),          # cero legítimo, no nulo
        ("1.234.567", 1234567.0),
        ("12,5", 12.5),      # coma decimal
    ],
)
def test_parse_kwh(raw, esperado):
    assert parse_kwh_value(raw, miles=".", decimal=",") == esperado


@pytest.mark.unit
def test_parse_kwh_vacio_es_none():
    assert parse_kwh_value("") is None
    assert parse_kwh_value("   ") is None
    assert parse_kwh_value("nan") is None


@pytest.mark.unit
def test_parse_coordenada_punto_decimal():
    assert parse_coord_value("621077.988000000") == 621077.988
    assert parse_coord_value("9755979.41000000") == 9755979.41


@pytest.mark.unit
def test_eje_meses_antiguo_primero():
    meses = build_month_axis("2026-05-01", 36, "antiguo_primero")
    assert len(meses) == 36
    assert meses[-1].isoformat() == "2026-05-01"   # KWH_36 = mes final
    assert meses[0].isoformat() == "2023-06-01"     # 36 meses antes
    assert meses == sorted(meses)


@pytest.mark.unit
def test_eje_meses_reciente_primero():
    meses = build_month_axis("2026-05-01", 36, "reciente_primero")
    assert meses[0].isoformat() == "2026-05-01"
    assert meses == sorted(meses, reverse=True)


@pytest.mark.unit
def test_orientacion_invertida_detectada():
    # Serie donde el mes reciente (según eje) correlaciona MAL con CLIULTCONM
    rng = np.random.default_rng(0)
    n = 200
    reciente = rng.uniform(50, 500, n)
    antiguo = reciente * 0.1 + rng.normal(0, 1, n)
    # matriz con 2 columnas: col0 = antiguo, col1 = reciente
    matriz = np.column_stack([antiguo, reciente])
    import datetime as dt

    meses = [dt.date(2025, 1, 1), dt.date(2025, 2, 1)]  # idx1 es el reciente
    # ultimo_consumo se parece al 'antiguo' -> orientación configurada errónea
    ultimo = antiguo
    ok, _ = verificar_orientacion(matriz, meses, ultimo)
    assert ok is False


@pytest.mark.unit
def test_parseo_csv_completo(synth_csv):
    """Parsea el CSV sintético y valida el formato largo (36 filas por cuenta)."""

    from ptnt.config.loader import load_config
    from ptnt.io.commercial_parser import parse_commercial_csv

    cfg = load_config("config/base.yaml")
    res = parse_commercial_csv(synth_csv.ruta_csv, cfg.comercial)
    assert res.n_cuentas == 600
    assert len(res.meses) == 36
    # 36 filas por cuenta en formato largo
    por_cuenta = res.consumo.groupby("contract_account").size()
    assert (por_cuenta == 36).all()
    # el grupo de lectura (CLIRLSCOD) se cargó
    assert res.clientes["grupo_lectura"].notna().any()


@pytest.mark.unit
def test_mes_final_invalido_falla():
    from ptnt.io.commercial_parser import build_month_axis

    with pytest.raises(CommercialParseError):
        build_month_axis("no-es-fecha", 36, "antiguo_primero")
