package ec.cnel.ptnt.field

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import ec.cnel.ptnt.field.ui.CampoViewModel
import ec.cnel.ptnt.field.ui.PantallaOrdenes
import ec.cnel.ptnt.field.ui.PantallaTrabajo
import ec.cnel.ptnt.field.ui.PantallaVinculacion
import ec.cnel.ptnt.field.ui.TemaCampo
import java.io.File

/** Aplicación: solo existe para dar un `Context` de proceso al ViewModel. */
class AplicacionCampo : Application()

/**
 * Única actividad de la aplicación.
 *
 * Una sola, y navegación por estado en vez de un grafo de destinos: son tres
 * pantallas con una transición lineal —vincular, elegir orden, trabajar— y el
 * estado que las une (el paquete abierto) es uno solo. Un componente de
 * navegación completo añadiría dependencias y ceremonia sin resolver nada.
 *
 * Los permisos se piden **cuando hacen falta y explicando para qué**. Pedirlos
 * todos al arrancar, antes de que el técnico haya visto nada, es la forma más
 * fiable de que los deniegue: no sabe todavía qué va a hacer la app.
 */
class MainActivity : ComponentActivity() {

    private var alTomarFoto: ((File) -> Unit)? = null
    private var archivoFotoPendiente: File? = null

    private val pedirUbicacion = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { /* el flujo de ubicación se reintenta solo al recomponer */ }

    private val pedirCamara = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { concedido -> if (concedido) lanzarCamara() }

    private val tomarFoto = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { ok ->
        val f = archivoFotoPendiente
        if (ok && f != null) alTomarFoto?.invoke(f)
        archivoFotoPendiente = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            TemaCampo {
                val vm: CampoViewModel = viewModel()
                val estado by vm.estado.collectAsState()
                val seleccionado by vm.seleccionado.collectAsState()
                val avisos = remember { SnackbarHostState() }

                LaunchedEffect(Unit) { asegurarUbicacion() }

                // Los avisos se muestran una vez y se limpian: un mensaje que
                // reaparece en cada recomposición tapa el mapa justo cuando el
                // técnico intenta tocar un elemento.
                LaunchedEffect(estado.mensaje, estado.error) {
                    val texto = estado.error ?: estado.mensaje
                    if (texto != null) {
                        avisos.showSnackbar(texto)
                        vm.limpiarAvisos()
                    }
                }

                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Scaffold(snackbarHost = { SnackbarHost(avisos) }) { _ ->
                        when (estado.pantalla) {
                            CampoViewModel.Pantalla.VINCULAR -> PantallaVinculacion(
                                estado = estado,
                                servidorInicial = vm.repo.sesion.servidor(),
                                dispositivoId = idDispositivo(),
                                onVincular = { servidor, usuario, clave ->
                                    vm.vincular(servidor, usuario, clave, idDispositivo())
                                }
                            )

                            CampoViewModel.Pantalla.ORDENES -> PantallaOrdenes(
                                estado = estado,
                                onDescargar = { vm.descargarTrabajo() },
                                onSincronizar = { vm.sincronizar() },
                                onAbrirOrden = { vm.abrirOrden(it) },
                                onDesvincular = { vm.desvincular() }
                            )

                            CampoViewModel.Pantalla.MAPA -> PantallaTrabajo(
                                vm = vm,
                                estado = estado,
                                seleccionado = seleccionado,
                                onTomarFoto = {
                                    alTomarFoto = { archivo ->
                                        vm.registrarFoto(archivo, "")
                                    }
                                    pedirFoto()
                                }
                            )
                        }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------- permisos
    private fun asegurarUbicacion() {
        val fino = ContextCompat.checkSelfPermission(
            this, Manifest.permission.ACCESS_FINE_LOCATION)
        if (fino != PackageManager.PERMISSION_GRANTED) {
            pedirUbicacion.launch(
                arrayOf(
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
                )
            )
        }
    }

    private fun pedirFoto() {
        val permiso = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
        if (permiso == PackageManager.PERMISSION_GRANTED) lanzarCamara()
        else pedirCamara.launch(Manifest.permission.CAMERA)
    }

    /**
     * Se usa la cámara del sistema en vez de una vista propia con CameraX.
     *
     * La app de cámara del fabricante ya trae enfoque, HDR y estabilización
     * ajustados a ese sensor. Reimplementar la captura con CameraX daría fotos
     * peores en la mitad del parque de equipos y sumaría megabytes al APK, a
     * cambio de una pantalla propia que nadie pidió. Lo que sí es nuestro —los
     * metadatos de ubicación, hora, autor y hash— se escribe después, sobre el
     * archivo que devuelve.
     */
    private fun lanzarCamara() {
        val dir = File(cacheDir, "camara").apply { mkdirs() }
        val archivo = File(dir, "captura_${System.currentTimeMillis()}.jpg")
        archivoFotoPendiente = archivo
        val uri: Uri = FileProvider.getUriForFile(
            this, "$packageName.fileprovider", archivo)
        tomarFoto.launch(uri)
    }

    /**
     * Identificador del equipo.
     *
     * `ANDROID_ID` cambia al restablecer de fábrica y es distinto por aplicación
     * desde Android 8, así que no sirve para rastrear a nadie: solo distingue
     * este teléfono de los otros de la cuadrilla, que es lo único que necesita el
     * backend para saber a qué equipo revocarle el token.
     */
    @Suppress("HardwareIds")
    private fun idDispositivo(): String {
        val id = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
        val modelo = "${Build.MANUFACTURER}-${Build.MODEL}".replace(' ', '_')
        return "$modelo-${(id ?: "sin-id").take(8)}"
    }
}
