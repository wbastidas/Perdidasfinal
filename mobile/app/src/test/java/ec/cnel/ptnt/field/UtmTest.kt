package ec.cnel.ptnt.field

import ec.cnel.ptnt.field.geo.Utm
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.random.Random

/**
 * La conversión de coordenadas es el punto donde una app de campo puede mentir
 * sin que se note: un error de proyección no rompe nada, solo deja los elementos
 * unos metros corridos. El técnico "corrige" posiciones que estaban bien y el SIG
 * queda peor que antes de la visita.
 */
class UtmTest {

    /** Guayaquil, valor de referencia calculado con la definición EPSG:32717. */
    @Test
    fun convierte_un_punto_conocido_de_la_zona_de_trabajo() {
        val utm = Utm.desdeLatLon(-2.170998, -79.922359, 32717)
        assertEquals(619836.06, utm[0], 0.05)
        assertEquals(9759995.64, utm[1], 0.05)
    }

    @Test
    fun la_ida_y_vuelta_no_mueve_el_elemento() {
        val rnd = Random(20260809)
        var peorM = 0.0
        repeat(5000) {
            val lat = -3.5 + rnd.nextDouble() * 3.0        // costa ecuatoriana
            val lon = -81.5 + rnd.nextDouble() * 3.0
            val utm = Utm.desdeLatLon(lat, lon, 32717)
            val ll = Utm.aLatLon(utm[0], utm[1], 32717)
            peorM = maxOf(peorM, Utm.distanciaM(lat, lon, ll[0], ll[1]))
        }
        // Un milímetro es tres órdenes de magnitud menos que el error del GPS de
        // un teléfono, que es lo que realmente limita el trabajo.
        assertTrue("error de ida y vuelta: $peorM m", peorM < 0.001)
    }

    @Test
    fun las_coordenadas_geograficas_pasan_sin_tocarse() {
        // Las fotos se guardan en 4326: reproyectarlas las mandaría al Atlántico.
        val r = Utm.aLatLon(-79.9, -2.17, 4326)
        assertEquals(-2.17, r[0], 1e-12)
        assertEquals(-79.9, r[1], 1e-12)
    }

    @Test
    fun la_envolvente_usa_las_cuatro_esquinas() {
        // La grilla UTM está rotada respecto de la geográfica: con solo dos
        // esquinas queda fuera una franja de elementos que sí están en pantalla.
        val e = Utm.envolventeUtm(-2.30, -80.00, -2.00, -79.70, 32717)
        val so = Utm.desdeLatLon(-2.30, -80.00, 32717)
        val ne = Utm.desdeLatLon(-2.00, -79.70, 32717)
        assertTrue(e[0] <= minOf(so[0], ne[0]) + 1e-6)
        assertTrue(e[1] <= minOf(so[1], ne[1]) + 1e-6)
        assertTrue(e[2] >= maxOf(so[0], ne[0]) - 1e-6)
        assertTrue(e[3] >= maxOf(so[1], ne[1]) - 1e-6)
        // Y la envolvente debe ser estrictamente mayor que la caja de dos esquinas.
        assertTrue(e[2] - e[0] > abs(ne[0] - so[0]) - 1e-6)
    }

    @Test
    fun reconoce_la_zona_y_el_hemisferio_del_srid() {
        assertEquals(17, Utm.zonaUtmDe(32717))
        assertEquals(17, Utm.zonaUtmDe(32617))
        assertTrue(Utm.esSur(32717))
        assertTrue(!Utm.esSur(32617))
        assertEquals(null, Utm.zonaUtmDe(4326))
        assertEquals(-81.0, Utm.meridianoCentral(17), 1e-9)
    }

    @Test
    fun la_distancia_en_metros_es_coherente_con_la_proyeccion() {
        // Dos puntos separados 100 m en UTM deben medir ~100 m en geográficas.
        val a = Utm.aLatLon(620000.0, 9760000.0, 32717)
        val b = Utm.aLatLon(620100.0, 9760000.0, 32717)
        val d = Utm.distanciaM(a[0], a[1], b[0], b[1])
        // El factor de escala de UTM (0.9996 en el meridiano central) explica la
        // diferencia; 0,5 m en 100 m es exactamente lo esperable.
        assertEquals(100.0, d, 0.5)
    }
}
