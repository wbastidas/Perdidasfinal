package ec.cnel.ptnt.field.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ec.cnel.ptnt.field.data.Elemento

/**
 * La pantalla de trabajo: mapa + atributos, con el reparto que decide el ancho.
 *
 * Aquí se junta todo lo demás. `PantallaCampo` resuelve la geometría de la
 * interfaz —lado a lado en tablet, hoja deslizante en teléfono— y esta capa pone
 * el contenido: el mapa de MapLibre, el formulario del elemento seleccionado, las
 * acciones de la orden y el cierre.
 *
 * El botón de cerrar la orden pide un resultado y no solo confirma. Una orden
 * cerrada sin decir qué se encontró no sirve para el reporte de gestión ni para
 * recalcular nada: es una visita que consumió una jornada y no dejó dato.
 */
@Composable
fun PantallaTrabajo(
    vm: CampoViewModel,
    estado: CampoViewModel.Estado,
    seleccionado: Elemento?,
    onTomarFoto: () -> Unit,
) {
    val relacionados by vm.relacionados.collectAsState()
    val fotos by vm.fotos.collectAsState()
    val moviendo by vm.moviendo.collectAsState()
    val posicion by vm.posicion.collectAsState()
    var centrar by remember { mutableStateOf<Pair<Double, Double>?>(null) }
    var cerrando by remember { mutableStateOf(false) }
    var resultado by remember { mutableStateOf("") }

    val orden = estado.ordenes.firstOrNull {
        it["orden_trabajo"]?.toString() == estado.ordenActiva
    }

    PantallaCampo(
        seleccionado = seleccionado,
        barraSuperior = {
            TopAppBar(
                navigationIcon = {
                    IconButton({ vm.volverAOrdenes() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Volver")
                    }
                },
                title = {
                    Column {
                        Text(estado.ordenActiva.ifBlank { "Trabajo" })
                        Text(
                            orden?.get("entidad")?.toString().orEmpty() +
                                    posicion?.let {
                                        "  ·  GPS ±%.0f m".format(it.precisionM)
                                    }.orEmpty(),
                            style = MaterialTheme.typography.labelMedium,
                            color = if (posicion?.confiable == false) ColorSospecha
                            else MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                actions = {
                    if (estado.cambiosPendientes > 0) {
                        Badge(Modifier.padding(end = 6.dp)) {
                            Text("${estado.cambiosPendientes}")
                        }
                    }
                    IconButton({ cerrando = true }) {
                        Icon(Icons.Default.Check, contentDescription = "Cerrar orden")
                    }
                }
            )
        },
        contenidoMapa = { m ->
            MapaCampo(
                modifier = m,
                repo = vm.repo,
                seleccionado = seleccionado,
                enModoMover = moviendo,
                onTocarMapa = { lat, lon -> vm.moverSeleccionadoA(lat, lon) },
                onSeleccionar = { vm.seleccionar(it) },
                centrarEn = centrar
            )
        },
        contenidoAtributos = { m, e ->
            PanelAtributos(
                modifier = m,
                elemento = e,
                capa = vm.capaDe(e),
                relacionados = relacionados,
                fotos = fotos,
                enModoMover = moviendo,
                onGuardar = { nuevos, motivo -> vm.guardarAtributos(nuevos, motivo) },
                onMover = { vm.activarMovimiento(it) },
                onEliminar = { vm.eliminarSeleccionado(it) },
                onAbrirRelacionado = { capa, guid -> vm.seleccionarPorGuid(capa, guid) },
                onTomarFoto = onTomarFoto
            )
        },
        accionesFlotantes = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                SmallFloatingActionButton(onClick = {
                    posicion?.let { centrar = it.lat to it.lon }
                }) {
                    Icon(Icons.Default.GpsFixed, contentDescription = "Mi ubicación")
                }
                SmallFloatingActionButton(onClick = {
                    // Centrar en la orden: al abrirla el mapa ya va ahí, pero
                    // tras caminar unas cuadras el técnico quiere volver.
                    val x = orden?.get("x")?.toString()?.toDoubleOrNull()
                    val y = orden?.get("y")?.toString()?.toDoubleOrNull()
                    if (x != null && y != null) {
                        val ll = vm.repo.aLatLon(x, y)
                        centrar = ll[0] to ll[1]
                    }
                }) {
                    Icon(Icons.Default.Layers, contentDescription = "Ir a la orden")
                }
            }
        }
    )

    if (cerrando) {
        AlertDialog(
            onDismissRequest = { cerrando = false },
            title = { Text("Cerrar ${estado.ordenActiva}") },
            text = {
                Column {
                    Text(
                        "Escriba qué se encontró. Es lo que va al reporte de " +
                                "gestión y lo que permite recalcular el balance " +
                                "con lo hallado."
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        resultado, { resultado = it },
                        label = { Text("Resultado de la visita") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    if (estado.cambiosPendientes > 0) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Quedan ${estado.cambiosPendientes} cambio(s) por " +
                                    "sincronizar. Se suben al volver a tener señal.",
                            style = MaterialTheme.typography.labelMedium
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        vm.cerrarOrden(estado.ordenActiva, resultado)
                        cerrando = false
                    },
                    enabled = resultado.isNotBlank()
                ) { Text("Cerrar orden") }
            },
            dismissButton = {
                TextButton({ cerrando = false }) { Text("Seguir trabajando") }
            }
        )
    }
}
