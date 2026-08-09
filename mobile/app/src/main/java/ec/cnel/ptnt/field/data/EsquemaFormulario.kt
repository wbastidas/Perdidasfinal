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
data class CampoFormulario(
    val nombre: String,
    val tipo: String,
    val etiqueta: String,
    val obligatorio: Boolean,
    val editable: Boolean,
    val dominio: List<String>,
    val ayuda: String
) {
    val esBooleano: Boolean get() = tipo.equals("BOOLEAN", ignoreCase = true)
    val esNumerico: Boolean get() =
        tipo.equals("REAL", ignoreCase = true) || tipo.equals("INTEGER", ignoreCase = true)
    val esFecha: Boolean get() = tipo.equals("DATETIME", ignoreCase = true)
    val tieneDominio: Boolean get() = dominio.isNotEmpty()
}

data class CapaFormulario(
    val nombre: String,
    val geometria: String,
    val descripcion: String,
    val editable: Boolean,
    val srid: Int,
    val campos: List<CampoFormulario>
) {
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
                                ayuda = f.optString("ayuda", "")
                            )
                        }
                    }
                    out[c.getString("nombre")] = CapaFormulario(
                        nombre = c.getString("nombre"),
                        geometria = c.optString("geometria", "NONE"),
                        descripcion = c.optString("descripcion", ""),
                        editable = c.optBoolean("editable", true),
                        srid = c.optInt("srid", 32717),
                        campos = campos
                    )
                }
                EsquemaFormulario(out).apply {
                    versionEsquema = raiz.optString("version_esquema", "")
                }
            } catch (_: Exception) {
                EsquemaFormulario(emptyMap())
            }
        }
    }
}
