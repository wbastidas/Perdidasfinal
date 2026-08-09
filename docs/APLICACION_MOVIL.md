# Aplicación móvil de campo — PTNT-BAL Campo

Aplicación Android (Kotlin) para llevar el trabajo al campo, editar la red sin
señal y devolver los cambios al modelo. Inspirada en el flujo de ArcGIS Field
Maps, **sin licencias de ArcGIS**: GeoPackage OGC y MapLibre GL.

---

## 1. El ciclo completo

```
   [Programa principal]
   El análisis dice dónde ir
            │
            ▼
   ┌────────────────────┐
   │  Interfaz web      │  el supervisor asigna N órdenes a un técnico
   │  (Streamlit)       │
   └────────┬───────────┘
            ▼
   ┌────────────────────┐  recorta la red al área de trabajo + contexto
   │  Paquete .gpkg     │  topológico, adjunta cartografía offline
   └────────┬───────────┘
            ▼  descarga por HTTPS (una vez, con señal)
   ┌────────────────────┐
   │  App móvil         │  ◄── SIN SEÑAL: crea, modifica, mueve, elimina
   │  (Kotlin)          │      snap y propagación topológica
   │                    │      fotos con ubicación y hora
   └────────┬───────────┘      diario de cambios con antes/después
            ▼  sincroniza al volver
   ┌────────────────────┐
   │  Revisión          │  el supervisor ve TODO lo que cambió
   │  (web + CLI)       │  acepta y rechaza por separado
   └────────┬───────────┘
            ▼
   ┌────────────────────┐
   │  Recálculo         │  invalidación SELECTIVA: solo las etapas afectadas
   │  selectivo         │  → balance y ranking actualizados
   └────────────────────┘
```

---

## 1.bis Despacho a varias cuadrillas

El equipo de campo es un equipo, no una persona. El backend resuelve tres cosas
que solo aparecen cuando hay más de un técnico:

### Reparto de la jornada

`repartir_ordenes()` atiende dos objetivos que compiten:

* **Carga pareja.** Un técnico con 18 000 kWh en juego y otro con 3 000 significa
  que el primero no termina y el segundo vuelve a media tarde. La carga se mide
  por energía recuperable, por clientes a inspeccionar o por número de visitas
  (`--criterio kwh | clientes | visitas`); el número de órdenes no sirve, porque
  una ruta comercial con 100 clientes no cuesta lo mismo que un sector con 14.
* **Coherencia geográfica.** Repartir por sorteo manda a la misma cuadrilla a dos
  extremos de la ciudad; el traslado se come las visitas, y las visitas son lo
  único que recupera energía.

Cada orden va a la cuadrilla que minimiza `distancia_normalizada + β · carga_relativa`,
con las asignaciones grandes colocadas primero (cuando aún hay holgura para
equilibrar). El resultado se reporta con **ambas** métricas —desbalance % y
dispersión km— para que la decisión sea visible:

```
usuario  ordenes  clientes  recuperable_kwh_mes  dispersion_km
    ana        9       256              69449.0           0.90
   beto        8       201              69471.0           0.34
  carla        7       219              72828.0           0.30
   → desbalance 4.8 %
```

El reparto es **determinista**: las semillas se eligen por punto más lejano, no al
azar. Un supervisor que reparte dos veces la misma lista obtiene el mismo
resultado, y puede explicarlo.

Sin coordenadas utilizables el problema se reduce a repartir pesos y se resuelve
con LPT (*longest processing time first*), cuyo error está acotado por
4/3 − 1/(3m) respecto del óptimo.

`--max-por-usuario` limita la jornada: lo que no cabe se devuelve aparte, no se
reparte igual. Una cuadrilla con más órdenes de las que puede hacer no las hace,
las arrastra.

### Sincronización simultánea

Cuando las cuadrillas vuelven a la base, sincronizan a la vez. La persistencia
original en JSON perdía trabajo en silencio: cada proceso cargaba el archivo
entero, lo modificaba y lo reescribía completo, así que **el último en guardar
pisaba a los demás**. Medido: de 9 actualizaciones concurrentes sobrevivían 3.

`ptnt.field.store.AlmacenCampo` lo resuelve con SQLite transaccional:

| Decisión | Por qué |
|---|---|
| `BEGIN IMMEDIATE` en toda escritura | Toma el bloqueo al empezar la transacción, no al primer `INSERT`. Sin eso, dos transacciones que leen y luego escriben se entrelazan y una recibe `SQLITE_BUSY` a medio camino. |
| `UPDATE … WHERE estado = <el leído>` | *Compare-and-set*: si otro proceso ya movió la orden, el `UPDATE` afecta cero filas y la operación falla explícitamente en vez de sobrescribir una transición ajena. |
| `busy_timeout` + WAL | Esperar 200 ms es invisible; fallar obliga al técnico a reintentar y a veces a rehacer el trabajo. Con WAL, un técnico consultando sus órdenes no frena la sincronización de otro. |
| Bitácora de operaciones | Una orden que cambió de manos es imposible de explicar tres semanas después sin registro de quién la movió y cuándo. |

La asignación de un lote es **todo o nada**: media jornada asignada es peor que
ninguna, porque nadie sabe qué falta.

### Paquetes en lote

`construir_paquetes()` genera el `.gpkg` de cada técnico con trabajo pendiente,
leyendo la red **una sola vez**. Es el despacho de la mañana: las cuadrillas salen
juntas. Un fallo con un técnico no cancela a los demás — que una cuadrilla se
quede sin paquete es un problema; que se queden las cinco, un día perdido.

Verificado de extremo a extremo en `scripts/demo_campo_multiusuario.py`: tres
técnicos, 24 órdenes, vinculación real por HTTP, descarga simultánea →
**24/24 órdenes** en estado correcto y cada técnico viendo exactamente su parte.

---

## 2. Por qué GeoPackage y no otra cosa

| | |
|---|---|
| **Es SQLite** | Un archivo, sin servidor. Funciona sin señal por definición, y el sistema operativo ya lo sabe manejar. |
| **Es estándar OGC** | El mismo archivo abre en QGIS, en ArcGIS y en cualquier lector. Si el proyecto cambia de herramienta, los datos siguen siendo legibles. |
| **Índice espacial R\*Tree** | El mapa consulta solo lo visible. Sin él, cada desplazamiento del dedo recorrería 30 000 filas. |
| **Sin GDAL en el servidor** | El escritor está implementado sobre `sqlite3` puro: cero dependencias binarias en la máquina de la distribuidora, donde GDAL suele chocar con la instalación de ArcGIS. |

El binario de geometría se escribe con **envolvente XY**, para que el móvil
descarte lo que está fuera de la vista sin decodificar el WKB.

---

## 3. Modelo de datos del paquete

13 capas. Las de red son editables; las de trabajo y referencia sostienen el ciclo.

### Red (editable en campo)

| Capa | Geometría | Notas |
|---|---|---|
| `ptnt_puesto_transformacion` | Punto | kVA, configuración de banco, totalizador |
| `ptnt_unidad_transformacion` | **Sin geometría** | 1..3 por puesto, una por fase |
| `ptnt_cliente` | Punto | Cuenta, tarifa, ruta, medidor + **hallazgo de inspección** |
| `ptnt_tramo` | Línea | Conductor, longitud, fases, tensión |
| `ptnt_poste` | Punto | Material, altura |
| `ptnt_luminaria` | Punto | Potencia, balastro, tipo (incluye semáforo y cámara) |
| `ptnt_seccionador` | Punto | Posición normal y actual, cabecera |
| `ptnt_capacitor` | Punto | kVAR |

### Trabajo

| Capa | Función |
|---|---|
| `ptnt_orden_trabajo` | Las órdenes asignadas, con su estado |
| `ptnt_conexion` | **El grafo de conectividad real** |
| `ptnt_foto` | Fotografías con ubicación, hora y hash |
| `ptnt_cambio` | **Diario de ediciones** con antes y después |

### Tres decisiones que gobiernan el modelo

**1. Todo elemento lleva `guid` estable, no solo `fid`.** El `fid` es el rowid
local del GeoPackage y cambia entre paquetes. Sin `guid` sería imposible saber si
un elemento editado en campo es el mismo que ya existía en el SIG, y cada
sincronización crearía duplicados en vez de actualizar.

**2. Puesto→Unidad es una tabla aparte, no columnas `kva_1/kva_2/kva_3`.** En
campo hay que poder agregar una unidad a un puesto, cambiarle el kVA a una, o
quitarla. Solo modelándolas como filas independientes se puede editar una sin
tocar las otras.

**3. La conectividad viaja como grafo, no como geometría.** Dos elementos pueden
estar dibujados uno encima del otro sin estar conectados, y estar conectados sin
tocarse (un tramo subterráneo, una acometida mal digitalizada). `ptnt_conexion`
guarda la topología real desde `CIRCUITSOURCEGUID` / `PARENTCIRCUITSOURCEGUID`.

---

## 4. Edición topológica: snap y propagación

El requisito: *si muevo un cliente, la red conectada se mueve con él*. Sin eso,
un técnico que corrige la posición de un medidor deja la acometida colgando.

### Las cuatro reglas

| Relación | Comportamiento al mover | Por qué |
|---|---|---|
| `PERTENECE_A` | Mueve el elemento **completo** | Las unidades no tienen posición propia: están *en* el puesto |
| `ACOMETIDA` | Mueve **solo el extremo** | El otro extremo sigue anclado al poste, que no se movió |
| `COMPARTE_VERTICE` | Mueve **el vértice compartido** | Evita que la red se rompa en el punto de unión |
| `ALIMENTA` | **No propaga geometría** | Un transformador alimenta a cien clientes: moverlo no debe mover el barrio |

**La cuarta es la que más caro sale equivocar.** Una propagación por conectividad
eléctrica sin límite arrastra el alimentador completo con un solo gesto. Hay
además un tope de seguridad de 200 elementos: si una relación viniera mal
clasificada, el técnico no tendría forma de deshacer el estropicio en campo.

### Snap

Tolerancia de 3 m, del orden del error del GPS de un teléfono. Más estricta
rechazaría ajustes válidos; más laxa pegaría el elemento al poste equivocado en
una vereda angosta. La interfaz destaca a qué elemento se pegó.

### Eliminación protegida

Eliminar un puesto con clientes colgando **no es una operación válida**: dejaría
esos clientes sin transformador y el balance de la zona sin sentido. La app lo
impide **en el sitio**, cuando el técnico todavía puede arreglarlo, en vez de
aceptarlo y que reviente tres días después en la oficina.

---

## 5. Interfaz adaptativa

El corte es **600 dp de ancho** —el umbral estándar de Android entre compacto y
medio— y se decide por ancho disponible, no por tipo de dispositivo: un teléfono
en horizontal o una tablet con la app en media pantalla caen del otro lado.

### Tablet: lado a lado

```
┌──────────────────────────────┬─────────────────┐
│                              │  ATRIBUTOS      │
│                              │  ─────────────  │
│          MAPA                │  Cuenta: …      │
│                              │  Tarifa: …      │
│                              │  Hallazgo: [▾]  │
│                              │  ─────────────  │
│                        [ + ] │  RELACIONADOS   │
│                              │  • Unidad A     │
│                              │  • Unidad B     │
└──────────────────────────────┴─────────────────┘
```
Hay ancho de sobra y el técnico necesita ver dónde está parado mientras llena el
formulario.

### Teléfono: hoja inferior con transición

```
┌──────────────────────┐    ┌──────────────────────┐
│                      │    │        MAPA          │
│        MAPA          │ →  ├──────────────────────┤
│                      │    │  ═══  (asa)          │
│                [ + ] │    │  Cuenta: …           │
│                      │    │  Hallazgo: [▾]       │
└──────────────────────┘    └──────────────────────┘
     sin selección              45 % de la pantalla
```

Tres alturas: **oculta**, **media** (45 %) y **completa** (92 %). Al seleccionar
un elemento la hoja sube sola a media altura —obligar a un gesto extra tras cada
toque es fricción pura en una jornada de campo— y el asa se puede **arrastrar o
pulsar**: con guantes, un arrastre preciso no siempre sale; un toque sí.

La altura media muestra la identificación y los campos que más se llenan
(hallazgo, lectura), que es el 80 % de las visitas.

---

## 6. Fotografías como evidencia

Una foto sin dónde ni cuándo no es evidencia: es una imagen. Si un caso llega a
un proceso administrativo, lo que sostiene el hallazgo es poder demostrar que
**esa** foto se tomó **en ese predio** y **ese día**.

Los metadatos se escriben **en dos lugares**:

* **EXIF del archivo** — GPS, fecha, precisión, rumbo, autor. Viaja con la imagen
  si alguien la extrae del sistema.
* **Tabla `ptnt_foto`** — permite consultar y filtrar sin abrir cada archivo, y
  sobrevive si la imagen se recomprime en el camino.

Se calcula el **SHA-256** en el momento de la captura. No es contra un atacante
sofisticado: es para detectar la sustitución de una foto entre la captura y la
sincronización.

**Varias fotos por elemento** es lo normal: el medidor, la acometida, el entorno,
el sello.

**Resolución**: 1600 px de lado mayor, calidad 80 %. Suficiente para distinguir
un puente en una acometida, y deja la foto en ~400 KB. A resolución completa, una
jornada de 60 fotos serían 300 MB que hay que subir por la red de la
distribuidora — y en la práctica se traduce en técnicos que dejan de tomar fotos.

Si el GPS no tiene posición, **la foto se guarda igual** y se marca la falta:
perder la evidencia visual por no tener señal sería peor.

---

## 7. Rendimiento en equipos modestos

El equipo que se entrega a cuadrillas no es el mejor del catálogo. Cinco
decisiones concretas:

| Decisión | Efecto |
|---|---|
| **Recorte por área de trabajo** | El paquete trae solo la red de las órdenes asignadas, no la unidad de negocio entera. La diferencia es 300 MB contra 5 MB. |
| **Consulta por ventana con R\*Tree** | El mapa lee lo visible, no la tabla. |
| **Límite de 3 000 elementos por consulta** | Dibujar 20 000 puntos en una pantalla de 6" no aporta información y consume batería. |
| **WAL + caché de 8 MB** | El mapa lee mientras el formulario escribe; sin WAL cada guardado congela el render. |
| **Sin librería de geometría pesada** | El códec GeoPackage está escrito a mano: JTS/GeoTools pesan varios MB de APK que se pagan en descarga e instalación. |

Tope recomendado: **60 000 elementos por paquete**. Por encima, el generador
avisa en vez de entregar algo que no se va a poder usar.

`minSdk 24` (Android 7): cubre el parque real de las cuadrillas.

---

## 8. Cartografía offline

El paquete acepta **MBTiles**, que es lo que producen tanto las cadenas libres
(QGIS, tippecanoe, un WMTS descargado) como una **caché exportada de ArcGIS
Server**. El formato de entrada es el mismo, así que el requisito de "open source
o ArcGIS Server" se cumple sin licencias.

Las teselas se copian a la tabla `cartografia` del GeoPackage, registrada como
`tiles` en `gpkg_contents`. Motor de render: **MapLibre GL Native** — libre, con
aceleración por GPU.

---

## 9. Seguridad

| Control | Implementación |
|---|---|
| **Los usuarios se crean en el backend** | Nunca en el dispositivo: quién puede editar la red es una decisión administrativa |
| **Contraseñas solo como hash** | bcrypt/PBKDF2, igual que el resto del sistema |
| **Token por dispositivo** | Se emite al vincular y se revoca sin tocar la cuenta del técnico |
| **Un usuario, un dispositivo** | Vincular otro revoca el anterior: un teléfono perdido deja de sincronizar |
| **Token cifrado en el equipo** | `EncryptedSharedPreferences`: en un teléfono compartido, en claro permitiría sincronizar a nombre de otro |
| **El paquete se valida al volver** | Se rechaza si lo sube un usuario distinto al que se emitió |
| **Huella SHA-256 del paquete** | Detecta un paquete editado por fuera con otra herramienta |

---

## 10. Revisión: nada entra al modelo sin pasar por ella

Un técnico puede equivocarse de elemento, arrastrar sin querer, o capturar con el
GPS derivando bajo un árbol. Aceptar sus ediciones a ciegas degradaría el SIG en
vez de mejorarlo.

### Validaciones automáticas

| Código | Qué detecta | Severidad |
|---|---|---|
| SYNC02 | El paquete lo sube un usuario distinto al que se emitió | **Bloqueante** |
| SYNC03 | La red avanzó desde que se generó el paquete | Advertencia |
| SYNC11 | Huecos en la secuencia del diario (**ediciones perdidas**) | **Bloqueante** |
| SYNC12 | Cambios sin autor y sin manifiesto (no atribuibles) | **Bloqueante** |
| SYNC13 | Geometría capturada con precisión GPS peor a 25 m | Advertencia |
| SYNC14 | Fotos sin ubicación o fecha | Advertencia |
| SYNC16 | Cambios sin autor completados por el manifiesto (defecto de la app) | Advertencia |

### Aceptación parcial

Es **parcial por diseño**: en una misma visita el técnico puede haber corregido
bien tres medidores y haber movido mal un poste. Obligar a aceptar o rechazar
todo el lote llevaría a aceptar errores por no perder el trabajo bueno.

El sistema controla la **coherencia de la propagación**: aceptar el movimiento de
un cliente y rechazar el arrastre de su acometida dejaría la red desconectada, y
se advierte.

---

## 11. Recálculo selectivo

Los cambios aceptados determinan **qué etapas se invalidan**, reutilizando el
criterio del versionado de topología:

| Operación | Etapas a recalcular |
|---|---|
| `CREAR` / `ELIMINAR` | topología, flujo, pérdidas, balance, focalización |
| `MOVER` | flujo, pérdidas, balance, focalización *(cambian longitudes y sectores)* |
| `MODIFICAR` | flujo, pérdidas, balance |
| Cualquiera sobre `ptnt_cliente` | **+ ranking** *(el hallazgo altera la sospecha)* |

Sin esa selectividad, cada visita de campo dispararía un recálculo completo de la
unidad de negocio.

```bash
ptnt campo-revisar <lote> --aceptar-todo
# → "3 cambio(s) aceptado(s)… Recalcular: balance, flujo, focalizacion, perdidas, ranking"
ptnt analizar-red && ptnt focalizar     # el ranking dice de nuevo dónde ir
```

---

## 12. Histórico de modificaciones

Acumula **tanto las ediciones de campo como las cargas desde archivo** (FGDB,
SQL), porque la pregunta de auditoría es siempre la misma —*¿quién cambió este
elemento, cuándo y por qué?*— y la respuesta no puede depender de por qué puerta
entró el cambio.

Consultas incluidas:

* `historia_de(guid)` — toda la historia de un elemento.
* `resumen_por_origen()` — cuánto viene de campo y cuánto de carga masiva.
* `elementos_mas_editados()` — los que cambian una y otra vez casi siempre son un
  problema de datos de origen, no una red que se modifica tanto.

---

## 13. Comandos

```bash
# --- alta de cuadrillas -------------------------------------------------
ptnt campo-usuario jperez --nombre "Juan Pérez"    # crea el usuario móvil

# --- despacho a UNA cuadrilla -------------------------------------------
ptnt campo-asignar --usuario jperez --top 10       # asigna la jornada
ptnt campo-paquete --usuario jperez \
    --teselas cartografia/gye.mbtiles              # arma el .gpkg

# --- despacho a VARIAS cuadrillas ---------------------------------------
ptnt campo-repartir --usuarios ana,beto,carla \
    --top 30 --criterio kwh                        # simula el reparto
ptnt campo-repartir --usuarios ana,beto,carla \
    --top 30 --max-por-usuario 10 --aplicar        # lo escribe
ptnt campo-paquetes --teselas cartografia/gye.mbtiles   # todos los .gpkg

# --- servicio y revisión -------------------------------------------------
ptnt campo-servir --puerto 8090                    # API de sincronización
ptnt campo-revisar <lote> --rechazar 4             # revisa y decide
```

`campo-repartir` sin `--aplicar` solo muestra el reparto propuesto: repartir una
jornada es reversible en pantalla y caro en la calle.

El reparto, la asignación y la revisión también están en el tablero (pestaña
**📱 Trabajo de campo**), con un control deslizante para graduar el compromiso
entre agrupar por cercanía e igualar cargas.

### API móvil

```
POST /movil/vincular      teléfono ↔ usuario, emite el token
GET  /movil/ordenes       órdenes asignadas
GET  /movil/paquete       descarga el .gpkg
POST /movil/sincronizar   sube el paquete de retorno
GET  /movil/estado        salud y versión del esquema
```

Superficie deliberadamente pequeña: cada endpoint que se agrega es una versión
más que mantener compatible, y publicar una actualización en una distribuidora
puede tardar semanas.

---

## 14. Estructura del proyecto Android

```
mobile/
├── settings.gradle.kts
├── build.gradle.kts
└── app/
    ├── build.gradle.kts
    └── src/main/java/ec/cnel/ptnt/field/
        ├── data/GeoPackageDao.kt        acceso SQLite directo + códec GeoPackage
        ├── domain/EditorTopologico.kt   snap, propagación, diario de cambios
        ├── domain/CapturaFoto.kt        EXIF, ubicación, hash
        ├── sync/ClienteSincronizacion.kt descarga y subida
        └── ui/PantallaCampo.kt         composición adaptativa tablet/teléfono
```

**Dependencias clave** (todas libres): MapLibre GL Native, AndroidX
ExifInterface, CameraX, OkHttp, Security-Crypto, WorkManager.

**No se usa Room ni una capa ORM**: el GeoPackage llega del servidor con su
esquema ya definido, y una capa de mapeo obligaría a declarar las entidades en el
código y a publicar una versión nueva cada vez que el backend agregue un campo.
Leyendo el esquema del propio archivo, la app se adapta sola.

---

## 15. Estado de la entrega

| Componente | Estado |
|---|---|
| Escritor/lector GeoPackage OGC | **Completo y probado** |
| Esquema del paquete (13 capas) | **Completo** |
| Motor topológico backend | **Completo y probado** |
| Órdenes, asignación, máquina de estados | **Completo y probado** |
| Almacén transaccional y concurrencia | **Completo y probado** (68 pruebas de campo) |
| Reparto entre varias cuadrillas | **Completo y probado** |
| Paquetes en lote | **Completo y probado** |
| Armado del paquete con recorte | **Completo y probado** |
| Sincronización, validación, revisión | **Completo y probado** |
| Recálculo selectivo | **Completo y probado** |
| Histórico de modificaciones | **Completo y probado** |
| API móvil (FastAPI) | **Completa y probada con varios usuarios a la vez** |
| Interfaz web de asignación y revisión | **Completa** |
| CLI de campo (7 comandos) | **Completa** |
| Kotlin: DAO, editor, fotos, sync, UI adaptativa | **Núcleo implementado** |
| Kotlin: integración MapLibre, cámara, pantallas completas | **Pendiente** |

El backend y el contrato de datos están terminados y probados. Del lado Android
están escritos los módulos que concentran la dificultad —acceso GeoPackage, códec
de geometría, motor topológico, metadatos de foto, cliente de sincronización y
composición adaptativa—; falta el cableado de pantallas, la integración con
MapLibre y CameraX, y las pruebas instrumentadas en dispositivo.

---

Ver también: [Especificación completa](ESPECIFICACION_COMPLETA.md) ·
[Focalización](FOCALIZACION.md) · [Guía de operación](GUIA_OPERACION.md)
