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

Sin dependencias externas y respetando contenedores:

| | |
|---|---|
| Núcleos | `sched_getaffinity` en Linux — `os.cpu_count()` devolvería los del anfitrión en un contenedor con `--cpuset-cpus` y sobredimensionaría el pool |
| Memoria | `psutil` si está; si no, `/proc/meminfo` (Linux), `GlobalMemoryStatusEx` (Windows) o `sysctl` (macOS) |
| Si no se puede medir | Se asume un equipo modesto: equivocarse por abajo cuesta velocidad, por arriba cuesta el trabajo entero |

Se usa `MemAvailable` y no `MemFree`: la segunda se queda corta porque ignora la
caché reclamable, y dejaría el servidor trabajando a un tercio de su capacidad.

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
2. **No hay prioridades entre tareas.** La cola es FIFO. Si hiciera falta que un
   alimentador crítico se adelante, es un cambio pequeño pero no está hecho.
3. **La medición de memoria es del proceso, no del contenedor.** En un contenedor
   con `--memory`, `/proc/meminfo` sigue mostrando la del anfitrión. Con `psutil`
   instalado tampoco lo resuelve; ahí hay que fijar `max_trabajadores` a mano.
4. **No se reintenta automáticamente una tarea fallida.** Se reporta y se sigue:
   reintentar un alimentador con datos corruptos solo consume el lote dos veces.

---

Ver también: [Arquitectura](ARQUITECTURA.md) ·
[Instalación en Windows](INSTALACION_WINDOWS.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Pruebas](PRUEBAS.md)
