# Cómo se usa el sistema: interfaces y actualización automática

Respuesta corta a la pregunta «¿hay una interfaz para ejecutar las cosas?»:
**sí, el tablero**. Desde el navegador se lanza el cálculo, se sigue su avance,
se analizan los resultados, se manda trabajo a campo, se ve el histórico y se
programan las actualizaciones automáticas. No hace falta escribir ningún
comando.

```bash
ptnt dashboard          # el tablero completo
ptnt servir-visor       # visor de solo lectura, para consultar sin tocar
ptnt campo-servir       # API para la aplicación móvil de las cuadrillas
```

---

## 1. Las tres interfaces, y para quién es cada una

| Interfaz | Quién la usa | Qué puede hacer |
|---|---|---|
| **Tablero** (Streamlit) | Analista, jefe de pérdidas, matriz | Todo: ejecutar, analizar, mandar a campo, programar |
| **Visor** (FastAPI) | Quien solo consulta | Ver. **No tiene ni un solo punto de escritura** |
| **App Android** | Cuadrillas | Levantar en campo, sin señal |

Y la **línea de comandos**, que sigue existiendo para quien la prefiera o para
automatizar. No es una segunda implementación: el tablero **invoca la CLI**, de
modo que lo que calcula un botón es exactamente lo que calcula el comando.

---

## 2. El tablero, pestaña a pestaña

| Pestaña | Para qué |
|---|---|
| ▶️ **Actualizar los números** | Lanzar el cálculo y ver cómo va |
| 📍 Dónde inspeccionar | El plan de levantamientos, ordenado por lo que rinde cada visita |
| 🎯 Sospecha de hurto | El ranking de clientes, con sus razones |
| 🔌 Reconciliación de potencia | SIG contra corregido, y por qué difieren |
| 📈 Cliente | La ficha de una cuenta concreta |
| ⚖️ Balance de red | Pérdidas técnicas y PNT del alimentador |
| 🏢 Unidad y subestación | El consolidado, con su tipo de balance |
| 📅 Histórico | La evolución en el tiempo, período a período |
| 📥 Carga de datos | Qué se ha cargado y qué falta |
| 📱 Trabajo de campo | Técnicos, reparto, paquetes y revisión |
| 🧪 **Probar cambios** | Escenarios: ver el balance de un cambio sin publicarlo |
| ⏰ **Automático** | Las actualizaciones periódicas |

### Ejecutar sin escribir comandos

La pestaña **Actualizar los números** presenta cuatro opciones en lenguaje
llano, no ocho comandos:

| Opción | Qué hace | Cuándo |
|---|---|---|
| Proceso completo | De la conexión a las órdenes de campo | Al empezar un ciclo |
| Actualizar los números | Datos y balance, sin rehacer el plan de campo | Mes a mes |
| Solo el balance | Red y pérdidas | Cambió la red y se quiere ver el efecto |
| Preparar trabajo de campo | Credibilidad y focalización | Antes de mandar cuadrillas |

Antes de arrancar, la pantalla dice **qué va a ocurrir, paso a paso, y cuánto
tarda**. Al pulsar, el cálculo se lanza **aparte**: se puede cerrar la ventana e
irse: no muere con la pestaña del navegador ni con la conexión.

Tres decisiones que se notan al usarlo:

- **Un paso que falla detiene la cadena.** Seguir calcularía el balance sobre una
  red que no se llegó a traer, y el número saldría con toda naturalidad. Los
  pasos posteriores se marcan como no ejecutados y se dice por qué.
- **El resumen es el resultado, no el diario técnico.** Las trazas de registro y
  los bordes de tabla se descartan de la vista; la salida completa se guarda para
  quien tenga que diagnosticar.
- **La primera vez no hay callejón sin salida.** Cuando no hay resultados
  calculados, el tablero ofrece el botón de empezar. Antes decía «ejecute este
  comando» a quien había entrado por el navegador justamente para no escribir
  comandos.

### Cada quien ve lo suyo

Al entrar, el sistema fija el **alcance** del usuario: las unidades de negocio
que le corresponden. Las tablas y las cifras de portada salen filtradas — no solo
las tablas: el número de «cuentas analizadas» es el de su unidad, no el de la
empresa, porque enseñarle el total llevaría a informar un número que no es suyo.

La **matriz** ve todas y, en la barra lateral, **elige** cuál mirar. Elegir y
acumular son dos preguntas distintas: «¿cómo va la empresa?» y «¿cómo va
Milagro?» no se responden con la misma tabla.

Un usuario **sin unidad asignada no ve nada**, y la pantalla le dice por qué y a
quién pedírselo. Es deliberado: lo contrario convertiría un alta a medias en el
padrón de otra unidad en manos de quien no debe. Ver
[SEGURIDAD.md §2.bis](SEGURIDAD.md).

---

## 3. Actualización automática

**El programa no se queda corriendo en segundo plano.** La cita la guarda el
**Programador de tareas de Windows**, y este sistema se limita a definir qué hay
que hacer y a generar la línea exacta que hay que registrar allí.

Es a propósito. Un servicio propio habría que vigilarlo, arrancarlo con la
máquina y explicar por qué un martes no se ejecutó. El Programador de Windows ya
hace eso, sobrevive a los reinicios y tiene su propio registro de errores.
Escribir uno peor no le sirve a nadie.

### Cómo se deja programada, paso a paso

**Desde el tablero** — pestaña *Automático* → *Crear una actualización
automática*. Se elige qué debe hacer y cada cuánto, y la pantalla devuelve la
línea a pegar.

**Desde la consola:**

```bash
ptnt tarea-crear actualizacion-diaria --plan actualizar --hora 03:00
```

Ambos caminos imprimen algo así:

```
schtasks /Create /TN "PTNT-BAL\actualizacion-diaria"
         /TR "cmd /c cd /d \"C:\PTNT\" && \"C:\ArcGIS\python.exe\" -m ptnt.cli
              tarea-ejecutar actualizacion-diaria --config \"C:\PTNT\config\base.yaml\""
         /SC DAILY /ST 03:00 /RL HIGHEST /F
```

1. Inicio → escriba `cmd` → clic derecho → **Ejecutar como administrador**.
2. Pegue la línea completa y pulse Intro.
3. Debe responder «CORRECTO: se ha creado la tarea programada».
4. Compruébelo en **Programador de tareas**, carpeta `PTNT-BAL`.

Dos detalles que evitan el fallo típico de las tres de la mañana: la ruta de
configuración va **absoluta** —el planificador no arranca desde la carpeta del
proyecto—, y la orden se envuelve en `cmd /c cd /d` porque `schtasks` no sabe
fijar la carpeta de trabajo y, sin eso, los resultados irían a parar a
`C:\Windows\System32`.

### Probarla sin esperar a mañana

```bash
ptnt tarea-ejecutar actualizacion-diaria
```

### Qué se puede programar

| Frecuencia | Uso habitual |
|---|---|
| **Diaria** (03:00) | Refrescar datos y balance |
| **Semanal** | Rehacer el plan de campo |
| **Mensual** (día 1–28) | Cierre: consolidar y guardar el punto del histórico |

El día del mes se corta en **28** a propósito: del 29 al 31 hay meses que no lo
tienen, y la tarea se saltaría sin avisar justo en febrero, que es cierre.

### Saber si de verdad se ejecutó

El planificador sabe si **lanzó** el proceso; no sabe si el proceso **hizo lo que
debía**. Eso lo dice la bitácora, y el sistema cruza las dos cosas: una tarea que
debía haber corrido y no dejó rastro sale marcada **«se saltó su cita»**, en el
tablero y en `ptnt tarea-listar`.

```bash
ptnt tarea-listar      # cuándo toca cada una, y si alguna falló
ptnt ejecuciones       # las últimas corridas, con su resultado
```

Una tarea recién creada nunca sale en rojo: no puede haberse saltado una cita
anterior a su propia creación. Sin ese detalle toda tarea nueva nacería marcada y
la señal dejaría de significar nada.

> **Al borrar una tarea aquí no se borra del Programador de Windows.** El sistema
> lo advierte y da la orden para quitarla allí. Son dos registros distintos y
> fingir que son uno acabaría con tareas fantasma ejecutándose solas.

---

## 4. Analizar, mandar a campo y ver la evolución

**Analizar.** *Dónde inspeccionar* da el plan ordenado por energía recuperable
por visita —no por score—, porque una cuadrilla tiene ocho horas y lo que importa
es lo que rinde cada parada. *Sospecha de hurto* da el ranking con las razones en
lenguaje operativo: «consumo cero con servicio activo desde marzo», no
«score 0,87».

**Mandar a campo.** En *Trabajo de campo*: dar de alta técnicos, repartir la
jornada entre cuadrillas equilibrando carga y cercanía, generar un paquete
GeoPackage por técnico y revisar lo que vuelve. Nada entra al modelo sin revisión
humana.

**Ver la evolución.** *Histórico* compara períodos y marca la tendencia
(MEJORA / ESTABLE / EMPEORA). Dos puntos calculados con configuraciones distintas
salen marcados **no comparables**: atribuir a la red una variación que fue de
parámetros es una forma silenciosa de mentir con un gráfico.

Y en *Probar cambios*, cada evaluación de un escenario queda como una iteración
que no se sobrescribe, con su gráfico de evolución. Ver
[ESCENARIOS.md](ESCENARIOS.md).

---

## 5. Los comandos, por si se prefieren

| Comando | Equivalente en el tablero |
|---|---|
| `ptnt pasos` | Ver qué se puede ejecutar |
| `ptnt ejecutar --plan actualizar` | ▶️ Actualizar los números |
| `ptnt ejecuciones` | El historial de esa misma pestaña |
| `ptnt tarea-crear` / `tarea-listar` / `tarea-ejecutar` | ⏰ Automático |
| `ptnt escenario-*` | 🧪 Probar cambios |
| `ptnt usuarios` / `usuario-unidad` | Alcance de cada usuario |

La lista completa está en `ptnt --help` y en
[ESPECIFICACION_COMPLETA.md §4.3](ESPECIFICACION_COMPLETA.md).

---

## 6. Lo que no está

- **El tablero no se refresca solo** mientras corre un cálculo: hay un botón de
  actualizar. Un refresco automático obligaría a mantener la página abierta, que
  es justo lo que se quería evitar al lanzar el proceso aparte.
- **`schtasks` no se ha ejecutado en este entorno** (no hay PowerShell ni
  Windows). La línea que se genera está construida y probada por sus piezas
  —rutas absolutas, cambio de carpeta, día de la semana, hora—, pero el registro
  efectivo en un Windows Server real está sin verificar.
- **No hay reparto entre varias máquinas.** El diseño es de servidor único, como
  pide la especificación.
