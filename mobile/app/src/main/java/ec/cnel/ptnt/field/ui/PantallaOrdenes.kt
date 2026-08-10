package ec.cnel.ptnt.field.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * Lo primero que ve el técnico al abrir la app: su trabajo del día.
 *
 * Responde a la pregunta con la que se entra —«¿qué me toca hoy?»— y a la que se
 * vuelve al final —«¿qué me falta?»—. Las órdenes ya vienen ordenadas por energía
 * recuperable: la primera de la lista es la visita que más recupera, y en una
 * jornada que se corta a media tarde eso decide cuál se hizo.
 *
 * El contador de cambios pendientes está siempre visible en la barra. Un técnico
 * que se va a casa con nueve ediciones sin subir las pierde si el teléfono se
 * moja o se rompe; verlas todo el tiempo es lo que hace que se sincronice.
 */
@Composable
fun PantallaOrdenes(
    estado: CampoViewModel.Estado,
    onDescargar: () -> Unit,
    onSincronizar: () -> Unit,
    onAbrirOrden: (String) -> Unit,
    onDesvincular: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Mi trabajo")
                        Text(
                            estado.nombre.ifBlank { estado.usuario },
                            style = MaterialTheme.typography.labelMedium
                        )
                    }
                },
                actions = {
                    if (estado.cambiosPendientes > 0) {
                        Badge(Modifier.padding(end = 8.dp)) {
                            Text("${estado.cambiosPendientes} sin subir")
                        }
                    }
                    IconButton(onDesvincular) {
                        Icon(Icons.Default.Logout, contentDescription = "Cerrar sesión")
                    }
                }
            )
        },
        bottomBar = {
            Surface(tonalElevation = 3.dp) {
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    OutlinedButton(
                        onDescargar, Modifier.weight(1f),
                        enabled = !estado.ocupado
                    ) {
                        Icon(Icons.Default.CloudDownload, null)
                        Spacer(Modifier.width(6.dp))
                        Text("Descargar")
                    }
                    Button(
                        onSincronizar, Modifier.weight(1f),
                        enabled = !estado.ocupado && estado.cambiosPendientes > 0
                    ) {
                        Icon(Icons.Default.CloudUpload, null)
                        Spacer(Modifier.width(6.dp))
                        Text("Sincronizar")
                    }
                }
            }
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (estado.ocupado) {
                estado.progreso?.let { LinearProgressIndicator({ it }, Modifier.fillMaxWidth()) }
                    ?: LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            if (estado.sesionSinCifrar) {
                Surface(color = MaterialTheme.colorScheme.errorContainer) {
                    Text(
                        "Este equipo no pudo cifrar la sesión. Avise al " +
                                "administrador antes de trabajar con datos reales.",
                        Modifier.padding(12.dp)
                    )
                }
            }

            if (estado.ordenes.isEmpty()) {
                Box(Modifier.fillMaxSize().padding(28.dp), Alignment.Center) {
                    Text(
                        "No hay trabajo descargado.\n\nConéctese a la red de la " +
                                "empresa y toque «Descargar». Después puede " +
                                "trabajar todo el día sin señal.",
                        style = MaterialTheme.typography.bodyLarge
                    )
                }
                return@Column
            }

            Resumen(estado)
            LazyColumn {
                items(estado.ordenes) { o -> FilaOrden(o, onAbrirOrden) }
            }
        }
    }
}

@Composable
private fun Resumen(estado: CampoViewModel.Estado) {
    val pendientes = estado.ordenes.count {
        it["estado"]?.toString() !in setOf("COMPLETADA", "SINCRONIZADA")
    }
    val clientes = estado.ordenes.sumOf {
        it["clientes_a_revisar"]?.toString()?.toDoubleOrNull()?.toInt() ?: 0
    }
    val kwh = estado.ordenes.sumOf {
        it["recuperable_kwh_mes"]?.toString()?.toDoubleOrNull() ?: 0.0
    }
    Surface(color = MaterialTheme.colorScheme.primaryContainer) {
        Row(
            Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Dato("Pendientes", "$pendientes de ${estado.ordenes.size}")
            Dato("Clientes", "%,d".format(clientes))
            Dato("kWh/mes en juego", "%,.0f".format(kwh))
        }
    }
}

@Composable
private fun Dato(titulo: String, valor: String) {
    Column {
        Text(titulo, style = MaterialTheme.typography.labelMedium)
        Text(valor, style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun FilaOrden(o: Map<String, Any?>, onAbrir: (String) -> Unit) {
    val ot = o["orden_trabajo"]?.toString() ?: return
    val estado = o["estado"]?.toString() ?: "DESCARGADA"
    val cerrada = estado in setOf("COMPLETADA", "SINCRONIZADA")

    ListItem(
        leadingContent = {
            Box(
                Modifier.size(14.dp).clip(CircleShape)
                    .background(colorEstadoOrden(estado))
            )
        },
        headlineContent = {
            Text(
                "$ot · ${o["entidad"] ?: ""}",
                fontWeight = if (cerrada) FontWeight.Normal else FontWeight.SemiBold
            )
        },
        supportingContent = {
            Column {
                Text(o["accion"]?.toString() ?: o["nivel"]?.toString() ?: "")
                Text(
                    "${o["clientes_a_revisar"] ?: 0} cliente(s) · " +
                            "%,.0f kWh/mes".format(
                                o["recuperable_kwh_mes"]?.toString()
                                    ?.toDoubleOrNull() ?: 0.0
                            ),
                    style = MaterialTheme.typography.labelMedium
                )
            }
        },
        trailingContent = {
            AssistChip(onClick = { onAbrir(ot) }, label = { Text(etiqueta(estado)) })
        },
        modifier = Modifier.clickable { onAbrir(ot) }
    )
    Divider()
}

private fun etiqueta(estado: String): String = when (estado) {
    "COMPLETADA" -> "Hecha"
    "SINCRONIZADA" -> "Subida"
    "EN_PROCESO" -> "Seguir"
    else -> "Abrir"
}
