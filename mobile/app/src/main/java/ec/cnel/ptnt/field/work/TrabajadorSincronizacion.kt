package ec.cnel.ptnt.field.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import ec.cnel.ptnt.field.data.RepositorioCampo
import ec.cnel.ptnt.field.sync.ClienteSincronizacion
import java.util.concurrent.TimeUnit

/**
 * Sincronización diferida: sube el trabajo cuando vuelve la señal.
 *
 * El técnico cierra la orden en un sector sin cobertura y guarda el teléfono. Sin
 * esto, los cambios esperan a que alguien se acuerde de pulsar «Sincronizar» —y
 * en la práctica esperan hasta la mañana siguiente, o hasta que el equipo se
 * moja. Con WorkManager, el sistema despierta la tarea al recuperar red aunque la
 * app esté cerrada.
 *
 * Dos límites deliberados:
 *
 * * **Solo con red no medida** (`NetworkType.UNMETERED` no se exige, pero sí
 *   `CONNECTED`): subir un GeoPackage de 40 MB por datos móviles consume el plan
 *   del técnico. El envío automático se hace cuando hay red; el manual, siempre.
 *
 * * **Nunca borra el paquete local.** El worker sube y reporta; descartar es
 *   decisión del flujo interactivo, después de que el supervisor acepte.
 */
class TrabajadorSincronizacion(
    contexto: Context,
    params: WorkerParameters
) : CoroutineWorker(contexto, params) {

    override suspend fun doWork(): Result {
        val repo = RepositorioCampo(applicationContext)
        if (!repo.sesion.vinculado) return Result.success()   // nada que hacer
        if (!repo.abrir()) return Result.success()
        return try {
            val dao = repo.dao ?: return Result.success()
            if (dao.cambiosPendientes() == 0) return Result.success()
            when (val r = repo.cliente.sincronizar(repo.archivoPaquete, dao)) {
                is ClienteSincronizacion.Resultado.Ok ->
                    // Un lote bloqueado por validación no se arregla reintentando:
                    // necesita que alguien mire los hallazgos.
                    if (r.valor.bloqueado) Result.failure() else Result.success()
                is ClienteSincronizacion.Resultado.Error ->
                    if (r.codigo == 401) Result.failure() else Result.retry()
            }
        } catch (_: Exception) {
            Result.retry()
        } finally {
            repo.cerrar()
        }
    }

    companion object {
        private const val NOMBRE = "sincronizacion_campo"

        /** Programa un intento en cuanto haya red. Reemplaza el anterior: dos
         *  subidas simultáneas del mismo paquete duplicarían el lote. */
        fun programar(contexto: Context) {
            val peticion = OneTimeWorkRequestBuilder<TrabajadorSincronizacion>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .setBackoffCriteria(
                    androidx.work.BackoffPolicy.EXPONENTIAL, 5, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(contexto)
                .enqueueUniqueWork(NOMBRE, ExistingWorkPolicy.REPLACE, peticion)
        }

        fun cancelar(contexto: Context) {
            WorkManager.getInstance(contexto).cancelUniqueWork(NOMBRE)
        }
    }
}
