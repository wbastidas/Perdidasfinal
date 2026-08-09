package ec.cnel.ptnt.field.geo

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/**
 * Ubicación del dispositivo durante la jornada.
 *
 * Dos decisiones que vienen del uso real y no del manual:
 *
 * * **Un segundo entre posiciones, no cien milisegundos.** El GPS es el mayor
 *   consumidor de batería del equipo y la jornada dura ocho horas sin enchufe.
 *   A pie, un segundo es de sobra: nadie se mueve tres metros en ese tiempo.
 *
 * * **La precisión se expone junto a la posición, no se esconde.** Bajo un árbol
 *   o entre paredes el error salta a 30 m, y una captura hecha con esa posición
 *   deja el elemento en la vereda de enfrente. La interfaz muestra el número para
 *   que el técnico espere o corrija a mano, y el diario lo guarda para que el
 *   supervisor sepa con qué precisión se trabajó.
 */
class ProveedorUbicacion(private val contexto: Context) {

    private val cliente = LocationServices.getFusedLocationProviderClient(contexto)

    /** Por encima de esto la posición sirve para orientarse, no para capturar. */
    var precisionAceptableM: Float = 15f

    data class Posicion(
        val lat: Double, val lon: Double,
        val precisionM: Float, val altitudM: Double?,
        val rumbo: Float?, val instante: Long,
        val original: Location
    ) {
        val confiable: Boolean get() = precisionM <= 15f
    }

    @SuppressLint("MissingPermission")
    fun flujo(intervaloMs: Long = 1_000L): Flow<Posicion> = callbackFlow {
        val peticion = LocationRequest.Builder(
            Priority.PRIORITY_HIGH_ACCURACY, intervaloMs
        ).setMinUpdateIntervalMillis(intervaloMs).build()

        val cb = object : LocationCallback() {
            override fun onLocationResult(res: LocationResult) {
                res.lastLocation?.let { trySend(deLocation(it)) }
            }
        }
        cliente.requestLocationUpdates(peticion, cb, contexto.mainLooper)
        awaitClose { cliente.removeLocationUpdates(cb) }
    }

    @SuppressLint("MissingPermission")
    fun ultima(alRecibir: (Posicion?) -> Unit) {
        cliente.lastLocation
            .addOnSuccessListener { alRecibir(it?.let(::deLocation)) }
            .addOnFailureListener { alRecibir(null) }
    }

    private fun deLocation(l: Location) = Posicion(
        lat = l.latitude, lon = l.longitude,
        precisionM = if (l.hasAccuracy()) l.accuracy else 999f,
        altitudM = if (l.hasAltitude()) l.altitude else null,
        rumbo = if (l.hasBearing()) l.bearing else null,
        instante = l.time, original = l
    )
}
