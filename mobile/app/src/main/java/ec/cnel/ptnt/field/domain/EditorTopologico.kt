package ec.cnel.ptnt.field.domain

import ec.cnel.ptnt.field.data.CambioCampo
import ec.cnel.ptnt.field.data.Elemento
import ec.cnel.ptnt.field.data.GeoPackageDao
import ec.cnel.ptnt.field.data.Geometria
import ec.cnel.ptnt.field.data.Operacion
import ec.cnel.ptnt.field.data.TipoRelacion
import java.util.UUID
import kotlin.math.hypot

/**
 * Edición topológica en el dispositivo: snap y propagación.
 *
 * Espeja la lógica del backend (`ptnt.field.topology_edit`) **a propósito**: la
 * edición ocurre sin señal, así que la app no puede consultar al servidor qué
 * arrastrar. Ambas implementaciones deben coincidir, y por eso las reglas están
 * escritas en un solo lugar conceptual —el tipo de relación— y no repartidas en
 * condicionales.
 *
 * Las cuatro reglas:
 *
 * 1. `PERTENECE_A` — mover un puesto mueve sus unidades: no tienen posición propia.
 * 2. `ACOMETIDA` — mover un cliente arrastra **el extremo** de su acometida; el
 *    otro sigue anclado al poste.
 * 3. `COMPARTE_VERTICE` — dos tramos que comparten vértice lo siguen compartiendo.
 * 4. `ALIMENTA` — la relación eléctrica **no** propaga geometría. Un transformador
 *    alimenta a cien clientes: moverlo no debe mover el barrio.
 *
 * La cuarta es la que más caro sale equivocar. Sin ella, un arrastre de dedo
 * reescribe la geometría del alimentador entero y el técnico no tiene forma de
 * deshacerlo en campo.
 */
class EditorTopologico(
    private val dao: GeoPackageDao,
    private val autor: String,
    private val ordenTrabajo: String
) {

    /** Tolerancia del snap, del orden del error del GPS de un teléfono. Más
     *  estricta rechazaría ajustes válidos; más laxa pegaría el elemento al
     *  poste equivocado en una vereda angosta. */
    var toleranciaSnapM: Double = 3.0

    /** Tope de seguridad de la propagación: si una relación viniera mal
     *  clasificada, sin tope un arrastre podría reescribir miles de elementos. */
    var maxPropagacion: Int = 200

    var ubicacionDispositivo: Ubicacion? = null

    data class Ubicacion(val lat: Double, val lon: Double, val precisionM: Double)

    data class ResultadoMovimiento(
        val movidos: List<ElementoMovido>,
        val advertencias: List<String>
    ) {
        val propagados: Int get() = movidos.count { it.propagadoDe != null }
    }

    data class ElementoMovido(
        val elemento: Elemento,
        val geometriaAntes: Geometria,
        val geometriaDespues: Geometria,
        val propagadoDe: String?,
        val motivo: String
    )

    // ------------------------------------------------------------------ snap
    /**
     * Ajusta una coordenada al vértice más cercano dentro de la tolerancia.
     *
     * Se aplica **al capturar**, para que un elemento nuevo quede exactamente
     * sobre el poste y no a 40 cm. Devuelve la coordenada ajustada y a qué
     * elemento se pegó, para que la interfaz lo destaque y el técnico vea que el
     * snap ocurrió.
     */
    fun snap(
        x: Double, y: Double,
        candidatos: List<Elemento>,
        excluirGuid: String? = null
    ): Triple<Double, Double, String?> {
        var mejorX = x; var mejorY = y
        var mejorD = toleranciaSnapM
        var mejorGuid: String? = null
        for (e in candidatos) {
            if (e.guid == excluirGuid) continue
            val g = e.geometria ?: continue
            for ((cx, cy) in g.coords) {
                val d = hypot(cx - x, cy - y)
                if (d < mejorD) { mejorD = d; mejorX = cx; mejorY = cy; mejorGuid = e.guid }
            }
        }
        return Triple(mejorX, mejorY, mejorGuid)
    }

    // ------------------------------------------------------------- movimiento
    /**
     * Mueve un elemento y arrastra lo que topológicamente debe seguirlo,
     * registrando **cada** desplazamiento en el diario.
     *
     * Los cambios propagados se anotan con `propagadoDe`, para que en la revisión
     * el supervisor distinga lo que el técnico movió a propósito de lo que se
     * movió por consecuencia — y pueda aceptar o rechazar coherentemente.
     */
    fun mover(
        elemento: Elemento,
        nuevaX: Double, nuevaY: Double,
        indiceVertice: Int = 0,
        motivo: String = ""
    ): ResultadoMovimiento {
        val geom = elemento.geometria
            ?: return ResultadoMovimiento(emptyList(),
                listOf("El elemento no tiene geometría."))

        val idx = indiceVertice.coerceIn(0, geom.coords.size - 1)
        val origen = geom.coords[idx]
        val dx = nuevaX - origen.first
        val dy = nuevaY - origen.second
        if (hypot(dx, dy) < 1e-9) return ResultadoMovimiento(emptyList(), emptyList())

        val movidos = mutableListOf<ElementoMovido>()
        val advertencias = mutableListOf<String>()

        val nuevasCoords = geom.coords.toMutableList()
        if (geom.tipo == "Point") nuevasCoords[0] = nuevaX to nuevaY
        else nuevasCoords[idx] = nuevaX to nuevaY
        val geomNueva = geom.copy(coords = nuevasCoords)

        aplicar(elemento, geom, geomNueva, null, motivo.ifBlank { "Movimiento directo" })
        movidos += ElementoMovido(elemento, geom, geomNueva, null,
            motivo.ifBlank { "Movimiento directo" })

        propagar(elemento.guid, origen, dx, dy, movidos, advertencias)

        if (movidos.size >= maxPropagacion) {
            advertencias += "La propagación alcanzó el tope de $maxPropagacion " +
                    "elementos. Revise la clasificación de relaciones."
        }
        return ResultadoMovimiento(movidos, advertencias)
    }

    private fun propagar(
        guidRaiz: String,
        posAntes: Pair<Double, Double>,
        dx: Double, dy: Double,
        movidos: MutableList<ElementoMovido>,
        advertencias: MutableList<String>
    ) {
        val visitados = mutableSetOf(guidRaiz)
        val cola = ArrayDeque<Pair<String, Pair<Double, Double>>>()
        cola += guidRaiz to posAntes

        while (cola.isNotEmpty() && movidos.size < maxPropagacion) {
            val (actual, pAntes) = cola.removeFirst()
            for (rel in dao.relacionados(actual)) {
                if (rel.guid in visitados || !rel.tipo.propagaGeometria) continue
                val e = dao.porGuid(rel.capa, rel.guid) ?: continue
                val g = e.geometria ?: continue

                val nuevas: List<Pair<Double, Double>>
                val motivo: String
                if (rel.tipo == TipoRelacion.PERTENECE_A) {
                    // El hijo no tiene posición propia: se mueve entero.
                    nuevas = g.coords.map { it.first + dx to it.second + dy }
                    motivo = "Pertenece al elemento movido"
                    visitados += rel.guid
                    cola += rel.guid to pAntes
                } else {
                    // Solo el vértice que coincidía con la posición anterior.
                    var tocado = false
                    nuevas = g.coords.map { (x, y) ->
                        if (hypot(x - pAntes.first, y - pAntes.second) < 0.5) {
                            tocado = true; x + dx to y + dy
                        } else x to y
                    }
                    if (!tocado) continue
                    motivo = if (rel.tipo == TipoRelacion.ACOMETIDA)
                        "Extremo de acometida" else "Vértice compartido"
                    visitados += rel.guid
                }

                val gNueva = g.copy(coords = nuevas)
                aplicar(e, g, gNueva, actual, motivo)
                movidos += ElementoMovido(e, g, gNueva, actual, motivo)
            }
        }
    }

    private fun aplicar(
        e: Elemento, antes: Geometria, despues: Geometria,
        propagadoDe: String?, motivo: String
    ) {
        dao.guardar(e.capa, e.fid, mapOf(
            "editado_por" to autor,
            "editado_en" to ahora(),
            "origen_edicion" to "MOVIL"
        ), despues)
        dao.registrarCambio(CambioCampo(
            guid = UUID.randomUUID().toString(),
            capa = e.capa, elementoGuid = e.guid,
            operacion = Operacion.MOVER,
            geomAntes = antes.wkt(), geomDespues = despues.wkt(),
            ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
            latDispositivo = ubicacionDispositivo?.lat,
            lonDispositivo = ubicacionDispositivo?.lon,
            precisionM = ubicacionDispositivo?.precisionM,
            motivo = motivo, propagadoDe = propagadoDe ?: ""
        ))
    }

    // -------------------------------------------------------------- atributos
    /**
     * Edita atributos, registrando **un cambio por campo**.
     *
     * Granular a propósito: en la revisión, el supervisor puede aceptar la
     * lectura del medidor y rechazar el cambio de tarifa del mismo cliente. Un
     * único cambio "se editó el cliente" obligaría a decidir por todo junto.
     */
    fun editarAtributos(
        elemento: Elemento,
        nuevos: Map<String, Any?>,
        motivo: String = ""
    ): Int {
        val cambiados = nuevos.filter { (k, v) ->
            elemento.atributos[k]?.toString() != v?.toString()
        }
        if (cambiados.isEmpty()) return 0

        dao.guardar(elemento.capa, elemento.fid, cambiados + mapOf(
            "editado_por" to autor,
            "editado_en" to ahora(),
            "origen_edicion" to "MOVIL"
        ))
        cambiados.forEach { (campo, valor) ->
            dao.registrarCambio(CambioCampo(
                guid = UUID.randomUUID().toString(),
                capa = elemento.capa, elementoGuid = elemento.guid,
                operacion = Operacion.MODIFICAR, campo = campo,
                valorAntes = elemento.atributos[campo]?.toString(),
                valorDespues = valor?.toString(),
                ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
                latDispositivo = ubicacionDispositivo?.lat,
                lonDispositivo = ubicacionDispositivo?.lon,
                precisionM = ubicacionDispositivo?.precisionM,
                motivo = motivo
            ))
        }
        return cambiados.size
    }

    // ------------------------------------------------------------ reconexión
    /**
     * Cambia de qué transformador cuelga un consumidor (o una luminaria).
     *
     * Es la corrección que más vale de todo el trabajo de campo, y la única que
     * altera **dos** balances con una sola edición: la zona que pierde el
     * cliente y la que lo gana. Un cliente colgado del transformador equivocado
     * produce PNT falsa en ambas —una pierde energía que no consumió, la otra la
     * gana— y ninguna cantidad de análisis desde la oficina lo detecta, porque
     * los dos balances son internamente coherentes.
     *
     * No mueve geometría: el cliente sigue donde está. Lo que cambia es el
     * grafo, así que se reescribe la fila de `ptnt_conexion` y se registra la
     * operación como `RECONECTAR` —no como `MODIFICAR`— para que el backend
     * sepa que tiene que rehacer la topología y las dos zonas.
     */
    fun reconectar(
        elemento: Elemento,
        nuevoPuestoGuid: String,
        nuevaUnidadGuid: String = "",
        motivo: String = ""
    ): Resultado {
        val anterior = elemento.atributos["puesto_guid"]?.toString().orEmpty()
        if (nuevoPuestoGuid.isBlank()) {
            return Resultado(false, "Debe elegir el transformador de destino.")
        }
        if (anterior == nuevoPuestoGuid) {
            return Resultado(false,
                "Ya estaba conectado a ese transformador: no hay nada que corregir.")
        }
        // El destino tiene que estar en el paquete. Reconectar a un guid que el
        // dispositivo no conoce produce un cambio que el backend no puede
        // aplicar, y el técnico se entera semanas después.
        val destino = dao.porGuid("ptnt_puesto_transformacion", nuevoPuestoGuid)
            ?: return Resultado(false,
                "El transformador de destino no está en el paquete descargado. " +
                        "Amplíe el área de trabajo o repórtelo al supervisor.")

        val atributos = mutableMapOf<String, Any?>(
            "puesto_guid" to nuevoPuestoGuid,
            "editado_por" to autor,
            "editado_en" to ahora(),
            "origen_edicion" to "MOVIL"
        )
        if (elemento.capa == "ptnt_cliente") {
            // La unidad puede quedar vacía a propósito: en un puesto simple no
            // hay de dónde elegir. Forzar un valor inventaría una fase.
            atributos["unidad_guid"] = nuevaUnidadGuid.ifBlank { null }
        }
        dao.guardar(elemento.capa, elemento.fid, atributos)
        dao.reemplazarConexion(elemento.guid, anterior, nuevoPuestoGuid,
            elemento.capa)

        dao.registrarCambio(CambioCampo(
            guid = UUID.randomUUID().toString(),
            capa = elemento.capa, elementoGuid = elemento.guid,
            operacion = Operacion.RECONECTAR,
            campo = "puesto_guid",
            valorAntes = anterior.ifBlank { null },
            valorDespues = nuevoPuestoGuid,
            ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
            latDispositivo = ubicacionDispositivo?.lat,
            lonDispositivo = ubicacionDispositivo?.lon,
            precisionM = ubicacionDispositivo?.precisionM,
            motivo = motivo.ifBlank { "Reconexión verificada en sitio" }
        ))
        if (elemento.capa == "ptnt_cliente" && nuevaUnidadGuid.isNotBlank()) {
            dao.registrarCambio(CambioCampo(
                guid = UUID.randomUUID().toString(),
                capa = elemento.capa, elementoGuid = elemento.guid,
                operacion = Operacion.MODIFICAR, campo = "unidad_guid",
                valorAntes = elemento.atributos["unidad_guid"]?.toString(),
                valorDespues = nuevaUnidadGuid,
                ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
                motivo = "Fase del banco verificada en sitio"
            ))
        }

        val codigo = destino.atributos["codigo"]?.toString() ?: nuevoPuestoGuid.take(8)
        return Resultado(true, "Reconectado a $codigo. Cambia el balance de la " +
                "zona anterior y de la nueva.")
    }

    data class Resultado(val ok: Boolean, val mensaje: String)

    /**
     * Transformadores candidatos para reconectar, ordenados por cercanía.
     *
     * Se listan los que están en el paquete: el técnico elige de una lista corta
     * en vez de teclear un identificador, que es la vía directa a reconectar al
     * transformador equivocado.
     */
    fun candidatosReconexion(
        elemento: Elemento, disponibles: List<Elemento>, maximo: Int = 8
    ): List<Elemento> {
        val g = elemento.geometria ?: return disponibles.take(maximo)
        val (x, y) = g.coords.first()
        return disponibles
            .filter { it.capa == "ptnt_puesto_transformacion" && it.guid != elemento.guid }
            .sortedBy { c ->
                c.geometria?.coords?.firstOrNull()?.let { (cx, cy) ->
                    hypot(cx - x, cy - y)
                } ?: Double.MAX_VALUE
            }
            .take(maximo)
    }

    // ----------------------------------------------------------------- altas
    fun crear(
        capa: String, atributos: Map<String, Any?>, geometria: Geometria?,
        motivo: String = ""
    ): String {
        val guid = UUID.randomUUID().toString()
        dao.insertar(capa, atributos + mapOf(
            "guid" to guid,
            "editado_por" to autor,
            "editado_en" to ahora(),
            "origen_edicion" to "MOVIL"
        ), geometria)
        dao.registrarCambio(CambioCampo(
            guid = UUID.randomUUID().toString(),
            capa = capa, elementoGuid = guid, operacion = Operacion.CREAR,
            geomDespues = geometria?.wkt(),
            ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
            latDispositivo = ubicacionDispositivo?.lat,
            lonDispositivo = ubicacionDispositivo?.lon,
            precisionM = ubicacionDispositivo?.precisionM,
            motivo = motivo.ifBlank { "Elemento nuevo capturado en campo" }
        ))
        return guid
    }

    // ------------------------------------------------------------ eliminación
    /**
     * Verifica si un elemento se puede eliminar sin dejar huérfanos.
     *
     * Eliminar un puesto con clientes colgando dejaría a esos clientes sin
     * transformador y el balance de la zona sin sentido. La app lo **impide** y
     * explica por qué, en vez de aceptarlo y que reviente en la sincronización —
     * cuando el técnico ya no está en el sitio para arreglarlo.
     */
    fun puedeEliminar(elemento: Elemento): Pair<Boolean, List<String>> {
        val dependientes = dao.relacionados(elemento.guid).filter {
            (it.tipo == TipoRelacion.PERTENECE_A || it.tipo == TipoRelacion.ALIMENTA) &&
                    it.capa in setOf("ptnt_cliente", "ptnt_luminaria",
                        "ptnt_unidad_transformacion")
        }
        if (dependientes.isEmpty()) return true to emptyList()
        val porCapa = dependientes.groupingBy { it.capa }.eachCount()
        val detalle = porCapa.entries.joinToString(", ") { "${it.value} de ${it.key}" }
        return false to listOf(
            "Tiene ${dependientes.size} elemento(s) dependiente(s) ($detalle). " +
                    "Reasígnelos o elimínelos primero."
        )
    }

    fun eliminar(elemento: Elemento, motivo: String): Pair<Boolean, List<String>> {
        val (ok, bloqueos) = puedeEliminar(elemento)
        if (!ok) return false to bloqueos
        dao.registrarCambio(CambioCampo(
            guid = UUID.randomUUID().toString(),
            capa = elemento.capa, elementoGuid = elemento.guid,
            operacion = Operacion.ELIMINAR,
            geomAntes = elemento.geometria?.wkt(),
            ordenTrabajo = ordenTrabajo, autor = autor, ocurridoEn = ahora(),
            latDispositivo = ubicacionDispositivo?.lat,
            lonDispositivo = ubicacionDispositivo?.lon,
            precisionM = ubicacionDispositivo?.precisionM,
            motivo = motivo
        ))
        dao.eliminar(elemento.capa, elemento.fid)
        return true to emptyList()
    }

    private fun ahora(): String = java.time.Instant.now().toString()
}
