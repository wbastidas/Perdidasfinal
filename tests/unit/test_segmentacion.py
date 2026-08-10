"""Pruebas de segmentación de clientes y grupos par (§11.3).

El eje de estas pruebas es que segmentar **cambia la decisión operativa**: a quién
se manda a inspeccionar. Por eso no basta con verificar que las columnas existan;
se comprueba que el recuperable, el grupo par y el orden de prioridad respondan
como deben.
"""

import numpy as np
import pandas as pd
import pytest

from ptnt.config.models import SignalsConfig
from ptnt.ntl.signals import s5_divergencia_grupo_par, s9_deficit_contra_base_propia
from ptnt.segment.classification import (
    ClaseConsumo,
    NivelTension,
    clasificar_tarifa,
    clasificar_tension,
    consumo_base,
    etiquetar_estrato,
    resolver_clave_config,
    segmentar_clientes,
    tiene_demanda_facturada,
)
from ptnt.segment.peers import (
    NIVELES_GRUPO_PAR,
    asignar_grupo_par,
    consumo_esperado_por_grupo,
    energia_recuperable,
)
from ptnt.segment.report import (
    cobertura_grupos_par,
    grandes_clientes_a_revisar,
    rendimiento_por_segmento,
)


# --------------------------------------------------------------------------- #
# Clasificación desde la descripción de tarifa
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("texto,esperado", [
    ("RESIDENCIAL BAJA TENSION", ClaseConsumo.RESIDENCIAL),
    ("TARIFA DIGNIDAD", ClaseConsumo.RESIDENCIAL),
    ("COMERCIAL SIN DEMANDA BAJA TENSION", ClaseConsumo.COMERCIAL),
    ("INDUSTRIAL CON DEMANDA MEDIA TENSION", ClaseConsumo.INDUSTRIAL),
    # "INDUSTRIAL ARTESANAL" contiene palabras de dos clases: gana industrial
    ("INDUSTRIAL ARTESANAL BAJA TENSION", ClaseConsumo.INDUSTRIAL),
    ("ENTIDADES OFICIALES", ClaseConsumo.OFICIAL),
    ("ASISTENCIA SOCIAL", ClaseConsumo.ASISTENCIA_SOCIAL),
    ("BOMBEO DE AGUA", ClaseConsumo.BOMBEO_AGUA),
    ("ALUMBRADO PUBLICO", ClaseConsumo.ALUMBRADO_PUBLICO),
])
def test_clasifica_tarifas_reales(texto, esperado):
    assert clasificar_tarifa(texto) is esperado


@pytest.mark.unit
def test_tarifa_desconocida_no_se_adivina_como_residencial():
    """Clasificar mal a un industrial como residencial es el error que la
    segmentación existe para evitar: ante la duda, no se adivina."""

    assert clasificar_tarifa("XYZ-99") is ClaseConsumo.NO_CLASIFICADO
    assert clasificar_tarifa("") is ClaseConsumo.NO_CLASIFICADO
    assert clasificar_tarifa(None) is ClaseConsumo.NO_CLASIFICADO
    assert clasificar_tarifa(float("nan")) is ClaseConsumo.NO_CLASIFICADO


@pytest.mark.unit
def test_clasifica_nivel_de_tension_y_demanda():
    assert clasificar_tension("INDUSTRIAL CON DEMANDA MEDIA TENSION") is NivelTension.MEDIA
    assert clasificar_tension("RESIDENCIAL BAJA TENSION") is NivelTension.BAJA
    assert clasificar_tension("RESIDENCIAL") is NivelTension.DESCONOCIDO
    assert tiene_demanda_facturada("COMERCIAL CON DEMANDA") is True
    assert tiene_demanda_facturada("COMERCIAL SIN DEMANDA") is False
    assert tiene_demanda_facturada("RESIDENCIAL") is None


@pytest.mark.unit
def test_resuelve_clave_de_configuracion_por_semantica():
    """El catálogo usa nombres cortos y DESTARI trae texto libre.

    Sin esta resolución, `cfg.clases.get(texto)` falla con TODAS las descripciones
    reales y cae a la clase por defecto, asignando coeficientes de Velander
    residenciales a un industrial de media tensión sin que nada falle a la vista.
    """

    claves = ("BT Residencial", "BT Comercial", "BT Industrial", "MT Industrial")
    assert resolver_clave_config(
        "INDUSTRIAL CON DEMANDA MEDIA TENSION", claves) == "MT Industrial"
    assert resolver_clave_config(
        "INDUSTRIAL ARTESANAL BAJA TENSION", claves) == "BT Industrial"
    assert resolver_clave_config("TARIFA DIGNIDAD", claves) == "BT Residencial"
    # sin nivel de tensión reconocible degrada a la clase, no falla
    assert resolver_clave_config("COMERCIAL", claves) == "BT Comercial"
    assert resolver_clave_config("XYZ", claves) is None


# --------------------------------------------------------------------------- #
# Estratos y consumo base
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_etiqueta_de_estrato_ordena_alfabeticamente():
    cortes = [50, 100, 200]
    etiquetas = [etiquetar_estrato(v, cortes) for v in (10, 75, 150, 900)]
    assert etiquetas == sorted(etiquetas)
    assert etiquetar_estrato(float("nan"), cortes) == "E00_SIN_DATO"


@pytest.mark.unit
def test_consumo_base_resiste_un_periodo_de_hurto():
    """El nivel base debe reflejar el tamaño del cliente, no el consumo deprimido.

    Un cliente de ~500 kWh que hurta el último tercio: la media cae mucho, el
    percentil alto conserva su nivel real.
    """

    serie = np.array([500.0] * 24 + [120.0] * 12)
    base = consumo_base(serie, percentil=75.0)
    assert base == pytest.approx(500.0, abs=1.0)
    assert np.mean(serie) < 400.0          # la media sí se contamina


# --------------------------------------------------------------------------- #
# Segmentación del padrón
# --------------------------------------------------------------------------- #
def _padron() -> tuple[pd.DataFrame, pd.DataFrame]:
    filas, consumo = [], []
    tarifas = {
        "R": "RESIDENCIAL BAJA TENSION",
        "C": "COMERCIAL CON DEMANDA BAJA TENSION",
        "I": "INDUSTRIAL CON DEMANDA MEDIA TENSION",
    }
    niveles = {"R": 150.0, "C": 900.0, "I": 40000.0}
    for tipo, n in (("R", 40), ("C", 12), ("I", 4)):
        for i in range(n):
            cta = f"{tipo}{i:03d}"
            filas.append({
                "contract_account": cta,
                "tariff_description": tarifas[tipo],
                "grupo_lectura": f"F001-R{i % 3:02d}",
                "phases_count": 1 if tipo == "R" else 3,
            })
            for m in range(12):
                consumo.append({"contract_account": cta, "period": f"2025-{m+1:02d}-01",
                                "kwh": niveles[tipo]})
    return pd.DataFrame(filas), pd.DataFrame(consumo)


@pytest.mark.unit
def test_segmenta_padron_y_resume_donde_esta_la_energia():
    clientes, consumo = _padron()
    res = segmentar_clientes(clientes, consumo)

    assert res.cobertura_pct == 100.0
    assert res.n_no_clasificados == 0
    clases = set(res.clientes["clase_consumo"])
    assert clases == {"RESIDENCIAL", "COMERCIAL", "INDUSTRIAL"}
    # los 4 industriales son el 7 % de los clientes pero la mayoría de la energía
    ind = res.por_clase.set_index("clase_consumo").loc["INDUSTRIAL"]
    assert ind["pct_clientes"] < 10.0
    assert ind["pct_energia"] > 70.0
    assert res.pct_energia_no_residencial > 70.0


@pytest.mark.unit
def test_gran_cliente_se_marca_por_tension_o_por_tamano():
    clientes, consumo = _padron()
    res = segmentar_clientes(clientes, consumo, umbral_gran_cliente_kwh=5000)
    df = res.clientes.set_index("contract_account")
    assert bool(df.loc["I000", "es_gran_cliente"]) is True
    assert bool(df.loc["R000", "es_gran_cliente"]) is False


@pytest.mark.unit
def test_sin_columna_de_tarifa_advierte_en_vez_de_fallar():
    clientes, consumo = _padron()
    res = segmentar_clientes(clientes.drop(columns=["tariff_description"]), consumo)
    assert res.n_no_clasificados == len(clientes)
    assert any("tarifa" in a for a in res.advertencias)


@pytest.mark.unit
def test_clase_industrial_no_admite_grupo_par():
    """En industrial hay pocos clientes y son muy heterogéneos: la comparación
    válida es contra la propia historia, no contra otros."""

    assert ClaseConsumo.RESIDENCIAL.admite_grupo_par is True
    assert ClaseConsumo.COMERCIAL.admite_grupo_par is True
    assert ClaseConsumo.INDUSTRIAL.admite_grupo_par is False
    assert ClaseConsumo.OFICIAL.admite_grupo_par is False


# --------------------------------------------------------------------------- #
# Grupo par jerárquico
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_estrato_de_consumo_no_entra_en_el_grupo_par():
    """REGRESIÓN CRÍTICA: estratificar por consumo para comparar consumos es
    circular y destruye la señal S5.

    Un cliente que hurta toda la ventana tiene su nivel base deprimido, cae en un
    estrato bajo y queda comparado contra clientes genuinamente pequeños —
    exactamente donde deja de destacar. Medido, incluir el estrato baja el lift de
    S5 de 10,0x a 2,4x. Todas las claves del grupo par deben ser EXÓGENAS al
    consumo.
    """

    for _nombre, columnas, _conf in NIVELES_GRUPO_PAR:
        assert "estrato_consumo" not in columnas
        assert "consumo_base_kwh" not in columnas


@pytest.mark.unit
def test_grupo_par_usa_el_nivel_mas_fino_disponible():
    clientes, consumo = _padron()
    seg = segmentar_clientes(clientes, consumo)
    res = asignar_grupo_par(seg.clientes, min_pares=5)
    df = res.clientes.set_index("contract_account")

    # Los residenciales son 40 repartidos en 3 rutas -> el nivel más fino alcanza
    assert df.loc["R000", "grupo_par_nivel"] == "CLASE_TENSION_FASES_RUTA"
    assert df.loc["R000", "grupo_par_confianza"] == pytest.approx(1.0)
    # Nadie se compara contra otra clase
    grupos_r = set(df.loc[df["clase_consumo"] == "RESIDENCIAL", "grupo_par_id"])
    grupos_c = set(df.loc[df["clase_consumo"] == "COMERCIAL", "grupo_par_id"])
    assert not (grupos_r & grupos_c)


@pytest.mark.unit
def test_grupo_par_degrada_cuando_el_grupo_fino_es_pequeno():
    """Con 4 industriales no hay grupo fino posible: debe degradar y decirlo."""

    clientes, consumo = _padron()
    seg = segmentar_clientes(clientes, consumo)
    res = asignar_grupo_par(seg.clientes, min_pares=8)
    df = res.clientes.set_index("contract_account")
    nivel = df.loc["I000", "grupo_par_nivel"]
    conf = df.loc["I000", "grupo_par_confianza"]
    # o degradó a un nivel más general, o se quedó sin grupo: nunca finge
    if nivel is not None:
        assert nivel != "CLASE_TENSION_FASES_RUTA"
        assert conf < 1.0
    else:
        assert conf == 0.0


@pytest.mark.unit
def test_sin_grupo_par_se_reporta_y_no_se_inventa():
    clientes = pd.DataFrame({
        "contract_account": ["A", "B"],
        "clase_consumo": ["RESIDENCIAL"] * 2,
        "nivel_tension": ["BT"] * 2,
        "phases_count": [1, 1],
        "grupo_lectura": ["R1", "R1"],
    })
    res = asignar_grupo_par(clientes, min_pares=8)
    assert res.n_sin_grupo == 2
    assert res.clientes["grupo_par_confianza"].eq(0.0).all()
    assert any("sin grupo par" in a for a in res.advertencias)


@pytest.mark.unit
def test_confianza_del_grupo_pondera_la_senal_s5():
    """Quedar bajo pares del mismo estrato y ruta no vale lo mismo que quedar bajo
    'toda la clase': sin ponderar, las comparaciones gruesas —que son muchas más—
    ahogarían a las finas en el ranking."""

    cm = pd.Series([100.0] * 9 + [5.0], index=[f"c{i}" for i in range(10)])
    grupos = pd.Series(["G"] * 10, index=cm.index)
    cfg = SignalsConfig(s5_min_pares=5, s5_percentil=20.0)

    fuerte = s5_divergencia_grupo_par(cm, grupos, cfg)
    debil = s5_divergencia_grupo_par(
        cm, grupos, cfg, confianza=pd.Series(0.4, index=cm.index))
    assert fuerte["c9"] > 0
    assert debil["c9"] == pytest.approx(fuerte["c9"] * 0.4)


# --------------------------------------------------------------------------- #
# S9 — déficit contra la base propia
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_s9_detecta_al_gran_cliente_que_el_grupo_par_no_ve():
    """Un industrial que cae a la mitad sigue muy por encima de cualquier mediana
    global; solo su propia historia lo delata."""

    cm = pd.Series({"IND": 20_000.0, "RES": 150.0})
    base = pd.Series({"IND": 45_000.0, "RES": 155.0})
    cfg = SignalsConfig(s9_deficit_min=0.35)
    s9 = s9_deficit_contra_base_propia(cm, base, cfg)
    assert s9["IND"] > 0.5           # cayó ~56 %
    assert s9["RES"] == 0.0          # variación normal, no se activa


@pytest.mark.unit
def test_s9_no_se_activa_por_dispersion_normal():
    cm = pd.Series({"A": 95.0})
    base = pd.Series({"A": 100.0})
    s9 = s9_deficit_contra_base_propia(cm, base, SignalsConfig(s9_deficit_min=0.35))
    assert s9["A"] == 0.0


# --------------------------------------------------------------------------- #
# Energía recuperable segmentada
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_recuperable_global_subestima_al_industrial_y_lo_inventa_al_pequeno():
    """El defecto concreto que la segmentación corrige.

    Con una mediana global de todo el padrón, un industrial que hurta el 50 % de
    20 000 kWh sigue estando por encima de la mediana y su recuperable sale CERO;
    a la vez, a un residencial pequeño y honesto se le atribuye recuperable
    inventado. Ambos errores desaparecen comparando dentro del segmento.
    """

    cm = pd.Series({"IND": 20_000.0, "RES": 60.0})
    mediana_global = 150.0
    rec_global = np.maximum(mediana_global - cm, 0.0)
    assert rec_global["IND"] == 0.0        # el industrial "no tiene nada que recuperar"
    assert rec_global["RES"] > 0.0         # y al pequeño se le inventa

    esperado = pd.Series({"IND": 40_000.0, "RES": 65.0})   # mediana de SU grupo
    base = pd.Series({"IND": 40_000.0, "RES": 62.0})
    rec_seg = energia_recuperable(cm, esperado, base)
    assert rec_seg["IND"] == pytest.approx(20_000.0)
    assert rec_seg["RES"] < 10.0


@pytest.mark.unit
def test_recuperable_toma_el_maximo_de_los_dos_estimadores():
    cm = pd.Series({"caida": 300.0, "siempre_bajo": 40.0})
    esperado = pd.Series({"caida": 320.0, "siempre_bajo": 200.0})
    base = pd.Series({"caida": 900.0, "siempre_bajo": 45.0})
    rec = energia_recuperable(cm, esperado, base)
    assert rec["caida"] == pytest.approx(600.0)        # gana su propia historia
    assert rec["siempre_bajo"] == pytest.approx(160.0)  # gana el grupo par


@pytest.mark.unit
def test_recuperable_nunca_es_negativo():
    cm = pd.Series({"a": 500.0})
    rec = energia_recuperable(cm, pd.Series({"a": 100.0}), pd.Series({"a": 200.0}))
    assert rec["a"] == 0.0


@pytest.mark.unit
def test_consumo_esperado_usa_mediana_robusta_a_hurtos_del_grupo():
    """El grupo par puede contener otros hurtos; la media se contaminaría."""

    cm = pd.Series([100.0, 100.0, 100.0, 100.0, 5.0], index=list("abcde"))
    grupos = pd.Series(["G"] * 5, index=cm.index)
    esperado = consumo_esperado_por_grupo(cm, grupos)
    assert esperado["a"] == pytest.approx(100.0)   # mediana, no media (81)


# --------------------------------------------------------------------------- #
# Reporte: a quién conviene ir a revisar
# --------------------------------------------------------------------------- #
def _ranking() -> pd.DataFrame:
    filas = []
    for i in range(200):
        filas.append({"contract_account": f"R{i}", "clase_consumo": "RESIDENCIAL",
                      "score": 0.9 - i * 0.001, "recuperable_kwh_mes": 50.0,
                      "es_gran_cliente": False, "n_senales_activas": 1})
    for i in range(10):
        filas.append({"contract_account": f"I{i}", "clase_consumo": "INDUSTRIAL",
                      "score": 0.7 - i * 0.01, "recuperable_kwh_mes": 8000.0,
                      "es_gran_cliente": True, "n_senales_activas": 1})
    return pd.DataFrame(filas)


@pytest.mark.unit
def test_rendimiento_por_visita_ordena_por_valor_no_por_score():
    """El ranking ordena por probabilidad; las cuadrillas se arman por valor.

    El industrial tiene MENOR score que los residenciales y aun así debe encabezar
    el rendimiento por visita: cuesta lo mismo inspeccionarlo y rinde 160x más.
    """

    r = rendimiento_por_segmento(_ranking(), top_pct=20.0)
    assert r.tabla.iloc[0]["clase_consumo"] == "INDUSTRIAL"
    assert r.tabla.iloc[0]["kwh_por_visita"] > r.tabla.iloc[-1]["kwh_por_visita"]
    assert any("cuadrilla dedicada" in x for x in r.recomendaciones)
    assert r.concentracion["pct_recuperable_no_residencial"] > 50.0


@pytest.mark.unit
def test_rendimiento_sin_segmentacion_lo_dice_en_vez_de_devolver_vacio():
    r = rendimiento_por_segmento(pd.DataFrame({"score": [1.0]}))
    assert r.tabla.empty
    assert any("segmentacion.habilitada" in x for x in r.recomendaciones)


@pytest.mark.unit
def test_grandes_clientes_se_listan_por_energia_no_por_ranking():
    gc = grandes_clientes_a_revisar(_ranking(), top=5)
    assert len(gc) == 5
    assert set(gc["clase_consumo"]) == {"INDUSTRIAL"}
    assert gc["recuperable_kwh_mes"].is_monotonic_decreasing
    assert gc["accion"].str.contains("individual").all()


@pytest.mark.unit
def test_cobertura_de_grupos_par_permite_juzgar_cuanto_vale_s5():
    clientes, consumo = _padron()
    seg = segmentar_clientes(clientes, consumo)
    res = asignar_grupo_par(seg.clientes, min_pares=5)
    cob = cobertura_grupos_par(res.clientes)
    assert not cob.empty
    assert cob["pct"].sum() == pytest.approx(100.0, abs=0.5)


@pytest.mark.unit
def test_grandes_clientes_excluye_a_los_que_no_tienen_ningun_indicio():
    """"Con indicios" tiene que significar algo: sin este filtro la tabla se llena
    de grandes clientes con recuperable 0 y ninguna señal, que desplazan a los
    casos que sí ameritan una visita."""

    r = pd.DataFrame({
        "contract_account": ["I1", "I2", "I3"],
        "clase_consumo": ["INDUSTRIAL"] * 3,
        "score": [0.9, 0.8, 0.7],
        "recuperable_kwh_mes": [0.0, 5000.0, 0.0],
        "n_senales_activas": [0, 1, 0],
        "es_gran_cliente": [True, True, True],
    })
    gc = grandes_clientes_a_revisar(r, top=10)
    assert list(gc["contract_account"]) == ["I2"]
