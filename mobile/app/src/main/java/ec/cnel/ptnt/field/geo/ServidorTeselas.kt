package ec.cnel.ptnt.field.geo

import android.database.sqlite.SQLiteDatabase
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Sirve la cartografía offline del GeoPackage por HTTP local.
 *
 * MapLibre sabe consumir teselas por URL, pero no sabe leerlas de una tabla
 * SQLite. Las alternativas eran: extraer las teselas a disco al abrir el paquete
 * (duplica cientos de MB en un equipo que suele estar lleno) o traer una
 * librería de servidor completa (varios MB de APK para responder a una ruta).
 *
 * Un socket escuchando solo en **loopback** resuelve el problema en unas líneas y
 * sin tráfico externo: nada de esto sale del teléfono. El puerto lo asigna el
 * sistema (`port = 0`) porque un puerto fijo choca cuando la app se reabre antes
 * de que el anterior se libere.
 *
 * Las teselas ya vienen numeradas desde el norte —el backend voltea la fila TMS
 * del MBTiles al armar el paquete—, así que la petición XYZ de MapLibre se
 * traduce directamente a una consulta.
 */
class ServidorTeselas(private val paquete: File) {

    private var socket: ServerSocket? = null
    private val activo = AtomicBoolean(false)
    private val hilos = Executors.newFixedThreadPool(4)
    private var db: SQLiteDatabase? = null

    var puerto: Int = 0
        private set

    /** Niveles de zoom presentes en el paquete, para acotar la fuente del mapa. */
    var zoomMin: Int = 0
        private set
    var zoomMax: Int = 0
        private set

    /** ¿Trae cartografía este paquete? Sin ella la red se dibuja sobre fondo liso. */
    val tieneCartografia: Boolean get() = db != null

    fun iniciar(): Boolean {
        if (activo.get()) return true
        val base = try {
            SQLiteDatabase.openDatabase(
                paquete.absolutePath, null, SQLiteDatabase.OPEN_READONLY
            )
        } catch (_: Exception) {
            return false
        }
        val zooms = mutableListOf<Int>()
        try {
            base.rawQuery(
                "SELECT DISTINCT zoom_level FROM cartografia ORDER BY zoom_level",
                null
            ).use { c -> while (c.moveToNext()) zooms += c.getInt(0) }
        } catch (_: Exception) {
            base.close()
            return false                       // paquete sin cartografía
        }
        if (zooms.isEmpty()) {
            base.close()
            return false
        }
        db = base
        zoomMin = zooms.first()
        zoomMax = zooms.last()

        val s = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
        socket = s
        puerto = s.localPort
        activo.set(true)
        Thread({ aceptar(s) }, "teselas-ptnt").apply { isDaemon = true }.start()
        return true
    }

    /** Plantilla que se le entrega a MapLibre como fuente ráster. */
    fun plantillaUrl(): String = "http://127.0.0.1:$puerto/{z}/{x}/{y}.png"

    fun detener() {
        activo.set(false)
        try { socket?.close() } catch (_: Exception) {}
        hilos.shutdownNow()
        db?.close()
        db = null
    }

    private fun aceptar(s: ServerSocket) {
        while (activo.get()) {
            val cliente = try { s.accept() } catch (_: Exception) { break }
            hilos.execute { atender(cliente) }
        }
    }

    private fun atender(cliente: Socket) {
        cliente.use { c ->
            try {
                val entrada = c.getInputStream().bufferedReader()
                val linea = entrada.readLine() ?: return
                // Se descartan las cabeceras: la petición es siempre un GET de
                // tesela y leerlas completas solo añade latencia por cuadro.
                val ruta = linea.split(" ").getOrNull(1) ?: return
                val partes = ruta.trim('/').removeSuffix(".png").split("/")
                if (partes.size != 3) return responder(c, 404, null)

                val z = partes[0].toIntOrNull() ?: return responder(c, 404, null)
                val x = partes[1].toIntOrNull() ?: return responder(c, 404, null)
                val y = partes[2].toIntOrNull() ?: return responder(c, 404, null)

                val datos = tesela(z, x, y)
                // 204 y no 404 cuando la tesela sencillamente no está: el área
                // descargada es un recorte, y MapLibre trata el 404 como error
                // de red y lo reintenta en bucle.
                if (datos == null) responder(c, 204, null) else responder(c, 200, datos)
            } catch (_: Exception) {
                // Un fallo sirviendo una tesela no puede tumbar el mapa.
            }
        }
    }

    private fun tesela(z: Int, x: Int, y: Int): ByteArray? {
        val base = db ?: return null
        return try {
            base.rawQuery(
                "SELECT tile_data FROM cartografia WHERE zoom_level = ? " +
                        "AND tile_column = ? AND tile_row = ? LIMIT 1",
                arrayOf(z.toString(), x.toString(), y.toString())
            ).use { c -> if (c.moveToFirst()) c.getBlob(0) else null }
        } catch (_: Exception) {
            null
        }
    }

    private fun responder(c: Socket, codigo: Int, cuerpo: ByteArray?) {
        val salida = c.getOutputStream()
        val estado = when (codigo) {
            200 -> "200 OK"; 204 -> "204 No Content"; else -> "404 Not Found"
        }
        val cabeceras = buildString {
            append("HTTP/1.1 $estado\r\n")
            append("Content-Type: image/png\r\n")
            append("Content-Length: ${cuerpo?.size ?: 0}\r\n")
            // Caché agresiva: el paquete es inmutable durante la jornada, así que
            // volver a leer de SQLite en cada desplazamiento es trabajo perdido.
            append("Cache-Control: max-age=86400\r\n")
            append("Connection: close\r\n\r\n")
        }
        salida.write(cabeceras.toByteArray())
        cuerpo?.let { salida.write(it) }
        salida.flush()
    }
}
