package ec.cnel.ptnt.field.data

import org.json.JSONObject

/**
 * Formularios construidos desde el esquema que viene en el paquete.
 *
 * La app **no** trae los formularios cableados. El manifiesto del GeoPackage
 * incluye la descripción completa de cada capa —campos, etiquetas, tipos,
 * dominios, obligatoriedad— y de ahí se arma la pantalla.
 *
 * El motivo es operativo, no estético: en una distribuidora, publicar una versión
 * nueva de la app puede tardar semanas entre pruebas y aprobación. Si agregar un
 * dominio de hallazgo o un campo de inspección obligara a publicar, el sistema se
 * congela en la primera versión. Leyendo el esquema del propio archivo, el
 * backend cambia el formulario y el técnico lo ve en la siguiente descarga.
 *
 * Si el paquete llegara sin esquema, se cae a los tipos que declara SQLite: se
 * pierden etiquetas y dominios, pero el técnico puede seguir trabajando.
 */
/** Un valor de dominio: lo que se guarda y lo que el técnico lee. */
data class ValorDominio(val codigo: String, val descripcion: String) {
    val etiqueta: String get() = descripcion.ifBlank { codigo }
}

/**
 * Valores permitidos de un campo: lista cerrada o rango.
 *
 * El rango existe porque un poste de 40 m no es una opción que falte en una
 * lista: es un error de tecleo, y lo único que hace falta es que no entre.
 */
data class Dominio(
    val nombre: String,
    val tipo: String,                       // CODIFICADO | RANGO
    val valores: List<ValorDominio> = emptyList(),
    val minimo: Double? = null,
    val maximo: Double? = null,
    val descripcion: String = ""
) {
    val esRango: Boolean get() = tipo.equals("RANGO", ignoreCase = true)

    fun admite(valor: Any?): Boolean {
        val s = valor?.toString().orEmpty()
        if (s.isBlank()) return true        // lo obligatorio se controla aparte
        if (esRango) {
            val n = s.toDoubleOrNull() ?: return false
            return (minimo == null || n >= minimo) && (maximo == null || n <= maximo)
        }
        return valores.any { clave(it.codigo) == clave(s) }
    }

    fun descripcionDe(codigo: Any?): String {
        val s = codigo?.toString().orEmpty()
        return valores.firstOrNull { clave(it.codigo) == clave(s) }?.etiqueta ?: s
    }

    val codigos: List<String> get() = valores.map { it.codigo }

    companion object {
        /**
         * Normaliza para comparar códigos con lo que SQLite devuelve.
         *
         * Un campo de tensión declarado REAL vuelve como `220.0` y el dominio
         * dice `"220"`. Comparar los textos crudos rechazaría un dato correcto
         * en cada tramo de baja tensión. Tiene que coincidir con `_clave()` de
         * `domains.py`: si las dos mitades normalizan distinto, el móvil acepta
         * lo que el servidor rechaza y el técnico no entiende por qué.
         */
        fun clave(v: Any?): String {
            val s = v?.toString()?.trim().orEmpty()
            val f = s.toDoubleOrNull() ?: return s
            return if (f == Math.floor(f) && !f.isInfinite()) f.toLong().toString()
            else f.toString()
        }
    }
}

/**
 * Un subtipo: no es «otro valor de un campo», es otra variante del elemento.
 *
 * Al elegirlo cambian los dominios de ciertos campos, sus valores por defecto y
 * cuáles siquiera aplican — el mismo comportamiento del modelo de datos del SIG.
 */
data class Subtipo(
    val codigo: String,
    val etiqueta: String,
    val descripcion: String = "",
    val dominios: Map<String, Dominio> = emptyMap(),
    val defectos: Map<String, Any?> = emptyMap(),
    val ocultos: Set<String> = emptySet(),
    val obligatorios: Set<String> = emptySet()
)

/** Contingencia: el dominio de un campo depende del valor de **otro** campo. */
data class ReglaDominio(
    val campoCondicion: String,
    val valorCondicion: String,
    val campoAfectado: String,
    val dominio: Dominio
) {
    fun aplica(atributos: Map<String, Any?>): Boolean =
        atributos[campoCondicion]?.toString() == valorCondicion
}

data class CampoFormulario(
    val nombre: String,
    val tipo: String,
    val etiqueta: String,
    val obligatorio: Boolean,
    val editable: Boolean,
    val dominio: List<String>,
    val ayuda: String,
    val dominioDetalle: Dominio? = null
) {
    val esBooleano: Boolean get() = tipo.equals("BOOLEAN", ignoreCase = true)
    val esNumerico: Boolean get() =
        tipo.equals("REAL", ignoreCase = true) || tipo.equals("INTEGER", ignoreCase = true)
    val esFecha: Boolean get() = tipo.equals("DATETIME", ignoreCase = true)
    val tieneDominio: Boolean get() = dominio.isNotEmpty()
}

/** Consecuencias de cambiar el subtipo de un elemento ya capturado. */
data class CambioSubtipo(
    val atributos: Map<String, Any?>,
    val ajustados: Map<String, Pair<Any?, Any?>> = emptyMap(),
    val invalidados: Map<String, Any?> = emptyMap(),
    val descartados: Map<String, Any?> = emptyMap()
) {
    val hayQueAvisar: Boolean
        get() = ajustados.isNotEmpty() || invalidados.isNotEmpty() || descartados.isNotEmpty()

    /** Lo que se le dice al técnico, en su idioma y sin tecnicismos del modelo. */
    fun resumen(etiquetaDe: (String) -> String): String {
        val partes = mutableListOf<String>()
        if (ajustados.isNotEmpty()) partes += ajustados.entries.joinToString("; ") {
            "${etiquetaDe(it.key)}: ${txt(it.value.first)} → ${txt(it.value.second)}"
        }
        if (invalidados.isNotEmpty()) partes += "vuelva a elegir " +
                invalidados.entries.joinToString(", ") {
                    "${etiquetaDe(it.key)} (antes ${txt(it.value)})"
                }
        if (descartados.isNotEmpty()) partes += "no aplican aquí: " +
                descartados.keys.joinToString(", ") { etiquetaDe(it) }
        return partes.joinToString(". ")
    }

    private fun txt(v: Any?): String =
        if (v == null || v.toString().isBlank()) "vacío" else v.toString()
}

data class CapaFormulario(
    val nombre: String,
    val geometria: String,
    val descripcion: String,
    val editable: Boolean,
    val srid: Int,
    val campos: List<CampoFormulario>,
    val campoSubtipo: String = "",
    val subtipos: List<Subtipo> = emptyList(),
    val reglas: List<ReglaDominio> = emptyList()
) {
    val tieneSubtipos: Boolean get() = campoSubtipo.isNotBlank() && subtipos.isNotEmpty()

    fun campo(nombre: String): CampoFormulario? = campos.firstOrNull { it.nombre == nombre }

    fun etiquetaDe(nombre: String): String = campo(nombre)?.etiqueta ?: nombre

    /**
     * El subtipo del elemento, o `null` si todavía no tiene ninguno.
     *
     * **No se supone uno.** Buena parte de la red llega del SIG sin el campo
     * poblado, y asumir el primero aplicaría a esos elementos unas reglas que
     * nadie eligió: un tramo de baja tensión heredaría el dominio de media y sus
     * 220 V aparecerían como inválidos. Sin subtipo rige el dominio base.
     */
    fun subtipoDe(atributos: Map<String, Any?>): Subtipo? {
        if (!tieneSubtipos) return null
        val actual = atributos[campoSubtipo]?.toString()?.trim().orEmpty()
        if (actual.isBlank()) return null
        return subtipos.firstOrNull { it.codigo == actual }
    }

    /** El que se propone al **crear** un elemento nuevo: ahí sí hay que elegir. */
    fun subtipoPorDefecto(): Subtipo? = subtipos.firstOrNull()

    /**
     * El dominio que rige para un campo **dado lo que el elemento tiene ahora**.
     *
     * De más específico a más general: contingencia, subtipo, dominio base. El
     * orden importa — si el subtipo ganara a la contingencia habría que duplicar
     * cada regla en cada subtipo.
     */
    fun dominioEfectivo(campo: String, atributos: Map<String, Any?>): Dominio? {
        reglas.firstOrNull { it.campoAfectado == campo && it.aplica(atributos) }
            ?.let { return it.dominio }
        subtipoDe(atributos)?.dominios?.get(campo)?.let { return it }
        return campo(campo)?.dominioDetalle
    }

    fun esObligatorio(campo: String, atributos: Map<String, Any?>): Boolean =
        subtipoDe(atributos)?.obligatorios?.contains(campo) == true ||
                campo(campo)?.obligatorio == true

    /** Campos que tiene sentido mostrar: preguntar el ducto de una red aérea es
     *  pedir un dato que no existe, y alguien acabará inventándolo. */
    fun camposVisibles(atributos: Map<String, Any?>): List<CampoFormulario> {
        val ocultos = subtipoDe(atributos)?.ocultos ?: emptySet()
        return camposOrdenados.filter { it.nombre !in ocultos }
    }

    /**
     * Cambia el subtipo y arregla lo que deja de encajar.
     *
     * Campo por campo: si lo capturado sigue siendo válido se respeta —el técnico
     * lo vio en sitio—; si no y el subtipo nuevo trae defecto, se aplica y se
     * avisa; y si no hay defecto se limpia pidiendo volver a elegir, mostrando lo
     * que había. Conservarlo dejaría entrar justo el dato imposible que el
     * subtipo existe para impedir; borrarlo callando perdería trabajo de campo
     * que nadie va a volver a hacer.
     */
    fun aplicarSubtipo(atributos: Map<String, Any?>, nuevo: String): CambioSubtipo {
        if (!tieneSubtipos) return CambioSubtipo(atributos)
        val destino = subtipos.firstOrNull { it.codigo == nuevo }
            ?: return CambioSubtipo(atributos + (campoSubtipo to nuevo))

        val salida = atributos.toMutableMap()
        salida[campoSubtipo] = nuevo
        val ajustados = mutableMapOf<String, Pair<Any?, Any?>>()
        val invalidados = mutableMapOf<String, Any?>()
        val descartados = mutableMapOf<String, Any?>()

        // El defecto solo rellena lo vacío: pisar un valor capturado en sitio
        // porque el subtipo «sugiere» otro es perder trabajo de campo.
        destino.defectos.forEach { (campo, valor) ->
            if (salida[campo]?.toString().isNullOrBlank()) salida[campo] = valor
        }

        campos.forEach { f ->
            if (f.nombre == campoSubtipo || f.nombre == "guid" || f.nombre == "fid") return@forEach
            val valor = salida[f.nombre]

            if (f.nombre in destino.ocultos) {
                if (!valor?.toString().isNullOrBlank()) {
                    descartados[f.nombre] = valor
                    salida[f.nombre] = null
                }
                return@forEach
            }
            if (valor?.toString().isNullOrBlank()) return@forEach

            val dom = dominioEfectivo(f.nombre, salida) ?: return@forEach
            if (dom.admite(valor)) return@forEach

            val defecto = destino.defectos[f.nombre]
            if (defecto != null) {
                ajustados[f.nombre] = valor to defecto
                salida[f.nombre] = defecto
            } else {
                invalidados[f.nombre] = valor
                salida[f.nombre] = null
            }
        }
        return CambioSubtipo(salida, ajustados, invalidados, descartados)
    }

    /**
     * Campos en el orden en que se muestran: primero los que se llenan en casi
     * toda visita.
     *
     * No es cosmético. En el teléfono la hoja se abre a media altura, y lo que
     * queda visible ahí es lo primero de esta lista. Si el hallazgo o la lectura
     * quedaran al final, cada visita costaría un gesto extra para desplazarse —
     * cientos de veces por jornada.
     */
    val camposOrdenados: List<CampoFormulario>
        get() = campos.sortedBy { c ->
            when {
                c.editable && c.nombre in PRIORITARIOS -> 0
                c.editable && c.obligatorio -> 1
                c.editable -> 2
                else -> 3                       // calculados, al final
            }
        }

    companion object {
        private val PRIORITARIOS = setOf(
            "hallazgo", "lectura_medidor", "inspeccionado", "estado",
            "observacion", "tipo_medidor", "medidor_serie", "potencia_kva", "fase"
        )
    }
}

class EsquemaFormulario(private val capas: Map<String, CapaFormulario>) {

    fun capa(nombre: String): CapaFormulario? = capas[nombre]
    fun todas(): List<CapaFormulario> = capas.values.toList()
    val version: String get() = versionEsquema
    private var versionEsquema: String = ""

    companion object {
        /** Construye el esquema desde el JSON del manifiesto. */
        fun desdeManifiesto(json: String?): EsquemaFormulario {
            if (json.isNullOrBlank()) return EsquemaFormulario(emptyMap())
            return try {
                val raiz = JSONObject(json)
                val out = mutableMapOf<String, CapaFormulario>()
                val arr = raiz.optJSONArray("capas") ?: return EsquemaFormulario(emptyMap())
                for (i in 0 until arr.length()) {
                    val c = arr.getJSONObject(i)
                    val campos = mutableListOf<CampoFormulario>()
                    val ca = c.optJSONArray("campos")
                    if (ca != null) {
                        for (j in 0 until ca.length()) {
                            val f = ca.getJSONObject(j)
                            val dom = mutableListOf<String>()
                            f.optJSONArray("dominio")?.let { d ->
                                for (k in 0 until d.length()) dom += d.getString(k)
                            }
                            campos += CampoFormulario(
                                nombre = f.getString("nombre"),
                                tipo = f.optString("tipo", "TEXT"),
                                etiqueta = f.optString("etiqueta", f.getString("nombre")),
                                obligatorio = f.optBoolean("obligatorio", false),
                                editable = f.optBoolean("editable", true),
                                dominio = dom,
                                ayuda = f.optString("ayuda", ""),
                                dominioDetalle = leerDominio(f.optJSONObject("dominio_detalle"))
                            )
                        }
                    }
                    out[c.getString("nombre")] = CapaFormulario(
                        nombre = c.getString("nombre"),
                        geometria = c.optString("geometria", "NONE"),
                        descripcion = c.optString("descripcion", ""),
                        editable = c.optBoolean("editable", true),
                        srid = c.optInt("srid", 32717),
                        campos = campos,
                        campoSubtipo = c.optString("campo_subtipo", ""),
                        subtipos = leerSubtipos(c.optJSONArray("subtipos")),
                        reglas = leerReglas(c.optJSONArray("reglas"))
                    )
                }
                EsquemaFormulario(out).apply {
                    versionEsquema = raiz.optString("version_esquema", "")
                }
            } catch (_: Exception) {
                EsquemaFormulario(emptyMap())
            }
        }

        private fun leerDominio(o: JSONObject?): Dominio? {
            if (o == null) return null
            val valores = mutableListOf<ValorDominio>()
            o.optJSONArray("valores")?.let { arr ->
                for (i in 0 until arr.length()) {
                    val v = arr.getJSONObject(i)
                    valores += ValorDominio(
                        v.optString("codigo", ""), v.optString("descripcion", ""))
                }
            }
            return Dominio(
                nombre = o.optString("nombre", ""),
                tipo = o.optString("tipo", "CODIFICADO"),
                valores = valores,
                minimo = if (o.has("minimo") && !o.isNull("minimo")) o.optDouble("minimo") else null,
                maximo = if (o.has("maximo") && !o.isNull("maximo")) o.optDouble("maximo") else null,
                descripcion = o.optString("descripcion", "")
            )
        }

        private fun leerSubtipos(arr: org.json.JSONArray?): List<Subtipo> {
            if (arr == null) return emptyList()
            val out = mutableListOf<Subtipo>()
            for (i in 0 until arr.length()) {
                val s = arr.getJSONObject(i)
                val dominios = mutableMapOf<String, Dominio>()
                s.optJSONObject("dominios")?.let { d ->
                    d.keys().forEach { k ->
                        leerDominio(d.optJSONObject(k))?.let { dominios[k] = it }
                    }
                }
                val defectos = mutableMapOf<String, Any?>()
                s.optJSONObject("defectos")?.let { d ->
                    d.keys().forEach { k -> defectos[k] = d.opt(k) }
                }
                out += Subtipo(
                    codigo = s.optString("codigo", ""),
                    etiqueta = s.optString("etiqueta", s.optString("codigo", "")),
                    descripcion = s.optString("descripcion", ""),
                    dominios = dominios,
                    defectos = defectos,
                    ocultos = listaDe(s.optJSONArray("ocultos")).toSet(),
                    obligatorios = listaDe(s.optJSONArray("obligatorios")).toSet()
                )
            }
            return out
        }

        private fun leerReglas(arr: org.json.JSONArray?): List<ReglaDominio> {
            if (arr == null) return emptyList()
            val out = mutableListOf<ReglaDominio>()
            for (i in 0 until arr.length()) {
                val r = arr.getJSONObject(i)
                val dom = leerDominio(r.optJSONObject("dominio")) ?: continue
                out += ReglaDominio(
                    campoCondicion = r.optString("campo_condicion", ""),
                    valorCondicion = r.optString("valor_condicion", ""),
                    campoAfectado = r.optString("campo_afectado", ""),
                    dominio = dom
                )
            }
            return out
        }

        private fun listaDe(arr: org.json.JSONArray?): List<String> {
            if (arr == null) return emptyList()
            return (0 until arr.length()).map { arr.getString(it) }
        }
    }
}
