package ec.cnel.ptnt.field

import ec.cnel.ptnt.field.data.Geometria
import ec.cnel.ptnt.field.data.GpkgGeometria
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * El códec de geometría es el contrato con el backend: lo que el móvil escribe
 * tiene que ser exactamente lo que Python lee, byte a byte. Un error aquí no
 * produce un fallo visible —produce elementos desplazados o ilegibles después de
 * sincronizar, cuando el técnico ya no está en el sitio.
 */
class GeometriaTest {

    @Test
    fun un_punto_sobrevive_la_ida_y_vuelta() {
        val g = Geometria.punto(619836.06, 9759995.64, 32717)
        val leido = GpkgGeometria.leer(GpkgGeometria.escribir(g))
        assertNotNull(leido)
        assertEquals("Point", leido!!.tipo)
        assertEquals(32717, leido.srid)
        assertEquals(619836.06, leido.coords[0].first, 1e-9)
        assertEquals(9759995.64, leido.coords[0].second, 1e-9)
    }

    @Test
    fun una_linea_conserva_todos_sus_vertices_y_su_orden() {
        // El orden importa: un tramo invertido cambia nodo origen y destino, y
        // con ellos el sentido del flujo en el cálculo.
        val pts = listOf(
            620000.0 to 9760000.0, 620050.0 to 9760030.0, 620110.0 to 9760090.0
        )
        val leido = GpkgGeometria.leer(
            GpkgGeometria.escribir(Geometria.linea(pts, 32717)))
        assertNotNull(leido)
        assertEquals("LineString", leido!!.tipo)
        assertEquals(pts.size, leido.coords.size)
        pts.forEachIndexed { i, (x, y) ->
            assertEquals(x, leido.coords[i].first, 1e-9)
            assertEquals(y, leido.coords[i].second, 1e-9)
        }
    }

    @Test
    fun el_binario_lleva_la_firma_y_la_envolvente_que_espera_el_estandar() {
        val b = GpkgGeometria.escribir(
            Geometria.linea(listOf(1.0 to 2.0, 5.0 to 9.0), 32717))
        assertEquals('G'.code.toByte(), b[0])
        assertEquals('P'.code.toByte(), b[1])
        // bit 0: little endian; bits 1-3: indicador de envolvente XY (=1)
        assertEquals(0b0000_0011, b[3].toInt())
        // La envolvente permite al móvil descartar lo que está fuera de la vista
        // sin decodificar el WKB completo, que es lo que sostiene el render.
        assertTrue(b.size > 8 + 32)
    }

    @Test
    fun un_blob_que_no_es_geopackage_no_revienta() {
        // El paquete puede traer datos de un origen imperfecto; una geometría
        // corrupta debe dejar el elemento sin dibujar, no cerrar la aplicación.
        assertNull(GpkgGeometria.leer(null))
        assertNull(GpkgGeometria.leer(ByteArray(3)))
        assertNull(GpkgGeometria.leer("no soy una geometria".toByteArray()))
    }

    @Test
    fun la_envolvente_de_la_geometria_es_la_caja_real() {
        val g = Geometria.linea(
            listOf(10.0 to 40.0, 30.0 to 5.0, 20.0 to 25.0), 32717)
        val e = g.envolvente()
        assertEquals(10.0, e[0], 1e-9)
        assertEquals(5.0, e[1], 1e-9)
        assertEquals(30.0, e[2], 1e-9)
        assertEquals(40.0, e[3], 1e-9)
    }

    @Test
    fun el_wkt_es_el_que_lee_el_diario_de_cambios() {
        // El diario guarda geom_antes/geom_despues como WKT: es lo que el
        // supervisor ve al revisar y lo que Python vuelve a interpretar.
        assertEquals("POINT(620000.0 9760000.0)",
            Geometria.punto(620000.0, 9760000.0).wkt())
        assertEquals("LINESTRING(1.0 2.0, 3.0 4.0)",
            Geometria.linea(listOf(1.0 to 2.0, 3.0 to 4.0)).wkt())
    }
}
