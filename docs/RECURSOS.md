# Ajuste a los recursos del equipo

Cómo la plataforma decide **cuánto hacer a la vez** y qué pasa con lo que no cabe.

```bash
ptnt recursos            # qué hay y cuánto cabe en este equipo
ptnt recursos --medir    # mide el coste real de un alimentador
python scripts/demo_recursos.py    # lo demuestra funcionando
```

---

## 1. La memoria manda, no los núcleos

La pregunta operativa no es «¿cuántos núcleos tiene el servidor?» sino «¿cuántos
alimentadores puedo procesar a la vez sin que el sistema empiece a paginar?».

Un alimentador urbano con 200 000 nodos ocupa cientos de megabytes mientras se
resuelve el flujo. En una máquina de 16 núcleos y 16 GB, lanzar 16 procesos **no
da 16 veces la velocidad: da swap**, y con swap el lote tarda más que en
secuencial. Peor: el sistema operativo puede matar procesos y perder horas de
cálculo sin decir por qué.

```
trabajadores = min(núcleos_utilizables, memoria_utilizable / coste_por_tarea)
```

En el equipo de pruebas (4 núcleos, 15,4 GB libres):

| Coste por alimentador | Trabajadores | Limita |
|---:|---:|---|
| 128 MB | 4 | cpu |
| 512 MB | 4 | cpu |
| 2 048 MB | 4 | cpu |
| 8 192 MB | **1** | **memoria** |

Nunca baja de 1: siempre hay que poder procesar aunque sea de a uno. Quedarse sin
hacer nada es peor que ir despacio.

### La reserva del sistema no se toca

`ram_reservada_mb` es lo que la plataforma **nunca** usa: el sistema operativo,
la base de datos y **la API móvil, que debe seguir respondiendo mientras se
recalcula un lote**. Sin esa reserva, un recálculo pesado deja a las cuadrillas
sin poder descargar su trabajo.

### Detección

Sin dependencias externas:

| | |
|---|---|
| Núcleos | `sched_getaffinity` en Linux — `os.cpu_count()` devolvería los del anfitrión en un contenedor con `--cpuset-cpus` y sobredimensionaría el pool |
| Memoria | `psutil` si está; si no, `/proc/meminfo` (Linux), `GlobalMemoryStatusEx` (Windows) o `sysctl` (macOS) |
| Si no se puede medir | Se asume un equipo modesto: equivocarse por abajo cuesta velocidad, por arriba cuesta el trabajo entero |

Se usa `MemAvailable` y no `MemFree`: la segunda se queda corta porque ignora la
caché reclamable, y dejaría el servidor trabajando a un tercio de su capacidad.

### Dentro de un contenedor manda el contenedor

Ni `psutil` ni `/proc/meminfo` ven el cgroup: dentro de un contenedor con
`--memory=2g` **los dos siguen mostrando la memoria del anfitrión**. La
plataforma creería tener 64 GB, lanzaría treinta alimentadores y el núcleo
mataría el contenedor entero — sin mensaje de error que explique nada.

Lo mismo con la CPU: `--cpus=1.5` no toca la afinidad sino la **cuota CFS**, así
que `sched_getaffinity` no lo ve y se lanzarían tantos procesos como núcleos
tenga el anfitrión, todos estrangulados por el planificador.

Por eso se leen los límites del cgroup —v1 y v2— y se toma **el menor de los
dos**:

| | cgroup v2 | cgroup v1 |
|---|---|---|
| Memoria | `memory.max`, `memory.current` | `memory/memory.limit_in_bytes`, `memory/memory.usage_in_bytes` |
| Reclamable | `memory.stat`: `inactive_file`, `slab_reclaimable` | `memory/memory.stat`: `total_inactive_file` |
| CPU | `cpu.max` (cuota y periodo) | `cpu/cpu.cfs_quota_us`, `cpu/cpu.cfs_period_us` |

Tres detalles que deciden si el número sale bien:

1. **Lo libre no es `límite − usado`.** Buena parte de lo usado es caché de
   ficheros que el núcleo devuelve en cuanto alguien pide memoria. Restarla sin
   más dejaría la plataforma procesando de a un alimentador después de leer un
   GeoPackage grande.
2. **Un cgroup más grande que el anfitrión no infla nada.** Puede declarar 1 TB
   en un equipo de 16 GB; ahí el que manda es el anfitrión.
3. **La cuota se redondea hacia abajo, pero nunca a cero.** Con 1,5 núcleos se
   trabaja de a uno: dos procesos no dan 1,5 veces la velocidad, dan cambios de
   contexto.

`ptnt recursos` lo dice cuando pasa:

```
Se está dentro de un contenedor (cgroup v2): manda su límite, no lo que tenga
el anfitrión.
```

Sin ese aviso, quien vea 2 GB en un servidor de 64 pensará que la plataforma
mide mal y subirá los límites a mano hasta que el núcleo mate el contenedor.

---

## 2. Muchos alimentadores: se procesa lo que cabe, se encola el resto

24 alimentadores en un equipo de 4 núcleos:

| Trabajadores | Tiempo | Procesados | Espera máxima en cola |
|---:|---:|---:|---:|
| 1 | 0,44 s | 24/24 | 0,43 s |
| 2 | 0,25 s | 24/24 | 0,23 s |
| 4 | **0,14 s** | 24/24 | 0,12 s |

**Las 24 se procesan siempre.** Lo que cambia es cuántas a la vez y cuánto espera
la última. Esa espera se mide y se reporta: es el argumento **con número** para
pedir más máquina.

Cuatro decisiones que gobiernan el ejecutor:

1. **Solo hay N tareas vivas a la vez.** Nunca se crean 500 futuros para 500
   alimentadores; el planificador mantiene la ventana llena y va tomando de la
   cola. Materializar todo consumiría memoria antes de calcular nada.

2. **Se vuelve a mirar la memoria antes de admitir cada tarea.** El presupuesto
   se calcula al empezar, pero el equipo cambia: alguien abre el tablero, arranca
   un respaldo. Si el margen se agotó, la tarea espera. Hay tope de espera —
   bloquear el lote para siempre sería peor que arriesgarse.

3. **Un fallo no cancela el lote.** Un alimentador con datos corruptos no puede
   arruinar las otras 499 horas de cálculo. Se registra, se sigue, y al final se
   reporta qué falló y por qué.

4. **Al trabajador no se le manda el modelo, se le manda dónde encontrarlo.**
   Serializar una red de 200 000 nodos para enviarla a un proceso hijo cuesta más
   que calcularla, y duplica la memoria justo cuando se intenta ahorrarla.

Y un detalle que en la práctica decide el rendimiento: cada proceso hijo arranca
con **un hilo de BLAS**. N procesos × N hilos de OpenBLAS es sobresuscripción, y
el equipo se pasa el tiempo cambiando de contexto en vez de calculando.

### La cola no es solo FIFO: lo urgente se adelanta

En un recálculo de una unidad de negocio entera el analista no está esperando los
400 alimentadores: está esperando **unos pocos** —el de la campaña de campo en
curso, el de la PNT más alta—. Y como los resultados se entregan según terminan,
el orden de la cola es literalmente el tiempo de esa persona.

```python
TareaAlimentador(feeder_code="GYE-04", ruta_config="config/base.yaml",
                 prioridad=10)      # a mayor número, antes se atiende
```

200 tareas rutinarias más una urgente **entrada la última**:

| | Sale en la posición |
|---|---:|
| Sin prioridad (FIFO) | 201 |
| Con prioridad | **1** |

Quien no use prioridades no nota nada: sin prioridad declarada todas valen 0 y el
desempate es el orden de llegada, es decir, FIFO exacto.

**Dónde está el límite.** Ordenar globalmente exigiría agotar la fuente antes de
empezar, y con una fuente perezosa de 5 000 alimentadores eso es materializarla
entera. Así que se ordena dentro de una **ventana de admisión** de 1 024 tareas:
lo que la cola guarda es la clave y los argumentos —un código de alimentador y
una ruta de YAML, jamás el modelo de red—, y mil de esos pares son unos cientos
de kilobytes. Para cualquier lote real el orden es el global; solo con una fuente
perezosa más larga que la ventana una tarea urgente en la posición 5 000 no puede
adelantar a las 1 000 primeras, porque todavía no se la ha visto.

### Se reintenta lo pasajero, nunca lo corrupto

Un corte de red de dos segundos en **una** de once bases no puede costar la
ingesta entera de esa unidad de negocio. Pero reintentar un alimentador con datos
malos solo consume el lote dos veces para fallar igual.

| Se reintenta | No se reintenta |
|---|---|
| `ConnectionError`, `TimeoutError`, `BrokenPipeError` | `ValueError`, `KeyError` — el dato está mal |
| `OSError` con `ECONNRESET`, `ETIMEDOUT`, `EHOSTUNREACH`… | `FileNotFoundError`, `PermissionError` — la `.gdb` seguirá ausente dentro de dos segundos |
| `OperationalError`, `InterfaceError` de los conectores | `DatabaseError`: en Oracle cubre «la tabla no existe» |

La clasificación se hace **en el trabajador**, donde la excepción todavía es un
objeto: al proceso padre solo llega el texto, y decidir por el texto si merece
reintento sería adivinar. Los conectores se reconocen por nombre de clase porque
importar `cx_Oracle` para clasificar su error obligaría a instalarlo solo para
eso, y el que falla vive en el proceso hijo.

La espera entre reintentos se duplica (2 s, 4 s, 8 s): volver de inmediato contra
una base que se está recuperando es lo que impide que se recupere. Y un reintento
vencido **se adelanta** en la cola: esa lectura ya tiene a alguien esperándola.

Lo que sale bien pero no a la primera se reporta aparte:

```
lecturas: 2 fuente(s) solo respondieron tras reintentar (UN-02, UN-07).
Revise el enlace antes de que deje de responder del todo.
```

Sin ese aviso el lote termina en verde y el enlace inestable queda invisible
hasta la mañana en que ya no responde.

---

## 3. Once unidades de negocio: lectura en paralelo

| Lecturas simultáneas | Tiempo (11 bases) |
|---:|---:|
| 1 | 3,30 s |
| 6 | 0,60 s |
| 11 | **0,31 s** |

En serie son once veces la latencia sumada; en paralelo, el tiempo de la base más
lenta.

**No se limita por núcleos.** Leer de una base se pasa el tiempo esperando a la
red, no calculando: cuatro núcleos no impiden tener once conexiones abiertas.
Atarlo al número de núcleos dejaría la ingesta a un tercio de su velocidad por un
límite que no aplica. Van en **hilos**, que no pagan el coste de arrancar un
intérprete.

Lo que sí limita es `max_por_fuente`: once unidades pueden apuntar a la misma
instancia Oracle, y un límite solo global permitiría seis conexiones contra la
misma base. Así se agotan las sesiones y el DBA corta el acceso a media mañana.

---

## 4. Muchos dispositivos: control de admisión

La API móvil no sirve JSON pequeños: entrega paquetes de decenas de megabytes y
recibe paquetes de retorno que hay que **abrir, validar y recorrer**. A las siete
de la mañana salen todas las cuadrillas; a las cinco de la tarde vuelven juntas.

12 cuadrillas sincronizando a la vez, con límite de 2 y cola de 4:

```
atendidas .......... 6
con reintento ...... 6
pico simultáneo .... 2   (nunca más de 2)
```

| Operación | Simultáneas | Por qué |
|---|---:|---|
| Consultas de órdenes | 16 | Baratas, y es lo primero que hace el técnico. Si se saturan, la app parece caída aunque todo lo demás funcione |
| Descargas de paquete | 4 | Leer un archivo ya construido |
| Subidas de trabajo | 2 | Abrir SQLite y recorrer el diario: mucho más caro |

Porteros **separados** a propósito: con un límite común, diez descargas baratas
bloquearían una subida, que es la operación que no se puede perder.

### Qué pasa cuando no cabe

1. **Espera en cola** hasta `espera_maxima_s`.
2. Si la cola se llena o se agota la espera → **503 con `Retry-After`**, que la
   app entiende y reintenta sola.
3. El reintento sugerido **escala con la cola**: si cuarenta rechazados vuelven
   en el mismo segundo, la saturación se repite en bucle en vez de resolverse.

Una cola infinita no es amabilidad: es un timeout de dos minutos en el teléfono
del técnico y un servidor acumulando peticiones que ya nadie está esperando.

### El trabajo del día no se pierde nunca

En la subida, **el archivo se guarda antes de pedir turno**. Si no hay capacidad,
se responde 503 pero el paquete ya está a salvo en el servidor:

```json
{
  "recibido": true,
  "procesado": false,
  "mensaje": "Su trabajo quedó guardado en el servidor, pero hay demasiadas
              sincronizaciones en curso. Reintente en 20 segundos.",
  "reintentar_en_s": 20
}
```

Decírselo importa: si el técnico cree que se perdió, lo vuelve a capturar todo a
mano.

### Métricas

`GET /movil/estado` expone la carga real:

```json
{"capacidad": {"subidas": {
  "limite": 2, "en_curso": 0, "en_cola": 0,
  "atendidas": 6, "rechazadas": 6,
  "espera_media_s": 0.25, "espera_maxima_s": 0.501,
  "pico_en_curso": 2, "pico_en_cola": 4}}}
```

No son adorno: si el pico de cola se acerca al tope todos los días a las cinco de
la tarde, el equipo se quedó corto y hay número para justificarlo.

---

## 5. Configuración

Todo se detecta solo. La sección `recursos` del YAML existe para cuando el
servidor **comparte** con otro servicio:

```yaml
recursos:
  cpus: null                  # vacío = todos los utilizables
  max_trabajadores: 0         # tope absoluto; 0 = sin tope
  ram_reservada_mb: 2048      # lo que nunca se toca
  fraccion_ram_utilizable: 0.75
  coste_mb_por_tarea: 512     # MÍDALO, no lo adivine
  espera_maxima_s: 30.0

  ventana_prioridad: 0        # 0 = automático (1024)
  reintentos_lectura: 2       # solo fallos pasajeros
  espera_reintento_s: 2.0     # y se duplica en cada reintento

  descargas_simultaneas: 4
  subidas_simultaneas: 2
  max_en_cola_api: 32

  lecturas_simultaneas: 6
  max_por_fuente: 2
```

### Medir el coste, no adivinarlo

```bash
ptnt recursos --medir
```

Ponerlo bajo hace que el sistema lance de más y acabe paginando; ponerlo alto
desperdicia núcleos. La medición usa `tracemalloc` sobre un alimentador
sintético: da el pico de asignación de Python, que es lo que multiplica el
paralelismo. **Un alimentador urbano real consume más que el sintético**: mida
con el suyo antes de producción y deje margen.

---

## 6. Qué no hace

Dicho explícitamente para que nadie lo suponga:

1. **No hay reparto entre varias máquinas.** El diseño es de servidor único, como
   pide la especificación. Escalar a un clúster exigiría una cola distribuida y
   almacenamiento compartido — otra arquitectura, no un parámetro.
2. **La prioridad ordena dentro de una ventana, no globalmente.** Con una fuente
   perezosa más larga que 1 024 tareas, lo urgente que aún no se ha leído no
   puede adelantar a lo que ya está en la ventana. Para lotes reales —una unidad
   de negocio son cientos de alimentadores— el orden es el global.
3. **No se reintenta lo que falla por los datos**, y es deliberado: repetir un
   alimentador con el padrón mal formado gasta el lote dos veces para fallar
   igual. Solo se reintenta lo pasajero.
4. **Un proceso muerto por memoria no se reintenta.** Llega como excepción del
   futuro, no como resultado clasificable, y si el pool se rompió reenviar solo
   reproduce el mismo error tantas veces como tareas queden.
5. **En Windows y macOS no hay cgroups que leer.** El ajuste al contenedor es de
   Linux; en los demás se usa lo que reporte el sistema operativo.

---

Ver también: [Arquitectura](ARQUITECTURA.md) ·
[Instalación en Windows](INSTALACION_WINDOWS.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Pruebas](PRUEBAS.md)
