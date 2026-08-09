package ec.cnel.ptnt.field.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import ec.cnel.ptnt.field.data.CapaFormulario
import ec.cnel.ptnt.field.data.Elemento
import ec.cnel.ptnt.field.data.Geometria
import ec.cnel.ptnt.field.data.Relacion
import ec.cnel.ptnt.field.data.RepositorioCampo
import ec.cnel.ptnt.field.domain.EditorTopologico
import ec.cnel.ptnt.field.geo.ProveedorUbicacion
import ec.cnel.ptnt.field.sync.ClienteSincronizacion
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Estado de la jornada de campo.
 *
 * Un único ViewModel para toda la app, y no uno por pantalla, porque las
 * pantallas comparten el mismo objeto pesado: el paquete abierto. Con un
 * ViewModel por pantalla, pasar del mapa al formulario reabriría el GeoPackage o
 * exigiría un singleton por debajo, que es lo mismo con más ceremonia.
 *
 * Las operaciones de disco y red van a `Dispatchers.IO`. En un equipo de gama
 * baja, leer 3 000 elementos en el hilo principal es medio segundo de interfaz
 * congelada por cada desplazamiento del mapa.
 */
class CampoViewModel(app: Application) : AndroidViewModel(app) {

    val repo = RepositorioCampo(app)
    private val ubicacion = ProveedorUbicacion(app)

    // -------------------------------------------------------------- estado
    enum class Pantalla { VINCULAR, ORDENES, MAPA }

    data class Estado(
        val pantalla: Pantalla = Pantalla.VINCULAR,
        val ocupado: Boolean = false,
        val mensaje: String? = null,
        val error: String? = null,
        val progreso: Float? = null,
        val usuario: String = "",
        val nombre: String = "",
        val cambiosPendientes: Int = 0,
        val ordenes: List<Map<String, Any?>> = emptyList(),
        val ordenActiva: String = "",
        val sesionSinCifrar: Boolean = false
    )

    private val _estado = MutableStateFlow(Estado())
    val estado: StateFlow<Estado> = _estado.asStateFlow()

    private val _seleccionado = MutableStateFlow<Elemento?>(null)
    val seleccionado: StateFlow<Elemento?> = _seleccionado.asStateFlow()

    private val _relacionados = MutableStateFlow<List<Relacion>>(emptyList())
    val relacionados: StateFlow<List<Relacion>> = _relacionados.asStateFlow()

    private val _fotos = MutableStateFlow<List<Map<String, Any?>>>(emptyList())
    val fotos: StateFlow<List<Map<String, Any?>>> = _fotos.asStateFlow()

    private val _posicion = MutableStateFlow<ProveedorUbicacion.Posicion?>(null)
    val posicion: StateFlow<ProveedorUbicacion.Posicion?> = _posicion.asStateFlow()

    /** Modo de edición geométrica: mover el elemento seleccionado al siguiente
     *  toque. Se hace explícito —no arrastrando— porque en campo el dedo roza la
     *  pantalla constantemente y un arrastre accidental reescribe la red. */
    private val _moviendo = MutableStateFlow(false)
    val moviendo: StateFlow<Boolean> = _moviendo.asStateFlow()

    private val _ultimoMovimiento = MutableStateFlow<EditorTopologico.ResultadoMovimiento?>(null)
    val ultimoMovimiento: StateFlow<EditorTopologico.ResultadoMovimiento?> =
        _ultimoMovimiento.asStateFlow()

    init {
        arrancar()
        seguirUbicacion()
    }

    private fun arrancar() {
        val vinculado = repo.sesion.vinculado
        val abierto = if (vinculado) repo.abrir() else false
        _estado.value = _estado.value.copy(
            pantalla = when {
                !vinculado -> Pantalla.VINCULAR
                abierto -> Pantalla.ORDENES
                else -> Pantalla.ORDENES
            },
            usuario = repo.sesion.usuario() ?: "",
            nombre = repo.sesion.nombre(),
            sesionSinCifrar = !repo.sesion.cifrado,
            cambiosPendientes = repo.cambiosPendientes
        )
        if (abierto) recargarOrdenes()
    }

    private fun seguirUbicacion() {
        viewModelScope.launch {
            try {
                // La posición se guarda aquí y se adjunta a cada cambio en el
                // momento de editar: el diario tiene que decir con qué precisión
                // se trabajó, no solo dónde.
                ubicacion.flujo().collect { p -> _posicion.value = p }
            } catch (_: SecurityException) {
                // Sin permiso todavía: la pantalla lo pide y el flujo se reintenta
                // al volver a entrar.
            }
        }
    }

    // ---------------------------------------------------------- vinculación
    fun vincular(servidor: String, usuario: String, password: String,
                 dispositivoId: String) {
        viewModelScope.launch {
            _estado.value = _estado.value.copy(ocupado = true, error = null)
            repo.sesion.guardarServidor(servidor)
            when (val r = repo.cliente.vincular(usuario, password, dispositivoId)) {
                is ClienteSincronizacion.Resultado.Ok -> {
                    _estado.value = _estado.value.copy(
                        ocupado = false, pantalla = Pantalla.ORDENES,
                        usuario = repo.sesion.usuario() ?: "",
                        nombre = repo.sesion.nombre(),
                        mensaje = "Equipo vinculado. Descargue su trabajo."
                    )
                }
                is ClienteSincronizacion.Resultado.Error ->
                    _estado.value = _estado.value.copy(ocupado = false, error = r.mensaje)
            }
        }
    }

    fun desvincular() {
        repo.sesion.limpiar()
        _estado.value = _estado.value.copy(pantalla = Pantalla.VINCULAR,
            mensaje = "Sesión cerrada. El trabajo sin subir sigue guardado.")
    }

    // -------------------------------------------------------------- descarga
    fun descargarTrabajo() {
        viewModelScope.launch {
            // Descargar encima de cambios sin subir es la forma más rápida de
            // perder una jornada: el paquete nuevo no los trae.
            if (repo.cambiosPendientes > 0) {
                _estado.value = _estado.value.copy(
                    error = "Hay ${repo.cambiosPendientes} cambio(s) sin sincronizar. " +
                            "Suba el trabajo antes de descargar uno nuevo.")
                return@launch
            }
            _estado.value = _estado.value.copy(ocupado = true, error = null, progreso = 0f)
            repo.cerrar()
            val r = repo.cliente.descargarPaquete(repo.archivoPaquete) { leidos, total ->
                _estado.value = _estado.value.copy(
                    progreso = if (total > 0) leidos.toFloat() / total else null)
            }
            when (r) {
                is ClienteSincronizacion.Resultado.Ok -> {
                    val ok = withContext(Dispatchers.IO) { repo.abrir() }
                    recargarOrdenes()
                    _estado.value = _estado.value.copy(
                        ocupado = false, progreso = null,
                        mensaje = if (ok) "Trabajo descargado. Ya puede salir a campo."
                        else "El paquete se descargó pero no se pudo abrir.",
                        error = if (ok) null else "Paquete ilegible. Vuelva a descargar."
                    )
                }
                is ClienteSincronizacion.Resultado.Error ->
                    _estado.value = _estado.value.copy(
                        ocupado = false, progreso = null, error = r.mensaje)
            }
        }
    }

    fun sincronizar() {
        viewModelScope.launch {
            val dao = repo.dao ?: return@launch
            _estado.value = _estado.value.copy(ocupado = true, error = null)
            val r = repo.cliente.sincronizar(repo.archivoPaquete, dao)
            when (r) {
                is ClienteSincronizacion.Resultado.Ok -> {
                    val s = r.valor
                    _estado.value = _estado.value.copy(
                        ocupado = false,
                        mensaje = s.mensaje.ifBlank {
                            "${s.cambios} cambio(s) y ${s.fotos} foto(s) enviados."
                        },
                        error = if (s.bloqueado)
                            s.hallazgos.joinToString("\n").ifBlank {
                                "El servidor no pudo procesar el paquete."
                            } else null,
                        cambiosPendientes = repo.cambiosPendientes
                    )
                }
                is ClienteSincronizacion.Resultado.Error ->
                    _estado.value = _estado.value.copy(ocupado = false, error = r.mensaje)
            }
        }
    }

    // -------------------------------------------------------------- órdenes
    fun recargarOrdenes() {
        viewModelScope.launch {
            val lista = withContext(Dispatchers.IO) {
                repo.dao?.ordenes() ?: emptyList()
            }
            _estado.value = _estado.value.copy(
                ordenes = lista,
                cambiosPendientes = repo.cambiosPendientes,
                ordenActiva = _estado.value.ordenActiva.ifBlank {
                    lista.firstOrNull()?.get("orden_trabajo")?.toString() ?: ""
                }
            )
        }
    }

    fun abrirOrden(ordenTrabajo: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                // Al abrirla por primera vez pasa a EN_PROCESO: el estado real lo
                // marca el trabajo, no un botón aparte que nadie pulsa.
                val actual = _estado.value.ordenes.firstOrNull {
                    it["orden_trabajo"]?.toString() == ordenTrabajo
                }
                if (actual?.get("estado")?.toString() == "DESCARGADA") {
                    repo.dao?.actualizarOrden(ordenTrabajo, "EN_PROCESO")
                }
            }
            _estado.value = _estado.value.copy(
                ordenActiva = ordenTrabajo, pantalla = Pantalla.MAPA)
            recargarOrdenes()
        }
    }

    fun cerrarOrden(ordenTrabajo: String, resultado: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                repo.dao?.actualizarOrden(ordenTrabajo, "COMPLETADA", resultado)
            }
            // Se programa la subida para cuando vuelva la señal. El técnico cierra
            // la orden donde no hay cobertura y guarda el teléfono; esperar a que
            // se acuerde de pulsar «Sincronizar» significa, en la práctica,
            // esperar a la mañana siguiente.
            ec.cnel.ptnt.field.work.TrabajadorSincronizacion.programar(getApplication())
            recargarOrdenes()
            _estado.value = _estado.value.copy(
                pantalla = Pantalla.ORDENES,
                mensaje = "Orden $ordenTrabajo cerrada. Se subirá al recuperar señal.")
        }
    }

    fun volverAOrdenes() {
        _seleccionado.value = null
        _estado.value = _estado.value.copy(pantalla = Pantalla.ORDENES)
        recargarOrdenes()
    }

    // ------------------------------------------------------------- selección
    fun seleccionar(e: Elemento?) {
        _seleccionado.value = e
        _moviendo.value = false
        _ultimoMovimiento.value = null
        if (e == null) {
            _relacionados.value = emptyList()
            _fotos.value = emptyList()
            return
        }
        viewModelScope.launch {
            val (rel, fot) = withContext(Dispatchers.IO) {
                (repo.dao?.relacionados(e.guid) ?: emptyList()) to
                        (repo.dao?.fotosDe(e.guid) ?: emptyList())
            }
            _relacionados.value = rel
            _fotos.value = fot
        }
    }

    fun seleccionarPorGuid(capa: String, guid: String) {
        viewModelScope.launch {
            val e = withContext(Dispatchers.IO) { repo.dao?.porGuid(capa, guid) }
            seleccionar(e)
        }
    }

    fun capaDe(e: Elemento): CapaFormulario? = repo.esquema.capa(e.capa)

    // -------------------------------------------------------------- edición
    fun guardarAtributos(nuevos: Map<String, Any?>, motivo: String = "") {
        val e = _seleccionado.value ?: return
        val ed = repo.editor(_estado.value.ordenActiva) ?: return
        ed.ubicacionDispositivo = _posicion.value?.let {
            EditorTopologico.Ubicacion(it.lat, it.lon, it.precisionM.toDouble())
        }
        viewModelScope.launch {
            val n = withContext(Dispatchers.IO) { ed.editarAtributos(e, nuevos, motivo) }
            _estado.value = _estado.value.copy(
                mensaje = if (n > 0) "$n campo(s) guardado(s)." else "Sin cambios.",
                cambiosPendientes = repo.cambiosPendientes)
            seleccionarPorGuid(e.capa, e.guid)
        }
    }

    fun activarMovimiento(activo: Boolean) { _moviendo.value = activo }

    /**
     * Mueve el elemento seleccionado al punto tocado, arrastrando lo que
     * topológicamente debe seguirlo.
     *
     * Antes de mover se hace snap contra lo que hay alrededor: en campo el
     * objetivo casi siempre es *pegar* el elemento a un poste o a un vértice
     * existente, no dejarlo a 40 cm.
     */
    fun moverSeleccionadoA(lat: Double, lon: Double, indiceVertice: Int = 0) {
        val e = _seleccionado.value ?: return
        val ed = repo.editor(_estado.value.ordenActiva) ?: return
        ed.ubicacionDispositivo = _posicion.value?.let {
            EditorTopologico.Ubicacion(it.lat, it.lon, it.precisionM.toDouble())
        }
        viewModelScope.launch {
            val res = withContext(Dispatchers.IO) {
                val destino = repo.aRed(lat, lon)
                val vecinos = candidatosSnap(lat, lon)
                val (sx, sy, _) = ed.snap(destino[0], destino[1], vecinos, e.guid)
                ed.mover(e, sx, sy, indiceVertice)
            }
            _ultimoMovimiento.value = res
            _moviendo.value = false
            _estado.value = _estado.value.copy(
                mensaje = if (res.movidos.isEmpty()) "No se movió nada."
                else "Movido. ${res.propagados} elemento(s) siguieron por " +
                        "conexión topológica.",
                error = res.advertencias.firstOrNull(),
                cambiosPendientes = repo.cambiosPendientes)
            seleccionarPorGuid(e.capa, e.guid)
        }
    }

    /** Elementos cercanos a los que puede pegarse la captura. */
    private fun candidatosSnap(lat: Double, lon: Double): List<Elemento> {
        val d = 0.0005                      // ~55 m: alcance útil del snap
        return listOf("ptnt_poste", "ptnt_tramo", "ptnt_puesto_transformacion")
            .flatMap { repo.enVentana(it, lat - d, lon - d, lat + d, lon + d, 200) }
    }

    fun crearElemento(capa: String, lat: Double, lon: Double,
                      atributos: Map<String, Any?> = emptyMap()) {
        val ed = repo.editor(_estado.value.ordenActiva) ?: return
        ed.ubicacionDispositivo = _posicion.value?.let {
            EditorTopologico.Ubicacion(it.lat, it.lon, it.precisionM.toDouble())
        }
        viewModelScope.launch {
            val guid = withContext(Dispatchers.IO) {
                val p = repo.aRed(lat, lon)
                val (sx, sy, _) = ed.snap(p[0], p[1], candidatosSnap(lat, lon))
                ed.crear(capa, atributos,
                    Geometria.punto(sx, sy, repo.sridRed))
            }
            _estado.value = _estado.value.copy(
                mensaje = "Elemento creado.", cambiosPendientes = repo.cambiosPendientes)
            seleccionarPorGuid(capa, guid)
        }
    }

    fun eliminarSeleccionado(motivo: String) {
        val e = _seleccionado.value ?: return
        val ed = repo.editor(_estado.value.ordenActiva) ?: return
        viewModelScope.launch {
            val (ok, bloqueos) = withContext(Dispatchers.IO) { ed.eliminar(e, motivo) }
            if (ok) {
                seleccionar(null)
                _estado.value = _estado.value.copy(
                    mensaje = "Elemento eliminado.",
                    cambiosPendientes = repo.cambiosPendientes)
            } else {
                _estado.value = _estado.value.copy(error = bloqueos.joinToString("\n"))
            }
        }
    }

    // ---------------------------------------------------------------- fotos
    fun registrarFoto(archivo: File, descripcion: String) {
        val e = _seleccionado.value ?: return
        val cam = repo.camara() ?: return
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) {
                cam.registrar(archivo, e.guid, e.capa, _estado.value.ordenActiva,
                    _posicion.value?.original, descripcion)
            }
            _fotos.value = withContext(Dispatchers.IO) { cam.fotosDe(e.guid) }
            _estado.value = _estado.value.copy(
                mensaje = if (r.conUbicacion) "Foto guardada con ubicación y hora."
                else "Foto guardada.",
                error = r.advertencia)
        }
    }

    // ---------------------------------------------------------------- avisos
    fun limpiarAvisos() {
        _estado.value = _estado.value.copy(mensaje = null, error = null)
    }

    override fun onCleared() {
        repo.cerrar()
        super.onCleared()
    }
}
