package ec.cnel.ptnt.field.domain

import android.location.Location
import androidx.exifinterface.media.ExifInterface
import ec.cnel.ptnt.field.data.FotoCampo
import ec.cnel.ptnt.field.data.GeoPackageDao
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.time.Instant
import java.util.Locale
import java.util.UUID

/**
 * Captura de fotografías con ubicación y fecha como metadatos.
 *
 * Una foto sin dónde ni cuándo no es evidencia: es una imagen. Si un caso de
 * hurto llega a un proceso administrativo, lo que sostiene el hallazgo es poder
 * demostrar que **esa** foto se tomó **en ese predio** y **ese día**.
 *
 * Los metadatos se escriben en dos lugares a la vez, y no es redundancia inútil:
 *
 * * **EXIF del archivo** — viaja con la imagen si alguien la extrae del sistema.
 * * **Tabla `ptnt_foto` del GeoPackage** — permite consultar y filtrar sin abrir
 *   cada archivo, y sobrevive si la imagen se recomprime en el camino.
 *
 * Además se calcula el **SHA-256** en el momento de la captura. No es contra un
 * atacante sofisticado: es para detectar la sustitución accidental o
 * intencionada de una foto entre la captura y la sincronización.
 */
class CapturaFoto(
    private val dao: GeoPackageDao,
    private val directorioFotos: File,
    private val autor: String
) {

    /**
     * Resolución de guardado.
     *
     * 1600 px de lado mayor y 80 % de calidad son suficientes para distinguir un
     * puente en una acometida o el número de un medidor, y dejan la foto en
     * ~400 KB. A resolución completa, una jornada de 60 fotos serían 300 MB que
     * hay que subir por la red de la distribuidora — y que en la práctica se
     * traduce en técnicos que dejan de tomar fotos.
     */
    var ladoMaximoPx: Int = 1600
    var calidadJpeg: Int = 80

    data class Resultado(
        val guid: String,
        val archivo: File,
        val conUbicacion: Boolean,
        val advertencia: String? = null
    )

    /**
     * Registra una foto ya capturada, escribiendo sus metadatos.
     *
     * [ubicacion] es la posición del **dispositivo** al disparar, no la del
     * elemento: es lo que prueba que el técnico estuvo ahí. Si el GPS no tiene
     * arreglo, la foto se guarda igual pero se marca la falta — perder la
     * evidencia visual por no tener señal sería peor.
     */
    fun registrar(
        archivoOriginal: File,
        elementoGuid: String,
        capaElemento: String,
        ordenTrabajo: String,
        ubicacion: Location?,
        descripcion: String = ""
    ): Resultado {
        val guid = UUID.randomUUID().toString()
        val destino = File(directorioFotos, "$guid.jpg").apply {
            parentFile?.mkdirs()
        }
        archivoOriginal.copyTo(destino, overwrite = true)

        val tomadaEn = Instant.now().toString()
        var advertencia: String? = null

        if (ubicacion != null) {
            escribirExif(destino, ubicacion, tomadaEn, descripcion)
        } else {
            advertencia = "Foto sin ubicación: el GPS no tenía posición al " +
                    "capturar. Sirve como referencia visual, no como evidencia " +
                    "de ubicación."
            escribirExifSinUbicacion(destino, tomadaEn, descripcion)
        }

        dao.registrarFoto(
            FotoCampo(
                guid = guid,
                elementoGuid = elementoGuid,
                capaElemento = capaElemento,
                ordenTrabajo = ordenTrabajo,
                archivo = "fotos/$guid.jpg",
                lat = ubicacion?.latitude ?: 0.0,
                lon = ubicacion?.longitude ?: 0.0,
                altitudM = ubicacion?.altitude,
                precisionM = ubicacion?.accuracy?.toDouble(),
                tomadaEn = tomadaEn,
                rumboGrados = ubicacion?.bearing?.toDouble(),
                tomadaPor = autor,
                descripcion = descripcion,
                hashSha256 = sha256(destino),
                bytes = destino.length()
            )
        )

        return Resultado(guid, destino, ubicacion != null, advertencia)
    }

    /** Todas las fotos de un elemento: varias por elemento es lo normal —el
     *  medidor, la acometida, el entorno, el sello—. */
    fun fotosDe(elementoGuid: String): List<Map<String, Any?>> =
        dao.fotosDe(elementoGuid)

    // ------------------------------------------------------------------ EXIF
    private fun escribirExif(
        f: File, loc: Location, tomadaEn: String, descripcion: String
    ) {
        try {
            ExifInterface(f.absolutePath).apply {
                setGpsInfo(loc)
                setAttribute(ExifInterface.TAG_DATETIME_ORIGINAL, exifFecha())
                setAttribute(ExifInterface.TAG_DATETIME_DIGITIZED, exifFecha())
                setAttribute(ExifInterface.TAG_GPS_DATESTAMP, exifFechaGps())
                if (loc.hasAccuracy()) {
                    setAttribute(
                        ExifInterface.TAG_GPS_HPOSITIONING_ERROR,
                        racional(loc.accuracy.toDouble())
                    )
                }
                if (loc.hasBearing()) {
                    setAttribute(ExifInterface.TAG_GPS_IMG_DIRECTION,
                        racional(loc.bearing.toDouble()))
                    setAttribute(ExifInterface.TAG_GPS_IMG_DIRECTION_REF, "T")
                }
                setAttribute(ExifInterface.TAG_IMAGE_DESCRIPTION,
                    descripcion.ifBlank { "PTNT-BAL levantamiento de campo" })
                setAttribute(ExifInterface.TAG_ARTIST, autor)
                setAttribute(ExifInterface.TAG_SOFTWARE, "PTNT-BAL Campo")
                // El instante ISO-8601 completo va en un campo de usuario: los
                // tags EXIF de fecha no llevan zona horaria, y sin ella una hora
                // es ambigua en cuanto el archivo cruza de sistema.
                setAttribute(ExifInterface.TAG_USER_COMMENT,
                    "tomada_en=$tomadaEn; autor=$autor")
                saveAttributes()
            }
        } catch (_: Exception) {
            // Si el EXIF falla, los metadatos siguen en el GeoPackage: la
            // evidencia no se pierde por un formato de archivo.
        }
    }

    private fun escribirExifSinUbicacion(
        f: File, tomadaEn: String, descripcion: String
    ) {
        try {
            ExifInterface(f.absolutePath).apply {
                setAttribute(ExifInterface.TAG_DATETIME_ORIGINAL, exifFecha())
                setAttribute(ExifInterface.TAG_IMAGE_DESCRIPTION,
                    descripcion.ifBlank { "PTNT-BAL levantamiento de campo" })
                setAttribute(ExifInterface.TAG_ARTIST, autor)
                setAttribute(ExifInterface.TAG_USER_COMMENT,
                    "tomada_en=$tomadaEn; autor=$autor; sin_ubicacion=1")
                saveAttributes()
            }
        } catch (_: Exception) { }
    }

    private fun exifFecha(): String =
        SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US)
            .format(java.util.Date())

    private fun exifFechaGps(): String =
        SimpleDateFormat("yyyy:MM:dd", Locale.US).format(java.util.Date())

    private fun racional(v: Double): String {
        val n = (v * 1000).toLong()
        return "$n/1000"
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { entrada ->
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = entrada.read(buf)
                if (n <= 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
