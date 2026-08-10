package ec.cnel.ptnt.field

import ec.cnel.ptnt.field.data.EsquemaFormulario
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Subtipos en el dispositivo: al cambiarlos cambian los dominios.
 *
 * Estos casos son **los mismos** que `tests/unit/test_campo_subtipos.py` fija en
 * el backend, y a propósito: la regla que decide si un dato es capturable no
 * puede diferir entre las dos mitades. Si el móvil acepta lo que el servidor
 * rechaza, el técnico llena el formulario, sincroniza por la tarde y le rebotan
 * el trabajo del día sin entender por qué.
 *
 * El manifiesto que se usa aquí está escrito a mano con la forma exacta que
 * emite `esquema_para_movil()`; el test de Python
 * `test_el_manifiesto_usa_las_claves_que_kotlin_lee` comprueba que esa forma
 * siga siendo la que se emite.
 */
class SubtipoTest {

    private val manifiesto = """
    {
      "version_esquema": "1.2.0",
      "capas": [{
        "nombre": "ptnt_puesto_transformacion",
        "geometria": "POINT",
        "descripcion": "Puesto de transformación",
        "editable": true,
        "srid": 32717,
        "campo_subtipo": "configuracion_banco",
        "subtipos": [
          {"codigo": "SIMPLE", "etiqueta": "Monofásico simple", "descripcion": "",
           "dominios": {"fases": {"nombre": "dom_fases_simple", "tipo": "CODIFICADO",
             "valores": [{"codigo": "A", "descripcion": "A"},
                         {"codigo": "B", "descripcion": "B"},
                         {"codigo": "C", "descripcion": "C"}]}},
           "defectos": {"fases": "A", "n_unidades": 1},
           "ocultos": [], "obligatorios": []},
          {"codigo": "DELTA_ABIERTO", "etiqueta": "Delta abierto (2 unidades)",
           "descripcion": "Dos unidades en V",
           "dominios": {"fases": {"nombre": "dom_fases_delta_abierto",
             "tipo": "CODIFICADO",
             "valores": [{"codigo": "AB", "descripcion": "AB"},
                         {"codigo": "BC", "descripcion": "BC"},
                         {"codigo": "CA", "descripcion": "CA"}]}},
           "defectos": {"fases": "AB", "n_unidades": 2},
           "ocultos": [], "obligatorios": []},
          {"codigo": "BANCO_3", "etiqueta": "Banco trifásico (3 unidades)",
           "descripcion": "",
           "dominios": {"fases": {"nombre": "dom_fases_banco3", "tipo": "CODIFICADO",
             "valores": [{"codigo": "ABC", "descripcion": "ABC"}]}},
           "defectos": {"fases": "ABC", "n_unidades": 3},
           "ocultos": [], "obligatorios": []}
        ],
        "reglas": [],
        "campos": [
          {"nombre": "guid", "tipo": "TEXT", "etiqueta": "Identificador",
           "obligatorio": true, "editable": false, "dominio": null,
           "dominio_detalle": null, "ayuda": ""},
          {"nombre": "configuracion_banco", "tipo": "TEXT",
           "etiqueta": "Configuración de banco", "obligatorio": false,
           "editable": true, "dominio": ["SIMPLE", "DELTA_ABIERTO", "BANCO_3"],
           "dominio_detalle": null, "ayuda": ""},
          {"nombre": "fases", "tipo": "TEXT", "etiqueta": "Fases",
           "obligatorio": false, "editable": true,
           "dominio": ["A", "B", "C", "AB", "BC", "CA", "ABC"],
           "dominio_detalle": {"nombre": "dom_fases", "tipo": "CODIFICADO",
             "valores": [{"codigo": "A", "descripcion": "A"},
                         {"codigo": "AB", "descripcion": "AB"},
                         {"codigo": "ABC", "descripcion": "ABC"}]},
           "ayuda": ""},
          {"nombre": "n_unidades", "tipo": "INTEGER", "etiqueta": "Unidades",
           "obligatorio": false, "editable": false, "dominio": null,
           "dominio_detalle": null, "ayuda": ""},
          {"nombre": "tension_v", "tipo": "REAL", "etiqueta": "Tensión (V)",
           "obligatorio": false, "editable": true, "dominio": null,
           "dominio_detalle": {"nombre": "dom_tension_bt", "tipo": "CODIFICADO",
             "valores": [{"codigo": "120", "descripcion": "120 V"},
                         {"codigo": "220", "descripcion": "220 V"}]},
           "ayuda": ""},
          {"nombre": "altura_m", "tipo": "REAL", "etiqueta": "Altura (m)",
           "obligatorio": false, "editable": true, "dominio": null,
           "dominio_detalle": {"nombre": "dom_altura", "tipo": "RANGO",
             "minimo": 6, "maximo": 18}, "ayuda": ""}
        ]
      }]
    }
    """.trimIndent()

    private val capa
        get() = EsquemaFormulario.desdeManifiesto(manifiesto)
            .capa("ptnt_puesto_transformacion")!!

    @Test
    fun `un banco de dos unidades no ofrece tres fases`() {
        val delta = mapOf<String, Any?>("configuracion_banco" to "DELTA_ABIERTO")
        val trifasico = mapOf<String, Any?>("configuracion_banco" to "BANCO_3")

        assertEquals(listOf("AB", "BC", "CA"),
            capa.dominioEfectivo("fases", delta)!!.codigos)
        assertEquals(listOf("ABC"),
            capa.dominioEfectivo("fases", trifasico)!!.codigos)
    }

    @Test
    fun `lo valido se respeta al cambiar de subtipo`() {
        val elem = mapOf<String, Any?>(
            "configuracion_banco" to "SIMPLE", "fases" to "B")
        // B sigue valiendo en SIMPLE; cambiar a SIMPLE otra vez no toca nada.
        val r = capa.aplicarSubtipo(elem, "SIMPLE")
        assertEquals("B", r.atributos["fases"])
        assertTrue(r.ajustados.isEmpty())
    }

    @Test
    fun `lo que deja de valer cae al defecto y se avisa`() {
        val elem = mapOf<String, Any?>(
            "configuracion_banco" to "BANCO_3", "fases" to "ABC")

        val r = capa.aplicarSubtipo(elem, "DELTA_ABIERTO")

        assertEquals("AB", r.atributos["fases"])
        assertEquals("ABC" to "AB", r.ajustados["fases"])
        assertTrue(r.hayQueAvisar)
        assertTrue(r.resumen { capa.etiquetaDe(it) }.contains("Fases"))
    }

    @Test
    fun `el defecto solo rellena lo vacio`() {
        // Pisar un valor capturado en sitio porque el subtipo sugiere otro es
        // perder trabajo de campo.
        val elem = mapOf<String, Any?>("configuracion_banco" to "SIMPLE",
            "fases" to "C")
        val r = capa.aplicarSubtipo(elem, "SIMPLE")
        assertEquals("C", r.atributos["fases"])
    }

    @Test
    fun `sin subtipo no se supone ninguno`() {
        // Un elemento que llega del SIG sin el campo poblado no puede heredar
        // reglas que nadie eligió.
        assertNull(capa.subtipoDe(emptyMap()))
        assertNull(capa.subtipoDe(mapOf("configuracion_banco" to "")))
        assertEquals("SIMPLE", capa.subtipoPorDefecto()!!.codigo)

        // Y sin subtipo rige el dominio base, que es el superconjunto.
        assertEquals(listOf("A", "AB", "ABC"),
            capa.dominioEfectivo("fases", emptyMap())!!.codigos)
    }

    @Test
    fun `el numero que vuelve de sqlite encaja en el dominio`() {
        // SQLite devuelve 220.0 en un campo REAL y el dominio dice "220". Si las
        // dos mitades normalizan distinto, el móvil acepta lo que el servidor
        // rechaza y el técnico no entiende por qué le rebotan el día.
        val dom = capa.campo("tension_v")!!.dominioDetalle!!
        assertTrue(dom.admite(220.0))
        assertTrue(dom.admite("220"))
        assertEquals("220 V", dom.descripcionDe(220.0))
        assertTrue(!dom.admite(221.0))
    }

    @Test
    fun `el rango rechaza el error de tecleo`() {
        val dom = capa.campo("altura_m")!!.dominioDetalle!!
        assertTrue(dom.esRango)
        assertTrue(dom.admite(11))
        assertTrue(!dom.admite(40))
        assertTrue(!dom.admite(2))
        // Vacío se admite: lo obligatorio se controla aparte.
        assertTrue(dom.admite(null))
        assertTrue(dom.admite(""))
    }

    @Test
    fun `un manifiesto sin subtipos no rompe nada`() {
        // Un paquete emitido por una versión anterior del backend tiene que
        // seguir abriéndose: la cuadrilla que no actualizó trabaja igual.
        val viejo = """
        {"version_esquema":"1.1.0","capas":[{"nombre":"ptnt_poste",
          "geometria":"POINT","descripcion":"","editable":true,"srid":32717,
          "campos":[{"nombre":"material","tipo":"TEXT","etiqueta":"Material",
            "obligatorio":false,"editable":true,
            "dominio":["HORMIGON","MADERA"],"ayuda":""}]}]}
        """.trimIndent()

        val c = EsquemaFormulario.desdeManifiesto(viejo).capa("ptnt_poste")!!
        assertTrue(!c.tieneSubtipos)
        assertEquals(listOf("HORMIGON", "MADERA"), c.campo("material")!!.dominio)
        // Y aplicar un subtipo sobre una capa que no los tiene es inocuo.
        assertEquals(emptyMap<String, Any?>(),
            c.aplicarSubtipo(emptyMap(), "LO_QUE_SEA").atributos)
    }
}
