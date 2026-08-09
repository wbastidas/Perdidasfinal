package ec.cnel.ptnt.field.data

import android.content.Context
import ec.cnel.ptnt.field.domain.CapturaFoto
import ec.cnel.ptnt.field.domain.EditorTopologico
import ec.cnel.ptnt.field.geo.ServidorTeselas
import ec.cnel.ptnt.field.geo.Utm
import ec.cnel.ptnt.field.sync.AlmacenSesionCifrado
import ec.cnel.ptnt.field.sync.ClienteSincronizacion
import java.io.File

/**
 * Punto único de acceso al paquete de trabajo.
 *
 * Concentra el ciclo de vida de todo lo que depende del GeoPackage: el DAO, el
 * editor topológico, el servidor de teselas y la captura de fotos. Están juntos
 * porque **caducan juntos**: al sincronizar y bajar un paquete nuevo, todos
 * apuntan a un archivo que ya no existe. Repartir esa responsabilidad entre las
 * pantallas es la receta para un cursor abierto sobre un archivo borrado, que en
 * Android se manifiesta como un cierre inesperado sin traza útil.
 *
 * El paquete vive en el almacenamiento **privado** de la app: no aparece en la
 * galería ni en el explorador de archivos, y se borra al desinstalar. Un
 * GeoPackage con la red y los datos de clientes de una zona no debería quedar
 * accesible a cualquier aplicación del teléfono.
 */
class RepositorioCampo(private val contexto: Context) {

    val sesion = AlmacenSesionCifrado(contexto)

    private val directorio: File
        get() = File(contexto.filesDir, "trabajo").apply { mkdirs() }

    val archivoPaquete: File get() = File(directorio, "trabajo.gpkg")
    val directorioFotos: File get() = File(contexto.filesDir, "fotos").apply { mkdirs() }

    var dao: GeoPackageDao? = null
        private set
    var esquema: EsquemaFormulario = EsquemaFormulario.desdeManifiesto(null)
        private set
    var servidorTeselas: ServidorTeselas? = null
        private set
    var manifiesto: Map<String, String> = emptyMap()
        private set

    /** SRID en el que está la red del paquete. Todo lo que se dibuja o se guarda
     *  pasa por aquí; asumirlo fijo rompería el día que una unidad de negocio
     *  entregue su red en otra zona UTM. */
    var sridRed: Int = 32717
        private set

    val hayPaquete: Boolean get() = archivoPaquete.exists() && dao != null

    val cliente: ClienteSincronizacion
        get() = ClienteSincronizacion(
            urlBase = sesion.servidor().ifBlank { "http://127.0.0.1:8090" },
            almacen = sesion
        )

    /**
     * Abre el paquete descargado. Idempotente: llamarlo dos veces no duplica
     * conexiones ni servidores.
     */
    fun abrir(): Boolean {
        if (dao != null) return true
        if (!archivoPaquete.exists()) return false
        return try {
            val d = GeoPackageDao(archivoPaquete)
            dao = d
            manifiesto = d.manifiesto()
            esquema = EsquemaFormulario.desdeManifiesto(manifiesto["esquema"])
            sridRed = manifiesto["srid"]?.toIntOrNull() ?: 32717
            servidorTeselas = ServidorTeselas(archivoPaquete).also { it.iniciar() }
            true
        } catch (_: Exception) {
            cerrar()
            false
        }
    }

    fun cerrar() {
        try { servidorTeselas?.detener() } catch (_: Exception) {}
        try { dao?.cerrar() } catch (_: Exception) {}
        servidorTeselas = null
        dao = null
    }

    fun editor(ordenTrabajo: String): EditorTopologico? {
        val d = dao ?: return null
        return EditorTopologico(d, sesion.usuario() ?: "desconocido", ordenTrabajo)
    }

    fun camara(): CapturaFoto? {
        val d = dao ?: return null
        return CapturaFoto(d, directorioFotos, sesion.usuario() ?: "desconocido")
    }

    // ------------------------------------------------------------- conversión
    /** Coordenada de la red → latitud/longitud, para dibujar. */
    fun aLatLon(x: Double, y: Double): DoubleArray = Utm.aLatLon(x, y, sridRed)

    /** Toque del dedo (lat/lon) → coordenada de la red, para guardar. */
    fun aRed(lat: Double, lon: Double): DoubleArray = Utm.desdeLatLon(lat, lon, sridRed)

    /**
     * Elementos de una capa visibles en la ventana geográfica actual.
     *
     * La ventana llega en grados porque así la reporta el mapa, y se convierte a
     * la proyección de la red por las cuatro esquinas: la grilla UTM está rotada
     * respecto de la geográfica y usar solo dos esquinas dejaría fuera una franja
     * de elementos que sí están en pantalla.
     */
    fun enVentana(
        capa: String, latMin: Double, lonMin: Double, latMax: Double,
        lonMax: Double, limite: Int = 3000
    ): List<Elemento> {
        val d = dao ?: return emptyList()
        val e = Utm.envolventeUtm(latMin, lonMin, latMax, lonMax, sridRed)
        return try {
            d.enVentana(capa, e[0], e[1], e[2], e[3], limite)
        } catch (_: Exception) {
            emptyList()      // capa sin geometría o sin índice: no es un error
        }
    }

    /**
     * Borra el paquete local. Solo debe llamarse **después** de que el backend
     * confirmó recepción: una jornada de ediciones no subidas no se recupera.
     */
    fun descartarPaquete() {
        cerrar()
        archivoPaquete.delete()
        File("${archivoPaquete.absolutePath}.descargando").delete()
    }

    val cambiosPendientes: Int get() = try { dao?.cambiosPendientes() ?: 0 }
        catch (_: Exception) { 0 }
}
