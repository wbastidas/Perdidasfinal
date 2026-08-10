"""Pruebas del escenario costero a escala y del motor de informes.

Dos cosas distintas que comparten propósito: que la **prueba a escala sea
significativa** (si el escenario no reproduce la estructura real, validar contra
él no prueba nada) y que los **entregables sean correctos** (un informe con
números mal formateados o gráficos rotos no se puede presentar).
"""

import numpy as np
import pandas as pd
import pytest

from ptnt.report.charts import (
    barra_apilada_balance,
    barras_horizontales,
    lineas_temporales,
    mapa_puntos,
)
from ptnt.report.pages import Informe, compendio
from ptnt.synth.escenario_costa import (
    ESTACIONALIDAD_COSTA,
    TARIFA_DIGNIDAD_KWH,
    UTM_X0,
    UTM_X1,
    UTM_Y0,
    UTM_Y1,
    build_escenario_costa,
)


# --------------------------------------------------------------------------- #
# Escenario costero
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def escenario(tmp_path_factory):
    """Escenario reducido: la estructura es la misma, el tamaño no importa aquí."""

    d = tmp_path_factory.mktemp("costa")
    return build_escenario_costa(d, n_clientes=1500, n_alimentadores=4,
                                 rutas_por_alimentador=6,
                                 transformadores_por_alimentador=6)


@pytest.mark.unit
def test_estacionalidad_es_costera_no_serrana():
    """El pico debe caer en la estación húmeda y calurosa (ene–abr).

    No es ambientación: un detector calibrado con estacionalidad de Sierra
    marcaría la subida estacional de la Costa como anomalía.
    """

    pico = int(np.argmax(ESTACIONALIDAD_COSTA))
    valle = int(np.argmin(ESTACIONALIDAD_COSTA))
    assert pico <= 3, "el pico debe estar entre enero y abril"
    assert 6 <= valle <= 9, "el valle debe estar entre julio y octubre"
    assert ESTACIONALIDAD_COSTA.mean() == pytest.approx(1.0, abs=0.03)


@pytest.mark.unit
def test_coordenadas_dentro_del_area_metropolitana(escenario):
    x = escenario.padron["ZZUTM_X"].astype(float)
    y = escenario.padron["ZZUTM_Y"].astype(float)
    assert x.between(UTM_X0, UTM_X1).all()
    assert y.between(UTM_Y0, UTM_Y1).all()


@pytest.mark.unit
def test_padron_tiene_mezcla_realista_de_clases(escenario):
    """~85 % residencial: si las clases salieran en partes iguales, el escenario
    sería trivial y ocultaría el problema que la segmentación resuelve."""

    prop = escenario.padron["_clase_real"].value_counts(normalize=True)
    assert 0.75 <= prop.get("RESIDENCIAL", 0) <= 0.95
    assert prop.get("COMERCIAL", 0) > 0.02
    assert {"INDUSTRIAL_BT", "INDUSTRIAL_MT"} & set(prop.index)


@pytest.mark.unit
def test_las_rutas_tienen_vocacion_no_son_muestras_aleatorias(escenario):
    """Cada ruta pertenece a una zona con su propia mezcla de clases.

    Sin esta correlación clase↔geografía, una ruta sería una muestra aleatoria
    del padrón, comparar dentro de la ruta ya bastaría, y la separación por clase
    no tendría nada que demostrar.
    """

    pad = escenario.padron
    mezcla = pad.groupby("CLIRLSCOD")["_clase_real"].apply(
        lambda s: (s == "RESIDENCIAL").mean())
    # la dispersión entre rutas debe ser apreciable, no un valor constante
    assert mezcla.std() > 0.05, "todas las rutas tienen la misma mezcla"


@pytest.mark.unit
def test_el_hurto_se_concentra_en_la_periferia(escenario):
    """Es el patrón real y lo que la focalización geográfica debe encontrar."""

    tasa = escenario.padron.groupby("_zona")["_hurto"].mean()
    assert tasa["periferia"] > tasa["residencial_consolidado"] * 2


@pytest.mark.unit
def test_tarifa_dignidad_solo_para_consumos_bajo_el_techo(escenario):
    """El techo de la Costa es 130 kWh/mes; asignarla a un cliente de 400 kWh
    sería un error de datos que contaminaría la clasificación."""

    pad = escenario.padron
    dig = pad[pad["DESTARI"].str.contains("DIGNIDAD")]
    if dig.empty:
        pytest.skip("el escenario no generó clientes con Tarifa Dignidad")
    series = dig[[f"KWH_{i}" for i in range(1, 37)]].apply(
        lambda c: c.str.replace(".", "", regex=False).astype(float))
    p75 = np.percentile(series.to_numpy(), 75, axis=1)
    assert (p75 <= TARIFA_DIGNIDAD_KWH * 1.05).all()


@pytest.mark.unit
def test_las_redes_llevan_los_clientes_reales_del_padron(escenario):
    """Si la red conservara sus clientes sintéticos propios, el balance de cada
    alimentador sería idéntico al de los demás y la focalización por ALIMENTADOR
    no podría discriminar nada."""

    cuentas_padron = set(escenario.padron["CUENTACONTRATO"].astype(str))
    for ali, red in escenario.redes.items():
        ids = {str(c["customer_id"])
               for lista in red.customer_nodes.values() for c in lista}
        assert ids, f"{ali} sin clientes"
        assert ids <= cuentas_padron, f"{ali} tiene clientes ajenos al padrón"


@pytest.mark.unit
def test_los_alimentadores_no_son_todos_iguales(escenario):
    """La energía de cabecera debe variar entre alimentadores: si fueran clones,
    ordenarlos por PNT no significaría nada."""

    vals = np.array(list(escenario.head_energy_kwh.values()))
    assert vals.std() / vals.mean() > 0.10


@pytest.mark.unit
def test_cabecera_cubre_facturado_mas_perdidas(escenario):
    """La cabecera debe ser mayor que lo facturado: si no, el balance daría PNT
    negativa y el resultado no sería publicable."""

    cab = pd.read_csv(escenario.csv_cabecera)
    pad = escenario.padron.copy()
    pad["_k"] = pad["KWH_36"].str.replace(".", "", regex=False).astype(float)
    fact = pad.groupby("_alimentador")["_k"].sum()
    ult = cab[cab["period"] == cab["period"].max()].set_index("feeder_code")
    for ali, f in fact.items():
        assert ult.loc[ali, "kwh_delivered"] > f, f"{ali}: cabecera < facturado"


@pytest.mark.unit
def test_la_verdad_del_escenario_es_recuperable(escenario):
    """Sin verdad conocida no se puede validar nada."""

    assert escenario.hurtos_reales
    assert set(escenario.multados) <= set(escenario.hurtos_reales)
    assert 0 < len(escenario.multados) < len(escenario.hurtos_reales), (
        "la distribuidora nunca detecta el 100 %: si los multados fueran todos "
        "los hurtos, el aprendizaje PU no tendría sentido")
    assert escenario.transferencia["origen"] != escenario.transferencia["destino"]
    assert escenario.clientes_sin_sig


# --------------------------------------------------------------------------- #
# Motor de informes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_los_conteos_enteros_no_se_muestran_con_decimales():
    """Mostrar "44,00 clientes" delata que el número salió de una división y
    resta credibilidad al informe."""

    inf = Informe("E", "T")
    inf.tabla(pd.DataFrame({"ordenes": [1, 13], "clientes": [14.0, 1601.0],
                            "prioridad": [0.975, 0.630]}))
    html = inf.bloques[0]
    assert ">14<" in html and ">1,601<" in html
    assert "14.00" not in html
    assert "0.97" in html or "0.98" in html      # los decimales sí se conservan


@pytest.mark.unit
def test_tabla_vacia_lo_dice_en_vez_de_romperse():
    inf = Informe("E", "T")
    inf.tabla(pd.DataFrame())
    assert "Sin registros" in inf.bloques[0]


@pytest.mark.unit
def test_tabla_avisa_cuantas_filas_omitio():
    """Un informe que muestra 25 de 1 453 filas sin decirlo induce a error."""

    inf = Informe("E", "T")
    inf.tabla(pd.DataFrame({"a": range(100)}), max_filas=10)
    assert "90" in inf.bloques[0]


@pytest.mark.unit
def test_graficos_producen_svg_valido():
    svg = barras_horizontales(["A", "B"], [10.0, 5.0], titulo="X", unidad=" kWh")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "viewBox" in svg

    svg = lineas_temporales(["2025-01-01", "2025-02-01"], {"s": [1.0, 2.0]},
                            marcar_periodo="2025-02-01")
    assert "polyline" in svg and "evento" in svg

    svg = mapa_puntos([620000.0, 621000.0], [9755000.0, 9756000.0],
                      valores=[1.0, 5.0])
    assert "circle" in svg

    svg = barra_apilada_balance([("Facturado", 80.0), ("PNT", 20.0)], 100.0)
    assert "rect" in svg


@pytest.mark.unit
def test_graficos_sin_datos_no_revientan():
    for svg in (barras_horizontales([], []),
                lineas_temporales([], {}),
                mapa_puntos([], []),
                barra_apilada_balance([], 0.0)):
        assert "Sin " in svg


@pytest.mark.unit
def test_el_mapa_conserva_la_escala_real_del_terreno():
    """En UTM un metro en X vale lo mismo que uno en Y. Deformar la escala daría
    una idea falsa de las distancias que va a recorrer la cuadrilla."""

    import re

    # terreno muy alargado: 20 km de ancho por 2 km de alto
    xs = [620_000.0, 640_000.0, 630_000.0]
    ys = [9_755_000.0, 9_755_000.0, 9_757_000.0]
    svg = mapa_puntos(xs, ys, ancho=800, alto=600)
    pts = [(float(a), float(b)) for a, b in
           re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', svg)]
    assert len(pts) == 3
    # la relación entre distancias en pantalla debe igualar la del terreno
    dx_px = abs(pts[1][0] - pts[0][0])
    dy_px = abs(pts[2][1] - pts[0][1])
    assert dx_px / dy_px == pytest.approx(20_000 / 2_000, rel=0.15)


@pytest.mark.unit
def test_informe_html_es_autocontenido(tmp_path):
    """Sin recursos externos: debe poder enviarse por correo o archivarse."""

    inf = Informe("Etapa 1", "Título", "subtítulo")
    inf.kpis([("K", "1", "n")]).seccion("S").texto("hola")
    inf.grafico(barras_horizontales(["A"], [1.0]))
    html = inf.escribir(tmp_path / "x.html", pie="pie").read_text(encoding="utf-8")
    assert "<style>" in html and "</style>" in html
    for prohibido in ("http://", "https://", "<script", "<link"):
        assert prohibido not in html


@pytest.mark.unit
def test_informe_escapa_contenido_de_los_datos(tmp_path):
    """Los identificadores vienen de la base de origen: no deben poder inyectar
    marcado en el informe que después circula por correo."""

    inf = Informe("E", "T")
    inf.tabla(pd.DataFrame({"entidad": ["<script>alert(1)</script>"]}))
    html = inf.html()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.unit
def test_compendio_encadena_informes_con_saltos_de_pagina(tmp_path):
    a = Informe("Etapa 1", "Primero").texto("uno")
    b = Informe("Etapa 2", "Segundo").texto("dos")
    p = compendio([a, b], tmp_path / "c.html", titulo="Compendio")
    html = p.read_text(encoding="utf-8")
    assert html.count("page-break-before:always") == 1   # solo entre secciones
    assert "Primero" in html and "Segundo" in html
    assert "Contenido" in html                            # índice
