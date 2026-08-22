"""Escenarios de trabajo y alcance por unidad de negocio.

Dos cosas que se fijan aquí:

1. **Un analista puede probar sus cambios sin publicarlos**, ver el balance al
   momento, y cada evaluación queda como un punto de la evolución de ese
   alimentador en el tiempo.
2. **Cada usuario ve solo su unidad de negocio**, la matriz las ve todas, y el
   control está donde se leen los datos — no en la interfaz.
"""

import pandas as pd
import pytest

from ptnt.security.auth import UserStore
from ptnt.security.scope import Alcance, AlcanceError, exigir_entidad, resolver_entidad
from ptnt.workspace import (AlmacenEscenarios, CambioPropuesto, EscenarioError,
                            EstadoEscenario, aplicar_cambios, comparar)


# --------------------------------------------------------------------------- #
# Alcance por unidad de negocio
# --------------------------------------------------------------------------- #
class _Alim:
    def __init__(self, unidad, subestacion):
        self.unidad_negocio = unidad
        self.subestacion = subestacion


class _Jerarquia:
    def __init__(self):
        self.alimentadores = {
            "GYE-01": _Alim("CNEL-GYE", "SE-NORTE"),
            "GYE-02": _Alim("CNEL-GYE", "SE-NORTE"),
            "GYE-03": _Alim("CNEL-GYE", "SE-SUR"),
            "MAN-01": _Alim("CNEL-MAN", "SE-PORTOVIEJO"),
            "MAN-02": _Alim("CNEL-MAN", "SE-PORTOVIEJO"),
        }


@pytest.fixture
def jerarquia():
    return _Jerarquia()


@pytest.mark.unit
def test_un_usuario_sin_unidad_asignada_no_ve_nada():
    """Es la decisión que evita la fuga: lo contrario —vacío significa todas—
    convierte un alta a medias en el padrón de otra unidad en manos de quien no
    debe."""

    a = Alcance(usuario="nuevo")

    assert a.sin_alcance
    assert not a.puede_ver("CNEL-GYE")
    assert not a.puede_ver(None)
    with pytest.raises(AlcanceError, match="no tiene ninguna unidad"):
        a.exigir("CNEL-GYE")


@pytest.mark.unit
def test_cada_unidad_ve_lo_suyo_y_no_lo_ajeno(jerarquia):
    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))

    exigir_entidad(gye, jerarquia, "GYE-01", "ALIMENTADOR")   # no lanza

    with pytest.raises(AlcanceError, match="Fuera de su alcance"):
        exigir_entidad(gye, jerarquia, "MAN-01", "ALIMENTADOR")


@pytest.mark.unit
def test_la_matriz_ve_todas_las_unidades(jerarquia):
    matriz = Alcance(usuario="central", matriz=True)

    for codigo in ("GYE-01", "MAN-01"):
        r = exigir_entidad(matriz, jerarquia, codigo, "ALIMENTADOR")
        assert r.encontrada
    assert "todas" in matriz.descripcion()


@pytest.mark.unit
def test_lo_que_no_esta_en_el_catalogo_no_se_entrega(jerarquia):
    """Si no se puede demostrar de quién es, no se entrega. Para la matriz sí,
    porque suele ser justo lo que hay que arreglar."""

    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))
    with pytest.raises(AlcanceError, match="no está en el catálogo"):
        exigir_entidad(gye, jerarquia, "XXX-99", "ALIMENTADOR")

    r = exigir_entidad(Alcance(usuario="c", matriz=True), jerarquia,
                       "XXX-99", "ALIMENTADOR")
    assert not r.encontrada, "la matriz pasa, pero enterada de que no está"


@pytest.mark.unit
def test_una_subestacion_resuelve_sus_alimentadores(jerarquia):
    r = resolver_entidad(jerarquia, "SE-NORTE", "SUBESTACION")

    assert r.encontrada
    assert r.unidad_negocio == "CNEL-GYE"
    assert r.alimentadores == ["GYE-01", "GYE-02"]


@pytest.mark.unit
def test_una_subestacion_repartida_entre_dos_unidades_se_bloquea(jerarquia):
    """Es un error del catálogo, no un caso a resolver en silencio: si se
    dejara pasar, un usuario de una unidad analizaría alimentadores de otra."""

    jerarquia.alimentadores["GYE-02"].unidad_negocio = "CNEL-MAN"
    r = resolver_entidad(jerarquia, "SE-NORTE", "SUBESTACION")

    assert r.encontrada and r.unidad_negocio == ""
    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))
    with pytest.raises(AlcanceError, match="No se pudo determinar"):
        exigir_entidad(gye, jerarquia, "SE-NORTE", "SUBESTACION")


@pytest.mark.unit
def test_el_filtro_de_datos_sin_columna_de_unidad_devuelve_vacio():
    """Un conjunto sin la unidad no es «de todos»: es uno que no se puede
    filtrar, y devolverlo entero sería entregar lo que no corresponde."""

    df = pd.DataFrame({"alimentador": ["GYE-01", "MAN-01"], "pnt": [5.0, 9.0]})
    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))

    assert gye.filtrar(df).empty
    # Con la columna, filtra lo suyo.
    df2 = df.assign(unidad_negocio=["CNEL-GYE", "CNEL-MAN"])
    assert list(gye.filtrar(df2)["alimentador"]) == ["GYE-01"]
    # Y la matriz recibe todo.
    assert len(Alcance(usuario="c", matriz=True).filtrar(df2)) == 2


@pytest.mark.unit
def test_el_administrador_ve_todas_sin_declararlo(tmp_path):
    """Puede crear usuarios, así que podría asignarse cualquier unidad: negarle
    los datos sería teatro."""

    store = UserStore(tmp_path / "u.json")
    store.add_user("admin1", "ClaveLarga123", "admin")
    a = Alcance.desde_usuario(store.authenticate("admin1", "ClaveLarga123"))
    assert a.matriz

    store.add_user("ana", "ClaveLarga123", "analyst", unidades=["CNEL-GYE"])
    b = Alcance.desde_usuario(store.authenticate("ana", "ClaveLarga123"))
    assert not b.matriz and b.unidades == frozenset({"CNEL-GYE"})


@pytest.mark.unit
def test_el_alcance_sobrevive_al_guardado(tmp_path):
    ruta = tmp_path / "u.json"
    UserStore(ruta).add_user("ana", "ClaveLarga123", "analyst",
                             unidades=["CNEL-GYE", "CNEL-MAN"])

    u = UserStore(ruta).authenticate("ana", "ClaveLarga123")
    assert set(u.unidades) == {"CNEL-GYE", "CNEL-MAN"}


@pytest.mark.unit
def test_un_archivo_de_usuarios_anterior_sigue_abriendo(tmp_path):
    """Una versión nueva no puede dejar a nadie sin poder entrar."""

    import json
    ruta = tmp_path / "u.json"
    ruta.write_text(json.dumps({"usuarios": [
        {"username": "viejo", "password_hash": "x", "role": "viewer",
         "disabled": False, "campo_desconocido": 1}]}), encoding="utf-8")

    store = UserStore(ruta)
    assert "viejo" in store
    assert store.usuarios()[0].unidades == []


# --------------------------------------------------------------------------- #
# Escenarios: acumular y evaluar
# --------------------------------------------------------------------------- #
@pytest.fixture
def almacen(tmp_path):
    with AlmacenEscenarios(tmp_path / "esc.db") as a:
        yield a


def _abrir(almacen, nombre="prueba", usuario="ana", unidad="CNEL-GYE",
           entidad="GYE-01", nivel="ALIMENTADOR", alims=None):
    return almacen.abrir(nombre=nombre, usuario=usuario, unidad_negocio=unidad,
                         nivel=nivel, entidad=entidad,
                         alimentadores=alims or [entidad])


def _cambio(guid="T1", campo="potencia_nominal_kva", valor=75.0,
            capa="ptnt_puesto_transformacion"):
    return CambioPropuesto(capa=capa, elemento_guid=guid, campo=campo,
                           valor_despues=valor, autor="ana")


@pytest.mark.unit
def test_acumular_cambios_no_toca_el_modelo(almacen):
    """La razón de existir del módulo: probar sin comprometer."""

    esc = _abrir(almacen)
    almacen.acumular(esc.escenario_id, [_cambio(), _cambio("T2", valor=50.0)])

    cambios = almacen.cambios(esc.escenario_id)
    assert len(cambios) == 2
    assert [c.secuencia for c in cambios] == [1, 2]
    assert almacen.obtener(esc.escenario_id).estado == EstadoEscenario.ABIERTO.value


@pytest.mark.unit
def test_cada_evaluacion_es_una_iteracion_que_se_conserva(almacen):
    """Las iteraciones son la evolución del alimentador en el tiempo. Sustituir
    la última «porque salió mal» borraría justo lo que explica el cambio de
    rumbo."""

    esc = _abrir(almacen)
    almacen.acumular(esc.escenario_id, [_cambio()])
    almacen.registrar_iteracion(esc.escenario_id, metricas={"pnt_pct": 9.1},
                                n_cambios=1)
    almacen.acumular(esc.escenario_id, [_cambio("T2", valor=50.0)])
    almacen.registrar_iteracion(esc.escenario_id, metricas={"pnt_pct": 6.4},
                                n_cambios=2)

    its = almacen.iteraciones(esc.escenario_id)
    assert [i.n for i in its] == [1, 2]
    assert [i.metricas["pnt_pct"] for i in its] == [9.1, 6.4]

    evo = almacen.evolucion(esc.escenario_id)
    assert list(evo["iteracion"]) == [1, 2]
    assert almacen.obtener(esc.escenario_id).estado == EstadoEscenario.EVALUADO.value


@pytest.mark.unit
def test_la_comparacion_avisa_si_la_red_cambio_entre_medias(almacen):
    """Sin este aviso, una carga del SIG entre las dos evaluaciones se
    atribuiría a los cambios del analista."""

    esc = _abrir(almacen)
    almacen.registrar_iteracion(esc.escenario_id, metricas={"pnt_pct": 9.0},
                                n_cambios=1, hash_topologia="aaa")
    almacen.registrar_iteracion(esc.escenario_id, metricas={"pnt_pct": 6.0},
                                n_cambios=2, hash_topologia="bbb")

    comp = comparar(almacen, esc.escenario_id, 1, 2)
    assert comp.diferencias["pnt_pct"][2] == -3.0
    assert any("versiones distintas" in a for a in comp.advertencias)

    # Con la misma versión, no hay aviso.
    esc2 = _abrir(almacen, nombre="otro")
    almacen.registrar_iteracion(esc2.escenario_id, metricas={"pnt_pct": 9.0},
                                n_cambios=1, hash_topologia="aaa")
    almacen.registrar_iteracion(esc2.escenario_id, metricas={"pnt_pct": 6.0},
                                n_cambios=2, hash_topologia="aaa")
    assert not comparar(almacen, esc2.escenario_id, 1, 2).advertencias


@pytest.mark.unit
def test_un_escenario_aplicado_ya_no_admite_cambios(almacen):
    """Modificarlo dejaría el histórico contando una evolución que no ocurrió
    así."""

    esc = _abrir(almacen)
    almacen.cambiar_estado(esc.escenario_id, EstadoEscenario.APLICADO)

    with pytest.raises(EscenarioError, match="ya no admite"):
        almacen.acumular(esc.escenario_id, [_cambio()])


@pytest.mark.unit
def test_solo_se_listan_los_escenarios_de_la_unidad_del_usuario(almacen):
    """El filtro va en la consulta, no después: traer todo y recortar en memoria
    es el atajo que se olvida el día que alguien añade una vista nueva."""

    _abrir(almacen, nombre="gye", unidad="CNEL-GYE", entidad="GYE-01")
    _abrir(almacen, nombre="man", unidad="CNEL-MAN", entidad="MAN-01",
           usuario="beto")

    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))
    assert [e.nombre for e in almacen.listar(alcance=gye)] == ["gye"]

    matriz = Alcance(usuario="central", matriz=True)
    assert len(almacen.listar(alcance=matriz)) == 2

    sin = Alcance(usuario="nuevo")
    assert almacen.listar(alcance=sin) == []


@pytest.mark.unit
def test_la_evolucion_de_un_alimentador_cruza_varios_escenarios(almacen):
    """La historia de un alimentador no cabe en un escenario: se abre uno nuevo
    cada vez que se aplica el anterior."""

    e1 = _abrir(almacen, nombre="enero", entidad="GYE-01")
    almacen.registrar_iteracion(e1.escenario_id, metricas={"pnt_pct": 9.0},
                                n_cambios=2)
    almacen.cambiar_estado(e1.escenario_id, EstadoEscenario.APLICADO)

    e2 = _abrir(almacen, nombre="febrero", entidad="GYE-01")
    almacen.registrar_iteracion(e2.escenario_id, metricas={"pnt_pct": 7.5},
                                n_cambios=3)

    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))
    evo = almacen.evolucion_de_entidad("GYE-01", alcance=gye)

    assert len(evo) == 2
    assert list(evo["escenario"]) == ["enero", "febrero"]
    assert list(evo["pnt_pct"]) == [9.0, 7.5]


@pytest.mark.unit
def test_la_evolucion_respeta_el_alcance(almacen):
    e = _abrir(almacen, unidad="CNEL-MAN", entidad="MAN-01")
    almacen.registrar_iteracion(e.escenario_id, metricas={"pnt_pct": 9.0},
                                n_cambios=1)

    gye = Alcance(usuario="ana", unidades=frozenset({"CNEL-GYE"}))
    assert almacen.evolucion_de_entidad("MAN-01", alcance=gye).empty


# --------------------------------------------------------------------------- #
# Aplicación de cambios sobre el modelo
# --------------------------------------------------------------------------- #
class _Modelo:
    """Modelo mínimo con la forma que usa `aplicar_cambios`."""

    def __init__(self):
        self.transformer_sites = {
            "N1": {"site_id": "T1", "kvas": 50.0, "config": "SIMPLE"},
            "N2": {"site_id": "T2", "kvas": 75.0, "config": "BANCO_3"},
        }
        self.customer_nodes = {
            "N1": [{"guid": "C1", "kwh": 300.0}, {"guid": "C2", "kwh": 250.0}],
        }


@pytest.mark.unit
def test_los_cambios_se_aplican_sobre_una_copia():
    """El modelo oficial no se toca en ningún momento: es el mecanismo, no un
    detalle de implementación."""

    m = _Modelo()
    copia, aplicados, fallidos = aplicar_cambios(
        m, [_cambio("T1", campo="kvas", valor=100.0)])

    assert aplicados == 1 and not fallidos
    assert copia.transformer_sites["N1"]["kvas"] == 100.0
    assert m.transformer_sites["N1"]["kvas"] == 50.0, "el original no cambió"


@pytest.mark.unit
def test_un_cambio_sobre_un_elemento_inexistente_se_reporta():
    """Callarlo sería peor que fallar: el usuario creería que probó algo que en
    realidad no se probó, y decidiría sobre eso."""

    m = _Modelo()
    _, aplicados, fallidos = aplicar_cambios(
        m, [_cambio("NO_EXISTE", campo="kvas", valor=100.0)])

    assert aplicados == 0 and len(fallidos) == 1
    assert "no está en la red migrada" in fallidos[0].motivo


@pytest.mark.unit
def test_los_clientes_se_encuentran_dentro_de_las_listas():
    """El modelo guarda unos elementos como nodo->dict y otros como nodo->[dict]."""

    m = _Modelo()
    copia, aplicados, fallidos = aplicar_cambios(
        m, [_cambio("C2", campo="kwh", valor=10.0, capa="ptnt_cliente")])

    assert aplicados == 1 and not fallidos
    assert copia.customer_nodes["N1"][1]["kwh"] == 10.0
    assert copia.customer_nodes["N1"][0]["kwh"] == 300.0, "no tocó al vecino"


@pytest.mark.unit
def test_crear_y_eliminar_no_se_evaluan_a_medias():
    """Cambian la topología y exigen rehacer el grafo. Simularlo a medias daría
    un balance que parece bueno y no lo es."""

    m = _Modelo()
    cam = _cambio("T1", campo="kvas", valor=1.0)
    cam.operacion = "ELIMINAR"
    _, aplicados, fallidos = aplicar_cambios(m, [cam])

    assert aplicados == 0
    assert "no se puede evaluar en un escenario" in fallidos[0].motivo


@pytest.mark.unit
def test_la_evaluacion_recalcula_el_porcentaje_no_lo_promedia(almacen, monkeypatch):
    """Promediar porcentajes de alimentadores de tamaños distintos da un número
    que no es el de nadie."""

    from ptnt.workspace import evaluacion as ev

    class _Bal:
        def __init__(self, entrada, pnt):
            self.e_input_kwh = entrada
            self.e_billed_kwh = entrada - pnt
            self.loss_technical_kwh = 0.0
            self.ntl_kwh = pnt
            self.ntl_pct = 100.0 * pnt / entrada
            self.balance_type = type("T", (), {"value": "MEDIDO"})()
            # Como el BalanceResult real: siempre trae ambas listas.
            self.warnings = []
            self.controls = []

    class _Res:
        def __init__(self, entrada, pnt):
            self.balance = _Bal(entrada, pnt)
            self.powerflow_converged = True
            self.v_min_pu = 0.97

    # Uno grande con poca PNT y uno chico con mucha: el promedio simple daría
    # 27,5 % y el correcto es 6,7 %.
    datos = {"A": (1_000_000.0, 50_000.0), "B": (20_000.0, 10_000.0)}
    monkeypatch.setattr(ev, "run_grid_analysis",
                        lambda m, cfg, **kw: _Res(*datos[m]), raising=False)
    monkeypatch.setitem(
        __import__("sys").modules, "ptnt.grid_pipeline",
        type("M", (), {"run_grid_analysis":
                       staticmethod(lambda m, cfg, **kw: _Res(*datos[m]))}))

    esc = _abrir(almacen, entidad="SE-X", nivel="SUBESTACION",
                 alims=["A", "B"])
    res = ev.evaluar_escenario(esc, [], cfg=None, cargar_red=lambda c: c)

    assert res.metricas["alimentadores"] == 2
    assert res.metricas["pnt_pct"] == pytest.approx(5.88, abs=0.05)
    assert res.metricas["pnt_pct"] < 27.5, "no promedió los porcentajes"


@pytest.mark.unit
def test_la_evaluacion_avisa_si_no_pudo_aplicar_todo(almacen):
    """El resultado no refleja lo que se quería probar, y eso hay que decirlo
    en el número, no en un registro que nadie lee."""

    from ptnt.workspace.evaluacion import CambioNoAplicado, ResultadoEvaluacion

    r = ResultadoEvaluacion(metricas={"pnt_pct": 6.0, "alimentadores": 1},
                            aplicados=2)
    assert r.confiable

    r.no_aplicados.append(CambioNoAplicado("ptnt_cliente", "C9", "kwh", "no está"))
    assert not r.confiable
    assert "NO se" in r.lectura() and "no refleja" in r.lectura()


@pytest.mark.unit
def test_el_tipo_de_balance_viaja_con_la_iteracion(almacen, monkeypatch):
    """Un escenario existe para decidir sobre su número. Dar una PNT sin decir
    si es medida o estimada convierte una estimación en un hecho."""

    from ptnt.workspace import evaluacion as ev

    class _Ctl:
        def __init__(self, code, detail):
            self.code, self.detail, self.triggered = code, detail, True

    class _Bal:
        def __init__(self, tipo):
            self.e_input_kwh = 1000.0
            self.e_billed_kwh = 1100.0
            self.loss_technical_kwh = 50.0
            self.ntl_kwh = -150.0
            self.ntl_pct = -15.0
            self.balance_type = type("T", (), {"value": tipo})()
            self.warnings = ["Balance INDICATIVO: sin medición de cabecera."]
            self.controls = [_Ctl("C01", "PNT negativa")]

    class _Res:
        def __init__(self, tipo):
            self.balance = _Bal(tipo)
            self.powerflow_converged = True
            self.v_min_pu = 0.96

    tipos = {"A": "MEDIDO", "B": "INDICATIVO"}
    monkeypatch.setitem(
        __import__("sys").modules, "ptnt.grid_pipeline",
        type("M", (), {"run_grid_analysis":
                       staticmethod(lambda m, cfg, **kw: _Res(tipos[m]))}))

    esc = _abrir(almacen, entidad="SE-X", nivel="SUBESTACION", alims=["A", "B"])
    res = ev.evaluar_escenario(esc, [], cfg=None, cargar_red=lambda c: c)

    # Uno medido y uno estimado: el conjunto NO es verificable.
    assert res.metricas["tipo_balance"] == "INDICATIVO"
    assert not res.verificable
    assert "estimación" in res.lectura()
    # El control que saltó llega al usuario, no se queda en el cálculo.
    assert any("C01" in c for c in res.controles)
    assert any("cabecera" in a for a in res.advertencias)


@pytest.mark.unit
def test_la_cabecera_incompleta_no_pasa_por_medida(almacen, monkeypatch):
    """Medir la mitad de una subestación y presentarla como medida es peor que
    no medir nada: nadie sabría qué mitad creerse."""

    from ptnt.workspace import evaluacion as ev

    class _Bal:
        def __init__(self, medido):
            self.e_input_kwh = 1000.0
            self.e_billed_kwh = 900.0
            self.loss_technical_kwh = 40.0
            self.ntl_kwh = 60.0
            self.ntl_pct = 6.0
            self.balance_type = type(
                "T", (), {"value": "MEDIDO" if medido else "INDICATIVO"})()
            self.warnings = []
            self.controls = []

    class _Res:
        def __init__(self, medido):
            self.balance = _Bal(medido)
            self.powerflow_converged = True
            self.v_min_pu = 0.96

    monkeypatch.setitem(
        __import__("sys").modules, "ptnt.grid_pipeline",
        type("M", (), {"run_grid_analysis": staticmethod(
            lambda m, cfg, **kw: _Res(kw.get("head_energy_kwh") is not None))}))

    esc = _abrir(almacen, entidad="SE-X", nivel="SUBESTACION", alims=["A", "B"])
    res = ev.evaluar_escenario(esc, [], cfg=None, cargar_red=lambda c: c,
                               cabecera={"A": 1000.0})   # falta B

    assert res.metricas["tipo_balance"] == "INDICATIVO"
    assert any("sin energía de cabecera" in a and "B" in a
               for a in res.advertencias)


@pytest.mark.unit
def test_la_comparacion_avisa_si_cambio_el_tipo_de_balance(almacen):
    """Restar una PNT medida de una estimada da un número, no una conclusión."""

    from ptnt.workspace import comparar

    esc = _abrir(almacen)
    almacen.registrar_iteracion(
        esc.escenario_id,
        metricas={"pnt_pct": -7.8, "tipo_balance": "INDICATIVO"},
        n_cambios=2)
    almacen.registrar_iteracion(
        esc.escenario_id,
        metricas={"pnt_pct": 4.3, "tipo_balance": "MEDIDO"},
        n_cambios=3)

    comp = comparar(almacen, esc.escenario_id, 1, 2)
    assert any("tipo de balance cambió" in a for a in comp.advertencias)
    assert "pnt_pct" in comp.diferencias
    # y el tipo, por ser texto, no se resta
    assert "tipo_balance" not in comp.diferencias


@pytest.mark.unit
def test_lectura_de_la_energia_de_cabecera(tmp_path):
    """Varios períodos del mismo alimentador se suman; faltar una columna se
    dice por su nombre."""

    import pandas as pd

    from ptnt.workspace import energia_cabecera

    ruta = tmp_path / "cabecera.csv"
    pd.DataFrame([
        {"feeder_code": "GYE-01", "period": "2026-06", "kwh_delivered": 1000.0},
        {"feeder_code": "GYE-01", "period": "2026-07", "kwh_delivered": 1100.0},
        {"feeder_code": "GYE-02", "period": "2026-07", "kwh_delivered": 500.0},
    ]).to_csv(ruta, index=False)

    medicion = energia_cabecera(ruta)
    assert medicion == {"GYE-01": 2100.0, "GYE-02": 500.0}

    malo = tmp_path / "malo.csv"
    pd.DataFrame([{"alimentador": "GYE-01", "kwh": 1.0}]).to_csv(malo, index=False)
    with pytest.raises(ValueError, match="feeder_code"):
        energia_cabecera(malo)

    with pytest.raises(FileNotFoundError):
        energia_cabecera(tmp_path / "no_existe.csv")
