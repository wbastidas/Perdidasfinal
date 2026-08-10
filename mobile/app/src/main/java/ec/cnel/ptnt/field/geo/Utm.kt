package ec.cnel.ptnt.field.geo

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tan

/**
 * Conversión UTM ⇄ WGS84.
 *
 * Hace falta porque las dos mitades del sistema hablan distinto y ninguna puede
 * ceder: la red de la distribuidora está en **UTM 17S** —así se levantó, así se
 * calculan las longitudes de tramo en metros y así vuelve al SIG— mientras que
 * MapLibre, como cualquier motor de teselas web, dibuja en **latitud/longitud**.
 * Convertir en el servidor no serviría: en campo se edita sin señal, y cada
 * arrastre de dedo produce una coordenada nueva que hay que llevar a UTM antes
 * de guardarla.
 *
 * Se implementa a mano en vez de traer proj4j (~1 MB con sus tablas): el proyecto
 * necesita **una** zona y una elipsoide. En una app de campo cada MB del APK es
 * tiempo de descarga en una conexión mala.
 *
 * Precisión: serie clásica de Snyder truncada en sexto orden, con error
 * submilimétrico dentro de la zona — tres órdenes de magnitud por debajo del
 * error del GPS de un teléfono, que es lo que realmente limita el trabajo.
 */
object Utm {

    // WGS84
    private const val A = 6_378_137.0
    private const val F = 1.0 / 298.257_223_563
    private const val K0 = 0.9996
    private const val FALSO_ESTE = 500_000.0
    private const val FALSO_NORTE_SUR = 10_000_000.0

    private const val E2 = F * (2 - F)                 // excentricidad²
    private const val EP2 = E2 / (1 - E2)              // excentricidad² prima

    /** Meridiano central de una zona UTM, en grados. */
    fun meridianoCentral(zona: Int): Double = (zona - 1) * 6.0 - 180.0 + 3.0

    /** Zona UTM que corresponde a una longitud. */
    fun zonaDe(lonGrados: Double): Int =
        ((lonGrados + 180.0) / 6.0).toInt() + 1

    /**
     * De UTM a latitud/longitud.
     *
     * [srid] acepta los EPSG de UTM WGS84 (326xx norte, 327xx sur); cualquier
     * otro se trata como 4326 y se devuelve sin tocar, porque el paquete puede
     * traer capas ya en geográficas —las fotos, por ejemplo, se guardan en 4326.
     */
    fun aLatLon(x: Double, y: Double, srid: Int): DoubleArray {
        if (srid == 4326) return doubleArrayOf(y, x)          // ya viene lat/lon
        val zona = zonaUtmDe(srid) ?: return doubleArrayOf(y, x)
        val sur = esSur(srid)
        return utmALatLon(x, y, zona, sur)
    }

    /** De latitud/longitud a UTM en el SRID indicado. */
    fun desdeLatLon(lat: Double, lon: Double, srid: Int): DoubleArray {
        if (srid == 4326) return doubleArrayOf(lon, lat)
        val zona = zonaUtmDe(srid) ?: return doubleArrayOf(lon, lat)
        return latLonAUtm(lat, lon, zona, esSur(srid))
    }

    fun zonaUtmDe(srid: Int): Int? = when (srid) {
        in 32601..32660 -> srid - 32600
        in 32701..32760 -> srid - 32700
        else -> null
    }

    fun esSur(srid: Int): Boolean = srid in 32701..32760

    // ------------------------------------------------------------------ núcleo
    fun latLonAUtm(latGrados: Double, lonGrados: Double, zona: Int,
                   sur: Boolean): DoubleArray {
        val lat = Math.toRadians(latGrados)
        val lon = Math.toRadians(lonGrados)
        val lon0 = Math.toRadians(meridianoCentral(zona))

        val sinLat = sin(lat)
        val cosLat = cos(lat)
        val n = A / sqrt(1 - E2 * sinLat * sinLat)
        val t = tan(lat).pow(2)
        val c = EP2 * cosLat * cosLat
        val a1 = (lon - lon0) * cosLat

        val m = A * ((1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2.pow(3) / 256) * lat -
                (3 * E2 / 8 + 3 * E2 * E2 / 32 + 45 * E2.pow(3) / 1024) * sin(2 * lat) +
                (15 * E2 * E2 / 256 + 45 * E2.pow(3) / 1024) * sin(4 * lat) -
                (35 * E2.pow(3) / 3072) * sin(6 * lat))

        val este = FALSO_ESTE + K0 * n * (a1 +
                (1 - t + c) * a1.pow(3) / 6 +
                (5 - 18 * t + t * t + 72 * c - 58 * EP2) * a1.pow(5) / 120)

        var norte = K0 * (m + n * tan(lat) * (a1 * a1 / 2 +
                (5 - t + 9 * c + 4 * c * c) * a1.pow(4) / 24 +
                (61 - 58 * t + t * t + 600 * c - 330 * EP2) * a1.pow(6) / 720))
        if (sur) norte += FALSO_NORTE_SUR

        return doubleArrayOf(este, norte)
    }

    fun utmALatLon(este: Double, norte: Double, zona: Int,
                   sur: Boolean): DoubleArray {
        val y = if (sur) norte - FALSO_NORTE_SUR else norte
        val x = este - FALSO_ESTE

        val m = y / K0
        val mu = m / (A * (1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2.pow(3) / 256))
        val e1 = (1 - sqrt(1 - E2)) / (1 + sqrt(1 - E2))

        val phi1 = mu +
                (3 * e1 / 2 - 27 * e1.pow(3) / 32) * sin(2 * mu) +
                (21 * e1 * e1 / 16 - 55 * e1.pow(4) / 32) * sin(4 * mu) +
                (151 * e1.pow(3) / 96) * sin(6 * mu) +
                (1097 * e1.pow(4) / 512) * sin(8 * mu)

        val sinP = sin(phi1)
        val cosP = cos(phi1)
        val c1 = EP2 * cosP * cosP
        val t1 = tan(phi1).pow(2)
        val n1 = A / sqrt(1 - E2 * sinP * sinP)
        val r1 = A * (1 - E2) / (1 - E2 * sinP * sinP).pow(1.5)
        val d = x / (n1 * K0)

        val lat = phi1 - (n1 * tan(phi1) / r1) * (d * d / 2 -
                (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * EP2) * d.pow(4) / 24 +
                (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * EP2 - 3 * c1 * c1) *
                d.pow(6) / 720)

        val lon = Math.toRadians(meridianoCentral(zona)) + (d -
                (1 + 2 * t1 + c1) * d.pow(3) / 6 +
                (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * EP2 + 24 * t1 * t1) *
                d.pow(5) / 120) / cosP

        return doubleArrayOf(Math.toDegrees(lat), Math.toDegrees(lon))
    }

    /**
     * Distancia en metros entre dos puntos geográficos (fórmula del haversine).
     *
     * Se usa solo para distancias cortas —cuánto se movió el dedo, a qué poste
     * hacer snap—, donde la esfera y el elipsoide difieren menos que el GPS.
     */
    fun distanciaM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = 6_371_008.8
        val dLat = Math.toRadians(lat2 - lat1)
        val dLon = Math.toRadians(lon2 - lon1)
        val a = sin(dLat / 2).pow(2) +
                cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) *
                sin(dLon / 2).pow(2)
        return 2 * r * Math.asin(minOf(1.0, sqrt(a)))
    }

    /**
     * Envolvente UTM que cubre una ventana geográfica.
     *
     * Se convierten las cuatro esquinas y no solo dos: la grilla UTM está rotada
     * respecto de la geográfica, así que tomar min/max de dos esquinas deja fuera
     * una franja del mapa — y los elementos de esa franja no se dibujarían.
     */
    fun envolventeUtm(
        latMin: Double, lonMin: Double, latMax: Double, lonMax: Double, srid: Int
    ): DoubleArray {
        val esquinas = listOf(
            desdeLatLon(latMin, lonMin, srid), desdeLatLon(latMin, lonMax, srid),
            desdeLatLon(latMax, lonMin, srid), desdeLatLon(latMax, lonMax, srid)
        )
        val xs = esquinas.map { it[0] }
        val ys = esquinas.map { it[1] }
        return doubleArrayOf(xs.min(), ys.min(), xs.max(), ys.max())
    }

    /** ¿Están estas coordenadas dentro del rango razonable de una zona UTM? */
    fun pareceUtm(x: Double, y: Double): Boolean =
        x in 100_000.0..900_000.0 && abs(y) <= 10_000_000.0
}
