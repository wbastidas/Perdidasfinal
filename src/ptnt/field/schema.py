"""Esquema del paquete de campo: el **contrato** entre el backend y el móvil.

Este módulo es la pieza que hace construible la aplicación móvil: define
exactamente qué tablas, campos, dominios y relaciones viajan en el GeoPackage.
Cambiar algo aquí cambia el contrato, y por eso el esquema lleva versión.

**Tres decisiones que gobiernan el diseño:**

1. **Todo elemento lleva `guid` estable, no solo `fid`.** El `fid` es el rowid
   local del GeoPackage y cambia entre paquetes; el `guid` es el mismo en el SIG,
   en el paquete de campo y en la sincronización. Sin él sería imposible saber si
   un elemento editado en campo es el mismo que ya existía en el SIG, y cada
   sincronización crearía duplicados.

2. **La relación Puesto→Unidad es explícita y editable.** Un puesto de
   transformación tiene 1..3 unidades (una por fase). En campo hay que poder
   agregar una unidad a un puesto existente, cambiarle el kVA a una, o
   eliminarla. Modelarlo como tabla aparte con `puesto_guid` —y no como columnas
   `kva_1/kva_2/kva_3`— es lo que permite editarlas de forma independiente.

3. **La conectividad viaja como grafo, no solo como geometría.** Dos elementos
   pueden estar dibujados encima sin estar conectados, y conectados sin
   tocarse. La tabla `ptnt_conexion` guarda la topología real
   (`CIRCUITSOURCEGUID` / `PARENTCIRCUITSOURCEGUID`), y es la que permite que al
   mover un cliente se muevan los tramos que efectivamente cuelgan de él —no los
   que casualmente pasan cerca—.
"""

from __future__ import annotations

from ptnt.field.gpkg import Campo, Capa

VERSION_ESQUEMA = "1.1.0"

# --------------------------------------------------------------------------- #
# Dominios (alimentan los desplegables del formulario móvil)
# --------------------------------------------------------------------------- #
DOM_ESTADO_ELEMENTO = ["OPERATIVO", "FUERA_SERVICIO", "RETIRADO", "PROYECTADO"]
DOM_FASE = ["A", "B", "C", "AB", "BC", "CA", "ABC"]
DOM_TIPO_ACOMETIDA = ["AEREA", "SUBTERRANEA", "MIXTA"]
DOM_MATERIAL_POSTE = ["HORMIGON", "MADERA", "METALICO", "FIBRA"]
DOM_TIPO_MEDIDOR = ["ELECTROMECANICO", "ELECTRONICO", "INTELIGENTE", "SIN_MEDIDOR"]
DOM_HALLAZGO = [
    "SIN_NOVEDAD",
    "CONEXION_DIRECTA",
    "MEDIDOR_MANIPULADO",
    "PUENTE_EN_ACOMETIDA",
    "SELLOS_VIOLENTADOS",
    "MEDIDOR_INVERTIDO",
    "MEDIDOR_FRENADO",
    "CLIENTE_NO_EXISTE",
    "PREDIO_CERRADO",
    "ACCESO_NEGADO",
    "ERROR_DE_DATOS",
]
DOM_ESTADO_OT = ["ASIGNADA", "DESCARGADA", "EN_PROCESO", "COMPLETADA",
                 "SINCRONIZADA", "RECHAZADA"]
DOM_OPERACION = ["CREAR", "MODIFICAR", "MOVER", "ELIMINAR", "RECONECTAR"]

# Qué se va a hacer en la visita. El sistema nació apuntando al hurto, pero una
# cuadrilla en la calle sirve para más de eso, y separar el tipo permite medir
# cada campaña por su cuenta: un censo no se evalúa por kWh recuperados.
DOM_TIPO_TRABAJO = [
    "INSPECCION_PNT",            # sospecha de pérdida no técnica (el defecto)
    "CENSO",                     # levantamiento de clientes no registrados
    "ACTUALIZACION_CARTOGRAFICA",  # corregir la red dibujada contra la real
    "VERIFICACION_MEDIDOR",      # contraste de lectura y estado del medidor
    "MANTENIMIENTO",             # estado de postes, luminarias, seccionadores
    "RECLAMO",                   # atención de un reclamo puntual
    "OBRA",                      # alta de red nueva construida
]


def _c(nombre: str, tipo: str = "TEXT", **kw) -> Campo:
    return Campo(nombre=nombre, tipo=tipo, **kw)


# --------------------------------------------------------------------------- #
# Campos comunes a todo elemento de red
# --------------------------------------------------------------------------- #
def _campos_base() -> list[Campo]:
    return [
        _c("guid", obligatorio=True, editable=False, etiqueta="Identificador"),
        _c("codigo", etiqueta="Código"),
        _c("feeder_code", editable=False, etiqueta="Alimentador"),
        _c("circuitsource_guid", editable=False, etiqueta="Fuente de circuito"),
        _c("parent_circuitsource_guid", editable=False, etiqueta="Fuente padre"),
        _c("estado", dominio=DOM_ESTADO_ELEMENTO, etiqueta="Estado"),
        _c("observacion", etiqueta="Observación"),
        # Auditoría de campo: quién y cuándo tocó esto por última vez.
        _c("editado_por", editable=False, etiqueta="Editado por"),
        _c("editado_en", tipo="DATETIME", editable=False, etiqueta="Editado en"),
        _c("origen_edicion", editable=False, etiqueta="Origen",
           ayuda="SIG | MOVIL | CARGA_ARCHIVO"),
    ]


# --------------------------------------------------------------------------- #
# Capas del modelo de red
# --------------------------------------------------------------------------- #
def capas_red() -> list[Capa]:
    """Las capas editables en campo, con el modelo de datos del proyecto."""

    return [
        Capa(
            nombre="ptnt_puesto_transformacion",
            tipo_geometria="POINT",
            descripcion="Puesto de transformación (PUESTOTRANSFDISTRIBUCION)",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de estructura",
                   ayuda="CATALOGOESTRUCTURA — define kVA y configuración de banco"),
                _c("potencia_nominal_kva", "REAL", etiqueta="Potencia nominal (kVA)"),
                _c("configuracion_banco", etiqueta="Configuración de banco",
                   dominio=["SIMPLE", "BANCO_3", "DELTA_ABIERTO", "DELTA_4H"]),
                _c("fases", dominio=DOM_FASE, etiqueta="Fases"),
                _c("tiene_totalizador", "BOOLEAN", etiqueta="Tiene totalizador",
                   ayuda="Si lo tiene, los medidores individuales NO se suman"),
                _c("n_unidades", "INTEGER", editable=False,
                   etiqueta="Unidades instaladas"),
            ],
        ),
        Capa(
            # Tabla sin geometría: las unidades están EN el puesto. Modelarlas
            # aparte es lo que permite agregar, editar o quitar una unidad sin
            # tocar las otras dos.
            nombre="ptnt_unidad_transformacion",
            tipo_geometria="NONE",
            descripcion="Unidad de transformación (UNIDADTRANSFDISTRIBUCION), 1..3 por puesto",
            campos=[
                _c("guid", obligatorio=True, editable=False),
                _c("puesto_guid", obligatorio=True, editable=False,
                   etiqueta="Puesto al que pertenece"),
                _c("codigo", etiqueta="Código"),
                _c("fase", dominio=["A", "B", "C"], obligatorio=True,
                   etiqueta="Fase"),
                _c("potencia_kva", "REAL", obligatorio=True,
                   etiqueta="Potencia (kVA)"),
                _c("codigo_estructura", etiqueta="Código de estructura"),
                _c("serie", etiqueta="Número de serie"),
                _c("estado", dominio=DOM_ESTADO_ELEMENTO),
                _c("editado_por", editable=False),
                _c("editado_en", "DATETIME", editable=False),
                _c("origen_edicion", editable=False),
            ],
        ),
        Capa(
            nombre="ptnt_cliente",
            tipo_geometria="POINT",
            descripcion="Conexión del consumidor (CONEXIONCONSUMIDOR + ATRIBUTOSCONSUMIDOR)",
            campos=_campos_base() + [
                _c("cuenta_contrato", etiqueta="Cuenta contrato"),
                _c("nombre", etiqueta="Nombre del cliente"),
                _c("tarifa", etiqueta="Tarifa (DESTARI)"),
                _c("ruta_lectura", etiqueta="Ruta de lectura (CLIRLSCOD)"),
                _c("n_fases", "INTEGER", dominio=["1", "2", "3"],
                   etiqueta="Número de fases (CDAFAS)"),
                _c("fase_conectada", dominio=DOM_FASE, etiqueta="Fase conectada"),
                _c("tipo_medidor", dominio=DOM_TIPO_MEDIDOR, etiqueta="Tipo de medidor"),
                _c("medidor_serie", etiqueta="Serie del medidor"),
                _c("tipo_acometida", dominio=DOM_TIPO_ACOMETIDA,
                   etiqueta="Tipo de acometida"),
                # La reconexión es editable **a propósito**. Es la corrección
                # que más vale de todo el trabajo de campo: un cliente colgado
                # del transformador equivocado desbalancea las dos zonas a la vez
                # —una pierde energía que no consumió y la otra la gana— y
                # produce PNT falsa en ambas. Marcarlo como no editable obligaba
                # a anotarlo en papel y corregirlo después en oficina, que en la
                # práctica significa que no se corrige.
                _c("puesto_guid", etiqueta="Transformador que lo alimenta",
                   ayuda="Cambiarlo mueve el cliente de zona de balance. "
                         "Use «Reconectar» para elegirlo del mapa."),
                _c("unidad_guid", etiqueta="Unidad del banco (fase)",
                   ayuda="En un banco trifásico, de qué unidad cuelga. "
                         "Determina a qué fase carga el consumo."),
                _c("consumo_promedio_kwh", "REAL", editable=False,
                   etiqueta="Consumo promedio (kWh/mes)"),
                _c("score_sospecha", "REAL", editable=False,
                   etiqueta="Score de sospecha",
                   ayuda="Calculado por el sistema; en campo es solo referencia"),
                # -- resultado de la inspección (lo que se llena en campo) --
                _c("hallazgo", dominio=DOM_HALLAZGO, etiqueta="Hallazgo"),
                _c("lectura_medidor", "REAL", etiqueta="Lectura del medidor"),
                _c("inspeccionado", "BOOLEAN", etiqueta="Inspeccionado"),
                _c("fecha_inspeccion", "DATETIME", etiqueta="Fecha de inspección"),
            ],
        ),
        Capa(
            nombre="ptnt_tramo",
            tipo_geometria="LINESTRING",
            descripcion="Tramo de red (TRAMODISTRIBUCIONAEREO / SUBTERRANEO)",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de conductor"),
                _c("longitud_m", "REAL", etiqueta="Longitud (m)"),
                _c("n_fases", "INTEGER", etiqueta="Número de fases"),
                _c("tension_v", "REAL", etiqueta="Tensión (V)"),
                _c("es_baja_tension", "BOOLEAN", etiqueta="Baja tensión"),
                _c("nodo_origen", editable=False, etiqueta="Nodo origen"),
                _c("nodo_destino", editable=False, etiqueta="Nodo destino"),
            ],
        ),
        Capa(
            nombre="ptnt_poste",
            tipo_geometria="POINT",
            descripcion="Estructura de soporte (ESTRUCTURASOPORTE)",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de estructura"),
                _c("material", dominio=DOM_MATERIAL_POSTE, etiqueta="Material"),
                _c("altura_m", "REAL", etiqueta="Altura (m)"),
            ],
        ),
        Capa(
            nombre="ptnt_luminaria",
            tipo_geometria="POINT",
            descripcion="Luminaria de alumbrado público (incluye semáforos y cámaras)",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de estructura"),
                _c("potencia_w", "REAL", etiqueta="Potencia (W)"),
                _c("perdida_balastro_w", "REAL", etiqueta="Pérdida de balastro (W)"),
                _c("medida", "BOOLEAN", etiqueta="Medida",
                   ayuda="Semáforos y cámaras van como AP NO medido, por regulación"),
                _c("tipo_ap", etiqueta="Tipo",
                   dominio=["LUMINARIA", "SEMAFORO", "CAMARA"]),
                # También reconectable: el alumbrado no medido se imputa al
                # transformador del que cuelga. Colgado del equivocado, esa
                # energía se le carga a la zona que no la consumió.
                _c("puesto_guid", etiqueta="Transformador que la alimenta",
                   ayuda="Determina a qué zona se imputa el consumo de AP."),
            ],
        ),
        Capa(
            nombre="ptnt_seccionador",
            tipo_geometria="POINT",
            descripcion="Puesto de protección / seccionador",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de estructura"),
                _c("posicion_normal", dominio=["CERRADO", "ABIERTO"],
                   etiqueta="Posición normal"),
                _c("posicion_actual", dominio=["CERRADO", "ABIERTO"],
                   etiqueta="Posición actual"),
                _c("es_cabecera", "BOOLEAN", editable=False,
                   etiqueta="Cabecera de alimentador"),
            ],
        ),
        Capa(
            nombre="ptnt_capacitor",
            tipo_geometria="POINT",
            descripcion="Banco de capacitores",
            campos=_campos_base() + [
                _c("codigo_estructura", etiqueta="Código de estructura"),
                _c("potencia_kvar", "REAL", etiqueta="Potencia (kVAR)"),
            ],
        ),
    ]


# --------------------------------------------------------------------------- #
# Capas de trabajo: órdenes, conectividad, fotos y diario de cambios
# --------------------------------------------------------------------------- #
def capas_trabajo() -> list[Capa]:
    return [
        Capa(
            nombre="ptnt_orden_trabajo",
            tipo_geometria="POINT",
            descripcion="Orden de trabajo asignada (el punto es el centroide del objetivo)",
            campos=[
                _c("guid", obligatorio=True, editable=False),
                _c("orden_trabajo", obligatorio=True, editable=False,
                   etiqueta="Orden"),
                _c("nivel", editable=False, etiqueta="Nivel",
                   ayuda="SECTOR | RUTA_COMERCIAL | PUESTO_TRANSFORMACION | ..."),
                _c("entidad", editable=False, etiqueta="Entidad"),
                _c("feeder_code", editable=False, etiqueta="Alimentador"),
                _c("accion", editable=False, etiqueta="Acción recomendada"),
                _c("motivo", editable=False, etiqueta="Motivo"),
                _c("clientes_a_revisar", "INTEGER", editable=False,
                   etiqueta="Clientes a revisar"),
                _c("recuperable_kwh_mes", "REAL", editable=False,
                   etiqueta="Recuperable estimado (kWh/mes)"),
                _c("asignado_a", editable=False, etiqueta="Asignado a"),
                _c("estado", dominio=DOM_ESTADO_OT, etiqueta="Estado"),
                _c("fecha_asignacion", "DATETIME", editable=False),
                _c("fecha_inicio", "DATETIME", etiqueta="Inicio en campo"),
                _c("fecha_cierre", "DATETIME", etiqueta="Cierre"),
                _c("resultado", etiqueta="Resultado del levantamiento"),
                _c("radio_m", "REAL", editable=False, etiqueta="Radio (m)"),
                _c("tipo_trabajo", editable=False, dominio=DOM_TIPO_TRABAJO,
                   etiqueta="Tipo de trabajo",
                   ayuda="Un censo no se evalúa por kWh recuperados"),
                # Una revisión de campo puede llevar varios días. El estado
                # EN_PROCESO se mantiene entre jornadas y el avance se sube cada
                # tarde; estos dos campos son los que permiten al supervisor
                # distinguir «va por la mitad» de «lleva una semana parada».
                _c("visitas", "INTEGER", editable=False,
                   etiqueta="Jornadas trabajadas"),
                _c("fecha_ultimo_avance", "DATETIME", editable=False,
                   etiqueta="Último avance sincronizado"),
            ],
        ),
        Capa(
            # La conectividad no se deduce de la geometría: dos elementos pueden
            # estar dibujados encima sin estar conectados. Este grafo es el que
            # permite arrastrar lo que REALMENTE cuelga de un elemento.
            nombre="ptnt_conexion",
            tipo_geometria="NONE",
            descripcion="Grafo de conectividad entre elementos (topología real)",
            campos=[
                _c("guid_origen", obligatorio=True, editable=False),
                _c("guid_destino", obligatorio=True, editable=False),
                _c("tipo_relacion", obligatorio=True, editable=False,
                   ayuda="ALIMENTA | PERTENECE_A | ACOMETIDA | COMPARTE_VERTICE"),
                _c("capa_origen", editable=False),
                _c("capa_destino", editable=False),
            ],
        ),
        Capa(
            nombre="ptnt_foto",
            tipo_geometria="POINT",
            descripcion="Fotografías por elemento, con ubicación y fecha de captura",
            campos=[
                _c("guid", obligatorio=True, editable=False),
                _c("elemento_guid", obligatorio=True, editable=False,
                   etiqueta="Elemento"),
                _c("capa_elemento", editable=False, etiqueta="Capa"),
                _c("orden_trabajo", editable=False, etiqueta="Orden"),
                _c("archivo", obligatorio=True, etiqueta="Archivo"),
                # La ubicación de la foto es la del DISPOSITIVO al dispararla, no
                # la del elemento: es la evidencia de que el técnico estuvo ahí.
                _c("lat", "REAL", editable=False, etiqueta="Latitud"),
                _c("lon", "REAL", editable=False, etiqueta="Longitud"),
                _c("altitud_m", "REAL", editable=False),
                _c("precision_m", "REAL", editable=False, etiqueta="Precisión GPS (m)"),
                _c("tomada_en", "DATETIME", obligatorio=True, editable=False,
                   etiqueta="Fecha y hora de captura"),
                _c("rumbo_grados", "REAL", editable=False, etiqueta="Rumbo"),
                _c("tomada_por", editable=False, etiqueta="Técnico"),
                _c("descripcion", etiqueta="Descripción"),
                _c("hash_sha256", editable=False, etiqueta="Hash del archivo",
                   ayuda="Detecta que la foto no fue sustituida después de la captura"),
                _c("bytes", "INTEGER", editable=False),
                _c("sincronizada", "BOOLEAN", editable=False),
            ],
        ),
        Capa(
            # El diario es la pieza que hace auditable todo el ciclo: sin él, la
            # sincronización sería un "aquí está la red nueva" imposible de
            # revisar, aceptar parcialmente o revertir.
            nombre="ptnt_cambio",
            tipo_geometria="NONE",
            descripcion="Diario de cambios: toda edición hecha en campo, con antes y después",
            campos=[
                _c("guid", obligatorio=True, editable=False),
                _c("secuencia", "INTEGER", obligatorio=True, editable=False),
                _c("capa", obligatorio=True, editable=False),
                _c("elemento_guid", obligatorio=True, editable=False),
                _c("operacion", obligatorio=True, editable=False,
                   dominio=DOM_OPERACION),
                _c("campo", editable=False, etiqueta="Campo modificado"),
                _c("valor_antes", editable=False),
                _c("valor_despues", editable=False),
                _c("geom_antes", editable=False, etiqueta="Geometría anterior (WKT)"),
                _c("geom_despues", editable=False, etiqueta="Geometría nueva (WKT)"),
                _c("orden_trabajo", editable=False),
                _c("autor", obligatorio=True, editable=False),
                _c("ocurrido_en", "DATETIME", obligatorio=True, editable=False),
                # Dónde estaba el técnico al hacer el cambio: distingue una
                # corrección hecha en sitio de una hecha desde la oficina.
                _c("lat_dispositivo", "REAL", editable=False),
                _c("lon_dispositivo", "REAL", editable=False),
                _c("precision_m", "REAL", editable=False),
                _c("motivo", etiqueta="Motivo del cambio"),
                _c("propagado_de", editable=False, etiqueta="Propagado de",
                   ayuda="Si el cambio lo causó mover otro elemento (snap)"),
                _c("estado_revision", editable=False,
                   dominio=["PENDIENTE", "ACEPTADO", "RECHAZADO"]),
                # Un trabajo de revisión puede llevar varios días. El técnico
                # sube el avance cada tarde y sigue al siguiente, así que el
                # diario acumula. Sin marcar lo ya enviado, cada sincronización
                # reenviaría todo lo anterior y el histórico contaría el mismo
                # cambio tantas veces como días duró la orden.
                _c("sincronizado", "BOOLEAN", editable=False,
                   etiqueta="Ya enviado"),
                _c("lote_id", editable=False, etiqueta="Lote en que se envió"),
            ],
        ),
    ]


def capas_referencia() -> list[Capa]:
    """Capas de solo lectura: contexto que el técnico necesita pero no edita."""

    return [
        Capa(
            nombre="ptnt_sector_objetivo",
            tipo_geometria="POINT",
            descripcion="Sectores priorizados por el análisis (solo consulta)",
            editable=False,
            campos=[
                _c("sector_id", editable=False),
                _c("prioridad", "REAL", editable=False),
                _c("clientes", "INTEGER", editable=False),
                _c("recuperable_kwh_mes", "REAL", editable=False),
                _c("radio_m", "REAL", editable=False),
                _c("motivo", editable=False),
            ],
        ),
    ]


def todas_las_capas() -> list[Capa]:
    return capas_red() + capas_trabajo() + capas_referencia()


def capas_editables() -> list[str]:
    return [c.nombre for c in capas_red() if c.editable]


def esquema_para_movil() -> dict:
    """Descripción del esquema que el móvil consume para armar los formularios.

    El móvil **no** trae los formularios cableados: los construye desde esta
    descripción. Así, agregar un campo o un dominio no obliga a publicar una
    versión nueva de la aplicación en la tienda — que en una distribuidora puede
    tardar semanas.
    """

    return {
        "version_esquema": VERSION_ESQUEMA,
        "capas": [
            {
                "nombre": c.nombre,
                "geometria": c.tipo_geometria,
                "descripcion": c.descripcion,
                "editable": c.editable,
                "srid": c.srid,
                "campos": [
                    {
                        "nombre": f.nombre, "tipo": f.tipo,
                        "etiqueta": f.etiqueta or f.nombre,
                        "obligatorio": f.obligatorio, "editable": f.editable,
                        "dominio": f.dominio, "ayuda": f.ayuda,
                    }
                    for f in c.campos
                ],
            }
            for c in todas_las_capas()
        ],
    }
