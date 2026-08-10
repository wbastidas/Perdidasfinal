"""Jerarquía organizacional, carga parcial e histórico de balance.

Los tres módulos comparten una misma regla, que es lo que estas pruebas fijan:
**la energía se puede agregar; la credibilidad no**. Un consolidado se puede
sumar hacia arriba, pero si uno solo de sus componentes no es verificable, el
consolidado tampoco lo es. Presentar el número de una unidad de negocio como
"medido" cuando parte de sus alimentadores es estimación es el fallo de
credibilidad más caro del proyecto, y aquí es imposible por construcción.
"""

import pandas as pd
import pytest

from ptnt.ingest.partial import (
    AlcanceCarga,
    EstadoCobertura,
    Insumo,
    marcar_balance_parcial,
)
from ptnt.org.hierarchy import (
    agregar_balance,
    jerarquia_desde_alimentadores,
    load_jerarquia,
)
from ptnt.store.history import HistoricoBalance


# --------------------------------------------------------------------------- #
# Jerarquía organizacional
# --------------------------------------------------------------------------- #
def _catalogo() -> pd.DataFrame:
    return pd.DataFrame({
        "feeder_code": ["GYE-01", "GYE-02", "GYE-03", "MIL-01"],
        "subestacion": ["Pascuales", "Pascuales", "Trinitaria", "Milagro Sur"],
        "unidad_negocio": ["GUAYAQUIL", "GUAYAQUIL", "GUAYAQUIL", "MILAGRO"],
    })


def _balances() -> pd.DataFrame:
    return pd.DataFrame([
        {"alimentador": "GYE-01", "entrada_kwh": 700_000, "pnt_kwh": 30_000,
         "clientes": 1500, "tipo_balance": "MEDIDO"},
        {"alimentador": "GYE-02", "entrada_kwh": 1_700_000, "pnt_kwh": 100_000,
         "clientes": 1600, "tipo_balance": "MEDIDO"},
        {"alimentador": "GYE-03", "entrada_kwh": 4_400_000, "pnt_kwh": 270_000,
         "clientes": 1700, "tipo_balance": "INDICATIVO"},
        {"alimentador": "MIL-01", "entrada_kwh": 625_000, "pnt_kwh": 50_000,
         "clientes": 900, "tipo_balance": "MEDIDO"},
    ])


@pytest.mark.unit
def test_carga_el_catalogo_y_resuelve_la_jerarquia():
    j = load_jerarquia(_catalogo())
    assert len(j) == 4
    assert j.unidades == ["GUAYAQUIL", "MILAGRO"]
    assert j.subestacion_de("GYE-01") == "Pascuales"
    assert j.unidad_de("MIL-01") == "MILAGRO"
    assert j.alimentadores_de(subestacion="Pascuales") == ["GYE-01", "GYE-02"]
    assert j.subestaciones_de("GUAYAQUIL") == ["Pascuales", "Trinitaria"]


@pytest.mark.unit
def test_catalogo_sin_columnas_obligatorias_falla_nombrandolas():
    with pytest.raises(ValueError, match="subestacion"):
        load_jerarquia(pd.DataFrame({"feeder_code": ["A"],
                                     "unidad_negocio": ["U"]}))


@pytest.mark.unit
def test_alimentador_repetido_se_reporta_y_no_se_duplica():
    """Un alimentador no puede pertenecer a dos subestaciones a la vez."""

    df = pd.concat([_catalogo(), _catalogo().head(1)], ignore_index=True)
    df.loc[4, "subestacion"] = "Otra"
    j = load_jerarquia(df)
    assert len(j) == 4
    assert j.subestacion_de("GYE-01") == "Pascuales"
    assert any("repetido" in a for a in j.advertencias)


@pytest.mark.unit
def test_jerarquia_inferida_advierte_que_la_subestacion_no_es_utilizable():
    """Sin catálogo se puede deducir la UN del prefijo, pero inventar una
    subestación daría consolidados que parecen correctos y no lo son."""

    j = jerarquia_desde_alimentadores(["GYE-01", "MIL-02"])
    assert j.unidad_de("GYE-01") == "GYE"
    assert j.subestacion_de("GYE-01") == "SIN_SUBESTACION"
    assert any("SIN_SUBESTACION" in a for a in j.advertencias)


@pytest.mark.unit
def test_la_energia_se_suma_hacia_arriba():
    ag = agregar_balance(_balances(), load_jerarquia(_catalogo()))
    un = ag["UNIDAD_NEGOCIO"].set_index("unidad_negocio")
    assert un.loc["GUAYAQUIL", "pnt_kwh"] == pytest.approx(400_000)
    assert un.loc["GUAYAQUIL", "entrada_kwh"] == pytest.approx(6_800_000)
    assert un.loc["GUAYAQUIL", "alimentadores"] == 3


@pytest.mark.unit
def test_los_porcentajes_se_recalculan_no_se_promedian():
    """Un alimentador de 700 MWh al 4,3 % y uno de 4 400 MWh al 6,1 % no dan el
    promedio simple: el consolidado debe pesar por energía."""

    ag = agregar_balance(_balances(), load_jerarquia(_catalogo()))
    un = ag["UNIDAD_NEGOCIO"].set_index("unidad_negocio")
    esperado = 400_000 / 6_800_000 * 100
    assert un.loc["GUAYAQUIL", "pnt_pct"] == pytest.approx(esperado, abs=0.01)

    promedio_simple = (30_000 / 700_000 + 100_000 / 1_700_000
                       + 270_000 / 4_400_000) / 3 * 100
    assert abs(un.loc["GUAYAQUIL", "pnt_pct"] - promedio_simple) > 0.01


@pytest.mark.unit
def test_un_solo_alimentador_indicativo_degrada_todo_el_consolidado():
    """LA REGLA CENTRAL. La energía se suma; la garantía de que ese número es
    verificable, no. Basta un INDICATIVO para que el consolidado no pueda
    presentarse como medido."""

    ag = agregar_balance(_balances(), load_jerarquia(_catalogo()))
    un = ag["UNIDAD_NEGOCIO"].set_index("unidad_negocio")
    se = ag["SUBESTACION"].set_index("subestacion")

    # GYE-03 (Trinitaria) es INDICATIVO
    assert se.loc["Trinitaria", "tipo_balance"] == "INDICATIVO"
    assert un.loc["GUAYAQUIL", "tipo_balance"] == "INDICATIVO"
    assert un.loc["GUAYAQUIL", "alimentadores_indicativos"] == 1
    # Milagro no se contamina: su único alimentador sí es medido
    assert un.loc["MILAGRO", "tipo_balance"] == "MEDIDO"
    assert se.loc["Pascuales", "tipo_balance"] == "MEDIDO"


@pytest.mark.unit
def test_alimentador_sin_catalogo_no_se_pierde_ni_se_asigna_a_ciegas():
    bal = pd.concat([_balances(), pd.DataFrame([
        {"alimentador": "ZZZ-99", "entrada_kwh": 100, "pnt_kwh": 10,
         "clientes": 5, "tipo_balance": "MEDIDO"}])], ignore_index=True)
    ag = agregar_balance(bal, load_jerarquia(_catalogo()))
    assert "SIN_UN" in set(ag["UNIDAD_NEGOCIO"]["unidad_negocio"])
    assert "advertencia" in ag["SUBESTACION"].attrs


# --------------------------------------------------------------------------- #
# Carga parcial
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_cobertura_detecta_lo_que_falta():
    a = AlcanceCarga(universo_alimentadores=["F1", "F2", "F3", "F4"])
    a.registrar(Insumo.PADRON_COMERCIAL, ["F1", "F2", "F3", "F4"])
    a.registrar(Insumo.RED, ["F1", "F2"])

    assert a.cobertura(Insumo.PADRON_COMERCIAL).estado is EstadoCobertura.COMPLETA
    red = a.cobertura(Insumo.RED)
    assert red.estado is EstadoCobertura.PARCIAL
    assert red.pct == pytest.approx(50.0)
    assert red.faltantes == ["F3", "F4"]
    assert a.cobertura(Insumo.CABECERA).estado is EstadoCobertura.VACIA


@pytest.mark.unit
def test_las_cargas_del_mismo_insumo_se_acumulan():
    """Cargar tres subestaciones en tres pasos equivale a cargarlas juntas."""

    a = AlcanceCarga(universo_alimentadores=["F1", "F2", "F3"])
    a.registrar(Insumo.RED, ["F1"])
    a.registrar(Insumo.RED, ["F2"])
    a.registrar(Insumo.RED, ["F3"])
    assert a.cobertura(Insumo.RED).estado is EstadoCobertura.COMPLETA


@pytest.mark.unit
def test_balance_medido_exige_padron_red_y_cabecera_del_mismo_alimentador():
    a = AlcanceCarga(universo_alimentadores=["F1", "F2"])
    a.registrar(Insumo.PADRON_COMERCIAL, ["F1", "F2"])
    a.registrar(Insumo.RED, ["F1", "F2"])
    a.registrar(Insumo.CABECERA, ["F1"])       # a F2 le falta la cabecera
    assert a.listos_para_balance_medido() == {"F1"}


@pytest.mark.unit
def test_alimentador_fuera_del_universo_se_advierte():
    a = AlcanceCarga(universo_alimentadores=["F1"])
    carga = a.registrar(Insumo.RED, ["F1", "INTRUSO"])
    assert any("fuera del universo" in x for x in carga.advertencias)


@pytest.mark.unit
def test_pendientes_es_la_lista_de_trabajo():
    a = AlcanceCarga(universo_alimentadores=["F1", "F2"])
    a.registrar(Insumo.PADRON_COMERCIAL, ["F1", "F2"])
    a.registrar(Insumo.RED, ["F1"])
    pend = a.pendientes()
    assert not pend.empty
    fila = pend[(pend["alimentador"] == "F2") & (pend["insumo_faltante"] == "RED")]
    assert len(fila) == 1


@pytest.mark.unit
def test_alcance_persiste_entre_sesiones(tmp_path):
    ruta = tmp_path / "alcance.json"
    a = AlcanceCarga(universo_alimentadores=["F1", "F2"])
    a.registrar(Insumo.RED, ["F1"], origen="fgdb", registros=120)
    a.save(ruta)

    b = AlcanceCarga.load(ruta)
    assert b.universo_alimentadores == ["F1", "F2"]
    assert b.alimentadores_con(Insumo.RED) == {"F1"}
    assert b.cargas[0].origen == "fgdb"


@pytest.mark.unit
def test_consolidado_incompleto_se_marca_parcial():
    """Un consolidado calculado sobre 1 de 2 alimentadores no es el balance de la
    entidad: es el de uno de sus alimentadores."""

    a = AlcanceCarga(universo_alimentadores=["F1", "F2"])
    for ins in (Insumo.PADRON_COMERCIAL, Insumo.RED, Insumo.CABECERA):
        a.registrar(ins, ["F1"])
    df = pd.DataFrame([{"subestacion": "S1", "alimentadores": 1,
                        "tipo_balance": "MEDIDO", "pnt_kwh": 100}])
    out = marcar_balance_parcial(df, a)
    assert out.loc[0, "tipo_balance"] == "PARCIAL"
    assert out.loc[0, "cobertura_pct"] == pytest.approx(50.0)
    assert "no es el total" in out.loc[0, "nota_cobertura"]


@pytest.mark.unit
def test_consolidado_completo_conserva_su_tipo():
    a = AlcanceCarga(universo_alimentadores=["F1"])
    for ins in (Insumo.PADRON_COMERCIAL, Insumo.RED, Insumo.CABECERA):
        a.registrar(ins, ["F1"])
    df = pd.DataFrame([{"subestacion": "S1", "alimentadores": 1,
                        "tipo_balance": "MEDIDO", "pnt_kwh": 100}])
    out = marcar_balance_parcial(df, a)
    assert out.loc[0, "tipo_balance"] == "MEDIDO"


# --------------------------------------------------------------------------- #
# Histórico
# --------------------------------------------------------------------------- #
def _snapshot(pnt_pct: float, tipo: str = "MEDIDO") -> dict:
    return {
        "UNIDAD_NEGOCIO": pd.DataFrame([{
            "unidad_negocio": "GUAYAQUIL", "entrada_kwh": 1_000_000,
            "pnt_kwh": pnt_pct * 10_000, "pnt_pct": pnt_pct,
            "clientes": 5000, "tipo_balance": tipo,
        }])
    }


@pytest.mark.unit
def test_historico_registra_y_devuelve_la_serie(tmp_path):
    h = HistoricoBalance.load(tmp_path / "h.csv")
    h.registrar(_snapshot(8.0), periodo="2026-01", config_hash="abc")
    h.registrar(_snapshot(6.5), periodo="2026-02", config_hash="abc")
    h.registrar(_snapshot(5.1), periodo="2026-03", config_hash="abc")

    s = h.serie("GUAYAQUIL", nivel="UNIDAD_NEGOCIO")
    assert list(s["periodo"]) == ["2026-01", "2026-02", "2026-03"]
    assert list(s["pnt_pct"]) == [8.0, 6.5, 5.1]


@pytest.mark.unit
def test_recalcular_un_periodo_lo_reemplaza_en_vez_de_duplicarlo(tmp_path):
    """Volver a correr el análisis de un mes es una corrección, no un dato nuevo:
    acumular las dos versiones haría que el histórico mostrara dos valores para
    el mismo mes."""

    h = HistoricoBalance.load(tmp_path / "h.csv")
    h.registrar(_snapshot(8.0), periodo="2026-01")
    h.registrar(_snapshot(7.2), periodo="2026-01")
    s = h.serie("GUAYAQUIL")
    assert len(s) == 1
    assert s.iloc[0]["pnt_pct"] == 7.2


@pytest.mark.unit
def test_comparar_periodos_marca_tendencia_y_comparabilidad(tmp_path):
    h = HistoricoBalance.load(tmp_path / "h.csv")
    h.registrar(_snapshot(9.0), periodo="2026-01", config_hash="v1")
    h.registrar(_snapshot(6.0), periodo="2026-06", config_hash="v1")
    comp = h.comparar_periodos("2026-01", "2026-06", nivel="UNIDAD_NEGOCIO")
    assert comp.iloc[0]["tendencia"] == "MEJORA"
    assert comp.iloc[0]["delta_pnt_pct"] == pytest.approx(-3.0)
    assert bool(comp.iloc[0]["comparable"]) is True


@pytest.mark.unit
def test_cambio_de_configuracion_invalida_la_comparacion(tmp_path):
    """Atribuir a la red una variación que fue de parámetros es mentir con un
    gráfico: la comparación debe marcarse como no comparable."""

    h = HistoricoBalance.load(tmp_path / "h.csv")
    h.registrar(_snapshot(9.0), periodo="2026-01", config_hash="v1")
    h.registrar(_snapshot(6.0), periodo="2026-06", config_hash="v2")
    comp = h.comparar_periodos("2026-01", "2026-06", nivel="UNIDAD_NEGOCIO")
    assert bool(comp.iloc[0]["comparable"]) is False
    assert any("configuraciones distintas" in a for a in h.advertencias())


@pytest.mark.unit
def test_historico_advierte_de_puntos_no_medidos(tmp_path):
    h = HistoricoBalance.load(tmp_path / "h.csv")
    h.registrar(_snapshot(9.0), periodo="2026-01", config_hash="v1")
    h.registrar(_snapshot(4.0, tipo="INDICATIVO"), periodo="2026-02",
                config_hash="v1")
    avisos = h.advertencias()
    assert any("no MEDIDO" in a for a in avisos)


@pytest.mark.unit
def test_historico_persiste_y_se_reabre(tmp_path):
    ruta = tmp_path / "h.csv"
    h = HistoricoBalance.load(ruta)
    h.registrar(_snapshot(7.0), periodo="2026-01", config_hash="v1")
    h.save()

    otro = HistoricoBalance.load(ruta)
    assert len(otro.df) == 1
    assert otro.periodos == ["2026-01"]


@pytest.mark.unit
def test_historico_vacio_lo_dice_en_vez_de_romperse(tmp_path):
    h = HistoricoBalance.load(tmp_path / "no_existe.csv")
    assert h.df.empty
    assert h.periodos == []
    assert h.serie("X").empty
    assert h.comparar_periodos("a", "b").empty
    assert any("vacío" in a for a in h.advertencias())
