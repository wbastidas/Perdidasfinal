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

## 1.ter Un archivo o varios: la decisión de arquitectura

**Un solo GeoPackage por técnico, con todas sus órdenes dentro.** No un archivo
por orden. La razón no es comodidad: con un archivo por orden, el sistema
**pierde correcciones y crea conflictos irresolubles**.

| | Un archivo por técnico *(elegido)* | Un archivo por orden |
|---|---|---|
| **Snap y propagación** | La red del área completa está en el mismo archivo: mover un cliente arrastra su acometida aunque el poste pertenezca a otra orden. | Un poste que quedó "en el otro archivo" no existe para el motor de snap. El cliente se mueve y **rompe la conexión sin que la app lo sepa**. |
| **Solape geográfico** | Los postes, tramos y transformadores compartidos se guardan **una vez**. | Las órdenes de un mismo técnico se solapan: los mismos elementos se duplican N veces. Más MB, más batería. |
| **Edición del mismo elemento** | Imposible tenerlo dos veces. | El mismo poste editado en dos archivos con valores distintos. Al sincronizar, **no hay forma de decidir cuál gana**. |
| **Diario de cambios** | Una sola secuencia, ordenable. El backend detecta huecos. | Varias secuencias sin orden relativo: una reconexión en el archivo A y un movimiento del mismo poste en el B llegan sin saber cuál fue antes. |
| **Atomicidad** | Una transacción SQLite cubre el cambio y su propagación. | Un cambio que toca dos órdenes no es atómico: puede quedar a medias. |
| **Descarga** | Una petición, un reemplazo atómico. Si se corta, el paquete anterior sigue intacto. | N descargas y estados intermedios: el técnico sale con la mitad del trabajo. |

**Cuándo sí conviene partir**: no por orden, sino por **campaña o zona**, y solo
cuando el trabajo de un técnico cubre áreas realmente disjuntas —dos cantones,
por ejemplo—. Ahí el recorte por área ya no ahorra nada y el archivo crece sin
sentido. El armado del paquete avisa por encima de **60 000 elementos**, que es
donde el render empieza a degradarse en un equipo de gama baja.

Y sí: **GeoPackage también en el retorno**, no un formato distinto para subir. El
técnico devuelve *el mismo archivo* que descargó, con el diario dentro. Convertir
a otro formato al subir añadiría un paso que puede fallar y una traducción que
puede perder datos, justo en el momento en que el trabajo del día todavía no está
a salvo en ningún otro sitio.

---

## 1.quater Editar la conexión del consumidor

**Sí, y es la corrección que más vale de toda la visita.**

Un cliente colgado del transformador equivocado desbalancea **dos zonas a la
vez**: la que lo tiene asignado pierde energía que ese cliente nunca consumió, y
la que realmente lo alimenta la gana. Los dos balances quedan internamente
coherentes, así que **ningún análisis desde la oficina lo detecta**. Solo se ve
siguiendo la acometida en sitio.

En la app, con el cliente seleccionado, la pestaña **Relacionados** muestra de
qué transformador cuelga y ofrece «Reconectar a otro transformador». El destino
se elige de una lista de los más cercanos —**no se teclea un identificador**: un
guid escrito a mano, de pie y a pleno sol, es la vía directa a reconectar al
equivocado, y ese error es peor que el original porque parece intencional—. Se
exige decir **cómo se verificó** (seguimiento de acometida, corte de prueba): sin
eso, el supervisor no puede distinguirlo de un toque accidental.

También se puede fijar de **qué unidad del banco** cuelga, que es lo que
determina a qué fase carga el consumo. Y las luminarias son reconectables por la
misma razón: el alumbrado no medido se imputa al transformador del que cuelga.

Lo que ocurre por debajo:

1. Se reescribe el vínculo en el grafo de conectividad **en una transacción** con
   el borrado del anterior. Si quedaran los dos, el cliente colgaría de dos
   transformadores y su consumo se contaría dos veces.
2. Se registra como operación **`RECONECTAR`**, no como `MODIFICAR`. No es un
   matiz: `MODIFICAR` solo invalida el balance; `RECONECTAR` invalida además
   **topología, focalización y ranking**, y marca los **dos** transformadores
   —origen y destino— como afectados.
3. El backend bloquea una reconexión sin origen o sin destino, y advierte si
   apunta al mismo transformador que ya tenía.

---

## 1.quinquies Definir el trabajo: no solo lo peor del ranking

El sistema nació apuntando al hurto, pero hay trabajo que **nunca** va a salir de
un ranking de sospecha: un censo de una zona nueva no tiene consumo anómalo que
detectar —no hay clientes registrados—, y justo por eso hay que ir. Una zona con
el SIG viejo no tiene mal balance: lo tiene **incalculable**.

`ptnt campo-definir` genera órdenes por **alimentador, sector, área dibujada o
lista de cuentas**, con siete tipos de campaña:

```bash
# Censo de un alimentador completo, en bloques de 40 clientes
ptnt campo-definir --tipo CENSO --alimentador GYE-04 --por-orden 40

# Verificar un listado que mandó el área comercial
ptnt campo-definir --tipo VERIFICACION_MEDIDOR --lista cuentas.csv

# Una urbanización nueva: círculo sobre el mapa
ptnt campo-definir --tipo ACTUALIZACION_CARTOGRAFICA \
    --centro 631000,9762500 --radio 600
```

| Tipo | Se evalúa por |
|---|---|
| `INSPECCION_PNT` | kWh recuperados |
| `VERIFICACION_MEDIDOR` | kWh recuperados |
| `CENSO` | clientes incorporados |
| `ACTUALIZACION_CARTOGRAFICA` | elementos corregidos |
| `MANTENIMIENTO` | estructuras revisadas |
| `RECLAMO` | resolución |
| `OBRA` | red incorporada |

La distinción no es burocrática: **un censo no recupera energía, corrige el
denominador del balance**. Evaluarlo por kWh lo haría parecer inútil y dejaría de
hacerse. Por eso las campañas que no persiguen energía llevan `0` en el campo de
recuperable, a propósito.

El trabajo se parte en bloques **geográficamente compactos** (recorrido en
serpentina sobre una grilla), no por orden de lista: una orden con 40 clientes
repartidos por todo el alimentador es una orden que no se hace. Y las cuentas que
no aparecen en el padrón **se reportan**: normalmente vienen de otro sistema, y
descartarlas en silencio manda a la cuadrilla a una dirección que no existe.

Todo entra al mismo circuito: `campo-repartir` → `campo-paquetes` → campo →
`campo-revisar` → recálculo.

---

## 1.sexties Trabajos de varios días

Una revisión de campo rara vez cabe en una jornada. El ciclo soporta que dure lo
que dure:

* **Sincronizar no cierra la orden.** Solo se cierran las que el técnico marcó
  `COMPLETADA` en el dispositivo. Antes se cerraban *todas* las del paquete: la
  sincronización del primer día daba por terminadas órdenes que ni se habían
  empezado, y el supervisor veía una jornada completa donde había media mañana.
* **El avance se cuenta.** Cada sincronización con la orden abierta suma una
  jornada y sella la fecha. Es lo que distingue «va por la mitad» de «lleva una
  semana parada» — `RegistroCampo.estancadas(dias)` lista lo segundo.
* **Lo ya enviado no se reprocesa.** El diario acumula durante todo el trabajo;
  los cambios subidos quedan marcados con su lote. Sin eso, cada tarde se
  reenviaría lo anterior y el histórico contaría el mismo cambio tantas veces
  como días duró la orden.
* **La numeración no se reinicia.** El backend rechaza un diario con huecos —un
  hueco significa ediciones perdidas—, así que la secuencia sigue contando sobre
  todo el diario, incluido lo ya enviado.
* **El marcado ocurre después del 200 del servidor**, nunca antes. Si se marcara
  al empezar la subida y esta fallara a mitad, esos cambios no se reenviarían
  nunca y se perderían en silencio.

Al reabrir la orden, la barra muestra «jornada 3»: el técnico que retoma un
trabajo ajeno necesita saber que ya se avanzó.

---

## 1.septies Descarga y subida simultáneas

Ambas verificadas contra la API real con tres técnicos a la vez:

* **Descarga**: cada uno recibe su paquete y solo sus órdenes cambian de estado.
  18/18 correctas.
* **Subida**: los tres lotes llegan enteros, cada uno con su identificador y a
  nombre de quien lo hizo; se cierra exactamente una orden por técnico.

Lo que lo hace posible es el almacén transaccional del backend (ver 1.bis) y que
cada lote entrante se escriba en su propio archivo. Al probarlo apareció un
defecto que nunca habría dado la cara en desarrollo: `from __future__ import
annotations` hacía que FastAPI recibiera `UploadFile` como una cadena y no
pudiera resolverla, así que **`/movil/sincronizar` devolvía 500 con cualquier
paquete real**. Corregido.

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

## 3.bis Subtipos: al cambiarlos cambian los dominios

Es el comportamiento que todo editor del SIG da por sentado, y **la razón por la
que el formulario sirve para algo más que ahorrar tecleo**.

Un banco en delta abierto tiene dos unidades: sus fases posibles son AB, BC o CA.
**ABC es físicamente imposible.** Si el desplegable ofrece las siete
combinaciones, alguien elige ABC alguna vez —y no es negligencia, es un
desplegable con siete opciones bajo el sol—. Ese dato entra al modelo, el flujo
reparte la carga entre tres fases que no existen, el desbalance de esa zona deja
de significar nada y la pérdida técnica que se le imputa queda mal para siempre.

El dominio dependiente del subtipo no es una ayuda al técnico: es lo que hace que
el dato imposible **no sea capturable**.

### Qué cambia al elegir el subtipo

| | |
|---|---|
| **Los dominios** | El desplegable pasa a ofrecer solo lo que ese subtipo admite |
| **Los valores por defecto** | Se rellena lo que estaba vacío, nunca lo capturado |
| **Qué campos aplican** | Los que no vienen al caso se ocultan y se vacían |
| **Qué es obligatorio** | El ducto lo es en red subterránea y no existe en aérea |

Los subtipos definidos hoy:

| Capa | Campo de subtipo | Qué gobierna |
|---|---|---|
| Puesto de transformación | `configuracion_banco` | Fases posibles y número de unidades |
| Tramo de red | `tipo_red` | Tensiones válidas, si hay ducto y profundidad |
| Cliente | `tipo_servicio` | Tarifas (DESTARI), fases y tipo de medidor |
| Luminaria | `tipo_ap` | Rango de potencia; semáforo y cámara van no medidos |
| Poste | `material` | Rango de altura razonable |

### Lo capturado no se pierde ni se falsea

Cuando el subtipo cambia, cada campo se resuelve así:

1. **Si lo capturado sigue siendo válido, se respeta.** El técnico lo vio en
   sitio; el sistema no tiene por qué opinar.
2. **Si no y el subtipo trae un defecto, se aplica y se avisa.** Un banco
   trifásico que pasa a delta abierto ve su `ABC` convertido en `AB`, y el panel
   lo dice: «Fases: ABC → AB».
3. **Si no hay defecto, se limpia y se pide volver a elegir**, mostrando lo que
   había. El campo queda marcado en rojo y, si es obligatorio, **no deja
   guardar**.

Ninguno de los tres es silencioso, a propósito. Una corrección invisible en un
teléfono es una corrección que el técnico descubre semanas después, cuando ya no
puede verificar nada. Y un obligatorio vaciado que sí dejara guardar haría que el
servidor rechazara el lote entero por la tarde, cuando ya no está delante del
elemento.

### Contingencias: cuando no depende del subtipo

El subtipo cubre lo habitual, pero no todo. El calibre de una acometida depende
de si es aérea o subterránea, y eso no es la clase de servicio del cliente. Para
eso están las reglas de contingencia, que **ganan al subtipo** por ser la
condición más específica:

```
tipo_acometida = AEREA        → calibre ∈ {dúplex 6, dúplex 4, tríplex 2, tríplex 1/0}
tipo_acometida = SUBTERRANEA  → calibre ∈ {TTU 6, TTU 4, TTU 2}
```

### Dónde viven las reglas

| | |
|---|---|
| Dominios **base** | `gpkg_data_columns` + `gpkg_data_column_constraints` — la **extensión estándar** del formato: QGIS y ArcGIS los muestran sin saber nada de este proyecto |
| Subtipos y contingencias | `ptnt_subtipo`, `ptnt_subtipo_dominio`, `ptnt_regla_dominio` — ningún formato de intercambio los expresa |
| Para la aplicación | El manifiesto del propio `.gpkg` |

Todo va **dentro del archivo que se lleva a campo**. Un formulario cuyas reglas
viven en otro sitio es un formulario que, sin señal, no tiene reglas.

En el estándar se publica el dominio **base** y no el del subtipo: es el
superconjunto correcto para un lector que no entiende subtipos, y publicar el del
subtipo haría que QGIS ocultara valores legítimos de otros subtipos de la misma
capa.

### Dos cosas que se aprendieron corriéndolo

**Sin subtipo no se supone ninguno.** Buena parte de la red llega del SIG con el
campo vacío. Asumir el primero aplicaría a esos elementos unas reglas que nadie
eligió: un tramo de baja tensión heredaría el dominio de media y sus 220 V
aparecerían como inválidos. Sin subtipo rige el dominio base. Al **crear** un
elemento sí se propone uno, porque ahí hay que elegir.

**El número que vuelve de SQLite no es el texto del dominio.** Un campo de
tensión declarado `REAL` vuelve como `220.0` y el dominio dice `"220"`. Comparar
los textos crudos rechazaba un dato correcto en cada tramo de baja tensión del
país. Las dos mitades —Python y Kotlin— normalizan igual, y tienen que seguir
haciéndolo: si divergen, el móvil acepta lo que el servidor rechaza.

### El servidor no confía en el cliente

La validación corre **también al sincronizar**, contra el elemento ya editado. No
es desconfianza del técnico: un paquete puede venir de una versión anterior de la
aplicación, de un dispositivo que no recibió el esquema nuevo, o de una edición
hecha con QGIS sobre el mismo archivo —que es legítimo y conviene que lo siga
siendo—. Validar solo en el móvil es validar donde no está la responsabilidad del
dato.

```
[BLOQUEANTE] 1 valor(es) fuera del dominio que les corresponde:
ptnt_puesto_transformacion/4ecad8fc · «fases» = ABC: no es válido para el
subtipo «Delta abierto (2 unidades)» (permitidos: AB, BC, CA).
```

El mensaje nombra el subtipo a propósito: sin eso el supervisor ve «valor
inválido» en un campo cuyo valor existe en el catálogo, y lo toma por un error
del sistema.

### Los dominios salen del catálogo, no de este archivo

`codigo_estructura` **no** se acota con una lista escrita en el esquema. Los
códigos de conductor son los de CATALOGOESTRUCTURA (`COO…`) y salen del catálogo
del cliente: inventar una lista propia obligaría a mantener dos catálogos, y el
de campo se quedaría viejo el primer mes. Con el catálogo cargado,
`dominios_conductor_desde_catalogo()` construye el dominio real; sin él, el campo
queda libre, que es lo correcto.

Por el mismo motivo las tarifas son las **descripciones de DESTARI tal como
llegan del sistema comercial**, y una prueba comprueba que cada tarifa ofrecida
clasifica en la clase que le corresponde **con el mismo clasificador que usa el
análisis**. Si alguien añade una tarifa que el clasificador no reconoce, falla la
prueba en vez de fallar callando: ese cliente entraría al análisis como «no
clasificado» y quedaría fuera de la comparación contra sus pares, que es la
señal que lo detecta.

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

# --- definir trabajo que NO sale del ranking ----------------------------
ptnt campo-definir --tipo CENSO --alimentador GYE-04    # censo completo
ptnt campo-definir --tipo VERIFICACION_MEDIDOR \
    --lista cuentas_comercial.csv                      # listado del área
ptnt campo-definir --tipo ACTUALIZACION_CARTOGRAFICA \
    --centro 631000,9762500 --radio 600                # área dibujada

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
    ├── build.gradle.kts            minSdk 24, R8 + reducción de recursos
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── res/                 cadenas, tema de arranque, icono vectorial
        │   └── java/ec/cnel/ptnt/field/
        │       ├── MainActivity.kt              actividad única, permisos, cámara
        │       ├── data/GeoPackageDao.kt        SQLite directo + códec GeoPackage
        │       ├── data/EsquemaFormulario.kt    formularios leídos del manifiesto
        │       ├── data/RepositorioCampo.kt     ciclo de vida del paquete abierto
        │       ├── domain/EditorTopologico.kt   snap, propagación, diario
        │       ├── domain/CapturaFoto.kt        EXIF, ubicación, hash
        │       ├── geo/Utm.kt                   UTM 17S ⇄ WGS84
        │       ├── geo/ProveedorUbicacion.kt    GPS con precisión a la vista
        │       ├── geo/ServidorTeselas.kt       cartografía offline por loopback
        │       ├── sync/ClienteSincronizacion.kt   descarga y subida
        │       ├── sync/AlmacenSesionCifrado.kt    token en Keystore
        │       ├── work/TrabajadorSincronizacion.kt subida al recuperar señal
        │       └── ui/
        │           ├── CampoViewModel.kt        estado de la jornada
        │           ├── Tema.kt                  alto contraste, tipografía grande
        │           ├── PantallaVinculacion.kt   alta del equipo
        │           ├── PantallaOrdenes.kt       «¿qué me toca hoy?»
        │           ├── PantallaCampo.kt         adaptativa tablet/teléfono
        │           ├── PantallaTrabajo.kt       mapa + atributos + cierre
        │           ├── MapaCampo.kt             MapLibre, consulta por ventana
        │           └── PanelAtributos.kt        formulario, relacionados, fotos
        └── test/java/ec/cnel/ptnt/field/
            ├── UtmTest.kt                      proyección y ida y vuelta
            └── GeometriaTest.kt                contrato binario con el backend
```

### Compilar

```bash
cd mobile
./gradlew assembleDebug        # APK de pruebas
./gradlew test                 # pruebas JVM (proyección y códec)
./gradlew assembleRelease      # APK firmado, con R8
```

El APK sale en `app/build/outputs/apk/`. No hace falta clave de ArcGIS ni de
ningún proveedor de mapas: las teselas viajan dentro del paquete de trabajo.

### Dos piezas que sostienen todo lo demás

**Proyección.** La red está en UTM 17S —así se levantó y así se calculan las
longitudes en metros— y MapLibre dibuja en latitud/longitud. Convertir en el
servidor no serviría: en campo se edita sin señal y cada arrastre de dedo produce
una coordenada nueva. `geo/Utm.kt` implementa la serie de Snyder para una zona y
una elipsoide, en vez de traer proj4j con sus tablas. Error de ida y vuelta
medido: **menos de 1 mm**, tres órdenes de magnitud por debajo del GPS del
teléfono.

**Códec de geometría.** Lo que el móvil escribe tiene que ser exactamente lo que
Python lee. Se verificó que ambos productores generan el **mismo binario byte a
byte** para punto y línea, y que cada uno lee lo del otro. Un error aquí no da
un fallo visible: da elementos desplazados después de sincronizar, cuando el
técnico ya no está en el sitio.

**Dependencias clave** (todas libres): MapLibre GL Native, AndroidX
ExifInterface, CameraX, OkHttp, Security-Crypto, WorkManager.

**No se usa Room ni una capa ORM**: el GeoPackage llega del servidor con su
esquema ya definido, y una capa de mapeo obligaría a declarar las entidades en el
código y a publicar una versión nueva cada vez que el backend agregue un campo.
Leyendo el esquema del propio archivo, la app se adapta sola.

---

## 14.bis Comparación con ArcGIS Field Maps

La pregunta de fondo es si esto cubre lo que cubre Field Maps sin pagar ArcGIS.
Lo que sigue es honesto en las dos direcciones:

| Capacidad de Field Maps | Aquí |
|---|---|
| Mapa con capas y símbolos | **Sí** — MapLibre GL, render por GPU |
| Trabajo sin conexión (áreas offline) | **Sí** — el paquete es autocontenido: red, cartografía y formularios |
| Formularios con dominios | **Sí** — definidos en el backend, sin publicar versión de la app |
| **Subtipos que cambian los dominios** | **Sí** — §3.bis |
| Dominios codificados (código ≠ etiqueta) | **Sí** — se guarda el código, se muestra la descripción |
| Dominios de rango (mín/máx) | **Sí** — con aviso mientras se teclea |
| Valores por defecto por subtipo | **Sí** — solo rellenan lo vacío |
| Contingencias (un campo condiciona a otro) | **Sí** — §3.bis |
| Campos que dejan de aplicar según el tipo | **Sí** — se ocultan y se vacían |
| Búsqueda por atributo | **Sí** — cuenta, código, serie, ruta |
| Edición de atributos y geometría | **Sí** |
| Snap a elementos existentes | **Sí** |
| Relaciones entre entidades (related records) | **Sí** — Puesto→Unidad, editable desde el mismo panel |
| Adjuntos (fotos) | **Sí** — con ubicación, hora, autor y hash SHA-256 |
| Captura de ubicación del dispositivo | **Sí**, con la precisión a la vista |
| Interfaz adaptativa tablet/teléfono | **Sí** — lado a lado / hoja deslizante |
| Sincronización bidireccional | **Sí**, con revisión humana obligatoria antes de aplicar |
| Cartografía base propia o de terceros | **Sí** — MBTiles libres o caché de ArcGIS Server |
| **Reconexión topológica con recálculo del balance** | **Sí** — esto Field Maps no lo hace: no conoce el modelo eléctrico |
| **Invalidación selectiva del análisis** | **Sí** — un cambio dice qué etapas rehacer |
| **Validación de dominios también en el servidor** | **Sí** — Field Maps confía en el cliente |
| Visibilidad condicional por expresión (Arcade) | **No** — se cubre el caso por subtipo, no la expresión libre |
| Valores calculados por expresión en el formulario | **No** — los calculados los produce el backend |
| Secciones plegables en el formulario | **No** — el orden por relevancia cumple la misma función |
| Lectura de código de barras / QR | **No** — el medidor se teclea o se fotografía |
| Filtros de capa definidos por el usuario | **No** — el paquete ya viene recortado a su trabajo |
| Marcadores y medición sobre el mapa | **No** |
| Rastreo de recorrido del técnico | **No** — fuera de alcance |
| Mapas 3D / escenas | **No** |
| Utility Network de Esri | **No** — el grafo de conectividad es propio y más simple |
| Marketplace de mapas base en línea | **No** — la cartografía se prepara y se empaqueta |

De lo que **no** está, tres cosas son decisiones y el resto son huecos:

* **Rastrear al técnico** es una decisión laboral que no corresponde a esta
  herramienta.
* **Utility Network** resolvería un problema que este proyecto no tiene: el grafo
  que hace falta cabe en una tabla de cinco columnas.
* **Los valores calculados por expresión** se dejan fuera a propósito: el score
  de sospecha o el consumo promedio los calcula el backend con la historia
  completa, y una fórmula en el teléfono daría un número distinto al del informe.

Los demás —Arcade para visibilidad, secciones plegables, código de barras,
filtros de usuario, marcadores, medición— son huecos reales, no decisiones. Se
listan aquí para que nadie los descubra en la calle.

Lo que **sí** está y Field Maps no puede dar es la parte que conoce el dominio:
un cambio de conexión no es «un atributo editado», es energía que se mueve de una
zona de balance a otra, y el sistema lo sabe.

---

## 15. Estado de la entrega

| Componente | Estado |
|---|---|
| Escritor/lector GeoPackage OGC | **Completo y probado** |
| Esquema del paquete (13 capas) | **Completo** |
| Subtipos, contingencias y dominios de rango | **Completo y probado** (backend) |
| Kotlin: subtipos y dominios efectivos en el formulario | **Escrito; sus pruebas no se han ejecutado** (hace falta Gradle y SDK de Android) |
| Motor topológico backend | **Completo y probado** |
| Órdenes, asignación, máquina de estados | **Completo y probado** |
| Reconexión de consumidor con recálculo de dos zonas | **Completo y probado** |
| Definición de trabajo por alimentador, sector, área y lista | **Completo y probado** |
| Trabajos de varios días con avance parcial | **Completo y probado** |
| Almacén transaccional y concurrencia | **Completo y probado** (68 pruebas de campo) |
| Reparto entre varias cuadrillas | **Completo y probado** |
| Paquetes en lote | **Completo y probado** |
| Armado del paquete con recorte | **Completo y probado** |
| Sincronización, validación, revisión | **Completo y probado** |
| Recálculo selectivo | **Completo y probado** |
| Histórico de modificaciones | **Completo y probado** |
| API móvil (FastAPI) | **Completa y probada con varios usuarios a la vez** |
| Interfaz web de asignación y revisión | **Completa** |
| CLI de campo (8 comandos) | **Completa** |
| Kotlin: DAO, editor topológico, fotos, sincronización | **Completo** |
| Kotlin: proyección UTM y códec de geometría | **Completo y probado** (contrato verificado contra Python) |
| Kotlin: pantallas (vinculación, órdenes, mapa, atributos) | **Completas** |
| Kotlin: MapLibre con cartografía offline del paquete | **Completo** |
| Kotlin: fotos con ubicación, hora y hash | **Completo** |
| Kotlin: subida diferida al recuperar señal | **Completa** |
| Pruebas instrumentadas en dispositivo | **Pendiente** |

El backend, el contrato de datos y la aplicación Android están escritos: el ciclo
completo —vincular, descargar, trabajar sin señal, editar con snap y propagación,
fotografiar con evidencia, cerrar la orden y sincronizar— está implementado.

Lo que falta es lo que **no se puede hacer sin equipos reales**: las pruebas
instrumentadas en dispositivo. La proyección y el códec binario, que son las
piezas donde un error pasa inadvertido, sí están verificados de forma
independiente; el render, los permisos y el comportamiento del GPS bajo cobertura
real solo se pueden comprobar en la calle.

---

Ver también: [Especificación completa](ESPECIFICACION_COMPLETA.md) ·
[Focalización](FOCALIZACION.md) · [Guía de operación](GUIA_OPERACION.md)
