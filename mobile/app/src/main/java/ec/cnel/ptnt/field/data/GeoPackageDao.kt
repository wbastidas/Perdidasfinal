package ec.cnel.ptnt.field.data

import android.database.sqlite.SQLiteDatabase
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Acceso directo al GeoPackage con SQLite de Android.
 *
 * No se usa Room ni una capa ORM a propósito. El GeoPackage llega **desde el
 * servidor** con su esquema ya definido; una capa de mapeo obligaría a declarar
 * las entidades en el código de la app y a publicar una versión nueva cada vez
 * que el backend agregue un campo. Leyendo el esquema del propio archivo, la app
 * se adapta sola.
 *
 * El otro motivo es el rendimiento en equipos modestos: SQLite directo con
 * cursores y consultas acotadas por la ventana del mapa evita materializar
 * miles de objetos en memoria, que es lo que hace que una app de campo se cierre
 * sola en un teléfono de 2 GB.
 */
class GeoPackageDao(private val archivo: File) {

    private val db: SQLiteDatabase = SQLiteDatabase.openDatabase(
        archivo.absolutePath, null,
        SQLiteDatabase.OPEN_READWRITE or SQLiteDatabase.NO_LOCALIZED_COLLATORS
    ).apply {
        // WAL: el mapa lee mientras el formulario escribe. Sin esto, cada
        // guardado congela el render y la app se siente rota.
        enableWriteAheadLogging()
        execSQL("PRAGMA synchronous = NORMAL")
        // El caché por defecto es demasiado chico para desplazar el mapa con
        // fluidez; 8 MB es un equilibrio razonable en equipos de gama baja.
        execSQL("PRAGMA cache_size = -8000")
    }

    fun cerrar() = db.close()

    // ---------------------------------------------------------------- capas
    /** Capas del paquete, leídas de los metadatos del propio GeoPackage. */
    fun capas(): List<CapaInfo> {
        val out = mutableListOf<CapaInfo>()
        db.rawQuery(
            """SELECT c.table_name, c.data_type, c.description, c.srs_id,
                      g.geometry_type_name
               FROM gpkg_contents c
               LEFT JOIN gpkg_geometry_columns g ON g.table_name = c.table_name""",
            null
        ).use { cur ->
            while (cur.moveToNext()) {
                out += CapaInfo(
                    nombre = cur.getString(0),
                    tipoDato = cur.getString(1),
                    descripcion = cur.getString(2) ?: "",
                    srid = cur.getInt(3),
                    tipoGeometria = cur.getString(4) ?: "NONE"
                )
            }
        }
        return out
    }

    /** Manifiesto del paquete: usuario, versión de red, esquema de formularios. */
    fun manifiesto(): Map<String, String> {
        val out = mutableMapOf<String, String>()
        try {
            db.rawQuery("SELECT clave, valor FROM ptnt_manifiesto", null).use { c ->
                while (c.moveToNext()) out[c.getString(0)] = c.getString(1)
            }
        } catch (_: Exception) { /* paquete sin manifiesto */ }
        return out
    }

    // ------------------------------------------------------- consulta espacial
    /**
     * Elementos visibles en la ventana actual del mapa.
     *
     * La consulta pasa **primero** por el índice R*Tree y solo después toca la
     * tabla de datos. Sin el R*Tree habría que recorrer las 30 000 filas y
     * decodificar cada geometría en cada desplazamiento del dedo: es la
     * diferencia entre un mapa que responde y uno que se traba.
     *
     * [limite] acota lo que se dibuja en un zoom alejado. Dibujar 20 000 puntos
     * en una pantalla de 6" no aporta información y sí consume la batería.
     */
    fun enVentana(
        capa: String,
        minX: Double, minY: Double, maxX: Double, maxY: Double,
        limite: Int = 3000
    ): List<Elemento> {
        val sql = """
            SELECT t.* FROM "$capa" t
            JOIN "rtree_${capa}_geom" r ON r.id = t.fid
            WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ?
            LIMIT ?
        """.trimIndent()
        val args = arrayOf(
            minX.toString(), maxX.toString(), minY.toString(), maxY.toString(),
            limite.toString()
        )
        return consultar(capa, sql, args)
    }

    fun porGuid(capa: String, guid: String): Elemento? =
        consultar(capa, """SELECT * FROM "$capa" WHERE guid = ? LIMIT 1""",
            arrayOf(guid)).firstOrNull()

    /**
     * Busca por número de cuenta, código o nombre en las capas indicadas.
     *
     * El técnico llega a una casa con la cuenta escrita en la orden, no con una
     * coordenada. Sin búsqueda tiene que reconocer el punto correcto entre
     * cientos de puntos iguales en una manzana, y en la práctica abre el que
     * está más cerca del dedo — que es como se inspecciona al vecino y se
     * levanta un hallazgo contra quien no era.
     *
     * Se buscan solo las columnas de identificación y con `LIKE 'texto%'`: un
     * `%texto%` sobre 30 000 filas sin índice hace que cada tecla tarde, y el
     * técnico deja de usarlo.
     */
    fun buscar(texto: String, capas: List<String>, limite: Int = 30): List<Elemento> {
        val patron = texto.trim()
        if (patron.length < 2) return emptyList()

        val out = mutableListOf<Elemento>()
        for (capa in capas) {
            val columnas = columnasDe(capa).filter { it in COLUMNAS_BUSQUEDA }
            if (columnas.isEmpty()) continue
            val where = columnas.joinToString(" OR ") { """"$it" LIKE ?""" }
            val args = Array(columnas.size) { "$patron%" } + arrayOf(limite.toString())
            out += consultar(
                capa, """SELECT * FROM "$capa" WHERE $where LIMIT ?""", args)
            if (out.size >= limite) break
        }
        return out.take(limite)
    }

    private fun columnasDe(capa: String): List<String> {
        val out = mutableListOf<String>()
        db.rawQuery("""PRAGMA table_info("$capa")""", null).use { c ->
            while (c.moveToNext()) out += c.getString(1)
        }
        return out
    }

    /**
     * Elementos relacionados con uno dado: el panel "Relacionados" del formulario.
     *
     * Es lo que hace operativo el modelo Puesto→Unidad: parado sobre un puesto,
     * el técnico ve y edita sus unidades sin buscarlas en otra pantalla.
     */
    fun relacionados(guid: String): List<Relacion> {
        val out = mutableListOf<Relacion>()
        db.rawQuery(
            """SELECT guid_origen, guid_destino, tipo_relacion,
                      capa_origen, capa_destino
               FROM ptnt_conexion
               WHERE guid_origen = ? OR guid_destino = ?""",
            arrayOf(guid, guid)
        ).use { c ->
            while (c.moveToNext()) {
                val esOrigen = c.getString(0) == guid
                out += Relacion(
                    guid = if (esOrigen) c.getString(1) else c.getString(0),
                    capa = if (esOrigen) c.getString(4) else c.getString(3),
                    tipo = TipoRelacion.desde(c.getString(2))
                )
            }
        }
        return out
    }

    /**
     * Cambia de qué elemento cuelga otro, en el grafo de conectividad.
     *
     * Se hace en una transacción con el borrado del vínculo anterior: si
     * quedaran los dos, el cliente aparecería colgando de dos transformadores y
     * su consumo se contaría dos veces en el balance —peor que el error que se
     * venía a corregir.
     */
    fun reemplazarConexion(
        guidElemento: String, puestoAnterior: String, puestoNuevo: String,
        capaElemento: String
    ) {
        db.beginTransaction()
        try {
            if (puestoAnterior.isNotBlank()) {
                db.execSQL(
                    """DELETE FROM ptnt_conexion
                       WHERE tipo_relacion = 'ALIMENTA'
                         AND ((guid_origen = ? AND guid_destino = ?)
                           OR (guid_origen = ? AND guid_destino = ?))""",
                    arrayOf(puestoAnterior, guidElemento,
                            guidElemento, puestoAnterior)
                )
            }
            val v = android.content.ContentValues().apply {
                put("guid_origen", puestoNuevo)
                put("guid_destino", guidElemento)
                put("tipo_relacion", "ALIMENTA")
                put("capa_origen", "ptnt_puesto_transformacion")
                put("capa_destino", capaElemento)
            }
            db.insert("ptnt_conexion", null, v)
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    private fun consultar(capa: String, sql: String, args: Array<String>): List<Elemento> {
        val out = mutableListOf<Elemento>()
        db.rawQuery(sql, args).use { c ->
            val idxGeom = c.getColumnIndex("geom")
            while (c.moveToNext()) {
                val attrs = mutableMapOf<String, Any?>()
                for (i in 0 until c.columnCount) {
                    if (i == idxGeom) continue
                    attrs[c.getColumnName(i)] = when (c.getType(i)) {
                        android.database.Cursor.FIELD_TYPE_NULL -> null
                        android.database.Cursor.FIELD_TYPE_INTEGER -> c.getLong(i)
                        android.database.Cursor.FIELD_TYPE_FLOAT -> c.getDouble(i)
                        else -> c.getString(i)
                    }
                }
                val geom = if (idxGeom >= 0 && !c.isNull(idxGeom))
                    GpkgGeometria.leer(c.getBlob(idxGeom)) else null
                out += Elemento(
                    fid = (attrs["fid"] as? Long) ?: 0L,
                    guid = attrs["guid"]?.toString() ?: "",
                    capa = capa,
                    geometria = geom,
                    atributos = attrs
                )
            }
        }
        return out
    }

    // ------------------------------------------------------------- escritura
    /**
     * Guarda atributos y/o geometría de un elemento, y **mantiene el índice
     * espacial al día en la misma transacción**.
     *
     * Si el índice se actualizara aparte, un cierre inesperado de la app dejaría
     * el elemento invisible en el mapa aunque el dato estuviera guardado — y el
     * técnico volvería a capturarlo, duplicándolo.
     */
    fun guardar(capa: String, fid: Long, atributos: Map<String, Any?>,
                geometria: Geometria? = null) {
        db.beginTransaction()
        try {
            val valores = android.content.ContentValues()
            atributos.forEach { (k, v) ->
                when (v) {
                    null -> valores.putNull(k)
                    is Boolean -> valores.put(k, if (v) 1 else 0)
                    is Int -> valores.put(k, v)
                    is Long -> valores.put(k, v)
                    is Double -> valores.put(k, v)
                    else -> valores.put(k, v.toString())
                }
            }
            geometria?.let { valores.put("geom", GpkgGeometria.escribir(it)) }
            db.update("\"$capa\"", valores, "fid = ?", arrayOf(fid.toString()))

            geometria?.let { g ->
                val e = g.envolvente()
                db.execSQL(
                    """INSERT OR REPLACE INTO "rtree_${capa}_geom"
                       VALUES (?, ?, ?, ?, ?)""",
                    arrayOf(fid, e[0], e[2], e[1], e[3])
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun insertar(capa: String, atributos: Map<String, Any?>,
                 geometria: Geometria?): Long {
        db.beginTransaction()
        try {
            val valores = android.content.ContentValues()
            atributos.forEach { (k, v) ->
                when (v) {
                    null -> valores.putNull(k)
                    is Boolean -> valores.put(k, if (v) 1 else 0)
                    is Int -> valores.put(k, v)
                    is Long -> valores.put(k, v)
                    is Double -> valores.put(k, v)
                    else -> valores.put(k, v.toString())
                }
            }
            geometria?.let { valores.put("geom", GpkgGeometria.escribir(it)) }
            val fid = db.insert("\"$capa\"", null, valores)
            geometria?.let { g ->
                val e = g.envolvente()
                db.execSQL(
                    """INSERT OR REPLACE INTO "rtree_${capa}_geom"
                       VALUES (?, ?, ?, ?, ?)""",
                    arrayOf(fid, e[0], e[2], e[1], e[3])
                )
            }
            db.setTransactionSuccessful()
            return fid
        } finally {
            db.endTransaction()
        }
    }

    fun eliminar(capa: String, fid: Long) {
        db.beginTransaction()
        try {
            db.delete("\"$capa\"", "fid = ?", arrayOf(fid.toString()))
            db.execSQL("""DELETE FROM "rtree_${capa}_geom" WHERE id = ?""",
                arrayOf(fid))
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    // ------------------------------------------------------ diario de cambios
    /**
     * Anota un cambio en el diario. **Toda** edición pasa por aquí.
     *
     * El diario es lo que hace revisable el trabajo de campo: sin él, la
     * sincronización sería un "aquí está la red nueva" que el supervisor no
     * podría auditar, aceptar parcialmente ni revertir.
     */
    fun registrarCambio(c: CambioCampo) {
        val v = android.content.ContentValues().apply {
            put("guid", c.guid)
            put("secuencia", siguienteSecuencia())
            put("capa", c.capa)
            put("elemento_guid", c.elementoGuid)
            put("operacion", c.operacion.name)
            put("campo", c.campo)
            put("valor_antes", c.valorAntes)
            put("valor_despues", c.valorDespues)
            put("geom_antes", c.geomAntes)
            put("geom_despues", c.geomDespues)
            put("orden_trabajo", c.ordenTrabajo)
            put("autor", c.autor)
            put("ocurrido_en", c.ocurridoEn)
            c.latDispositivo?.let { put("lat_dispositivo", it) }
            c.lonDispositivo?.let { put("lon_dispositivo", it) }
            c.precisionM?.let { put("precision_m", it) }
            put("motivo", c.motivo)
            put("propagado_de", c.propagadoDe)
            put("estado_revision", "PENDIENTE")
            put("sincronizado", 0)
        }
        db.insert("ptnt_cambio", null, v)
    }

    /**
     * Siguiente número de secuencia, contando **también** lo ya sincronizado.
     *
     * El backend rechaza un diario con huecos —un hueco significa ediciones
     * perdidas—. Si la numeración se reiniciara al subir, el lote del día
     * siguiente empezaría en 1 y chocaría con el del día anterior.
     */
    private fun siguienteSecuencia(): Long {
        db.rawQuery("SELECT COALESCE(MAX(secuencia), 0) + 1 FROM ptnt_cambio", null)
            .use { c -> return if (c.moveToFirst()) c.getLong(0) else 1L }
    }

    /**
     * Cambios que todavía no se han enviado.
     *
     * Un trabajo de revisión puede llevar varios días: el diario acumula y el
     * técnico sube el avance cada tarde. Contar todo el diario haría que el
     * contador nunca bajara y que la app pidiera sincronizar lo ya sincronizado.
     */
    fun cambiosPendientes(): Int {
        db.rawQuery(
            "SELECT COUNT(*) FROM ptnt_cambio " +
                    "WHERE COALESCE(sincronizado, 0) = 0", null
        ).use { c -> return if (c.moveToFirst()) c.getInt(0) else 0 }
    }

    /**
     * Marca como enviado lo que el backend acaba de aceptar.
     *
     * Se llama **después** de la respuesta del servidor, nunca antes: si se
     * marcara al empezar la subida y esta fallara a mitad, esos cambios no se
     * volverían a enviar nunca y se perderían en silencio.
     */
    fun marcarSincronizados(loteId: String): Int {
        val v = android.content.ContentValues().apply {
            put("sincronizado", 1)
            put("lote_id", loteId)
        }
        return db.update("ptnt_cambio", v,
            "COALESCE(sincronizado, 0) = 0", null)
    }

    fun marcarFotosSincronizadas(): Int {
        val v = android.content.ContentValues().apply { put("sincronizada", 1) }
        return db.update("ptnt_foto", v, "COALESCE(sincronizada, 0) = 0", null)
    }

    // ------------------------------------------------------------------ fotos
    fun registrarFoto(f: FotoCampo) {
        val v = android.content.ContentValues().apply {
            put("guid", f.guid)
            put("elemento_guid", f.elementoGuid)
            put("capa_elemento", f.capaElemento)
            put("orden_trabajo", f.ordenTrabajo)
            put("archivo", f.archivo)
            put("lat", f.lat); put("lon", f.lon)
            f.altitudM?.let { put("altitud_m", it) }
            f.precisionM?.let { put("precision_m", it) }
            put("tomada_en", f.tomadaEn)
            f.rumboGrados?.let { put("rumbo_grados", it) }
            put("tomada_por", f.tomadaPor)
            put("descripcion", f.descripcion)
            put("hash_sha256", f.hashSha256)
            put("bytes", f.bytes)
            put("sincronizada", 0)
        }
        db.insert("ptnt_foto", null, v)
        // La geometría de la foto es la posición del DISPOSITIVO al dispararla:
        // es la evidencia de que el técnico estuvo en el sitio.
        db.execSQL(
            "UPDATE ptnt_foto SET geom = ? WHERE guid = ?",
            arrayOf(GpkgGeometria.escribir(
                Geometria.punto(f.lon, f.lat, srid = 4326)), f.guid)
        )
    }

    fun fotosDe(elementoGuid: String): List<Map<String, Any?>> {
        val out = mutableListOf<Map<String, Any?>>()
        db.rawQuery(
            "SELECT * FROM ptnt_foto WHERE elemento_guid = ? ORDER BY tomada_en",
            arrayOf(elementoGuid)
        ).use { c ->
            while (c.moveToNext()) {
                val m = mutableMapOf<String, Any?>()
                for (i in 0 until c.columnCount) {
                    if (c.getColumnName(i) == "geom") continue
                    m[c.getColumnName(i)] = c.getString(i)
                }
                out += m
            }
        }
        return out
    }

    // --------------------------------------------------------------- órdenes
    fun ordenes(): List<Map<String, Any?>> {
        val out = mutableListOf<Map<String, Any?>>()
        db.rawQuery(
            "SELECT * FROM ptnt_orden_trabajo ORDER BY recuperable_kwh_mes DESC",
            null
        ).use { c ->
            while (c.moveToNext()) {
                val m = mutableMapOf<String, Any?>()
                for (i in 0 until c.columnCount) {
                    if (c.getColumnName(i) == "geom") continue
                    m[c.getColumnName(i)] = c.getString(i)
                }
                out += m
            }
        }
        return out
    }

    fun actualizarOrden(ordenTrabajo: String, estado: String,
                        resultado: String? = null) {
        val v = android.content.ContentValues().apply {
            put("estado", estado)
            resultado?.let { put("resultado", it) }
            when (estado) {
                "EN_PROCESO" -> put("fecha_inicio", ahoraIso())
                "COMPLETADA" -> put("fecha_cierre", ahoraIso())
            }
        }
        db.update("ptnt_orden_trabajo", v, "orden_trabajo = ?",
            arrayOf(ordenTrabajo))
    }

    private fun ahoraIso(): String =
        java.time.Instant.now().toString()
}

// ---------------------------------------------------------------- modelos
data class CapaInfo(
    val nombre: String, val tipoDato: String, val descripcion: String,
    val srid: Int, val tipoGeometria: String
) {
    val editable: Boolean get() = nombre.startsWith("ptnt_") &&
            nombre !in setOf("ptnt_sector_objetivo", "ptnt_conexion")
}

data class Elemento(
    val fid: Long, val guid: String, val capa: String,
    val geometria: Geometria?, val atributos: Map<String, Any?>
) {
    /** Etiqueta corta para la lista y el globo del mapa. */
    val etiqueta: String
        get() = (atributos["codigo"] ?: atributos["cuenta_contrato"]
            ?: atributos["orden_trabajo"] ?: guid.take(8)).toString()
}

data class Relacion(val guid: String, val capa: String, val tipo: TipoRelacion)

enum class TipoRelacion {
    PERTENECE_A, ACOMETIDA, COMPARTE_VERTICE, ALIMENTA, DESCONOCIDA;

    /** La relación eléctrica NO arrastra geometría: mover un transformador no
     *  debe mover el barrio que alimenta. */
    val propagaGeometria: Boolean get() = this != ALIMENTA && this != DESCONOCIDA

    companion object {
        fun desde(s: String?): TipoRelacion =
            entries.firstOrNull { it.name == s } ?: DESCONOCIDA
    }
}

enum class Operacion { CREAR, MODIFICAR, MOVER, ELIMINAR, RECONECTAR }

data class CambioCampo(
    val guid: String, val capa: String, val elementoGuid: String,
    val operacion: Operacion, val campo: String = "",
    val valorAntes: String? = null, val valorDespues: String? = null,
    val geomAntes: String? = null, val geomDespues: String? = null,
    val ordenTrabajo: String = "", val autor: String,
    val ocurridoEn: String,
    val latDispositivo: Double? = null, val lonDispositivo: Double? = null,
    val precisionM: Double? = null,
    val motivo: String = "", val propagadoDe: String = ""
)

data class FotoCampo(
    val guid: String, val elementoGuid: String, val capaElemento: String,
    val ordenTrabajo: String, val archivo: String,
    val lat: Double, val lon: Double, val altitudM: Double?,
    val precisionM: Double?, val tomadaEn: String, val rumboGrados: Double?,
    val tomadaPor: String, val descripcion: String,
    val hashSha256: String, val bytes: Long
)

// ------------------------------------------------------------- geometría
data class Geometria(
    val tipo: String, val coords: List<Pair<Double, Double>>, val srid: Int
) {
    fun envolvente(): DoubleArray {
        val xs = coords.map { it.first }; val ys = coords.map { it.second }
        return doubleArrayOf(xs.min(), ys.min(), xs.max(), ys.max())
    }

    fun wkt(): String = when (tipo) {
        "Point" -> "POINT(${coords[0].first} ${coords[0].second})"
        "LineString" -> "LINESTRING(" +
                coords.joinToString(", ") { "${it.first} ${it.second}" } + ")"
        else -> ""
    }

    companion object {
        fun punto(x: Double, y: Double, srid: Int = 32717) =
            Geometria("Point", listOf(x to y), srid)
        fun linea(pts: List<Pair<Double, Double>>, srid: Int = 32717) =
            Geometria("LineString", pts, srid)
    }
}

/**
 * Códec del binario GeoPackage: cabecera `GP` + WKB estándar.
 *
 * Se implementa a mano en vez de usar una librería de geometría completa
 * (JTS/GeoTools pesan varios MB) porque este modelo solo tiene puntos y líneas.
 * En una app de campo, cada MB del APK es tiempo de descarga en una conexión
 * mala y espacio en un dispositivo lleno.
 */
object GpkgGeometria {

    fun leer(blob: ByteArray?): Geometria? {
        if (blob == null || blob.size < 8) return null
        if (blob[0] != 'G'.code.toByte() || blob[1] != 'P'.code.toByte()) return null
        val flags = blob[3].toInt()
        val bb = ByteBuffer.wrap(blob).order(
            if (flags and 0x01 == 1) ByteOrder.LITTLE_ENDIAN else ByteOrder.BIG_ENDIAN
        )
        val srid = bb.getInt(4)
        val indEnv = (flags shr 1) and 0x07
        val tamEnv = when (indEnv) { 0 -> 0; 1 -> 32; 2, 3 -> 48; 4 -> 64; else -> 0 }
        val off = 8 + tamEnv
        val wkb = ByteBuffer.wrap(blob, off, blob.size - off)
            .order(ByteOrder.LITTLE_ENDIAN)
        wkb.get()                                  // byte order del WKB
        return when (wkb.int) {
            1 -> Geometria("Point", listOf(wkb.double to wkb.double), srid)
            2 -> {
                val n = wkb.int
                Geometria("LineString",
                    (0 until n).map { wkb.double to wkb.double }, srid)
            }
            else -> null
        }
    }

    fun escribir(g: Geometria): ByteArray {
        val e = g.envolvente()
        val wkbSize = when (g.tipo) {
            "Point" -> 21
            "LineString" -> 9 + g.coords.size * 16
            else -> 0
        }
        val bb = ByteBuffer.allocate(8 + 32 + wkbSize).order(ByteOrder.LITTLE_ENDIAN)
        bb.put('G'.code.toByte()); bb.put('P'.code.toByte())
        bb.put(0)                                   // versión
        bb.put(0b0000_0011)                         // little endian + envolvente XY
        bb.putInt(g.srid)
        bb.putDouble(e[0]); bb.putDouble(e[2]); bb.putDouble(e[1]); bb.putDouble(e[3])
        bb.put(1)                                   // WKB little endian
        when (g.tipo) {
            "Point" -> {
                bb.putInt(1)
                bb.putDouble(g.coords[0].first); bb.putDouble(g.coords[0].second)
            }
            "LineString" -> {
                bb.putInt(2); bb.putInt(g.coords.size)
                g.coords.forEach { bb.putDouble(it.first); bb.putDouble(it.second) }
            }
        }
        return bb.array()
    }
}

/**
 * Columnas por las que se busca.
 *
 * Es una lista corta y no "todas las de texto" a propósito: buscar por
 * observación u origen de edición devuelve decenas de coincidencias inútiles y
 * entierra la que el técnico quería.
 */
private val COLUMNAS_BUSQUEDA = setOf(
    "cuenta_contrato", "codigo", "medidor_serie", "nombre", "serie",
    "ruta_lectura", "orden_trabajo"
)
