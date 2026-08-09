package ec.cnel.ptnt.field.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * Tema de la aplicación de campo.
 *
 * Tres decisiones que no son de gusto:
 *
 * * **Alto contraste.** Se trabaja a pleno sol de la costa. Los grises suaves de
 *   Material por defecto desaparecen al mediodía en Guayaquil; el azul profundo
 *   sobre blanco se lee con el brillo al máximo y con la pantalla rayada.
 *
 * * **Tipografía grande.** El cuerpo base sube a 16 sp y las etiquetas a 14 sp.
 *   El técnico lee de pie, con el teléfono a distancia de brazo y a veces con
 *   gafas de seguridad.
 *
 * * **Sin color dinámico.** Material You tomaría la paleta del fondo de pantalla
 *   del equipo y el rojo de una alerta podría acabar en un tono que no alarma.
 *   Los colores de estado tienen que significar lo mismo en todos los teléfonos
 *   de la cuadrilla.
 */

private val AzulCnel = Color(0xFF0B5FA5)
private val AzulProfundo = Color(0xFF083E6C)
private val Ambar = Color(0xFFE8A317)

// Colores de estado, usados también en el mapa. Se declaran aquí para que la
// leyenda de la pantalla y el símbolo del mapa no puedan divergir.
val ColorPendiente = Color(0xFF8A94A6)
val ColorEnProceso = Color(0xFF0B5FA5)
val ColorCompletada = Color(0xFF1E8E3E)
val ColorSospecha = Color(0xFFD93025)
val ColorEditado = Color(0xFFE8A317)

private val ClaroCnel = lightColorScheme(
    primary = AzulCnel,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD7E7F7),
    onPrimaryContainer = AzulProfundo,
    secondary = Ambar,
    onSecondary = Color(0xFF201600),
    background = Color(0xFFF5F7FA),
    onBackground = Color(0xFF10151C),
    surface = Color.White,
    onSurface = Color(0xFF10151C),
    surfaceVariant = Color(0xFFE3E8EF),
    onSurfaceVariant = Color(0xFF3C4451),
    error = ColorSospecha,
    onError = Color.White
)

private val OscuroCnel = darkColorScheme(
    primary = Color(0xFF7FB6E8),
    onPrimary = Color(0xFF00304F),
    primaryContainer = AzulProfundo,
    onPrimaryContainer = Color(0xFFD7E7F7),
    secondary = Ambar,
    background = Color(0xFF0E1319),
    onBackground = Color(0xFFE6EAF0),
    surface = Color(0xFF161C24),
    onSurface = Color(0xFFE6EAF0),
    surfaceVariant = Color(0xFF2A323D),
    onSurfaceVariant = Color(0xFFC2CAD6),
    error = Color(0xFFFF8A80)
)

private val TipografiaCampo = Typography(
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    labelLarge = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 14.sp),
    titleMedium = TextStyle(fontSize = 18.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
)

@Composable
fun TemaCampo(oscuro: Boolean = isSystemInDarkTheme(), contenido: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (oscuro) OscuroCnel else ClaroCnel,
        typography = TipografiaCampo,
        content = contenido
    )
}

/** Color del estado de una orden, único para lista y mapa. */
fun colorEstadoOrden(estado: String?): Color = when (estado) {
    "COMPLETADA", "SINCRONIZADA" -> ColorCompletada
    "EN_PROCESO" -> ColorEnProceso
    "RECHAZADA" -> ColorSospecha
    else -> ColorPendiente
}
