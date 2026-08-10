package ec.cnel.ptnt.field.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp

/**
 * Vinculación del equipo.
 *
 * Es la **única** pantalla donde se teclea una contraseña. A partir de aquí todo
 * viaja con el token del dispositivo: si el teléfono se pierde, el backend revoca
 * ese token y el equipo deja de sincronizar sin tocar la cuenta del técnico ni
 * obligarlo a cambiar la clave.
 *
 * La cuenta **no se crea aquí**. Quién puede editar la red de una distribuidora
 * es una decisión administrativa, no algo que se resuelva instalando una app; el
 * usuario lo da de alta el backend. El texto de ayuda lo dice explícitamente para
 * que el técnico no busque un botón de registro que no existe.
 */
@Composable
fun PantallaVinculacion(
    estado: CampoViewModel.Estado,
    servidorInicial: String,
    dispositivoId: String,
    onVincular: (servidor: String, usuario: String, password: String) -> Unit,
) {
    var servidor by remember { mutableStateOf(servidorInicial) }
    var usuario by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var verClave by remember { mutableStateOf(false) }

    val listo = servidor.isNotBlank() && usuario.isNotBlank() &&
            password.isNotBlank() && !estado.ocupado

    Scaffold { pad ->
        Column(
            Modifier.padding(pad).fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(Modifier.height(32.dp))
            Text("PTNT-BAL Campo", style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold)
            Text("Levantamiento de red y pérdidas",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(28.dp))

            OutlinedTextField(
                value = servidor, onValueChange = { servidor = it },
                label = { Text("Servidor") },
                placeholder = { Text("https://ptnt.cnel.gob.ec") },
                supportingText = {
                    Text("Lo entrega el administrador. Queda guardado para las " +
                            "próximas veces.")
                },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(12.dp))

            OutlinedTextField(
                value = usuario, onValueChange = { usuario = it.trim() },
                label = { Text("Usuario") }, singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(12.dp))

            OutlinedTextField(
                value = password, onValueChange = { password = it },
                label = { Text("Contraseña") }, singleLine = true,
                visualTransformation = if (verClave) VisualTransformation.None
                else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton({ verClave = !verClave }) {
                        Icon(
                            if (verClave) Icons.Default.VisibilityOff
                            else Icons.Default.Visibility,
                            contentDescription = "Ver contraseña"
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done
                ),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(20.dp))
            Button(
                onClick = { onVincular(servidor.trim(), usuario, password) },
                enabled = listo, modifier = Modifier.fillMaxWidth().height(52.dp)
            ) {
                if (estado.ocupado) {
                    CircularProgressIndicator(
                        Modifier.size(20.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary
                    )
                    Spacer(Modifier.width(10.dp))
                }
                Text("Vincular este equipo")
            }

            Spacer(Modifier.height(18.dp))
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Text("Cómo funciona", fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "• El usuario lo crea el administrador; aquí no hay registro.\n" +
                                "• Al vincular, este teléfono queda asociado a su cuenta.\n" +
                                "• Vincular otro equipo desconecta este.\n" +
                                "• Si lo pierde, avise: se revoca sin tocar su cuenta.",
                        style = MaterialTheme.typography.bodyMedium
                    )
                    Spacer(Modifier.height(8.dp))
                    Text("Identificador de este equipo: $dispositivoId",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}
