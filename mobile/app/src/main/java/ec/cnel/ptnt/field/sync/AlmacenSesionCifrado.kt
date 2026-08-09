package ec.cnel.ptnt.field.sync

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Sesión guardada en `EncryptedSharedPreferences`, respaldada por el Keystore.
 *
 * El token de dispositivo es, en la práctica, la identidad del técnico frente al
 * backend: quien lo tenga puede subir ediciones de red a su nombre. En un
 * teléfono que rota entre cuadrillas —lo normal, los equipos son del área, no de
 * la persona— guardarlo en claro deja esa suplantación al alcance de cualquiera
 * con el aparato en la mano.
 *
 * Si el Keystore del equipo está dañado (ocurre en algunos Android 7 de gama
 * baja tras un restablecimiento), se cae a preferencias normales **y se marca**,
 * para que la app pueda avisar en vez de fingir una seguridad que no tiene.
 */
class AlmacenSesionCifrado(contexto: Context) : AlmacenSesion {

    var cifrado: Boolean = true
        private set

    private val prefs: SharedPreferences = try {
        val clave = MasterKey.Builder(contexto)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            contexto, "sesion_campo", clave,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    } catch (_: Exception) {
        cifrado = false
        contexto.getSharedPreferences("sesion_campo_plano", Context.MODE_PRIVATE)
    }

    override fun guardar(token: String, usuario: String, nombre: String, rol: String) {
        prefs.edit()
            .putString("token", token)
            .putString("usuario", usuario)
            .putString("nombre", nombre)
            .putString("rol", rol)
            .apply()
    }

    override fun token(): String? = prefs.getString("token", null)
    override fun usuario(): String? = prefs.getString("usuario", null)
    fun nombre(): String = prefs.getString("nombre", "") ?: ""
    fun rol(): String = prefs.getString("rol", "TECNICO") ?: "TECNICO"

    /** URL del backend. Se guarda porque cada unidad de negocio tiene la suya y
     *  el técnico no debería teclearla cada mañana. */
    fun servidor(): String = prefs.getString("servidor", "") ?: ""
    fun guardarServidor(url: String) {
        prefs.edit().putString("servidor", url.trimEnd('/')).apply()
    }

    /**
     * Cierra la sesión **sin** borrar el paquete de trabajo.
     *
     * Desvincular no puede destruir una jornada de ediciones todavía sin subir:
     * el técnico que se equivoca de botón perdería el día entero. El GeoPackage
     * se conserva y se sincroniza en cuanto vuelva a vincular.
     */
    override fun limpiar() {
        prefs.edit().remove("token").remove("usuario").remove("nombre")
            .remove("rol").apply()
    }

    val vinculado: Boolean get() = !token().isNullOrBlank()
}
