"""Aprendizaje: el campo realimenta el modelo, y los agregados se comparan entre pares.

Dos mecanismos que hacen que el sistema mejore con el uso en vez de quedarse en
la calibración del primer día:

1. **Bucle campo → modelo.** Lo que la cuadrilla encuentra entra a la base de
   casos confirmados con su fecha, y los negativos verificados —el dato más
   escaso del problema— se guardan aparte.
2. **Grupo par de agregados.** Un alimentador se juzga contra alimentadores
   parecidos, no contra el promedio de la empresa.
"""

import numpy as np
import pandas as pd
import pytest

from ptnt.anomalies.peer_entities import (analizar_niveles, comparar_contra_pares,
                                          entidades_similares)
from ptnt.ntl.confirmed import load_confirmed_theft
from ptnt.ntl.feedback import (RegistroInspecciones, Veredicto,
                               contrastar_con_ranking, extraer_resultados)


# --------------------------------------------------------------------------- #
# 1. El campo realimenta el modelo
# --------------------------------------------------------------------------- #
def _clientes_inspeccionados() -> pd.DataFrame:
    return pd.DataFrame([
        {"guid": "g1", "cuenta_contrato": "1001", "inspeccionado": 1,
         "hallazgo": "MEDIDOR_MANIPULADO", "fecha_inspeccion": "2026-03-04",
         "orden_trabajo": "OT-0001", "editado_por": "jperez",
         "lectura_medidor": 12345.0},
        {"guid": "g2", "cuenta_contrato": "1002", "inspeccionado": 1,
         "hallazgo": "SIN_NOVEDAD", "fecha_inspeccion": "2026-03-04",
         "orden_trabajo": "OT-0001", "editado_por": "jperez",
         "lectura_medidor": 880.0},
        {"guid": "g3", "cuenta_contrato": "1003", "inspeccionado": 1,
         "hallazgo": "PREDIO_CERRADO", "fecha_inspeccion": "2026-03-05",
         "orden_trabajo": "OT-0001", "editado_por": "jperez",
         "lectura_medidor": None},
        {"guid": "g4", "cuenta_contrato": "1004", "inspeccionado": 1,
         "hallazgo": "CONEXION_DIRECTA", "fecha_inspeccion": "2026-03-05",
         "orden_trabajo": "OT-0002", "editado_por": "mvera",
         "lectura_medidor": 5.0},
        {"guid": "g5", "cuenta_contrato": "1005", "inspeccionado": 1,
         "hallazgo": "CLIENTE_NO_EXISTE", "fecha_inspeccion": "2026-03-05",
         "orden_trabajo": "OT-0002", "editado_por": "mvera",
         "lectura_medidor": None},
        # Viajó en el paquete pero nadie lo visitó: no es un dato.
        {"guid": "g6", "cuenta_contrato": "1006", "inspeccionado": 0,
         "hallazgo": None, "fecha_inspeccion": None,
         "orden_trabajo": "OT-0002", "editado_por": "", "lectura_medidor": None},
    ])


@pytest.mark.unit
def test_un_predio_cerrado_no_es_un_cliente_inocente():
    """La decisión que más pesa de todo el módulo.

    Meter «predio cerrado» como negativo enseñaría al modelo que un cliente
    sospechoso al que nadie pudo entrar está limpio, y dejaría de señalar justo
    el caso donde más conviene insistir.
    """

    res = {r.cuenta: r.veredicto for r in extraer_resultados(_clientes_inspeccionados())}

    assert res["1001"] is Veredicto.CONFIRMADO
    assert res["1002"] is Veredicto.DESCARTADO
    assert res["1003"] is Veredicto.NO_CONCLUYENTE      # ni hurto ni inocencia
    assert res["1004"] is Veredicto.CONFIRMADO
    assert res["1005"] is Veredicto.PROBLEMA_DATOS
    assert not Veredicto.NO_CONCLUYENTE.es_etiqueta
    assert not Veredicto.PROBLEMA_DATOS.es_etiqueta


@pytest.mark.unit
def test_el_cliente_que_nadie_visito_no_entra():
    """Contarlo como «sin novedad» inventaría un negativo que nadie verificó."""

    cuentas = {r.cuenta for r in extraer_resultados(_clientes_inspeccionados())}
    assert "1006" not in cuentas
    assert len(cuentas) == 5


@pytest.mark.unit
def test_los_confirmados_llegan_a_la_base_con_su_fecha(tmp_path):
    """La fecha tiene que ser la de la INSPECCIÓN: es la que `fecha_corte` usa
    para descartar fuga temporal."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    resumen = reg.registrar(extraer_resultados(_clientes_inspeccionados()))

    assert resumen.confirmados == 2
    assert resumen.descartados == 1
    assert resumen.no_concluyentes == 1
    assert resumen.problemas_datos == 1

    base = reg.base_confirmados()
    assert set(base["contract_account"]) == {"1001", "1004"}
    assert set(base["fecha_multa"]) == {"2026-03-04", "2026-03-05"}

    # Y el formato es el que `load_confirmed_theft` ya sabe leer.
    conf = load_confirmed_theft(base)
    assert conf.n == 2
    assert conf.con_fecha == 2


@pytest.mark.unit
def test_la_fecha_de_corte_sigue_funcionando_sobre_lo_de_campo(tmp_path):
    """Sin esto, una señal calculada con meses posteriores a la visita
    «predeciría» algo que ya había ocurrido."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    reg.registrar(extraer_resultados(_clientes_inspeccionados()))

    conf = load_confirmed_theft(reg.base_confirmados(), fecha_corte="2026-03-05")
    assert conf.cuentas == {"1001"}, "la del 05 queda fuera del corte"
    assert any("fuga temporal" in a for a in conf.advertencias)


@pytest.mark.unit
def test_los_negativos_verificados_se_guardan_aparte(tmp_path):
    """Es el dato más escaso del problema: alguien fue, miró, y no había nada.

    El aprendizaje PU existe porque los negativos no se conocen; estos sí, y
    permiten medir la tasa de falsos positivos en vez de estimarla.
    """

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    reg.registrar(extraer_resultados(_clientes_inspeccionados()))

    assert reg.negativos_verificados() == {"1002"}
    # Y no se mezclan con los confirmados.
    assert not set(reg.base_confirmados()["contract_account"]) & \
        reg.negativos_verificados()


@pytest.mark.unit
def test_reprocesar_el_mismo_lote_no_duplica(tmp_path):
    """Una sincronización repetida no puede inflar la base de casos: el lift
    saldría mejor solo por haber cargado dos veces."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    resultados = extraer_resultados(_clientes_inspeccionados())
    reg.registrar(resultados)
    reg.registrar(resultados)

    assert len(reg.df) == 5
    assert reg.resumen_acumulado().confirmados == 2


@pytest.mark.unit
def test_el_mismo_cliente_en_dos_campanas_son_dos_datos(tmp_path):
    """Un reincidente se inspecciona, se normaliza y se vuelve a revisar.
    Quedarse solo con la última visita perdería la historia justo de los
    clientes que más importan."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    reg.registrar(extraer_resultados(pd.DataFrame([
        {"guid": "x", "cuenta_contrato": "2001", "inspeccionado": 1,
         "hallazgo": "MEDIDOR_MANIPULADO", "fecha_inspeccion": "2025-06-01",
         "orden_trabajo": "OT-A", "editado_por": "t1"}])))
    reg.registrar(extraer_resultados(pd.DataFrame([
        {"guid": "x", "cuenta_contrato": "2001", "inspeccionado": 1,
         "hallazgo": "SIN_NOVEDAD", "fecha_inspeccion": "2026-01-15",
         "orden_trabajo": "OT-B", "editado_por": "t2"}])))

    assert len(reg.df) == 2
    assert reg.resumen_acumulado().confirmados == 1
    assert reg.resumen_acumulado().descartados == 1


@pytest.mark.unit
def test_un_hallazgo_desconocido_avisa_y_no_se_usa(tmp_path):
    """Un hallazgo nuevo en el formulario no puede tumbar la carga del día, pero
    tampoco entrar como etiqueta sin que alguien decida qué significa."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    r = reg.registrar(extraer_resultados(pd.DataFrame([
        {"guid": "z", "cuenta_contrato": "3001", "inspeccionado": 1,
         "hallazgo": "MEDIDOR_QUEMADO", "fecha_inspeccion": "2026-04-01",
         "orden_trabajo": "OT-Z", "editado_por": "t3"}])))

    assert r.confirmados == 0 and r.no_concluyentes == 1
    assert any("sin traducción" in a for a in r.advertencias)
    assert reg.base_confirmados().empty


@pytest.mark.unit
def test_la_precision_de_campo_no_se_hunde_por_los_predios_cerrados(tmp_path):
    """Incluir lo no verificable mediría la logística de las visitas, no la
    calidad del ranking. Los dos números se reportan por separado."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    r = reg.registrar(extraer_resultados(_clientes_inspeccionados()))

    # 2 confirmados de 3 verificables = 66,7 %, no 2 de 5 = 40 %.
    assert r.precision_campo_pct == pytest.approx(66.7, abs=0.1)
    assert r.cobertura_pct == pytest.approx(60.0, abs=0.1)
    assert "66 %" in r.lectura() or "67 %" in r.lectura()


@pytest.mark.unit
def test_el_contraste_avisa_si_no_hay_grupo_de_comparacion(tmp_path):
    """Si todas las visitas salieron del top del ranking, no hay contra qué
    compararlas y el lift queda sobreestimado."""

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    reg.registrar(extraer_resultados(_clientes_inspeccionados()))
    ranking = pd.DataFrame({"contract_account": ["1001", "1002", "1004"],
                            "rank": [1, 2, 3]})

    c = contrastar_con_ranking(reg, ranking, top_pct=100.0)
    assert any("al azar" in a for a in c.advertencias)


@pytest.mark.unit
def test_el_contraste_mide_el_lift_con_visitas_reales(tmp_path):
    """La única medición que no depende de supuestos: no contra una base
    heredada de procedencia desconocida, sino contra visitas que esta empresa
    hizo, en estas calles."""

    filas = []
    for i in range(40):
        # Los 10 primeros del ranking: 8 con hurto. El resto: 2 de 30.
        en_top = i < 10
        hurto = (i < 8) or (not en_top and i in (20, 21))
        filas.append({
            "guid": f"g{i}", "cuenta_contrato": f"{9000 + i}", "inspeccionado": 1,
            "hallazgo": "CONEXION_DIRECTA" if hurto else "SIN_NOVEDAD",
            "fecha_inspeccion": "2026-05-01", "orden_trabajo": "OT-M",
            "editado_por": "t", "lectura_medidor": 1.0})

    reg = RegistroInspecciones(tmp_path / "insp.csv")
    reg.registrar(extraer_resultados(pd.DataFrame(filas)))
    ranking = pd.DataFrame({"contract_account": [f"{9000 + i}" for i in range(40)],
                            "rank": list(range(1, 41))})

    c = contrastar_con_ranking(reg, ranking, top_pct=25.0)
    assert c.inspeccionados == 40
    assert c.precision_top_pct == pytest.approx(80.0, abs=0.1)
    assert c.precision_resto_pct < 20.0
    assert c.lift_campo > 2.5


# --------------------------------------------------------------------------- #
# 2. Grupo par de agregados
# --------------------------------------------------------------------------- #
def _alimentadores() -> pd.DataFrame:
    """Doce alimentadores: seis urbanos compactos y seis rurales extensos.

    Los rurales tienen más pérdidas **por física** —más red por cliente— y eso
    no es un hallazgo. El que sí lo es va marcado abajo.
    """

    rng = np.random.default_rng(7)
    filas = []
    for i in range(6):
        filas.append({
            "entidad": f"URB-{i:02d}", "unidad_negocio": "GYE",
            "clientes": 1800 + rng.integers(-200, 200),
            "energia_kwh_mes": 900_000 + rng.integers(-60_000, 60_000),
            "km_red": 22 + rng.normal(0, 2), "transformadores": 60 + rng.integers(-6, 6),
            "kva_instalado": 4500 + rng.integers(-300, 300),
            "pct_residencial": 72 + rng.normal(0, 3),
            "kwh_por_cliente": 500 + rng.normal(0, 20),
            "pnt_pct": 5.0 + rng.normal(0, 0.4),
        })
    for i in range(6):
        filas.append({
            "entidad": f"RUR-{i:02d}", "unidad_negocio": "GYE",
            "clientes": 420 + rng.integers(-60, 60),
            "energia_kwh_mes": 160_000 + rng.integers(-20_000, 20_000),
            "km_red": 95 + rng.normal(0, 8), "transformadores": 85 + rng.integers(-8, 8),
            "kva_instalado": 1900 + rng.integers(-200, 200),
            "pct_residencial": 91 + rng.normal(0, 2),
            "kwh_por_cliente": 380 + rng.normal(0, 15),
            "pnt_pct": 9.5 + rng.normal(0, 0.4),      # alta, pero es lo normal ahí
        })
    return pd.DataFrame(filas)


@pytest.mark.unit
def test_un_rural_con_perdidas_altas_no_es_un_hallazgo():
    """Es la razón de ser del módulo: contra el promedio de la empresa, los seis
    rurales al 9,5 % saldrían todos marcados, y mandar cuadrillas ahí es gastar
    el presupuesto en física."""

    rep = comparar_contra_pares(
        _alimentadores(), nivel="ALIMENTADOR", columna_id="entidad",
        columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "energia_kwh_mes", "km_red",
                         "transformadores", "kva_instalado", "kwh_por_cliente"])

    assert not rep.atipicas, (
        "marcó como atípicos alimentadores que están como deben estar: "
        f"{[a.entidad for a in rep.atipicas]}")


@pytest.mark.unit
def test_el_que_se_sale_de_su_grupo_si_aparece():
    """Un urbano al 11 % entre urbanos al 5 % es la pregunta que hay que hacer,
    aunque en el promedio de la empresa quede por debajo de los rurales."""

    d = _alimentadores()
    d.loc[d["entidad"] == "URB-03", "pnt_pct"] = 11.0

    rep = comparar_contra_pares(
        d, nivel="ALIMENTADOR", columna_id="entidad", columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "energia_kwh_mes", "km_red",
                         "transformadores", "kva_instalado", "kwh_por_cliente"])

    assert [a.entidad for a in rep.atipicas] == ["URB-03"]
    a = rep.atipicas[0]
    assert a.direccion == "ALTA"
    assert a.esperado < 7.0, "se comparó contra urbanos, no contra la empresa"
    assert all(p.startswith("URB") for p in a.pares), \
        f"eligió pares que no se le parecen: {a.pares}"
    assert "parecidas" in a.descripcion()


@pytest.mark.unit
def test_el_perfil_no_puede_contener_la_metrica_evaluada():
    """Buscar parecidos por PNT y luego evaluar la PNT hace que nada resulte
    atípico, por construcción. Es el mismo error que en la segmentación de
    clientes hizo caer el lift de S5 de 10,0× a 2,4×."""

    d = _alimentadores()
    d.loc[d["entidad"] == "URB-03", "pnt_pct"] = 11.0

    rep = comparar_contra_pares(
        d, nivel="ALIMENTADOR", columna_id="entidad", columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "km_red", "pnt_pct"])

    assert any("por construcción" in a for a in rep.advertencias)
    # Y aun así encuentra el atípico, porque la métrica se excluyó del perfil.
    assert [a.entidad for a in rep.atipicas] == ["URB-03"]


@pytest.mark.unit
def test_la_mediana_del_grupo_aguanta_que_haya_varios_malos():
    """Con media y desviación típica, dos alimentadores con hurto grave
    arrastran el promedio y dejan de parecer atípicos — justo los buscados."""

    d = _alimentadores()
    d.loc[d["entidad"].isin(["URB-01", "URB-03"]), "pnt_pct"] = 12.0

    rep = comparar_contra_pares(
        d, nivel="ALIMENTADOR", columna_id="entidad", columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "energia_kwh_mes", "km_red",
                         "transformadores", "kva_instalado", "kwh_por_cliente"])

    marcados = {a.entidad for a in rep.atipicas}
    assert {"URB-01", "URB-03"} <= marcados


@pytest.mark.unit
def test_cada_unidad_de_negocio_se_juzga_con_su_propio_nivel():
    """La forma honesta de reconocer que las unidades se comportan distinto: en
    vez de estimar un efecto por unidad con pocos datos, no se comparan entre
    sí. Una UN entera con PNT alta no puede marcar a todos sus alimentadores."""

    a = _alimentadores().head(6).copy()
    a["unidad_negocio"] = "GYE"
    b = a.copy()
    b["entidad"] = b["entidad"].str.replace("URB", "MAN")
    b["unidad_negocio"] = "MAN"
    b["pnt_pct"] = b["pnt_pct"] + 6.0          # toda la UN está peor
    d = pd.concat([a, b], ignore_index=True)

    rep = comparar_contra_pares(
        d, nivel="ALIMENTADOR", columna_id="entidad", columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "energia_kwh_mes", "km_red",
                         "kwh_por_cliente"],
        columnas_categoricas=["unidad_negocio"])

    assert not rep.atipicas, (
        "marcó alimentadores solo por pertenecer a una unidad con nivel más "
        f"alto: {[x.entidad for x in rep.atipicas]}")


@pytest.mark.unit
def test_sin_grupo_par_suficiente_se_reporta_en_vez_de_inventar():
    """Una entidad que no se parece a nada no está bien: es que no se puede
    evaluar así, y decirlo es distinto de callarlo."""

    d = _alimentadores().head(4)
    rep = comparar_contra_pares(
        d, nivel="ALIMENTADOR", columna_id="entidad", columna_metrica="pnt_pct",
        columnas_perfil=["clientes", "km_red"], min_pares=6)

    assert not rep.atipicas
    assert rep.advertencias


@pytest.mark.unit
def test_las_rutas_parecidas_se_pueden_consultar():
    """La pregunta inversa, que es la de la reunión: «esta ruta va mal, ¿cómo
    están las que se le parecen?». Ver las cinco con sus números convence más
    que un z-score."""

    d = _alimentadores().rename(columns={"entidad": "entidad"})
    similares = entidades_similares(d, "URB-02", nivel="ALIMENTADOR", k=4)

    assert not similares.empty
    assert similares.iloc[0]["entidad"] == "URB-02", "la primera es ella misma"
    assert sum(1 for e in similares["entidad"] if e.startswith("URB")) >= 4


@pytest.mark.unit
def test_los_niveles_se_reportan_por_separado():
    """Un sector y un alimentador no son comparables ni en la misma escala:
    juntarlos invitaría a ordenar por desviación y mandar la cuadrilla al
    primero de la lista."""

    d = _alimentadores()
    d.loc[d["entidad"] == "URB-03", "pnt_pct"] = 11.0
    sectores = pd.DataFrame([
        {"entidad": f"SEC-{i:02d}", "alimentador": "URB-00",
         "clientes": 20 + i, "energia_kwh_mes": 9_000 + i * 200,
         "kwh_por_cliente": 450, "radio_m": 120, "pnt_pct": 6.0 + (i % 3) * 0.2}
        for i in range(10)
    ])

    reportes = analizar_niveles({"ALIMENTADOR": d, "SECTOR": sectores})

    assert set(reportes) == {"ALIMENTADOR", "SECTOR"}
    assert reportes["ALIMENTADOR"].resumen()["nivel"] == "ALIMENTADOR"
    assert [a.entidad for a in reportes["ALIMENTADOR"].atipicas] == ["URB-03"]
