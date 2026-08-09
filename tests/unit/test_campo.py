"""Trabajo de campo: GeoPackage, edición topológica, órdenes y sincronización.

El eje de estas pruebas es que el trabajo de campo **no degrade el modelo**. Un
técnico puede equivocarse de elemento, arrastrar sin querer o capturar con el GPS
derivando bajo un árbol; el sistema tiene que absorber eso sin que el SIG quede
peor que antes de la visita.
"""

import uuid

import pandas as pd
import pytest

from ptnt.field.gpkg import (
    Campo,
    Capa,
    GeoPackage,
    leer_geometria,
    linea,
    punto,
)
from ptnt.field.package import AreaTrabajo, construir_paquete, huella_paquete
from ptnt.field.schema import (
    VERSION_ESQUEMA,
    capas_red,
    esquema_para_movil,
    todas_las_capas,
)
from ptnt.field.sync import (
    EstadoRevision,
    HistoricoCambios,
    LoteSincronizacion,
    Severidad,
    aplicar,
    recibir_paquete,
    revisar,
)
from ptnt.field.topology_edit import (
    Conexion,
    Elemento,
    GrafoEdicion,
    TipoRelacion,
)
from ptnt.field.workorders import (
    Asignacion,
    EstadoOrden,
    RegistroCampo,
    RolCampo,
    TransicionInvalida,
)


# --------------------------------------------------------------------------- #
# GeoPackage
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_geometria_ida_y_vuelta():
    """El binario GeoPackage debe decodificarse exactamente: es lo que el móvil
    lee para dibujar y lo que escribe al editar."""

    g = leer_geometria(punto(631850.5, 9762250.25))
    assert g["tipo"] == "Point"
    assert g["coords"][0] == pytest.approx((631850.5, 9762250.25))
    assert g["srid"] == 32717

    pts = [(1.0, 2.0), (3.5, 4.5), (6.0, 7.0)]
    g = leer_geometria(linea(pts))
    assert g["tipo"] == "LineString"
    assert g["coords"] == [pytest.approx(p) for p in pts]


@pytest.mark.unit
def test_geometria_lleva_envolvente():
    """El móvil descarta geometrías fuera de la vista **sin decodificar el WKB**:
    sin envolvente tendría que abrir cada una en cada desplazamiento."""

    blob = punto(100.0, 200.0)
    flags = blob[3]
    assert (flags >> 1) & 0b111 == 1, "debe declarar envolvente XY"


@pytest.mark.unit
def test_geopackage_se_crea_con_los_metadatos_ogc(tmp_path):
    """Sin las tablas de metadatos no es un GeoPackage: no abriría en QGIS ni en
    ArcGIS, y el requisito es que el archivo sea estándar."""

    with GeoPackage(tmp_path / "p.gpkg") as gp:
        gp.crear_capa(Capa("prueba", "POINT", [Campo("nombre")]))
        tablas = {r[0] for r in gp.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"gpkg_contents", "gpkg_spatial_ref_sys",
                "gpkg_geometry_columns"} <= tablas
        srids = {r[0] for r in gp.con.execute(
            "SELECT srs_id FROM gpkg_spatial_ref_sys")}
        assert 32717 in srids and 4326 in srids


@pytest.mark.unit
def test_insertar_no_pierde_columnas_cuando_las_filas_difieren(tmp_path):
    """REGRESIÓN: tomar las columnas del PRIMER diccionario perdía datos en
    silencio — un cambio propagado llegaba sin su `propagado_de` y el supervisor
    no podía distinguir lo que el técnico movió de lo que se movió solo."""

    with GeoPackage(tmp_path / "p.gpkg") as gp:
        gp.crear_capa(Capa("t", "NONE", [Campo("a"), Campo("b"), Campo("c")]))
        gp.insertar("t", [{"a": "1"}, {"a": "2", "b": "B"}, {"c": "C"}])
        filas = gp.leer("t")
    assert filas[1]["b"] == "B"
    assert filas[2]["c"] == "C"


@pytest.mark.unit
def test_indice_espacial_permite_consulta_por_ventana(tmp_path):
    with GeoPackage(tmp_path / "p.gpkg") as gp:
        gp.crear_capa(Capa("pts", "POINT", [Campo("guid")]))
        gp.insertar("pts", [
            {"guid": "a", "geom": punto(0, 0)},
            {"guid": "b", "geom": punto(1000, 1000)},
        ])
        assert gp.reindexar("pts") == 2
        gp.actualizar_extension("pts")
        r = gp.con.execute(
            'SELECT COUNT(*) FROM "rtree_pts_geom" '
            "WHERE maxx >= -10 AND minx <= 10 AND maxy >= -10 AND miny <= 10"
        ).fetchone()[0]
        assert r == 1


@pytest.mark.unit
def test_manifiesto_viaja_dentro_del_paquete(tmp_path):
    """En campo el .gpkg viaja solo: si el manifiesto fuera un archivo aparte, se
    perdería la trazabilidad de qué versión de red se editó."""

    ruta = tmp_path / "p.gpkg"
    with GeoPackage(ruta) as gp:
        gp.escribir_manifiesto({"usuario": "jperez", "version_red": "v42",
                                "ordenes": ["OT-1", "OT-2"]})
    with GeoPackage(ruta) as gp:
        m = gp.leer_manifiesto()
    assert m["usuario"] == "jperez"
    assert m["ordenes"] == ["OT-1", "OT-2"]


# --------------------------------------------------------------------------- #
# Esquema
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_modelo_puesto_unidad_es_editable_por_separado():
    """Modelar las unidades como tabla aparte —y no como columnas kva_1/2/3— es
    lo que permite agregar, editar o quitar una unidad sin tocar las otras."""

    nombres = {c.nombre for c in capas_red()}
    assert "ptnt_puesto_transformacion" in nombres
    assert "ptnt_unidad_transformacion" in nombres
    unidad = next(c for c in capas_red()
                  if c.nombre == "ptnt_unidad_transformacion")
    campos = {f.nombre for f in unidad.campos}
    assert "puesto_guid" in campos and "fase" in campos
    assert unidad.tipo_geometria == "NONE"     # está EN el puesto


@pytest.mark.unit
def test_todo_elemento_lleva_guid_estable():
    """El `fid` es el rowid local y cambia entre paquetes; sin `guid` cada
    sincronización crearía duplicados en vez de actualizar."""

    for capa in capas_red():
        assert "guid" in {f.nombre for f in capa.campos}, capa.nombre


@pytest.mark.unit
def test_el_esquema_para_el_movil_trae_dominios_y_etiquetas():
    """El móvil construye los formularios desde esta descripción: agregar un campo
    no debe obligar a publicar una versión nueva en la tienda."""

    esq = esquema_para_movil()
    assert esq["version_esquema"] == VERSION_ESQUEMA
    cliente = next(c for c in esq["capas"] if c["nombre"] == "ptnt_cliente")
    hallazgo = next(f for f in cliente["campos"] if f["nombre"] == "hallazgo")
    assert hallazgo["dominio"] and "CONEXION_DIRECTA" in hallazgo["dominio"]
    assert all(f["etiqueta"] for f in cliente["campos"])


@pytest.mark.unit
def test_la_foto_guarda_ubicacion_y_fecha_como_metadatos():
    """Una foto sin dónde ni cuándo no es evidencia: es una imagen."""

    foto = next(c for c in todas_las_capas() if c.nombre == "ptnt_foto")
    campos = {f.nombre for f in foto.campos}
    assert {"lat", "lon", "tomada_en", "precision_m", "hash_sha256"} <= campos
    assert next(f for f in foto.campos if f.nombre == "tomada_en").obligatorio


# --------------------------------------------------------------------------- #
# Edición topológica
# --------------------------------------------------------------------------- #
def _grafo():
    els = {
        "TX": Elemento("TX", "ptnt_puesto_transformacion", [(100.0, 100.0)]),
        "U1": Elemento("U1", "ptnt_unidad_transformacion", [(100.0, 100.0)]),
        "U2": Elemento("U2", "ptnt_unidad_transformacion", [(100.0, 100.0)]),
        "POSTE": Elemento("POSTE", "ptnt_poste", [(130.0, 100.0)]),
        "CLI": Elemento("CLI", "ptnt_cliente", [(150.0, 120.0)],
                        {"cuenta_contrato": "300001"}),
        "ACO": Elemento("ACO", "ptnt_tramo", [(130.0, 100.0), (150.0, 120.0)]),
    }
    cons = [
        Conexion("U1", "TX", TipoRelacion.PERTENECE_A),
        Conexion("U2", "TX", TipoRelacion.PERTENECE_A),
        Conexion("CLI", "ACO", TipoRelacion.ACOMETIDA),
        Conexion("ACO", "POSTE", TipoRelacion.COMPARTE_VERTICE),
        Conexion("TX", "CLI", TipoRelacion.ALIMENTA),
    ]
    return GrafoEdicion(els, cons)


@pytest.mark.unit
def test_mover_un_cliente_arrastra_el_extremo_de_su_acometida():
    """EL REQUISITO CENTRAL. Sin esto, un técnico que corrige la posición de un
    medidor deja la acometida colgando en el aire."""

    g = _grafo()
    r = g.mover("CLI", 158.0, 126.0)

    assert g.elementos["CLI"].coords == [(158.0, 126.0)]
    aco = g.elementos["ACO"].coords
    assert aco[0] == (130.0, 100.0), "el extremo del poste NO debe moverse"
    assert aco[1] == pytest.approx((158.0, 126.0))
    assert r.n_propagados == 1


@pytest.mark.unit
def test_mover_un_puesto_arrastra_sus_unidades():
    """Las unidades no tienen posición propia: están EN el puesto."""

    g = _grafo()
    r = g.mover("TX", 110.0, 105.0)
    assert g.elementos["U1"].coords == [(110.0, 105.0)]
    assert g.elementos["U2"].coords == [(110.0, 105.0)]
    assert {c.guid for c in r.cambios} == {"TX", "U1", "U2"}


@pytest.mark.unit
def test_la_relacion_electrica_no_arrastra_geometria():
    """LA REGLA QUE MÁS CARO SALE EQUIVOCAR. Un transformador alimenta a cien
    clientes: moverlo no debe mover el barrio."""

    g = _grafo()
    antes = list(g.elementos["CLI"].coords)
    g.mover("TX", 110.0, 105.0)
    assert g.elementos["CLI"].coords == antes
    assert TipoRelacion.ALIMENTA.propaga_geometria is False


@pytest.mark.unit
def test_la_propagacion_tiene_tope_de_seguridad():
    """Si una relación viniera mal clasificada, sin tope un arrastre podría
    reescribir miles de elementos y el técnico no podría deshacerlo en campo."""

    els = {f"N{i}": Elemento(f"N{i}", "ptnt_tramo", [(0.0, 0.0)])
           for i in range(60)}
    cons = [Conexion(f"N{i}", f"N{i+1}", TipoRelacion.PERTENECE_A)
            for i in range(59)]
    g = GrafoEdicion(els, cons)
    r = g.mover("N0", 5.0, 5.0, max_propagacion=10)
    assert len(r.cambios) <= 11
    assert any("tope" in a for a in r.advertencias)


@pytest.mark.unit
def test_snap_ajusta_al_vertice_cercano_y_dice_a_cual():
    """Se aplica al capturar, para que el elemento nuevo quede sobre el poste y
    no a 40 cm."""

    g = _grafo()
    x, y, guid = g.snap(130.8, 100.5, tolerancia_m=3.0)
    assert (x, y) == (130.0, 100.0)
    assert guid in ("POSTE", "ACO")

    x2, y2, guid2 = g.snap(200.0, 200.0, tolerancia_m=3.0)
    assert (x2, y2) == (200.0, 200.0) and guid2 is None


@pytest.mark.unit
def test_no_se_puede_eliminar_un_puesto_con_dependientes():
    """Dejaría clientes sin transformador y el balance de la zona sin sentido.
    La app lo impide **en el sitio**, cuando el técnico aún puede arreglarlo."""

    g = _grafo()
    ok, bloqueos = g.puede_eliminar("TX")
    assert ok is False
    assert bloqueos and "dependiente" in bloqueos[0]

    ok2, _ = g.eliminar("TX")
    assert ok2 is False
    assert "TX" in g.elementos


@pytest.mark.unit
def test_se_puede_eliminar_un_elemento_sin_dependientes():
    g = _grafo()
    ok, _ = g.eliminar("CLI")
    assert ok and "CLI" not in g.elementos


@pytest.mark.unit
def test_relacionados_alimenta_el_panel_puesto_unidad():
    g = _grafo()
    rel = g.relacionados("TX")
    tipos = {r["relacion"] for r in rel}
    assert "PERTENECE_A" in tipos and "ALIMENTA" in tipos
    unidades = [r for r in rel if r["capa"] == "ptnt_unidad_transformacion"]
    assert len(unidades) == 2


@pytest.mark.unit
def test_validar_detecta_clientes_sin_acometida():
    """Es más barato que el técnico lo arregle en el sitio que descubrirlo tres
    días después en la oficina."""

    els = {"C": Elemento("C", "ptnt_cliente", [(0.0, 0.0)],
                         {"cuenta_contrato": "999"})}
    g = GrafoEdicion(els, [])
    problemas = g.validar()
    assert any("999" in p for p in problemas)


# --------------------------------------------------------------------------- #
# Órdenes de trabajo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_usuario_movil_guarda_solo_el_hash(tmp_path):
    reg = RegistroCampo(tmp_path / "r.json")
    u = reg.crear_usuario("jperez", "Juan Pérez", "clave-larga-2026")
    assert u.password_hash and "clave-larga-2026" not in u.password_hash
    reg.save()
    # La contraseña en claro no debe aparecer en ningún archivo del almacén,
    # tampoco en el WAL: ahí es donde acaban las escrituras recientes.
    for f in tmp_path.iterdir():
        assert b"clave-larga-2026" not in f.read_bytes(), f.name


@pytest.mark.unit
def test_contrasena_corta_se_rechaza(tmp_path):
    reg = RegistroCampo(tmp_path / "r.json")
    with pytest.raises(ValueError, match="8 caracteres"):
        reg.crear_usuario("a", "A", "corta")


@pytest.mark.unit
def test_vincular_otro_dispositivo_revoca_el_anterior(tmp_path):
    """Un usuario, un dispositivo: si el token no fuera exclusivo, un teléfono
    extraviado seguiría sincronizando a nombre del técnico."""

    reg = RegistroCampo(tmp_path / "r.json")
    reg.crear_usuario("jperez", "Juan", "clave-larga-2026")
    t1 = reg.vincular_dispositivo("jperez", "TEL-1")
    t2 = reg.vincular_dispositivo("jperez", "TEL-2")
    assert t1 != t2
    assert reg.autenticar_token(t1) is None
    assert reg.autenticar_token(t2).usuario == "jperez"


@pytest.mark.unit
def test_revocar_desconecta_el_equipo(tmp_path):
    reg = RegistroCampo(tmp_path / "r.json")
    reg.crear_usuario("jperez", "Juan", "clave-larga-2026")
    t = reg.vincular_dispositivo("jperez", "TEL-1")
    reg.revocar_dispositivo("jperez")
    assert reg.autenticar_token(t) is None


def _ordenes() -> pd.DataFrame:
    return pd.DataFrame([
        {"orden_trabajo": "OT-0001", "nivel": "SECTOR", "entidad": "SEC-1",
         "clientes_a_revisar": 14, "recuperable_kwh_mes": 8000.0,
         "x": 631800.0, "y": 9762200.0, "accion": "Recorrido"},
        {"orden_trabajo": "OT-0002", "nivel": "RUTA_COMERCIAL", "entidad": "R-1",
         "clientes_a_revisar": 100, "recuperable_kwh_mes": 12000.0,
         "x": 632000.0, "y": 9762400.0, "accion": "Relectura"},
    ])


@pytest.mark.unit
def test_asignacion_masiva(tmp_path):
    """Una cuadrilla sale con la jornada completa, no con una orden."""

    reg = RegistroCampo(tmp_path / "r.json")
    reg.crear_usuario("jperez", "Juan", "clave-larga-2026")
    nuevas = reg.asignar(_ordenes(), "jperez")
    assert len(nuevas) == 2
    assert len(reg.de_usuario("jperez")) == 2


@pytest.mark.unit
def test_no_se_reasigna_en_silencio_una_orden_de_otro_tecnico(tmp_path):
    """Que dos cuadrillas vayan al mismo sitio es el desperdicio más común de
    este trabajo."""

    reg = RegistroCampo(tmp_path / "r.json")
    reg.crear_usuario("a", "A", "clave-larga-2026")
    reg.crear_usuario("b", "B", "clave-larga-2026")
    reg.asignar(_ordenes(), "a")
    with pytest.raises(ValueError, match="ya están asignadas"):
        reg.asignar(_ordenes(), "b")


@pytest.mark.unit
def test_la_maquina_de_estados_rechaza_transiciones_invalidas():
    """Una orden no puede "completarse" sin haberse descargado: el informe de
    gestión dejaría de significar algo."""

    a = Asignacion(orden_trabajo="OT-1", asignado_a="x", nivel="SECTOR",
                   entidad="S1")
    with pytest.raises(TransicionInvalida, match="ASIGNADA"):
        a.transicionar(EstadoOrden.COMPLETADA)

    a.transicionar(EstadoOrden.DESCARGADA)
    a.transicionar(EstadoOrden.EN_PROCESO)
    a.transicionar(EstadoOrden.COMPLETADA)
    a.transicionar(EstadoOrden.SINCRONIZADA)
    assert a.fecha_cierre
    with pytest.raises(TransicionInvalida):
        a.transicionar(EstadoOrden.EN_PROCESO)


@pytest.mark.unit
def test_registro_persiste_entre_sesiones(tmp_path):
    ruta = tmp_path / "r.json"
    reg = RegistroCampo(ruta)
    reg.crear_usuario("jperez", "Juan", "clave-larga-2026", rol=RolCampo.SUPERVISOR)
    reg.asignar(_ordenes(), "jperez")
    reg.save()

    otro = RegistroCampo(ruta)
    assert otro.usuarios["jperez"].rol is RolCampo.SUPERVISOR
    assert len(otro.de_usuario("jperez")) == 2


# --------------------------------------------------------------------------- #
# Paquete
# --------------------------------------------------------------------------- #
def _red():
    g_tx, g_cli = str(uuid.uuid4()), str(uuid.uuid4())
    return {
        "ptnt_puesto_transformacion": pd.DataFrame([
            {"guid": g_tx, "codigo": "TS1", "x": 631800.0, "y": 9762200.0,
             "feeder_code": "F1"}]),
        "ptnt_cliente": pd.DataFrame([
            {"guid": g_cli, "cuenta_contrato": "300001", "x": 631820.0,
             "y": 9762220.0, "feeder_code": "F1"},
            # Muy lejos: debe quedar FUERA del recorte
            {"guid": str(uuid.uuid4()), "cuenta_contrato": "999999",
             "x": 700000.0, "y": 9800000.0, "feeder_code": "F9"}]),
    }, g_tx, g_cli


@pytest.mark.unit
def test_el_paquete_recorta_al_area_de_trabajo(tmp_path):
    """Bajar la red completa a un teléfono de gama baja lo vuelve inusable: el
    recorte es la diferencia entre una app que se usa y una que se abandona."""

    red, _, _ = _red()
    a = Asignacion(orden_trabajo="OT-1", asignado_a="jperez", nivel="SECTOR",
                   entidad="S1", x=631800.0, y=9762200.0, radio_m=150.0)
    res = construir_paquete(tmp_path / "p.gpkg", usuario="jperez",
                            asignaciones=[a], red=red)
    assert res.elementos_por_capa["ptnt_cliente"] == 1
    assert res.bytes > 0


@pytest.mark.unit
def test_el_paquete_trae_ordenes_y_manifiesto(tmp_path):
    red, _, _ = _red()
    a = Asignacion(orden_trabajo="OT-1", asignado_a="jperez", nivel="SECTOR",
                   entidad="S1", x=631800.0, y=9762200.0)
    construir_paquete(tmp_path / "p.gpkg", usuario="jperez", asignaciones=[a],
                      red=red, version_red="v42")
    with GeoPackage(tmp_path / "p.gpkg") as gp:
        assert gp.contar("ptnt_orden_trabajo") == 1
        m = gp.leer_manifiesto()
    assert m["usuario"] == "jperez"
    assert m["version_red"] == "v42"
    assert m["esquema"]["version_esquema"] == VERSION_ESQUEMA


@pytest.mark.unit
def test_el_paquete_avisa_si_es_demasiado_grande(tmp_path):
    red = {"ptnt_cliente": pd.DataFrame([
        {"guid": str(i), "x": 631800.0 + i * 0.1, "y": 9762200.0,
         "cuenta_contrato": str(i)} for i in range(50)])}
    a = Asignacion(orden_trabajo="OT-1", asignado_a="u", nivel="SECTOR",
                   entidad="S", x=631800.0, y=9762200.0, radio_m=500.0)
    res = construir_paquete(tmp_path / "p.gpkg", usuario="u", asignaciones=[a],
                            red=red, max_elementos=10)
    assert any("tope recomendado" in x for x in res.advertencias)


@pytest.mark.unit
def test_area_de_trabajo_usa_los_circulos_de_las_ordenes():
    a = AreaTrabajo(circulos=[(0.0, 0.0, 100.0)], margen_m=50.0)
    assert a.contiene(140.0, 0.0)
    assert not a.contiene(200.0, 0.0)
    assert a.area_km2 > 0


@pytest.mark.unit
def test_huella_del_paquete_detecta_modificaciones(tmp_path):
    red, _, _ = _red()
    a = Asignacion(orden_trabajo="OT-1", asignado_a="u", nivel="SECTOR",
                   entidad="S", x=631800.0, y=9762200.0)
    ruta = tmp_path / "p.gpkg"
    construir_paquete(ruta, usuario="u", asignaciones=[a], red=red)
    h1 = huella_paquete(ruta)
    with GeoPackage(ruta) as gp:
        gp.insertar("ptnt_poste", [{"guid": "x", "codigo": "P9"}])
    assert huella_paquete(ruta) != h1


# --------------------------------------------------------------------------- #
# Sincronización y revisión
# --------------------------------------------------------------------------- #
def _paquete_con_cambios(tmp_path, *, con_hueco=False, sin_autor=False):
    red, _, g_cli = _red()
    a = Asignacion(orden_trabajo="OT-1", asignado_a="jperez", nivel="SECTOR",
                   entidad="S1", x=631800.0, y=9762200.0)
    ruta = tmp_path / "ret.gpkg"
    construir_paquete(ruta, usuario="jperez", asignaciones=[a], red=red,
                      version_red="v42")
    secuencias = [1, 3] if con_hueco else [1, 2]
    with GeoPackage(ruta) as gp:
        gp.insertar("ptnt_cambio", [
            {"guid": str(uuid.uuid4()), "secuencia": secuencias[0],
             "capa": "ptnt_cliente", "elemento_guid": g_cli,
             "operacion": "MODIFICAR", "campo": "hallazgo",
             "valor_despues": "CONEXION_DIRECTA", "orden_trabajo": "OT-1",
             "autor": "" if sin_autor else "jperez",
             "ocurrido_en": "2026-08-09T09:00:00Z", "precision_m": 4.0},
            {"guid": str(uuid.uuid4()), "secuencia": secuencias[1],
             "capa": "ptnt_tramo", "elemento_guid": str(uuid.uuid4()),
             "operacion": "MOVER", "geom_despues": "LINESTRING(1 1, 2 2)",
             "orden_trabajo": "OT-1", "autor": "jperez",
             "ocurrido_en": "2026-08-09T09:01:00Z", "precision_m": 3.5,
             "propagado_de": g_cli},
        ])
    return ruta, g_cli


@pytest.mark.unit
def test_recibir_lee_el_diario_y_conserva_la_propagacion(tmp_path):
    ruta, g_cli = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    assert len(lote.cambios) == 2
    assert lote.resumen()["propagados"] == 1
    assert not lote.bloqueado


@pytest.mark.unit
def test_un_paquete_de_otro_usuario_se_rechaza(tmp_path):
    """Aceptarlo rompería la trazabilidad de quién editó qué."""

    ruta, _ = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="otro")
    assert lote.bloqueado
    assert any(h.codigo == "SYNC02" for h in lote.hallazgos)


@pytest.mark.unit
def test_un_hueco_en_la_secuencia_bloquea_el_lote(tmp_path):
    """Un hueco significa ediciones perdidas: aceptar el resto dejaría el modelo
    a medias."""

    ruta, _ = _paquete_con_cambios(tmp_path, con_hueco=True)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    assert lote.bloqueado
    assert any(h.codigo == "SYNC11" for h in lote.hallazgos)


@pytest.mark.unit
def test_un_cambio_sin_autor_se_completa_pero_se_reporta(tmp_path):
    """La edición de red debe ser siempre atribuible.

    Si el móvil no escribe el autor, se completa con el dueño del paquete —el
    trabajo de campo no se descarta por un bug de la app— pero se reporta:
    taparlo en silencio dejaría el defecto llegando cada mes.
    """

    ruta, _ = _paquete_con_cambios(tmp_path, sin_autor=True)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    assert not lote.bloqueado
    assert lote.autores_completados == 1
    assert all(c.autor == "jperez" for c in lote.cambios)
    assert any(h.codigo == "SYNC16" for h in lote.hallazgos)


@pytest.mark.unit
def test_sin_autor_y_sin_manifiesto_si_bloquea():
    """Cuando no hay forma de atribuir la edición, no se acepta."""

    from ptnt.field.sync import CambioRecibido, _validar
    lote = LoteSincronizacion(lote_id="l", usuario="", paquete_id="p",
                              recibido_en="")
    lote.cambios = [CambioRecibido(
        guid="g", secuencia=1, capa="ptnt_poste", elemento_guid="e",
        operacion="CREAR", autor="")]
    h = _validar(lote)
    assert any(x.codigo == "SYNC12" and x.severidad is Severidad.BLOQUEANTE
               for x in h)


@pytest.mark.unit
def test_version_de_red_distinta_advierte_sin_bloquear(tmp_path):
    """El trabajo de campo es válido aunque la red haya avanzado: se advierte
    para que la revisión sea más cuidadosa, no se descarta."""

    ruta, _ = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez",
                           version_red_actual="v99")
    assert not lote.bloqueado
    h = next(h for h in lote.hallazgos if h.codigo == "SYNC03")
    assert h.severidad is Severidad.ADVERTENCIA


@pytest.mark.unit
def test_gps_impreciso_se_advierte():
    lote = LoteSincronizacion(lote_id="l", usuario="u", paquete_id="p",
                              recibido_en="")
    from ptnt.field.sync import CambioRecibido, _validar
    lote.cambios = [CambioRecibido(
        guid="g", secuencia=1, capa="ptnt_poste", elemento_guid="e",
        operacion="CREAR", autor="u", precision_m=45.0)]
    h = _validar(lote)
    assert any(x.codigo == "SYNC13" for x in h)


@pytest.mark.unit
def test_revision_parcial_acepta_unos_y_rechaza_otros(tmp_path):
    """En una misma visita el técnico puede haber corregido bien tres medidores y
    movido mal un poste. Obligar a decidir por todo junto llevaría a aceptar
    errores por no perder el trabajo bueno."""

    ruta, _ = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    r = revisar(lote, aceptar=[1], rechazar=[2], revisor="sup",
                motivo_rechazo="revisar en sitio")
    assert r["aceptados"] == 1 and r["rechazados"] == 1
    assert lote.cambios[0].estado_revision is EstadoRevision.ACEPTADO
    assert lote.cambios[1].estado_revision is EstadoRevision.RECHAZADO


@pytest.mark.unit
def test_rechazar_un_propagado_cuyo_origen_se_acepto_se_advierte(tmp_path):
    """Dejaría la red desconectada en ese punto."""

    ruta, g_cli = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    # el cambio 1 es del cliente (origen) y el 2 su propagado
    r = revisar(lote, aceptar=[1], rechazar=[2])
    assert r["propagaciones_inconsistentes"] == [2]
    assert "desconectada" in r["advertencia"]


@pytest.mark.unit
def test_un_lote_bloqueado_no_se_puede_revisar(tmp_path):
    ruta, _ = _paquete_con_cambios(tmp_path, con_hueco=True)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    with pytest.raises(ValueError, match="bloqueantes"):
        revisar(lote, aceptar_todo=True)


@pytest.mark.unit
def test_aplicar_determina_que_etapas_recalcular(tmp_path):
    """La conexión con el programa principal: los cambios aceptados invalidan
    **solo** las etapas afectadas, no todo el análisis."""

    ruta, g_cli = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    revisar(lote, aceptar_todo=True)
    ap = aplicar(lote, feeder_por_elemento={g_cli: "GYE-08"})

    assert ap.aplicados == 2
    assert "GYE-08" in ap.alimentadores_afectados
    # tocar un cliente altera el ranking de sospecha
    assert "ranking" in ap.etapas_a_recalcular
    # mover cambia longitudes y por tanto pérdidas
    assert "perdidas" in ap.etapas_a_recalcular
    # pero no hubo altas ni bajas: la conectividad no cambió
    assert "topologia" not in ap.etapas_a_recalcular


@pytest.mark.unit
def test_crear_o_eliminar_si_invalida_la_topologia(tmp_path):
    lote = LoteSincronizacion(lote_id="l", usuario="u", paquete_id="p",
                              recibido_en="")
    from ptnt.field.sync import CambioRecibido
    lote.cambios = [CambioRecibido(
        guid="g", secuencia=1, capa="ptnt_poste", elemento_guid="e",
        operacion="CREAR", autor="u",
        estado_revision=EstadoRevision.ACEPTADO)]
    ap = aplicar(lote)
    assert "topologia" in ap.etapas_a_recalcular


# --------------------------------------------------------------------------- #
# Histórico de cambios
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_historico_acumula_campo_y_carga_de_archivo(tmp_path):
    """La pregunta de auditoría es siempre la misma —¿quién cambió esto y
    cuándo?— y la respuesta no puede depender de por qué puerta entró."""

    ruta, g_cli = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    h = HistoricoCambios(tmp_path / "h.csv")
    assert h.registrar_lote(lote) == 2
    assert h.registrar_carga_archivo(
        origen="FGDB", capa="ptnt_tramo", elementos=["a", "b"]) == 2

    resumen = h.resumen_por_origen()
    fuentes = set(resumen["fuente"])
    assert {"MOVIL", "ARCHIVO"} <= fuentes


@pytest.mark.unit
def test_historia_de_un_elemento_responde_por_que_esta_asi(tmp_path):
    ruta, g_cli = _paquete_con_cambios(tmp_path)
    lote = recibir_paquete(ruta, usuario_esperado="jperez")
    h = HistoricoCambios(tmp_path / "h.csv")
    h.registrar_lote(lote)
    hist = h.historia_de(g_cli)
    assert len(hist) == 1
    assert hist.iloc[0]["autor"] == "jperez"


@pytest.mark.unit
def test_elementos_mas_editados_delata_problemas_de_datos(tmp_path):
    h = HistoricoCambios(tmp_path / "h.csv")
    h.registrar_carga_archivo(origen="A", capa="c", elementos=["x"] * 5)
    h.registrar_carga_archivo(origen="B", capa="c", elementos=["y"])
    top = h.elementos_mas_editados(5)
    assert top.iloc[0]["elemento_guid"] == "x"
    assert top.iloc[0]["cambios"] == 5
