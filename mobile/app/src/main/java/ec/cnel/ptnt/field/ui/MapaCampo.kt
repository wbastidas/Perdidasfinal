package ec.cnel.ptnt.field.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import ec.cnel.ptnt.field.data.Elemento
import ec.cnel.ptnt.field.data.RepositorioCampo
import org.maplibre.android.MapLibre
import org.maplibre.android.camera.CameraPosition
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.geometry.LatLng
import org.maplibre.android.maps.MapLibreMap
import org.maplibre.android.maps.MapView
import org.maplibre.android.maps.Style
import org.maplibre.android.style.layers.CircleLayer
import org.maplibre.android.style.layers.LineLayer
import org.maplibre.android.style.layers.PropertyFactory
import org.maplibre.android.style.layers.RasterLayer
import org.maplibre.android.style.sources.GeoJsonSource
import org.maplibre.android.style.sources.RasterSource
import org.maplibre.android.style.sources.TileSet
import org.maplibre.geojson.Feature
import org.maplibre.geojson.FeatureCollection
import org.maplibre.geojson.LineString
import org.maplibre.geojson.Point

/**
 * El mapa de campo, sobre MapLibre GL Native.
 *
 * Por qué MapLibre y no una vista propia con Canvas: el render va por GPU. En un
 * teléfono de gama baja, dibujar mil geometrías por cuadro en el hilo principal
 * da ~8 fps y la app se siente rota; con teselas vectorizadas y capas nativas se
 * mantiene fluida mientras el técnico camina.
 *
 * Tres decisiones sobre el rendimiento, que aquí no es un lujo sino la diferencia
 * entre una herramienta que se usa y una que se abandona:
 *
 * 1. **Se consulta por ventana, no la capa entera.** Cada movimiento de cámara
 *    pide al GeoPackage solo lo visible, apoyado en el R*Tree. Cargar los 30 000
 *    elementos del paquete de una vez agota la memoria del equipo.
 *
 * 2. **Umbral de zoom para la red de detalle.** Por debajo de z15 los clientes no
 *    se dibujan: a esa escala son un borrón que no aporta y sí cuesta batería.
 *
 * 3. **Fuentes GeoJSON reutilizadas.** Se actualiza el contenido de la fuente en
 *    lugar de recrear capas; recrearlas provoca un parpadeo en cada desplazamiento.
 */

private const val ZOOM_DETALLE = 15.0
private const val LIMITE_POR_CAPA = 2000

// Orden de dibujo: primero lo lineal, encima lo puntual. Si los tramos se
// dibujaran al final taparían los clientes, que es justo lo que se toca.
private val CAPAS_LINEA = listOf("ptnt_tramo")
private val CAPAS_PUNTO = listOf(
    "ptnt_poste", "ptnt_seccionador", "ptnt_capacitor", "ptnt_luminaria",
    "ptnt_puesto_transformacion", "ptnt_cliente"
)

@Composable
fun MapaCampo(
    modifier: Modifier,
    repo: RepositorioCampo,
    seleccionado: Elemento?,
    enModoMover: Boolean,
    onTocarMapa: (lat: Double, lon: Double) -> Unit,
    onSeleccionar: (Elemento?) -> Unit,
    centrarEn: Pair<Double, Double>? = null,
) {
    val contexto = LocalContext.current
    val ciclo = LocalLifecycleOwner.current.lifecycle

    // MapLibre exige inicialización antes de inflar la vista. Sin clave de API:
    // las teselas salen del propio paquete, servidas en loopback.
    remember { MapLibre.getInstance(contexto) }

    val vista = remember { MapView(contexto) }
    val estado = remember { EstadoMapa() }

    DisposableEffect(ciclo) {
        val obs = LifecycleEventObserver { _, evento ->
            when (evento) {
                Lifecycle.Event.ON_START -> vista.onStart()
                Lifecycle.Event.ON_RESUME -> vista.onResume()
                Lifecycle.Event.ON_PAUSE -> vista.onPause()
                Lifecycle.Event.ON_STOP -> vista.onStop()
                Lifecycle.Event.ON_DESTROY -> vista.onDestroy()
                else -> Unit
            }
        }
        ciclo.addObserver(obs)
        vista.onCreate(null)
        onDispose {
            ciclo.removeObserver(obs)
            vista.onDestroy()
        }
    }

    LaunchedEffect(seleccionado?.guid, enModoMover) {
        estado.guidSeleccionado = seleccionado?.guid
        estado.refrescar(repo)
    }

    LaunchedEffect(centrarEn) {
        centrarEn?.let { (lat, lon) ->
            estado.mapa?.animateCamera(
                CameraUpdateFactory.newCameraPosition(
                    CameraPosition.Builder()
                        .target(LatLng(lat, lon)).zoom(17.5).build()))
        }
    }

    // El modo de movimiento se propaga al estado del mapa por efecto secundario:
    // escribirlo en el cuerpo del composable lo haría durante la composición, que
    // es justo lo que Compose no garantiza que ocurra una sola vez.
    SideEffect { estado.enModoMover = enModoMover }

    AndroidView(modifier = modifier, factory = { vista }) { mv ->
        if (estado.mapa != null) return@AndroidView
        mv.getMapAsync { mapa ->
            estado.mapa = mapa
            mapa.setStyle(estiloBase(repo)) { estilo ->
                estado.estilo = estilo
                prepararCapas(estilo)
                estado.encuadrar(repo, mapa)
                estado.refrescar(repo)
            }
            mapa.addOnCameraIdleListener { estado.refrescar(repo) }
            mapa.addOnMapClickListener { punto ->
                if (estado.enModoMover) {
                    onTocarMapa(punto.latitude, punto.longitude)
                } else {
                    onSeleccionar(estado.masCercano(repo, punto.latitude, punto.longitude))
                }
                true
            }
        }
    }

}

/**
 * Estilo base del mapa.
 *
 * Si el paquete trae cartografía, se declara como fuente ráster apuntando al
 * servidor local. Si no la trae, se usa un fondo liso y se dibuja la red encima:
 * es peor que tener calles, pero mucho mejor que un mapa vacío que no arranca.
 */
private fun estiloBase(repo: RepositorioCampo): Style.Builder {
    val fondo = """
        {"version":8,"name":"PTNT Campo","sources":{},
         "layers":[{"id":"fondo","type":"background",
                    "paint":{"background-color":"#EAEDF2"}}]}
    """.trimIndent()
    val builder = Style.Builder().fromJson(fondo)

    val servidor = repo.servidorTeselas
    if (servidor != null && servidor.tieneCartografia) {
        val conjunto = TileSet("2.1.0", servidor.plantillaUrl()).apply {
            minZoom = servidor.zoomMin.toFloat()
            maxZoom = servidor.zoomMax.toFloat()
        }
        builder.withSource(RasterSource("cartografia", conjunto, 256))
        builder.withLayer(RasterLayer("cartografia", "cartografia"))
    }
    return builder
}

private fun prepararCapas(estilo: Style) {
    for (capa in CAPAS_LINEA) {
        estilo.addSource(GeoJsonSource(capa, FeatureCollection.fromFeatures(emptyList())))
        estilo.addLayer(
            LineLayer("l_$capa", capa).withProperties(
                PropertyFactory.lineColor("#4B5563"),
                PropertyFactory.lineWidth(2.2f),
                PropertyFactory.lineOpacity(0.9f)
            )
        )
    }
    // Resalte del elemento seleccionado, en su propia capa por encima de todo:
    // pintar el resalte dentro de la capa de datos obligaría a reconstruir toda
    // la colección en cada toque.
    estilo.addSource(GeoJsonSource("seleccion", FeatureCollection.fromFeatures(emptyList())))

    for (capa in CAPAS_PUNTO) {
        estilo.addSource(GeoJsonSource(capa, FeatureCollection.fromFeatures(emptyList())))
        estilo.addLayer(
            CircleLayer("c_$capa", capa).withProperties(
                PropertyFactory.circleColor(colorDeCapa(capa)),
                PropertyFactory.circleRadius(radioDeCapa(capa)),
                PropertyFactory.circleStrokeColor("#FFFFFF"),
                PropertyFactory.circleStrokeWidth(1.2f)
            )
        )
    }

    estilo.addLayer(
        CircleLayer("c_seleccion", "seleccion").withProperties(
            PropertyFactory.circleColor("#00000000"),
            PropertyFactory.circleRadius(14f),
            PropertyFactory.circleStrokeColor("#E8A317"),
            PropertyFactory.circleStrokeWidth(3.5f)
        )
    )
}

private fun colorDeCapa(capa: String): String = when (capa) {
    "ptnt_cliente" -> "#0B5FA5"
    "ptnt_puesto_transformacion" -> "#D93025"
    "ptnt_luminaria" -> "#E8A317"
    "ptnt_seccionador" -> "#7B1FA2"
    "ptnt_capacitor" -> "#00796B"
    else -> "#6B7280"
}

private fun radioDeCapa(capa: String): Float = when (capa) {
    "ptnt_puesto_transformacion" -> 9f
    "ptnt_cliente" -> 6f
    else -> 4.5f
}

/**
 * Estado vivo del mapa.
 *
 * Vive fuera de Compose a propósito: se actualiza en cada cuadro de cámara y
 * recomponer la pantalla a esa frecuencia haría inútil todo lo demás.
 */
private class EstadoMapa {
    var mapa: MapLibreMap? = null
    var estilo: Style? = null
    var guidSeleccionado: String? = null
    var enModoMover: Boolean = false

    private var ultimaVentana: DoubleArray? = null
    private val cache = mutableMapOf<String, List<Elemento>>()

    fun encuadrar(repo: RepositorioCampo, mapa: MapLibreMap) {
        // El manifiesto trae la envolvente del área de trabajo en coordenadas de
        // la red: se abre donde está el trabajo, no en medio del océano.
        val env = repo.manifiesto["envolvente"] ?: return
        val n = env.trim('[', ']').split(",").mapNotNull { it.trim().toDoubleOrNull() }
        if (n.size < 4) return
        val a = repo.aLatLon(n[0], n[1])
        val b = repo.aLatLon(n[2], n[3])
        mapa.moveCamera(
            CameraUpdateFactory.newCameraPosition(
                CameraPosition.Builder()
                    .target(LatLng((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
                    .zoom(16.0).build()))
    }

    fun refrescar(repo: RepositorioCampo) {
        val mapa = this.mapa ?: return
        val estilo = this.estilo ?: return
        val region = mapa.projection.visibleRegion.latLngBounds
        val zoom = mapa.cameraPosition.zoom

        val ventana = doubleArrayOf(
            region.latitudeSouth, region.longitudeWest,
            region.latitudeNorth, region.longitudeEast)
        // Si la vista no cambió lo suficiente, no se vuelve a consultar: el
        // listener de cámara dispara varias veces por gesto.
        if (!cambioSignificativo(ventana)) {
            pintarSeleccion(repo, estilo)
            return
        }
        ultimaVentana = ventana

        for (capa in CAPAS_LINEA) {
            val els = repo.enVentana(capa, ventana[0], ventana[1], ventana[2],
                ventana[3], LIMITE_POR_CAPA)
            cache[capa] = els
            (estilo.getSource(capa) as? GeoJsonSource)?.setGeoJson(
                FeatureCollection.fromFeatures(els.mapNotNull { aLinea(repo, it) }))
        }

        for (capa in CAPAS_PUNTO) {
            val detalle = capa == "ptnt_cliente" || capa == "ptnt_poste"
            val els = if (detalle && zoom < ZOOM_DETALLE) emptyList()
            else repo.enVentana(capa, ventana[0], ventana[1], ventana[2],
                ventana[3], LIMITE_POR_CAPA)
            cache[capa] = els
            (estilo.getSource(capa) as? GeoJsonSource)?.setGeoJson(
                FeatureCollection.fromFeatures(els.mapNotNull { aPunto(repo, it) }))
        }
        pintarSeleccion(repo, estilo)
    }

    private fun cambioSignificativo(v: DoubleArray): Boolean {
        val u = ultimaVentana ?: return true
        val alto = u[2] - u[0]
        val ancho = u[3] - u[1]
        return kotlin.math.abs(v[0] - u[0]) > alto * 0.2 ||
                kotlin.math.abs(v[1] - u[1]) > ancho * 0.2 ||
                kotlin.math.abs((v[2] - v[0]) - alto) > alto * 0.2
    }

    private fun pintarSeleccion(repo: RepositorioCampo, estilo: Style) {
        val guid = guidSeleccionado
        val fuente = estilo.getSource("seleccion") as? GeoJsonSource ?: return
        if (guid == null) {
            fuente.setGeoJson(FeatureCollection.fromFeatures(emptyList()))
            return
        }
        val e = cache.values.flatten().firstOrNull { it.guid == guid }
        val f = e?.let { aPunto(repo, it) ?: primerVertice(repo, it) }
        fuente.setGeoJson(
            FeatureCollection.fromFeatures(listOfNotNull(f)))
    }

    fun masCercano(repo: RepositorioCampo, lat: Double, lon: Double): Elemento? {
        var mejor: Elemento? = null
        var mejorD = 25.0                       // radio de toque, en metros
        // Se recorren primero las capas puntuales: si el dedo cae entre un tramo
        // y un cliente, el técnico quiere el cliente.
        for (capa in CAPAS_PUNTO + CAPAS_LINEA) {
            for (e in cache[capa].orEmpty()) {
                val g = e.geometria ?: continue
                for ((x, y) in g.coords) {
                    val ll = repo.aLatLon(x, y)
                    val d = ec.cnel.ptnt.field.geo.Utm.distanciaM(lat, lon, ll[0], ll[1])
                    if (d < mejorD) { mejorD = d; mejor = e }
                }
            }
            if (mejor != null && capa in CAPAS_PUNTO) return mejor
        }
        return mejor
    }

    private fun aPunto(repo: RepositorioCampo, e: Elemento): Feature? {
        val g = e.geometria ?: return null
        if (g.tipo != "Point") return null
        val ll = repo.aLatLon(g.coords[0].first, g.coords[0].second)
        return Feature.fromGeometry(Point.fromLngLat(ll[1], ll[0])).apply {
            addStringProperty("guid", e.guid)
            addStringProperty("capa", e.capa)
            addStringProperty("etiqueta", e.etiqueta)
        }
    }

    private fun primerVertice(repo: RepositorioCampo, e: Elemento): Feature? {
        val g = e.geometria ?: return null
        val ll = repo.aLatLon(g.coords[0].first, g.coords[0].second)
        return Feature.fromGeometry(Point.fromLngLat(ll[1], ll[0]))
    }

    private fun aLinea(repo: RepositorioCampo, e: Elemento): Feature? {
        val g = e.geometria ?: return null
        if (g.tipo != "LineString" || g.coords.size < 2) return null
        val pts = g.coords.map {
            val ll = repo.aLatLon(it.first, it.second)
            Point.fromLngLat(ll[1], ll[0])
        }
        return Feature.fromGeometry(LineString.fromLngLats(pts)).apply {
            addStringProperty("guid", e.guid)
            addStringProperty("capa", e.capa)
        }
    }
}
