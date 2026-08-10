package ec.cnel.ptnt.field.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ec.cnel.ptnt.field.data.CampoFormulario
import ec.cnel.ptnt.field.data.CapaFormulario
import ec.cnel.ptnt.field.data.Dominio
import ec.cnel.ptnt.field.data.Elemento
import ec.cnel.ptnt.field.data.Relacion
import ec.cnel.ptnt.field.data.Subtipo

/**
 * Formulario de atributos del elemento seleccionado.
 *
 * Se construye **desde el esquema del paquete**, no desde código: los campos, sus
 * etiquetas, sus dominios y qué es obligatorio vienen en el manifiesto. Agregar
 * un tipo de hallazgo en el backend aparece aquí en la siguiente descarga, sin
 * publicar una versión de la app.
 *
 * Tres cosas que la interfaz resuelve y que no son evidentes hasta verlo en la
 * calle:
 *
 * * **Los cambios no se guardan solos.** Un campo tocado sin querer no puede
 *   convertirse en una edición de la red. Se guarda con un botón, y hasta
 *   entonces el panel muestra cuántos campos están pendientes.
 *
 * * **El panel de relacionados es parte del formulario, no otra pantalla.**
 *   Parado sobre un puesto, el técnico ve y abre sus unidades ahí mismo: es lo
 *   que hace operativo el modelo Puesto→Unidad. Buscarlas en otro lado
 *   significa, en la práctica, que no se corrigen.
 *
 * * **Los campos calculados se muestran pero no se editan.** El score de sospecha
 *   o el consumo promedio orientan la visita; dejarlos editables invitaría a
 *   "corregir" en campo un número que el sistema recalcula igual.
 */
@Composable
fun PanelAtributos(
    modifier: Modifier,
    elemento: Elemento,
    capa: CapaFormulario?,
    relacionados: List<Relacion>,
    fotos: List<Map<String, Any?>>,
    enModoMover: Boolean,
    onGuardar: (Map<String, Any?>, String) -> Unit,
    onMover: (Boolean) -> Unit,
    onEliminar: (String) -> Unit,
    onAbrirRelacionado: (String, String) -> Unit,
    onTomarFoto: () -> Unit,
    candidatosReconexion: List<Elemento> = emptyList(),
    onReconectar: (String, String, String) -> Unit = { _, _, _ -> },
) {
    // La clave del `remember` es el guid: al cambiar de elemento el borrador se
    // descarta. Sin eso, los valores tecleados en un cliente aparecerían en el
    // siguiente.
    var borrador by remember(elemento.guid) { mutableStateOf(mapOf<String, Any?>()) }
    var confirmarBorrado by remember(elemento.guid) { mutableStateOf(false) }
    var motivo by remember(elemento.guid) { mutableStateOf("") }
    var pestana by remember(elemento.guid) { mutableStateOf(0) }
    var reconectando by remember(elemento.guid) { mutableStateOf(false) }
    // Qué se ajustó al cambiar el subtipo, y qué campos quedaron esperando que
    // el técnico vuelva a elegir. Se recuerda por elemento: al cambiar de
    // elemento el aviso ya no viene a cuento.
    var avisoSubtipo by remember(elemento.guid) { mutableStateOf("") }
    var pendientesDeElegir by remember(elemento.guid) { mutableStateOf(setOf<String>()) }

    Column(modifier) {
        Encabezado(elemento, capa, borrador.size)

        TabRow(selectedTabIndex = pestana) {
            Tab(pestana == 0, { pestana = 0 }, text = { Text("Atributos") })
            Tab(pestana == 1, { pestana = 1 },
                text = { Text("Relacionados (${relacionados.size})") })
            Tab(pestana == 2, { pestana = 2 }, text = { Text("Fotos (${fotos.size})") })
        }

        Box(Modifier.weight(1f)) {
            when (pestana) {
                0 -> FormularioCampos(
                    elemento = elemento,
                    capa = capa,
                    borrador = borrador,
                    avisoSubtipo = avisoSubtipo,
                    pendientes = pendientesDeElegir,
                    onCambiar = { k, v ->
                        borrador = borrador + (k to v)
                        pendientesDeElegir = pendientesDeElegir - k
                    },
                    onCambiarSubtipo = { nuevo ->
                        val capaActual = capa
                        if (capaActual == null) {
                            borrador = borrador + (nuevo to nuevo)
                        } else {
                            val cambio = capaActual.aplicarSubtipo(
                                elemento.atributos + borrador, nuevo)
                            // Solo lo que difiere del original entra al borrador:
                            // meterlo entero marcaría como editados campos que
                            // nadie tocó, y el contador de pendientes mentiría.
                            borrador = cambio.atributos.filter { (k, v) ->
                                elemento.atributos[k]?.toString() != v?.toString()
                            }
                            pendientesDeElegir = cambio.invalidados.keys
                            avisoSubtipo = if (cambio.hayQueAvisar)
                                cambio.resumen { capaActual.etiquetaDe(it) } else ""
                        }
                    }
                )
                1 -> ListaRelacionados(
                    relacionados, onAbrirRelacionado,
                    puedeReconectar = candidatosReconexion.isNotEmpty(),
                    puestoActual = elemento.atributos["puesto_guid"]?.toString(),
                    onReconectar = { reconectando = true })
                else -> ListaFotos(fotos, onTomarFoto)
            }
        }

        Divider()
        // Un obligatorio que quedó vacío porque el subtipo cambió no puede
        // guardarse: el servidor rechazaría el lote entero al sincronizar, y eso
        // se descubre en la oficina, cuando el técnico ya no está delante del
        // elemento y no puede verificar nada.
        val faltan = pendientesDeElegir.filter {
            capa?.esObligatorio(it, elemento.atributos + borrador) == true
        }
        BarraAcciones(
            hayCambios = borrador.isNotEmpty(),
            bloqueadoPor = if (faltan.isEmpty()) "" else
                "Falta elegir " + faltan.joinToString(", ") {
                    capa?.etiquetaDe(it) ?: it
                },
            enModoMover = enModoMover,
            tieneGeometria = elemento.geometria != null,
            onGuardar = { onGuardar(borrador, motivo); borrador = emptyMap() },
            onDescartar = { borrador = emptyMap() },
            onMover = onMover,
            onEliminar = { confirmarBorrado = true },
            onFoto = onTomarFoto
        )
    }

    if (reconectando) {
        DialogoReconexion(
            candidatos = candidatosReconexion,
            actual = elemento.atributos["puesto_guid"]?.toString(),
            onCerrar = { reconectando = false },
            onConfirmar = { guid, unidad, razon ->
                onReconectar(guid, unidad, razon)
                reconectando = false
            }
        )
    }

    if (confirmarBorrado) {
        AlertDialog(
            onDismissRequest = { confirmarBorrado = false },
            title = { Text("Eliminar ${elemento.etiqueta}") },
            text = {
                Column {
                    Text("La eliminación queda registrada y la revisa el " +
                            "supervisor antes de aplicarse al modelo. Indique por qué:")
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(motivo, { motivo = it },
                        label = { Text("Motivo") }, singleLine = false,
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(
                    onClick = { onEliminar(motivo); confirmarBorrado = false },
                    enabled = motivo.isNotBlank()
                ) { Text("Eliminar") }
            },
            dismissButton = {
                TextButton({ confirmarBorrado = false }) { Text("Cancelar") }
            }
        )
    }
}

@Composable
private fun Encabezado(elemento: Elemento, capa: CapaFormulario?, pendientes: Int) {
    Surface(color = MaterialTheme.colorScheme.primaryContainer) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Text(elemento.etiqueta, style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onPrimaryContainer)
            Text(capa?.descripcion ?: elemento.capa,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = .8f))
            if (pendientes > 0) {
                Spacer(Modifier.height(6.dp))
                AssistChip(onClick = {},
                    label = { Text("$pendientes campo(s) sin guardar") })
            }
        }
    }
}

@Composable
private fun FormularioCampos(
    elemento: Elemento,
    capa: CapaFormulario?,
    borrador: Map<String, Any?>,
    avisoSubtipo: String,
    pendientes: Set<String>,
    onCambiar: (String, Any?) -> Unit,
    onCambiarSubtipo: (String) -> Unit,
) {
    // El estado del elemento tal como quedaría si se guardara ahora: es lo que
    // decide qué dominios rigen. Mirar solo los atributos guardados haría que al
    // cambiar el subtipo los desplegables siguieran mostrando los de antes.
    val estado = elemento.atributos + borrador

    val campos = capa?.camposVisibles(estado) ?: elemento.atributos.keys.map {
        CampoFormulario(it, "TEXT", it, false, it !in NO_EDITABLES, emptyList(), "")
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())
        .padding(horizontal = 14.dp, vertical = 8.dp)) {

        if (avisoSubtipo.isNotBlank()) AvisoCambioSubtipo(avisoSubtipo)

        campos.filter { it.nombre !in OCULTOS }.forEach { c ->
            // `containsKey` y no elvis: vaciar un campo guarda null en el
            // borrador, y con `?:` el valor original reaparecería en pantalla
            // como si el borrado no hubiera ocurrido.
            val valor = if (borrador.containsKey(c.nombre)) borrador[c.nombre]
            else elemento.atributos[c.nombre]
            val esSubtipo = capa?.tieneSubtipos == true && c.nombre == capa.campoSubtipo

            CampoEditable(
                campo = c,
                valor = valor,
                dominio = capa?.dominioEfectivo(c.nombre, estado),
                obligatorio = capa?.esObligatorio(c.nombre, estado) ?: c.obligatorio,
                subtipos = if (esSubtipo) capa!!.subtipos else emptyList(),
                pendienteDeElegir = c.nombre in pendientes,
                onCambiar = {
                    if (esSubtipo) onCambiarSubtipo(it?.toString().orEmpty())
                    else onCambiar(c.nombre, it)
                }
            )
            Spacer(Modifier.height(10.dp))
        }
        Spacer(Modifier.height(80.dp))       // aire bajo la barra de acciones
    }
}

/**
 * Lo que cambió al elegir otro subtipo, dicho de frente.
 *
 * Una corrección invisible en un teléfono es una corrección que el técnico
 * descubre semanas después, cuando ya no puede verificar nada. Si el sistema
 * ajustó o limpió un valor que él capturó en sitio, tiene que enterarse ahí
 * mismo, mientras todavía está parado delante del elemento.
 */
@Composable
private fun AvisoCambioSubtipo(texto: String) {
    Surface(
        color = MaterialTheme.colorScheme.tertiaryContainer,
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp)
    ) {
        Column(Modifier.padding(12.dp)) {
            Text("Al cambiar el tipo se ajustaron campos",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onTertiaryContainer)
            Spacer(Modifier.height(4.dp))
            Text(texto, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onTertiaryContainer)
        }
    }
}

@Composable
private fun CampoEditable(
    campo: CampoFormulario,
    valor: Any?,
    dominio: Dominio?,
    obligatorio: Boolean,
    subtipos: List<Subtipo>,
    pendienteDeElegir: Boolean,
    onCambiar: (Any?) -> Unit,
) {
    when {
        !campo.editable -> SoloLectura(campo, valor, dominio)
        // El subtipo se elige de su propia lista, con la etiqueta descriptiva:
        // «Delta abierto (2 unidades)» se entiende; DELTA_ABIERTO hay que
        // saberlo.
        subtipos.isNotEmpty() -> Desplegable(
            campo, valor?.toString() ?: "", obligatorio, pendienteDeElegir,
            opciones = subtipos.map { it.codigo to it.etiqueta },
            ayudaExtra = subtipos.firstOrNull {
                it.codigo == valor?.toString() }?.descripcion.orEmpty(),
            onCambiar = onCambiar
        )
        campo.esBooleano -> {
            val marcado = valor?.toString()?.let { it == "1" || it == "true" } ?: false
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth()) {
                Checkbox(marcado, { onCambiar(if (it) 1 else 0) })
                Spacer(Modifier.width(6.dp))
                Text(campo.etiqueta, style = MaterialTheme.typography.bodyLarge)
            }
        }
        dominio != null && !dominio.esRango -> Desplegable(
            campo, valor?.toString() ?: "", obligatorio, pendienteDeElegir,
            opciones = dominio.valores.map { it.codigo to it.etiqueta },
            ayudaExtra = dominio.descripcion,
            onCambiar = onCambiar
        )
        else -> {
            val texto = valor?.toString() ?: ""
            // El rango se comprueba mientras se teclea: avisar al guardar
            // obligaría a volver a buscar el campo entre veinte.
            val fueraDeRango = dominio != null && !dominio.admite(texto)
            OutlinedTextField(
                value = texto,
                onValueChange = { onCambiar(it.ifBlank { null }) },
                label = { Text(campo.etiqueta + if (obligatorio) " *" else "") },
                isError = fueraDeRango,
                supportingText = {
                    when {
                        fueraDeRango -> Text(
                            "Debe estar entre ${dominio!!.minimo} y ${dominio.maximo}",
                            color = MaterialTheme.colorScheme.error)
                        dominio?.descripcion?.isNotBlank() == true -> Text(dominio.descripcion)
                        campo.ayuda.isNotBlank() -> Text(campo.ayuda)
                    }
                },
                singleLine = true,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    keyboardType = if (campo.esNumerico) KeyboardType.Number
                    else KeyboardType.Text),
                modifier = Modifier.fillMaxWidth()
            )
        }
    }
}

/**
 * Desplegable de dominio.
 *
 * Los dominios llegan del backend y son la razón por la que el dato de campo es
 * comparable: escribir "medidor puenteado", "puenteado" y "PUENTEADO" a mano
 * produce tres hallazgos distintos que ningún reporte agrupa.
 *
 * Las opciones son las del dominio **efectivo**: el del subtipo actual si lo
 * hay, no el catálogo completo. Ofrecer las siete combinaciones de fase en un
 * banco de dos unidades es dejar que alguien elija una que no existe.
 */
@Composable
private fun Desplegable(
    campo: CampoFormulario,
    valor: String,
    obligatorio: Boolean,
    pendienteDeElegir: Boolean,
    opciones: List<Pair<String, String>>,
    ayudaExtra: String,
    onCambiar: (String?) -> Unit,
) {
    var abierto by remember { mutableStateOf(false) }
    val visible = opciones.firstOrNull { it.first == valor }?.second ?: valor
    Box(Modifier.fillMaxWidth()) {
        OutlinedTextField(
            value = visible, onValueChange = {}, readOnly = true,
            label = { Text(campo.etiqueta + if (obligatorio) " *" else "") },
            // El campo que quedó sin valor porque el subtipo cambió se marca:
            // si no, se guarda vacío sin que nadie lo note.
            isError = pendienteDeElegir,
            trailingIcon = {
                Icon(Icons.Default.ArrowDropDown, contentDescription = null)
            },
            supportingText = {
                when {
                    pendienteDeElegir -> Text(
                        "El valor anterior no aplica a este tipo: elija uno",
                        color = MaterialTheme.colorScheme.error)
                    ayudaExtra.isNotBlank() -> Text(ayudaExtra)
                    campo.ayuda.isNotBlank() -> Text(campo.ayuda)
                }
            },
            modifier = Modifier.fillMaxWidth()
        )
        // Superficie transparente encima: el campo de texto de solo lectura no
        // recibe clics de forma fiable en todas las versiones de Compose.
        Box(Modifier.matchParentSize().clickable { abierto = true })
        DropdownMenu(abierto, { abierto = false }) {
            DropdownMenuItem(text = { Text("(sin valor)") },
                onClick = { onCambiar(null); abierto = false })
            opciones.forEach { (codigo, etiqueta) ->
                DropdownMenuItem(
                    text = { Text(etiqueta) },
                    // El código se muestra al lado porque es lo que verá el
                    // supervisor en el reporte: si solo se ve la descripción,
                    // nadie puede cruzar lo capturado con la base comercial.
                    trailingIcon = {
                        if (etiqueta != codigo) Text(
                            codigo, style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    },
                    onClick = { onCambiar(codigo); abierto = false })
            }
        }
    }
}

@Composable
private fun SoloLectura(campo: CampoFormulario, valor: Any?, dominio: Dominio?) {
    Column(Modifier.fillMaxWidth()) {
        Text(campo.etiqueta, style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        // También aquí se muestra la descripción: un campo calculado que diga
        // «RB01» obliga al técnico a saberse el catálogo de memoria.
        val texto = if (valor == null) "—"
        else dominio?.descripcionDe(valor) ?: valor.toString()
        Text(texto, style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun ListaRelacionados(
    relacionados: List<Relacion>,
    onAbrir: (String, String) -> Unit,
    puedeReconectar: Boolean,
    puestoActual: String?,
    onReconectar: () -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        if (puedeReconectar) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                modifier = Modifier.fillMaxWidth().padding(8.dp),
                shape = RoundedCornerShape(10.dp)
            ) {
                Column(Modifier.padding(12.dp)) {
                    Text("Transformador que lo alimenta",
                        style = MaterialTheme.typography.labelMedium)
                    Text(
                        puestoActual?.takeIf { it.isNotBlank() }?.take(8)
                            ?: "sin asignar",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Si en sitio cuelga de otro, corríjalo aquí. Es la " +
                                "corrección que más pesa: mueve el consumo de " +
                                "una zona de balance a otra.",
                        style = MaterialTheme.typography.bodyMedium
                    )
                    Spacer(Modifier.height(8.dp))
                    Button(onReconectar, Modifier.fillMaxWidth()) {
                        Icon(Icons.Default.SwapHoriz, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("Reconectar a otro transformador")
                    }
                }
            }
        }
        ListaRelacionadosInterna(relacionados, onAbrir)
    }
}

@Composable
private fun ListaRelacionadosInterna(
    relacionados: List<Relacion>, onAbrir: (String, String) -> Unit
) {
    if (relacionados.isEmpty()) {
        Vacio("Este elemento no tiene relacionados en el paquete.")
        return
    }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 8.dp)) {
        items(relacionados) { r ->
            ListItem(
                headlineContent = { Text(etiquetaCapa(r.capa)) },
                supportingContent = { Text("${r.tipo.name} · ${r.guid.take(8)}") },
                trailingContent = {
                    Icon(Icons.Default.OpenInNew, contentDescription = "Abrir")
                },
                modifier = Modifier.clickable { onAbrir(r.capa, r.guid) }
            )
            Divider()
        }
    }
}

@Composable
private fun ListaFotos(fotos: List<Map<String, Any?>>, onTomar: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Button(onTomar, Modifier.fillMaxWidth()) {
            Icon(Icons.Default.PhotoCamera, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Tomar foto")
        }
        Spacer(Modifier.height(8.dp))
        if (fotos.isEmpty()) {
            Vacio("Sin fotos. Varias por elemento es lo normal: el medidor, la " +
                    "acometida, el entorno y el sello.")
            return@Column
        }
        LazyColumn {
            items(fotos) { f ->
                ListItem(
                    headlineContent = {
                        Text(f["descripcion"]?.toString()?.ifBlank { "Sin descripción" }
                            ?: "Sin descripción")
                    },
                    supportingContent = {
                        val lat = f["lat"]?.toString()?.toDoubleOrNull() ?: 0.0
                        val lon = f["lon"]?.toString()?.toDoubleOrNull() ?: 0.0
                        val prec = f["precision_m"]?.toString() ?: "?"
                        Text(
                            if (lat == 0.0 && lon == 0.0)
                                "Sin ubicación · ${f["tomada_en"]}"
                            else "%.5f, %.5f · ±%s m · %s".format(
                                lat, lon, prec, f["tomada_en"])
                        )
                    },
                    leadingContent = {
                        Icon(Icons.Default.Place, contentDescription = null,
                            tint = if ((f["lat"]?.toString()?.toDoubleOrNull() ?: 0.0) != 0.0)
                                ColorCompletada else ColorSospecha)
                    }
                )
                Divider()
            }
        }
    }
}

@Composable
private fun BarraAcciones(
    hayCambios: Boolean, bloqueadoPor: String, enModoMover: Boolean,
    tieneGeometria: Boolean,
    onGuardar: () -> Unit, onDescartar: () -> Unit, onMover: (Boolean) -> Unit,
    onEliminar: () -> Unit, onFoto: () -> Unit
) {
    Column(Modifier.fillMaxWidth().padding(10.dp)) {
        if (bloqueadoPor.isNotBlank()) {
            Text(bloqueadoPor, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(6.dp))
        }
        if (enModoMover) {
            Surface(color = MaterialTheme.colorScheme.secondary,
                shape = RoundedCornerShape(8.dp), modifier = Modifier.fillMaxWidth()) {
                Text("Toque en el mapa la posición correcta. Lo conectado se " +
                        "moverá con el elemento.",
                    Modifier.padding(10.dp), color = Color.Black)
            }
            Spacer(Modifier.height(8.dp))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onGuardar, enabled = hayCambios && bloqueadoPor.isBlank(),
                modifier = Modifier.weight(1f)) {
                Text("Guardar")
            }
            OutlinedButton(onDescartar, enabled = hayCambios) { Text("Descartar") }
        }
        Spacer(Modifier.height(8.dp))
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                FilterChip(enModoMover, { onMover(!enModoMover) },
                    label = { Text("Mover") },
                    enabled = tieneGeometria,
                    leadingIcon = { Icon(Icons.Default.Place, null) })
            }
            item {
                AssistChip(onFoto, label = { Text("Foto") },
                    leadingIcon = { Icon(Icons.Default.PhotoCamera, null) })
            }
            item {
                AssistChip(onEliminar, label = { Text("Eliminar") },
                    leadingIcon = { Icon(Icons.Default.Delete, null) })
            }
        }
    }
}

/**
 * Elección del transformador de destino.
 *
 * Se elige de una lista de los más cercanos, **no** se teclea un identificador.
 * Un guid escrito a mano en un teléfono, de pie y a pleno sol, es la vía directa
 * a reconectar al transformador equivocado — y ese error es peor que el que se
 * venía a corregir, porque deja las dos zonas mal y parece intencional.
 */
@Composable
private fun DialogoReconexion(
    candidatos: List<Elemento>,
    actual: String?,
    onCerrar: () -> Unit,
    onConfirmar: (String, String, String) -> Unit,
) {
    var elegido by remember { mutableStateOf<Elemento?>(null) }
    var razon by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onCerrar,
        title = { Text("Reconectar a otro transformador") },
        text = {
            Column {
                Text(
                    "Elija el transformador del que realmente cuelga. Se " +
                            "recalculará el balance de la zona anterior y de la " +
                            "nueva.",
                    style = MaterialTheme.typography.bodyMedium
                )
                Spacer(Modifier.height(10.dp))
                LazyColumn(Modifier.heightIn(max = 260.dp)) {
                    items(candidatos) { c ->
                        val esActual = c.guid == actual
                        ListItem(
                            headlineContent = { Text(c.etiqueta) },
                            supportingContent = {
                                Text(
                                    (c.atributos["potencia_nominal_kva"]
                                        ?.let { "$it kVA · " } ?: "") +
                                            (c.atributos["configuracion_banco"]
                                                ?.toString() ?: "")
                                )
                            },
                            leadingContent = {
                                RadioButton(
                                    selected = elegido?.guid == c.guid,
                                    onClick = { if (!esActual) elegido = c },
                                    enabled = !esActual
                                )
                            },
                            trailingContent = {
                                if (esActual) Text(
                                    "actual",
                                    style = MaterialTheme.typography.labelMedium
                                )
                            },
                            modifier = Modifier.clickable(enabled = !esActual) {
                                elegido = c
                            }
                        )
                        Divider()
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    razon, { razon = it },
                    label = { Text("Cómo lo verificó") },
                    placeholder = { Text("Seguimiento de acometida, corte de prueba…") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    elegido?.let {
                        onConfirmar(
                            it.guid,
                            it.atributos["unidad_guid"]?.toString().orEmpty(),
                            razon
                        )
                    }
                },
                // Sin decir cómo se verificó, la reconexión no es auditable: el
                // supervisor no puede distinguirla de un toque accidental.
                enabled = elegido != null && razon.isNotBlank()
            ) { Text("Reconectar") }
        },
        dismissButton = { TextButton(onCerrar) { Text("Cancelar") } }
    )
}

@Composable
private fun Vacio(texto: String) {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(texto, style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private val OCULTOS = setOf("fid", "geom", "guid")
private val NO_EDITABLES = setOf(
    "fid", "guid", "editado_por", "editado_en", "origen_edicion",
    "score_sospecha", "consumo_promedio_kwh", "n_unidades",
    "nodo_origen", "nodo_destino", "puesto_guid"
)

private fun etiquetaCapa(capa: String): String = when (capa) {
    "ptnt_unidad_transformacion" -> "Unidad de transformación"
    "ptnt_puesto_transformacion" -> "Puesto de transformación"
    "ptnt_cliente" -> "Cliente"
    "ptnt_tramo" -> "Tramo de red"
    "ptnt_poste" -> "Poste"
    "ptnt_luminaria" -> "Luminaria"
    "ptnt_seccionador" -> "Seccionador"
    "ptnt_capacitor" -> "Capacitor"
    else -> capa.removePrefix("ptnt_").replace('_', ' ')
        .replaceFirstChar { it.uppercase() }
}
