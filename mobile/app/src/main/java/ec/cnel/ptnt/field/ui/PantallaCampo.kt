package ec.cnel.ptnt.field.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import ec.cnel.ptnt.field.data.Elemento

/**
 * Composición adaptativa de la pantalla de campo.
 *
 * **Tablet**: mapa y atributos lado a lado. Hay ancho de sobra y el técnico
 * necesita ver dónde está parado mientras llena el formulario.
 *
 * **Teléfono**: el mapa ocupa todo y los atributos suben desde abajo en una hoja
 * que el dedo arrastra entre tres alturas —oculta, media y completa—. Es el
 * patrón de Field Maps, y no por imitarlo: en una pantalla de 6" partir el
 * espacio deja el mapa inútil y el formulario apretado, y en campo se trabaja
 * con una sola mano.
 *
 * El corte es 600 dp de ancho, el umbral estándar de Android entre "compacto" y
 * "medio". Se decide por **ancho disponible** y no por tipo de dispositivo,
 * porque un teléfono en horizontal o una tablet con la app en media pantalla
 * caen del otro lado.
 */

private val ANCHO_TABLET = 600.dp
private val ANCHO_PANEL_TABLET = 380.dp

enum class AlturaHoja { OCULTA, MEDIA, COMPLETA }

@Composable
fun PantallaCampo(
    seleccionado: Elemento?,
    contenidoMapa: @Composable (Modifier) -> Unit,
    contenidoAtributos: @Composable (Modifier, Elemento) -> Unit,
    barraSuperior: @Composable () -> Unit = {},
    accionesFlotantes: @Composable () -> Unit = {},
) {
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val esAncho = maxWidth >= ANCHO_TABLET
        if (esAncho) {
            DiseñoTablet(seleccionado, contenidoMapa, contenidoAtributos,
                barraSuperior, accionesFlotantes)
        } else {
            DiseñoTelefono(seleccionado, contenidoMapa, contenidoAtributos,
                barraSuperior, accionesFlotantes, maxHeight)
        }
    }
}

/** Tablet: mapa y atributos simultáneos, sin transición. */
@Composable
private fun DiseñoTablet(
    seleccionado: Elemento?,
    contenidoMapa: @Composable (Modifier) -> Unit,
    contenidoAtributos: @Composable (Modifier, Elemento) -> Unit,
    barraSuperior: @Composable () -> Unit,
    accionesFlotantes: @Composable () -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        barraSuperior()
        Row(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f).fillMaxHeight()) {
                contenidoMapa(Modifier.fillMaxSize())
                Box(Modifier.align(Alignment.BottomEnd).padding(16.dp)) {
                    accionesFlotantes()
                }
            }
            if (seleccionado != null) {
                Surface(
                    modifier = Modifier.width(ANCHO_PANEL_TABLET).fillMaxHeight(),
                    tonalElevation = 2.dp,
                    shadowElevation = 8.dp
                ) {
                    contenidoAtributos(Modifier.fillMaxSize(), seleccionado)
                }
            }
        }
    }
}

/**
 * Teléfono: hoja inferior arrastrable sobre el mapa.
 *
 * La altura MEDIA muestra la identificación del elemento y los campos que más se
 * llenan (hallazgo, lectura), que es el 80 % de las visitas. Subir a COMPLETA
 * solo hace falta para editar el resto o ver los relacionados.
 */
@Composable
private fun DiseñoTelefono(
    seleccionado: Elemento?,
    contenidoMapa: @Composable (Modifier) -> Unit,
    contenidoAtributos: @Composable (Modifier, Elemento) -> Unit,
    barraSuperior: @Composable () -> Unit,
    accionesFlotantes: @Composable () -> Unit,
    altoTotal: Dp,
) {
    var altura by remember { mutableStateOf(AlturaHoja.OCULTA) }

    // Al seleccionar un elemento la hoja sube sola a media altura: obligar a un
    // gesto extra tras cada toque es fricción pura en una jornada de campo.
    LaunchedEffect(seleccionado?.guid) {
        altura = if (seleccionado != null) AlturaHoja.MEDIA else AlturaHoja.OCULTA
    }

    val altoHoja = when (altura) {
        AlturaHoja.OCULTA -> 0.dp
        AlturaHoja.MEDIA -> altoTotal * 0.45f
        AlturaHoja.COMPLETA -> altoTotal * 0.92f
    }

    Column(Modifier.fillMaxSize()) {
        barraSuperior()
        Box(Modifier.fillMaxSize()) {
            contenidoMapa(Modifier.fillMaxSize())

            if (altura != AlturaHoja.COMPLETA) {
                Box(Modifier.align(Alignment.BottomEnd)
                    .padding(end = 16.dp, bottom = altoHoja + 16.dp)) {
                    accionesFlotantes()
                }
            }

            AnimatedVisibility(
                visible = seleccionado != null && altura != AlturaHoja.OCULTA,
                enter = slideInVertically { it },
                exit = slideOutVertically { it },
                modifier = Modifier.align(Alignment.BottomCenter)
            ) {
                Surface(
                    modifier = Modifier.fillMaxWidth().height(altoHoja),
                    shape = MaterialTheme.shapes.large,
                    tonalElevation = 3.dp,
                    shadowElevation = 12.dp
                ) {
                    Column(Modifier.fillMaxSize()) {
                        AsaHoja(
                            altura = altura,
                            onCambiar = { altura = it }
                        )
                        seleccionado?.let {
                            contenidoAtributos(Modifier.fillMaxSize(), it)
                        }
                    }
                }
            }
        }
    }
}

/**
 * Asa de la hoja: arrastrable y también pulsable.
 *
 * El toque alterna entre media y completa. En campo, con guantes o con el
 * teléfono en una mano, un arrastre preciso no siempre sale; un toque sí.
 */
@Composable
private fun AsaHoja(altura: AlturaHoja, onCambiar: (AlturaHoja) -> Unit) {
    Box(
        Modifier.fillMaxWidth().height(28.dp),
        contentAlignment = Alignment.Center
    ) {
        Surface(
            modifier = Modifier.width(44.dp).height(5.dp),
            shape = MaterialTheme.shapes.small,
            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f)
        ) {}
        Box(Modifier.matchParentSize().clickableSinRipple {
            onCambiar(
                when (altura) {
                    AlturaHoja.MEDIA -> AlturaHoja.COMPLETA
                    AlturaHoja.COMPLETA -> AlturaHoja.MEDIA
                    AlturaHoja.OCULTA -> AlturaHoja.MEDIA
                }
            )
        })
    }
}

@Composable
private fun Modifier.clickableSinRipple(onClick: () -> Unit): Modifier =
    this.then(
        Modifier.clickable(
            interactionSource = remember {
                androidx.compose.foundation.interaction.MutableInteractionSource()
            },
            indication = null,
            onClick = onClick
        )
    )

private fun Modifier.clickable(
    interactionSource: androidx.compose.foundation.interaction.MutableInteractionSource,
    indication: androidx.compose.foundation.Indication?,
    onClick: () -> Unit
): Modifier = androidx.compose.foundation.clickable(
    interactionSource = interactionSource, indication = indication, onClick = onClick
).let { this.then(it) }
