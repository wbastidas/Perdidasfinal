"""El tablero, ejecutado de verdad.

Un tablero que no arranca es un tablero roto, y comprobar que el servidor
responde HTTP 200 no prueba nada: Streamlit ejecuta el guion al conectarse, y es
ahí donde revienta. ``AppTest`` lo ejecuta igual que un navegador.

Lo que se fija aquí es sobre todo el **alcance**: los resultados son archivos ya
calculados con todas las unidades de negocio dentro, y el tablero los lee
directamente. Si una pestaña se olvida de filtrar, un analista de una unidad ve
el padrón de otra.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from ptnt.security.auth import UserStore  # noqa: E402

# AppTest resuelve las rutas relativas contra el archivo que la llama, no contra
# la raíz del proyecto: se le da absoluta.
APP = str(Path(__file__).resolve().parents[2] / "src/ptnt/dashboard/app.py")
RAIZ = Path(__file__).resolve().parents[2]

CATALOGO = [
    ("GYE-01", "SE-CENTRO", "GUAYAQUIL"),
    ("GYE-02", "SE-CENTRO", "GUAYAQUIL"),
    ("MIL-01", "SE-MILAGRO", "MILAGRO"),
]


@pytest.fixture
def entorno(tmp_path):
    """Una instalación completa en miniatura: config, usuarios y resultados."""

    import yaml

    salidas = tmp_path / "salidas"
    salidas.mkdir()

    pd.DataFrame(CATALOGO,
                 columns=["feeder_code", "subestacion", "unidad_negocio"]
                 ).to_csv(tmp_path / "jerarquia.csv", index=False)

    # Resultados con clientes de las dos unidades de negocio, con las mismas
    # columnas que produce el pipeline de verdad.
    pd.DataFrame([
        {"contract_account": "C1", "unidad_negocio": "GUAYAQUIL", "score": 0.9,
         "recuperable_kwh_mes": 120.0, "razones": "S1, S4",
         "n_senales_activas": 2, "rank": 1},
        {"contract_account": "C2", "unidad_negocio": "GUAYAQUIL", "score": 0.8,
         "recuperable_kwh_mes": 90.0, "razones": "S5",
         "n_senales_activas": 1, "rank": 2},
        {"contract_account": "C3", "unidad_negocio": "MILAGRO", "score": 0.7,
         "recuperable_kwh_mes": 60.0, "razones": "S9",
         "n_senales_activas": 1, "rank": 3},
    ]).to_csv(salidas / "ranking_clientes.csv", index=False)
    (salidas / "metricas.json").write_text(json.dumps({
        "n_cuentas": 3, "n_meses": 36, "n_sospechosos": 3,
        "unidad_negocio": "TODAS"}), encoding="utf-8")

    cfg = yaml.safe_load(open(RAIZ / "config/base.yaml", encoding="utf-8"))
    cfg["rutas"]["salidas"] = str(salidas)
    cfg["rutas"]["duckdb"] = str(tmp_path / "ptnt.duckdb")
    cfg["organizacion"]["catalogo"] = str(tmp_path / "jerarquia.csv")
    cfg["seguridad"]["ruta_usuarios"] = str(tmp_path / "usuarios.json")
    ruta_cfg = tmp_path / "config.yaml"
    ruta_cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    store = UserStore(tmp_path / "usuarios.json")
    store.add_user("ana", "ClaveLarga123", "analyst", unidades=["GUAYAQUIL"])
    store.add_user("beto", "ClaveLarga123", "analyst", unidades=["MILAGRO"])
    store.add_user("central", "ClaveLarga123", "admin", matriz=True)
    store.add_user("nadie", "ClaveLarga123", "analyst")
    return ruta_cfg


def _abrir(ruta_cfg) -> AppTest:
    """AppTest ejecuta el guion en este mismo proceso, así que la configuración
    se le pasa por ``sys.argv``, que es de donde la lee el tablero."""

    sys.argv = ["app.py", "--config", str(ruta_cfg)]
    return AppTest.from_file(APP, default_timeout=90)


def _entrar(ruta_cfg, usuario: str) -> AppTest:
    at = _abrir(ruta_cfg)
    at.run()
    at.text_input[0].set_value(usuario)
    at.text_input[1].set_value("ClaveLarga123")
    at.button[0].click().run()
    return at


@pytest.mark.integration
def test_el_tablero_arranca_sin_reventar(entorno):
    at = _abrir(entorno)
    at.run()

    assert not at.exception, f"el tablero falló al arrancar: {at.exception}"
    # Sin autenticar, solo debe verse la pantalla de acceso.
    assert any("Acceso" in t.value for t in at.title)


@pytest.mark.integration
def test_una_credencial_mala_no_deja_pasar(entorno):
    at = _abrir(entorno)
    at.run()
    at.text_input[0].set_value("ana")
    at.text_input[1].set_value("la-que-no-es")
    at.button[0].click().run()

    assert not at.exception
    assert at.error, "debería avisar de credenciales inválidas"
    # session_state de AppTest no expone .get(); se comprueba por pertenencia.
    assert "auth_ok" not in at.session_state, "no debería haber entrado"


@pytest.mark.integration
def test_cada_analista_solo_ve_su_unidad(entorno):
    """El punto crítico: los resultados traen las tres unidades dentro y el
    tablero los lee tal cual. Sin filtro, Milagro vería Guayaquil."""

    at = _entrar(entorno, "ana")
    assert not at.exception
    assert at.session_state["auth_ok"]
    assert at.session_state["unidades"] == ["GUAYAQUIL"]

    # Dos de las tres cuentas son de Guayaquil.
    cuentas = [m for m in at.metric if m.label == "Cuentas analizadas"]
    assert cuentas and cuentas[0].value == "2", \
        f"debería ver solo las suyas, vio {cuentas[0].value if cuentas else 'nada'}"

    at2 = _entrar(entorno, "beto")
    cuentas2 = [m for m in at2.metric if m.label == "Cuentas analizadas"]
    assert cuentas2 and cuentas2[0].value == "1", "Milagro tiene una sola cuenta"


@pytest.mark.integration
def test_la_matriz_ve_todas_y_puede_elegir_una(entorno):
    at = _entrar(entorno, "central")
    assert not at.exception
    assert at.session_state["matriz"]

    cuentas = [m for m in at.metric if m.label == "Cuentas analizadas"]
    assert cuentas and cuentas[0].value == "3", "la matriz ve las tres"

    # Y puede acotar a una unidad concreta: elegir, no acumular.
    selector = at.sidebar.selectbox[0]
    assert "Todas las unidades" in selector.options
    selector.set_value("MILAGRO").run()
    cuentas = [m for m in at.metric if m.label == "Cuentas analizadas"]
    assert cuentas and cuentas[0].value == "1"


@pytest.mark.integration
def test_un_usuario_sin_unidad_no_ve_datos(entorno):
    """Falla cerrado: sin unidad asignada no se ve nada, no se ve todo."""

    at = _entrar(entorno, "nadie")

    assert not at.exception
    cuentas = [m for m in at.metric if m.label == "Cuentas analizadas"]
    assert cuentas and cuentas[0].value == "0"
    avisos = [e.value for e in [*at.error, *at.warning, *at.sidebar.error]]
    assert any("no verá datos" in a or "No hay resultados de su unidad" in a
               for a in avisos), f"debería explicar por qué no ve nada: {avisos}"


@pytest.mark.integration
def test_sin_resultados_ofrece_calcular_en_vez_de_un_comando(tmp_path):
    """Antes era un callejón sin salida: le decía «ejecute este comando» a quien
    había entrado por el navegador justamente para no escribir comandos."""

    import yaml

    salidas = tmp_path / "vacio"
    salidas.mkdir()
    cfg = yaml.safe_load(open(RAIZ / "config/base.yaml", encoding="utf-8"))
    cfg["rutas"]["salidas"] = str(salidas)
    cfg["seguridad"]["autenticacion_habilitada"] = False
    ruta = tmp_path / "c.yaml"
    ruta.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    at = _abrir(ruta)
    at.run()

    assert not at.exception
    textos = " ".join(b.label for b in at.button)
    assert "Empezar" in textos, "debería ofrecer el botón de calcular"
