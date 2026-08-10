"""Subtipos: al cambiarlos cambian los dominios, igual que en el modelo del SIG.

Lo que se fija aquí no es una comodidad de la interfaz. Un banco en delta abierto
tiene dos unidades y sus fases posibles son AB, BC o CA: ABC es físicamente
imposible. Si el formulario ofrece las siete combinaciones, alguien elige ABC
alguna vez y ese dato entra al modelo — el flujo reparte la carga entre tres
fases que no existen y el desbalance de esa zona deja de significar nada.

El dominio dependiente del subtipo es lo que hace que el dato imposible **no sea
capturable**, ni en el móvil ni al sincronizar.
"""

import json

import pytest

from ptnt.field.domains import (Dominio, aplicar_subtipo, campos_aplicables,
                                dominio_efectivo, es_obligatorio,
                                subtipo_actual, validar)
from ptnt.field.schema import capa_por_nombre, esquema_para_movil


# --------------------------------------------------------------------------- #
# El dominio depende del subtipo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_un_banco_de_dos_unidades_no_puede_servir_tres_fases():
    """El caso que justifica todo el mecanismo."""

    capa = capa_por_nombre("ptnt_puesto_transformacion")

    trifasico = {"configuracion_banco": "BANCO_3"}
    delta = {"configuracion_banco": "DELTA_ABIERTO"}
    simple = {"configuracion_banco": "SIMPLE"}

    assert dominio_efectivo(capa, "fases", trifasico).codigos == ["ABC"]
    assert dominio_efectivo(capa, "fases", delta).codigos == ["AB", "BC", "CA"]
    assert dominio_efectivo(capa, "fases", simple).codigos == ["A", "B", "C"]

    # Y "ABC" no está entre las opciones del delta abierto: no es que se avise
    # después, es que no se puede elegir.
    assert "ABC" not in dominio_efectivo(capa, "fases", delta).codigos


@pytest.mark.unit
def test_la_tarifa_depende_de_la_clase_de_servicio():
    """Un industrial con tarifa residencial no es un error de captura: es una
    imposibilidad comercial que después nadie sabe si fue error o fraude."""

    capa = capa_por_nombre("ptnt_cliente")

    res = dominio_efectivo(capa, "tarifa", {"tipo_servicio": "RESIDENCIAL"})
    ind = dominio_efectivo(capa, "tarifa", {"tipo_servicio": "INDUSTRIAL"})

    assert not set(res.codigos) & set(ind.codigos)
    assert "RESIDENCIAL BAJA TENSION" in res.codigos
    assert "INDUSTRIAL CON DEMANDA MEDIA TENSION" in ind.codigos
    # El industrial es trifásico por definición del servicio.
    assert dominio_efectivo(capa, "n_fases",
                            {"tipo_servicio": "INDUSTRIAL"}).codigos == ["3"]


@pytest.mark.unit
def test_las_tarifas_del_formulario_clasifican_donde_el_analisis_espera():
    """El formulario no puede alejarse de lo que el sistema entiende.

    Las tarifas son las descripciones de DESTARI tal como llegan del sistema
    comercial, y el resto de la plataforma clasifica por ese texto. Si el
    formulario ofreciera un texto que el clasificador no reconoce, el cliente
    capturado en campo entraría al análisis como «no clasificado» y quedaría
    fuera de la comparación contra sus pares — que es la señal que lo detecta.
    """

    from ptnt.segment.classification import ClaseConsumo, clasificar_tarifa

    capa = capa_por_nombre("ptnt_cliente")

    for sub in capa.subtipos:
        # El código del subtipo ES el de `ClaseConsumo`: si alguien inventa uno
        # propio, esto falla aquí en vez de fallar callando en el análisis.
        esperada = ClaseConsumo(sub.codigo)
        for tarifa in sub.dominios["tarifa"].codigos:
            assert clasificar_tarifa(tarifa) is esperada, (
                f"«{tarifa}» se ofrece en {sub.codigo} pero el análisis la "
                f"clasifica como {clasificar_tarifa(tarifa).value}")


@pytest.mark.unit
def test_la_tension_depende_de_si_es_media_o_baja():
    """Un tramo de baja tensión a 13,8 kV no es una preferencia de diseño: es un
    dato imposible que el flujo daría por bueno."""

    capa = capa_por_nombre("ptnt_tramo")

    mt = dominio_efectivo(capa, "tension_v", {"tipo_red": "AEREO_MT"})
    bt = dominio_efectivo(capa, "tension_v", {"tipo_red": "AEREO_BT"})

    assert "13800" in mt.codigos and "13800" not in bt.codigos
    assert "220" in bt.codigos and "220" not in mt.codigos
    assert not set(mt.codigos) & set(bt.codigos)


@pytest.mark.unit
def test_el_conductor_no_se_acota_con_un_catalogo_inventado():
    """Los códigos de conductor son los de CATALOGOESTRUCTURA y salen del
    catálogo del cliente. Escribir una lista propia en el esquema obligaría a
    mantener dos catálogos, y el de campo se quedaría viejo el primer mes."""

    capa = capa_por_nombre("ptnt_tramo")
    for tipo in ("AEREO_MT", "SUBTERRANEO_BT"):
        assert dominio_efectivo(capa, "codigo_estructura",
                                {"tipo_red": tipo}) is None


@pytest.mark.unit
def test_el_catalogo_real_si_acota_el_conductor():
    """Con CATALOGOESTRUCTURA cargado, el campo deja de ser texto libre: teclear
    un código de conductor a mano en un teléfono es la principal fuente de error
    de captura."""

    from ptnt.field.schema import dominios_conductor_desde_catalogo
    from ptnt.ref.structure_catalog import load_structure_catalog

    catalogo = load_structure_catalog()
    capa = dominios_conductor_desde_catalogo(catalogo, capa_por_nombre("ptnt_tramo"))
    dom = dominio_efectivo(capa, "codigo_estructura", {"tipo_red": "AEREO_MT"})

    assert dom is not None and dom.codigos, "el catálogo trae conductores"
    assert all(c.startswith("COO") for c in dom.codigos)
    # Y la descripción del catálogo viaja con el código.
    alguno = dom.codigos[0]
    assert dom.descripcion_de(alguno).startswith(alguno)


# --------------------------------------------------------------------------- #
# Qué pasa con lo ya capturado cuando el subtipo cambia
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_lo_valido_se_respeta_al_cambiar_de_subtipo():
    """El técnico lo vio en sitio; el sistema no tiene por qué opinar."""

    capa = capa_por_nombre("ptnt_puesto_transformacion")
    elem = {"configuracion_banco": "BANCO_3", "fases": "ABC",
            "potencia_nominal_kva": 75.0, "codigo": "PT-001"}

    r = aplicar_subtipo(capa, elem, "DELTA_4H")

    assert r.atributos["fases"] == "ABC", "sigue siendo válido, se respeta"
    assert r.atributos["codigo"] == "PT-001"
    assert not r.invalidados and not r.ajustados


@pytest.mark.unit
def test_lo_que_deja_de_valer_cae_al_defecto_del_subtipo_y_se_avisa():
    """Cambiarlo por debajo sería una corrección invisible: el técnico la
    descubre semanas después, cuando ya no puede verificar nada."""

    capa = capa_por_nombre("ptnt_puesto_transformacion")
    elem = {"configuracion_banco": "BANCO_3", "fases": "ABC"}

    r = aplicar_subtipo(capa, elem, "DELTA_ABIERTO")

    assert r.atributos["fases"] == "AB", "cayó al defecto del subtipo nuevo"
    assert r.ajustados["fases"] == ("ABC", "AB")
    assert "ABC" in r.resumen() and "AB" in r.resumen()


@pytest.mark.unit
def test_sin_defecto_al_que_caer_se_limpia_y_se_pide_volver_a_elegir():
    """Conservarlo dejaría entrar justo el dato imposible que el subtipo existe
    para impedir; borrarlo en silencio perdería trabajo de campo."""

    capa = capa_por_nombre("ptnt_poste")
    # 16 m cabe en hormigón (6–18) pero no en madera (6–12), y el subtipo de
    # madera no trae defecto de altura al que caer.
    elem = {"material": "HORMIGON", "altura_m": 16.0}

    r = aplicar_subtipo(capa, elem, "MADERA")

    assert r.atributos["altura_m"] is None
    assert r.invalidados["altura_m"] == 16.0
    assert r.hubo_perdida
    # El aviso dice qué había: sin eso el técnico no sabe qué volver a poner.
    assert "16.0" in r.resumen()


@pytest.mark.unit
def test_los_campos_que_no_aplican_se_ocultan_y_se_vacian():
    """Preguntar el ducto de una red aérea es pedir un dato que no existe, y
    alguien acabará inventándolo con tal de cerrar el formulario."""

    capa = capa_por_nombre("ptnt_tramo")
    elem = {"tipo_red": "SUBTERRANEO_BT", "tipo_ducto": "PVC",
            "profundidad_m": 1.2}

    assert "tipo_ducto" in campos_aplicables(capa, elem)

    r = aplicar_subtipo(capa, elem, "AEREO_BT")

    assert "tipo_ducto" not in campos_aplicables(capa, r.atributos)
    assert r.descartados["tipo_ducto"] == "PVC"
    assert r.atributos["tipo_ducto"] is None, "no viaja un dato que no aplica"


@pytest.mark.unit
def test_el_defecto_no_pisa_lo_que_el_tecnico_capturo():
    """Pisar un valor tomado en sitio porque el subtipo «sugiere» otro es perder
    trabajo de campo."""

    capa = capa_por_nombre("ptnt_luminaria")
    elem = {"tipo_ap": "LUMINARIA", "potencia_w": 250.0}

    r = aplicar_subtipo(capa, elem, "SEMAFORO")

    # 250 W cabe en el rango del semáforo (20–300): se respeta, no se pone 60.
    assert r.atributos["potencia_w"] == 250.0
    assert not r.ajustados


@pytest.mark.unit
def test_el_subtipo_rellena_lo_vacio():
    """Ahorra toques y, sobre todo, evita el valor que el técnico dejaría por
    omisión sin darse cuenta: el semáforo va como AP no medido por regulación,
    y si se marca como medido su energía se cuenta dos veces."""

    capa = capa_por_nombre("ptnt_luminaria")
    r = aplicar_subtipo(capa, {"tipo_ap": "LUMINARIA"}, "SEMAFORO")

    assert r.atributos["potencia_w"] == 60
    assert r.atributos["medida"] == 0


# --------------------------------------------------------------------------- #
# Contingencias: el dominio depende de otro campo, no del subtipo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_calibre_de_acometida_depende_del_tipo_de_acometida():
    """El subtipo cubre lo habitual pero no todo: el calibre depende de si la
    acometida es aérea o subterránea, y eso no es la clase de servicio."""

    capa = capa_por_nombre("ptnt_cliente")

    aerea = dominio_efectivo(capa, "calibre_acometida",
                             {"tipo_servicio": "RESIDENCIAL",
                              "tipo_acometida": "AEREA"})
    subte = dominio_efectivo(capa, "calibre_acometida",
                             {"tipo_servicio": "RESIDENCIAL",
                              "tipo_acometida": "SUBTERRANEA"})

    assert "DUPLEX-6" in aerea.codigos and "DUPLEX-6" not in subte.codigos
    assert "TTU-6" in subte.codigos


@pytest.mark.unit
def test_la_contingencia_gana_al_subtipo():
    """Si el subtipo ganara, la regla más específica no serviría de nada y
    habría que duplicarla dentro de cada subtipo."""

    capa = capa_por_nombre("ptnt_cliente")
    for servicio in ("RESIDENCIAL", "INDUSTRIAL", "COMERCIAL"):
        dom = dominio_efectivo(capa, "calibre_acometida",
                               {"tipo_servicio": servicio,
                                "tipo_acometida": "SUBTERRANEA"})
        assert dom.codigos == ["TTU-6", "TTU-4", "TTU-2"]


# --------------------------------------------------------------------------- #
# Dominios de rango y códigos con descripción
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_rango_rechaza_lo_que_no_es_un_valor_de_lista():
    """Un poste de 40 m no es una opción que falte en el catálogo: es un error
    de tecleo, y lo único que hace falta es que no entre."""

    capa = capa_por_nombre("ptnt_poste")
    dom = dominio_efectivo(capa, "altura_m", {"material": "HORMIGON"})

    assert dom.tipo == "RANGO"
    assert dom.admite(11) and dom.admite(18)
    # Un poste de 40 m y uno de 2 m son el mismo error de tecleo con distinto
    # signo. La torre metálica sí llega a 40, y por eso el rango es del subtipo.
    assert not dom.admite(40) and not dom.admite(2)
    assert dominio_efectivo(capa, "altura_m", {"material": "METALICO"}).admite(40)
    # Vacío se admite: lo obligatorio se controla aparte, y confundirlos haría
    # imposible dejar un campo sin llenar mientras se decide.
    assert dom.admite(None) and dom.admite("")


@pytest.mark.unit
def test_se_guarda_el_codigo_y_se_muestra_la_descripcion():
    """Lo que se guarda es lo que el sistema necesita; lo que se ve es lo que una
    persona puede elegir bien en un teléfono a pleno sol.

    `ACSR-4/0` es lo que va a la base y lo que el supervisor cruza con el
    catálogo; «ACSR 4/0 AWG» es lo que el técnico reconoce mirando el conductor.
    """

    capa = capa_por_nombre("ptnt_tramo")
    dom = dominio_efectivo(capa, "tension_v", {"tipo_red": "AEREO_MT"})

    assert "13800" in dom.codigos
    assert dom.descripcion_de("13800") == "13,8 kV"

    # En la tarifa, en cambio, el código ES la descripción: así llega DESTARI del
    # sistema comercial, y traducirlo a un código propio obligaría a mantener una
    # tabla de equivalencias que nadie actualizaría.
    tarifas = dominio_efectivo(capa_por_nombre("ptnt_cliente"), "tarifa",
                               {"tipo_servicio": "RESIDENCIAL"})
    assert tarifas.descripcion_de("RESIDENCIAL BAJA TENSION") == \
        "RESIDENCIAL BAJA TENSION"


# --------------------------------------------------------------------------- #
# Validación: el servidor no confía en que el cliente se haya portado bien
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_servidor_rechaza_el_valor_fuera_del_dominio_del_subtipo():
    """Un paquete puede venir de una versión anterior de la app, de un
    dispositivo con el esquema viejo o de una edición hecha con QGIS sobre el
    mismo archivo. Validar solo en el móvil es validar donde no está la
    responsabilidad del dato."""

    capa = capa_por_nombre("ptnt_puesto_transformacion")
    fallos = validar(capa, {"guid": "g1", "configuracion_banco": "DELTA_ABIERTO",
                            "fases": "ABC"})

    assert len(fallos) == 1
    assert fallos[0].campo == "fases"
    # El mensaje nombra el subtipo: sin eso, el supervisor ve «valor inválido»
    # en un campo cuyo valor existe en el catálogo y lo toma por un error del
    # sistema.
    assert "Delta abierto" in fallos[0].motivo
    assert "AB, BC, CA" in fallos[0].motivo


@pytest.mark.unit
def test_lo_coherente_no_produce_hallazgos():
    capa = capa_por_nombre("ptnt_puesto_transformacion")
    assert validar(capa, {"guid": "g1", "configuracion_banco": "DELTA_ABIERTO",
                          "fases": "BC"}) == []


@pytest.mark.unit
def test_el_obligatorio_del_subtipo_se_exige_solo_donde_aplica():
    """El ducto es obligatorio en red subterránea y no existe en aérea."""

    capa = capa_por_nombre("ptnt_tramo")

    assert es_obligatorio(capa, "tipo_ducto", {"tipo_red": "SUBTERRANEO_MT"})
    assert not es_obligatorio(capa, "tipo_ducto", {"tipo_red": "AEREO_MT"})

    faltante = validar(capa, {"guid": "t1", "tipo_red": "SUBTERRANEO_MT"})
    assert any(f.campo == "tipo_ducto" and "obligatorio" in f.motivo
               for f in faltante)
    # En aérea el mismo elemento sin ducto no es un problema.
    assert not any(f.campo == "tipo_ducto"
                   for f in validar(capa, {"guid": "t1", "tipo_red": "AEREO_MT"}))


@pytest.mark.unit
def test_sin_subtipo_no_se_supone_ninguno_y_rige_el_dominio_base():
    """Buena parte de la red llega del SIG sin el campo poblado.

    Suponer el primer subtipo aplicaría a esos elementos unas reglas que nadie
    eligió: un tramo de baja tensión heredaría el dominio de media y sus 220 V
    se rechazarían como inválidos. Sin subtipo rige el dominio base, que es el
    superconjunto correcto.
    """

    from ptnt.field.domains import subtipo_por_defecto

    tramo = capa_por_nombre("ptnt_tramo")
    assert subtipo_actual(tramo, {}) is None
    assert subtipo_actual(tramo, {"tipo_red": ""}) is None

    # 220 V en un tramo sin tipo declarado no es un error: es un dato del SIG.
    assert validar(tramo, {"guid": "t1", "tension_v": 220.0}) == []

    # Al CREAR un elemento sí hay que proponer uno: un formulario en blanco sin
    # subtipo obliga al técnico a acertar el campo correcto antes de que el
    # resto tenga sentido.
    assert subtipo_por_defecto(tramo).codigo == "AEREO_MT"


@pytest.mark.unit
def test_el_numero_que_vuelve_de_sqlite_encaja_en_el_dominio():
    """Un campo de tensión declarado REAL vuelve como 220.0 y el dominio dice
    «220». Comparar los textos crudos rechazaría un dato correcto en cada tramo
    de baja tensión del país."""

    capa = capa_por_nombre("ptnt_tramo")
    dom = dominio_efectivo(capa, "tension_v", {"tipo_red": "AEREO_BT"})

    assert dom.admite(220.0) and dom.admite("220") and dom.admite(220)
    assert dom.descripcion_de(220.0) == "220 V"
    assert not dom.admite(221.0)

    assert validar(capa, {"guid": "t1", "tipo_red": "AEREO_BT",
                          "tension_v": 220.0}) == []


# --------------------------------------------------------------------------- #
# El contrato con la aplicación móvil
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_el_manifiesto_lleva_las_reglas_al_dispositivo():
    """En campo no hay señal para ir a consultar las reglas: si no viajan en el
    paquete, el formulario se queda sin ellas justo donde hacen falta."""

    esquema = esquema_para_movil()
    capas = {c["nombre"]: c for c in esquema["capas"]}
    puesto = capas["ptnt_puesto_transformacion"]

    assert puesto["campo_subtipo"] == "configuracion_banco"
    codigos = {s["codigo"] for s in puesto["subtipos"]}
    assert {"SIMPLE", "DELTA_ABIERTO", "BANCO_3"} <= codigos

    delta = next(s for s in puesto["subtipos"] if s["codigo"] == "DELTA_ABIERTO")
    fases = delta["dominios"]["fases"]
    assert [v["codigo"] for v in fases["valores"]] == ["AB", "BC", "CA"]
    assert delta["defectos"]["fases"] == "AB"

    # Y las contingencias del cliente.
    assert any(r["campo_condicion"] == "tipo_acometida"
               for r in capas["ptnt_cliente"]["reglas"])


@pytest.mark.unit
def test_el_manifiesto_usa_las_claves_que_kotlin_lee():
    """Contrato con `EsquemaFormulario.kt`. Un renombre en un lado deja al móvil
    sin subtipos **en silencio** —el parseo cae al vacío— y el formulario vuelve
    a ofrecer el catálogo completo sin que nadie se entere hasta ver los datos.
    """

    esquema = json.loads(json.dumps(esquema_para_movil()))     # ida y vuelta real
    capa = next(c for c in esquema["capas"]
                if c["nombre"] == "ptnt_tramo")

    assert {"campo_subtipo", "subtipos", "reglas", "campos"} <= set(capa)
    sub = capa["subtipos"][0]
    assert {"codigo", "etiqueta", "descripcion", "dominios", "defectos",
            "ocultos", "obligatorios"} <= set(sub)

    dom = next(iter(sub["dominios"].values()))
    assert {"nombre", "tipo"} <= set(dom)
    assert all({"codigo", "descripcion"} <= set(v) for v in dom["valores"])

    campo = capa["campos"][0]
    assert {"nombre", "tipo", "etiqueta", "obligatorio", "editable", "dominio",
            "dominio_detalle", "ayuda"} <= set(campo)

    # El rango viaja con sus extremos, no como lista vacía.
    poste = next(c for c in esquema["capas"] if c["nombre"] == "ptnt_poste")
    metalico = next(s for s in poste["subtipos"] if s["codigo"] == "METALICO")
    assert metalico["dominios"]["altura_m"]["tipo"] == "RANGO"
    assert metalico["dominios"]["altura_m"]["minimo"] == 6


@pytest.mark.unit
def test_el_dominio_simple_sigue_viajando_para_los_lectores_viejos():
    """Un dispositivo con la versión anterior de la app tiene que seguir
    mostrando desplegables: si el esquema nuevo lo deja sin dominios, la
    cuadrilla que no actualizó captura texto libre todo el día."""

    esquema = esquema_para_movil()
    cliente = next(c for c in esquema["capas"] if c["nombre"] == "ptnt_cliente")
    hallazgo = next(f for f in cliente["campos"] if f["nombre"] == "hallazgo")

    assert isinstance(hallazgo["dominio"], list)
    assert "CONEXION_DIRECTA" in hallazgo["dominio"]


@pytest.mark.unit
def test_todo_subtipo_declara_dominios_de_campos_que_existen():
    """Un dominio sobre un campo inexistente no falla: simplemente no se aplica
    nunca, y el formulario sigue ofreciendo el catálogo completo sin avisar."""

    from ptnt.field.schema import todas_las_capas

    for capa in todas_las_capas():
        nombres = {f.nombre for f in capa.campos}
        if capa.campo_subtipo:
            assert capa.campo_subtipo in nombres, (
                f"{capa.nombre}: el campo de subtipo '{capa.campo_subtipo}' "
                "no existe en la capa")
        for s in capa.subtipos:
            for campo in list(s.dominios) + list(s.defectos) + list(s.ocultos):
                assert campo in nombres, (
                    f"{capa.nombre}/{s.codigo}: '{campo}' no existe en la capa")
        for r in capa.reglas or ():
            assert r.campo_condicion in nombres and r.campo_afectado in nombres


@pytest.mark.unit
def test_los_defectos_del_subtipo_caben_en_sus_propios_dominios():
    """Un defecto fuera de su dominio se aplicaría al cambiar de subtipo y
    quedaría inválido al instante: el sistema metería el error él solo."""

    from ptnt.field.schema import todas_las_capas

    for capa in todas_las_capas():
        for s in capa.subtipos:
            for campo, valor in s.defectos.items():
                dom = s.dominios.get(campo) or (
                    capa.campo(campo).dominio_obj() if capa.campo(campo) else None)
                if dom is None:
                    continue
                assert dom.admite(valor), (
                    f"{capa.nombre}/{s.codigo}: el defecto de '{campo}' "
                    f"({valor}) no cabe en su propio dominio {dom.codigos}")


# --------------------------------------------------------------------------- #
# En el paquete que se lleva a campo
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_los_dominios_van_en_la_extension_estandar_del_formato(tmp_path):
    """Así QGIS y ArcGIS abren el paquete con los desplegables puestos sin saber
    nada de este proyecto. Que el archivo de campo sea legible con herramientas
    corrientes es media garantía de que el dato sobrevive al proyecto."""

    from ptnt.field.gpkg import GeoPackage
    from ptnt.field.schema import capas_red

    ruta = tmp_path / "p.gpkg"
    with GeoPackage(ruta) as gp:
        capas = capas_red()
        for c in capas:
            gp.crear_capa(c)
        gp.escribir_dominios(capas)

        ext = [r["extension_name"] for r in gp.con.execute(
            "SELECT extension_name FROM gpkg_extensions")]
        assert "gpkg_schema" in ext

        # Lo que se registra en el estándar es el dominio **base**: es el
        # superconjunto correcto para un lector que no entiende subtipos.
        # Publicar ahí el del subtipo haría que QGIS ocultara valores legítimos
        # de otros subtipos de la misma capa.
        vinculo = gp.con.execute(
            "SELECT constraint_name, title FROM gpkg_data_columns "
            "WHERE table_name = 'ptnt_puesto_transformacion' "
            "AND column_name = 'fases'").fetchone()
        assert vinculo["title"] == "Fases"

        enum = {r["value"] for r in gp.con.execute(
            "SELECT value FROM gpkg_data_column_constraints "
            "WHERE constraint_name = ?", (vinculo["constraint_name"],))}
        assert {"A", "B", "C", "AB", "BC", "CA", "ABC"} == enum

        # Y la descripción del código viaja, que es lo que hace legible el
        # archivo fuera de este proyecto.
        tarifa = gp.con.execute(
            "SELECT c.value, c.description FROM gpkg_data_column_constraints c "
            "JOIN gpkg_data_columns d ON d.constraint_name = c.constraint_name "
            "WHERE d.table_name = 'ptnt_cliente' AND d.column_name = 'tarifa'"
        ).fetchall()
        assert tarifa == [] or all(r["description"] for r in tarifa)


@pytest.mark.unit
def test_los_subtipos_viajan_dentro_del_mismo_archivo(tmp_path):
    """Un formulario cuyas reglas viven en otro sitio es un formulario que en
    campo, sin señal, no tiene reglas."""

    from ptnt.field.gpkg import GeoPackage
    from ptnt.field.schema import capas_red

    ruta = tmp_path / "p.gpkg"
    with GeoPackage(ruta) as gp:
        capas = capas_red()
        for c in capas:
            gp.crear_capa(c)
        gp.escribir_subtipos(capas)

        fila = gp.con.execute(
            "SELECT campo_subtipo, etiqueta, defectos FROM ptnt_subtipo "
            "WHERE capa = 'ptnt_puesto_transformacion' AND codigo = 'DELTA_ABIERTO'"
        ).fetchone()
        assert fila["campo_subtipo"] == "configuracion_banco"
        assert json.loads(fila["defectos"])["fases"] == "AB"

        dom = gp.con.execute(
            "SELECT dominio FROM ptnt_subtipo_dominio "
            "WHERE capa = 'ptnt_puesto_transformacion' "
            "AND subtipo = 'DELTA_ABIERTO' AND campo = 'fases'").fetchone()
        assert [v["codigo"] for v in json.loads(dom["dominio"])["valores"]] == \
            ["AB", "BC", "CA"]

        reglas = gp.con.execute(
            "SELECT campo_condicion, campo_afectado FROM ptnt_regla_dominio "
            "WHERE capa = 'ptnt_cliente'").fetchall()
        assert any(r["campo_condicion"] == "tipo_acometida"
                   and r["campo_afectado"] == "calibre_acometida" for r in reglas)
