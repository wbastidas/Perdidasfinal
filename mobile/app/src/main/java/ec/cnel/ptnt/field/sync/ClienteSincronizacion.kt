package ec.cnel.ptnt.field.sync

import ec.cnel.ptnt.field.data.GeoPackageDao
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Cliente de sincronización con el backend.
 *
 * Tres decisiones que vienen de trabajar sin señal confiable:
 *
 * 1. **Tiempos de espera largos y reintentos manuales.** En zona periurbana la
 *    conexión aparece y desaparece; un timeout de 30 s aborta descargas que
 *    habrían terminado. Se usan minutos, y el reintento lo decide el técnico —no
 *    un bucle automático que consuma su plan de datos sin que lo sepa.
 *
 * 2. **Descarga a archivo temporal y reemplazo atómico.** Si la conexión se corta
 *    a mitad, el paquete anterior sigue intacto. Sobrescribir directamente
 *    dejaría al técnico con un GeoPackage truncado y sin trabajo del día.
 *
 * 3. **La subida NO borra nada local.** El paquete con los cambios se conserva
 *    hasta que el backend confirma recepción *y* el supervisor acepta. Borrar al
 *    recibir el 200 perdería una jornada entera si algo falla del otro lado.
 */
class ClienteSincronizacion(
    private val urlBase: String,
    private val almacen: AlmacenSesion
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.MINUTES)
        .retryOnConnectionFailure(true)
        .build()

    sealed class Resultado<out T> {
        data class Ok<T>(val valor: T) : Resultado<T>()
        data class Error(val mensaje: String, val codigo: Int = 0) : Resultado<Nothing>()
    }

    // ------------------------------------------------------------ vinculación
    /**
     * Vincula el dispositivo y guarda el token.
     *
     * Es la única llamada que usa usuario y contraseña. Después, todo va con el
     * token: si el equipo se pierde, el backend lo revoca sin tocar la cuenta del
     * técnico.
     */
    suspend fun vincular(
        usuario: String, password: String, dispositivoId: String
    ): Resultado<String> = withContext(Dispatchers.IO) {
        val cuerpo = JSONObject().apply {
            put("usuario", usuario)
            put("password", password)
            put("dispositivo_id", dispositivoId)
        }.toString().toRequestBody("application/json".toMediaType())

        try {
            http.newCall(
                Request.Builder().url("$urlBase/movil/vincular").post(cuerpo).build()
            ).execute().use { r ->
                val texto = r.body?.string() ?: ""
                if (!r.isSuccessful) {
                    return@withContext Resultado.Error(
                        mensajeDeError(texto, r.code), r.code)
                }
                val j = JSONObject(texto)
                almacen.guardar(
                    token = j.getString("token"),
                    usuario = j.getString("usuario"),
                    nombre = j.optString("nombre"),
                    rol = j.optString("rol")
                )
                Resultado.Ok(j.getString("token"))
            }
        } catch (e: Exception) {
            Resultado.Error("No se pudo conectar con el servidor: ${e.message}")
        }
    }

    // --------------------------------------------------------------- descarga
    /**
     * Descarga el paquete de trabajo.
     *
     * [onProgreso] recibe bytes descargados y total (o -1 si el servidor no lo
     * informa): en una conexión lenta, una barra que avanza es la diferencia
     * entre esperar y cancelar creyendo que se colgó.
     */
    suspend fun descargarPaquete(
        destino: File,
        onProgreso: (Long, Long) -> Unit = { _, _ -> }
    ): Resultado<File> = withContext(Dispatchers.IO) {
        val token = almacen.token()
            ?: return@withContext Resultado.Error("Dispositivo no vinculado.")

        val temporal = File(destino.parentFile, "${destino.name}.descargando")
        try {
            http.newCall(
                Request.Builder().url("$urlBase/movil/paquete")
                    .header("Authorization", "Bearer $token").build()
            ).execute().use { r ->
                if (!r.isSuccessful) {
                    return@withContext Resultado.Error(
                        mensajeDeError(r.body?.string() ?: "", r.code), r.code)
                }
                val total = r.body?.contentLength() ?: -1L
                var leidos = 0L
                r.body?.byteStream()?.use { entrada ->
                    temporal.outputStream().use { salida ->
                        val buf = ByteArray(1 shl 16)
                        while (true) {
                            val n = entrada.read(buf)
                            if (n <= 0) break
                            salida.write(buf, 0, n)
                            leidos += n
                            onProgreso(leidos, total)
                        }
                    }
                }
            }
            // Reemplazo atómico: si algo falló antes, el paquete anterior sigue
            // intacto y el técnico no se queda sin trabajo del día.
            if (destino.exists()) destino.delete()
            if (!temporal.renameTo(destino)) {
                return@withContext Resultado.Error(
                    "No se pudo reemplazar el paquete local.")
            }
            Resultado.Ok(destino)
        } catch (e: Exception) {
            temporal.delete()
            Resultado.Error("Fallo la descarga: ${e.message}")
        }
    }

    // ------------------------------------------------------------------ subida
    data class ResumenSubida(
        val loteId: String,
        val cambios: Int,
        val fotos: Int,
        val bloqueado: Boolean,
        val hallazgos: List<String>,
        val mensaje: String
    )

    /**
     * Sube el paquete con las ediciones.
     *
     * Se verifica **antes** que haya algo que subir: una sincronización vacía
     * consume datos y genera un lote inútil que el supervisor tiene que revisar.
     */
    suspend fun sincronizar(
        paquete: File,
        dao: GeoPackageDao,
        onProgreso: (Long, Long) -> Unit = { _, _ -> }
    ): Resultado<ResumenSubida> = withContext(Dispatchers.IO) {
        val token = almacen.token()
            ?: return@withContext Resultado.Error("Dispositivo no vinculado.")

        val pendientes = dao.cambiosPendientes()
        if (pendientes == 0) {
            return@withContext Resultado.Error(
                "No hay cambios que sincronizar. Registre las novedades antes de " +
                        "cerrar la orden.")
        }

        val cuerpo = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart(
                "archivo", paquete.name,
                paquete.asRequestBody("application/octet-stream".toMediaType())
            ).build()

        try {
            http.newCall(
                Request.Builder().url("$urlBase/movil/sincronizar")
                    .header("Authorization", "Bearer $token")
                    .post(cuerpo).build()
            ).execute().use { r ->
                val texto = r.body?.string() ?: ""
                val j = try { JSONObject(texto) } catch (_: Exception) { JSONObject() }
                val hallazgos = mutableListOf<String>()
                j.optJSONArray("hallazgos")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        val h = arr.getJSONObject(i)
                        hallazgos += "[${h.optString("severidad")}] " +
                                h.optString("detalle")
                    }
                }
                if (r.code !in 200..299 && r.code != 422) {
                    return@withContext Resultado.Error(
                        mensajeDeError(texto, r.code), r.code)
                }
                val resumen = j.optJSONObject("resumen") ?: JSONObject()
                Resultado.Ok(
                    ResumenSubida(
                        loteId = j.optString("lote_id"),
                        cambios = resumen.optInt("cambios"),
                        fotos = resumen.optInt("fotos"),
                        bloqueado = resumen.optBoolean("bloqueado"),
                        hallazgos = hallazgos,
                        mensaje = j.optString("mensaje")
                    )
                )
            }
        } catch (e: Exception) {
            Resultado.Error("Fallo la sincronización: ${e.message}. " +
                    "Los cambios siguen guardados en el dispositivo.")
        }
    }

    private fun mensajeDeError(cuerpo: String, codigo: Int): String = when (codigo) {
        401 -> "Sesión no válida. Vuelva a vincular el dispositivo."
        404 -> try { JSONObject(cuerpo).optString("detail") } catch (_: Exception) {
            "No hay paquete disponible."
        }
        else -> try {
            JSONObject(cuerpo).optString("detail").ifBlank { "Error $codigo" }
        } catch (_: Exception) { "Error $codigo del servidor" }
    }
}

/**
 * Almacenamiento de la sesión.
 *
 * El token va en `EncryptedSharedPreferences`: en un teléfono compartido entre
 * cuadrillas, guardarlo en claro permitiría a cualquiera con acceso al
 * dispositivo sincronizar cambios a nombre de otro técnico.
 */
interface AlmacenSesion {
    fun guardar(token: String, usuario: String, nombre: String, rol: String)
    fun token(): String?
    fun usuario(): String?
    fun limpiar()
}
