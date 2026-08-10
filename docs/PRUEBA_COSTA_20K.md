# Prueba a escala — 20 000 clientes, Costa ecuatoriana

Segundo grupo de prueba, independiente de la demo de 1 200 clientes. Ejecuta el
proceso **completo** sobre un escenario de 20 000 clientes con las
características de una unidad de negocio costera de CNEL EP, y emite un
**informe HTML + PDF por etapa** hasta responder la pregunta operativa: *¿dónde
hay que ir a hacer el levantamiento — por alimentador, ramal, transformador o
sector?*

```bash
python scripts/prueba_costa_20k.py
```

Tarda **≈ 55 segundos** y deja todo en `outputs/costa20k/`.

Opciones: `--clientes N`, `--salidas RUTA`, `--datos RUTA`.

---

## Por qué un escenario costero y no uno genérico

Las diferencias no son ambientación: **cambian el resultado del análisis**.

| Característica | Efecto sobre el análisis |
|---|---|
| **Estacionalidad invertida** — pico en enero–abril por climatización | Un detector calibrado con estacionalidad de Sierra marcaría la subida estacional como anomalía y produciría cientos de falsos positivos |
| **Tarifa Dignidad, techo 130 kWh/mes** (Costa/Oriente/Insular; 110 en Sierra) | Genera una masa grande de clientes muy pequeños — justo donde un grupo par mal armado dispara falsos positivos |
| **Zonificación urbana marcada** (centro comercial, polígonos industriales, barrios, periferia) | La composición por clase varía fuertemente entre rutas: es lo que hace que separar por clase antes de comparar tenga valor |
| **Coordenadas UTM 17S reales** (Guayaquil–Durán) | La focalización geográfica y la estabilidad de los sectores se pueden verificar sobre el terreno |

El escenario inyecta **verdad conocida** —hurtos, una transferencia de carga
entre alimentadores y clientes faltantes— de modo que cada etapa no solo muestra
números: muestra **si son correctos**.

---

## Las 11 etapas, paso a paso

### Etapa 1 — Ingesta y calidad
`etapa1_ingesta.pdf`

Carga el padrón (20 000 × 36 meses en **≈ 19 s**), verifica la orientación
temporal de las columnas `KWH_n` contra `CLIULTCONM` y recalcula la potencia.

**Qué mirar:** la diferencia de potencia contra el SIG (−85 %). No es un error
del sistema: es la magnitud del sesgo que introduce calcular la potencia con el
consumo de un solo mes.

### Etapa 2 — Segmentación
`etapa2_segmentacion.pdf`

Clasifica el padrón por clase tarifaria, nivel de tensión, fases y ruta, y arma
los grupos par.

**Qué mirar:** las dos barras cuentan historias opuestas. Los no residenciales
son el **5 % de los clientes** y el **90 % de la energía**. Un plan que ordene
por número de casos ataca el gráfico equivocado.

### Etapa 3 — Detección de PNT
`etapa3_deteccion.pdf`

Señales de comportamiento sobre 36 meses y ranking de sospecha.

**Qué mirar:** el lift por tramo del ranking, y la concentración por zona — el
sistema encuentra que la PNT está en la periferia **a partir del consumo**, sin
que se le haya dicho.

### Etapa 4 — Balance por alimentador
`etapa4_balance.pdf`

Flujo trifásico desbalanceado con neutro sobre los 12 alimentadores
(**≈ 2,7 s**) y balance energético de cada uno.

**Qué mirar:** `MEDIDO` vs `INDICATIVO` — sin cabecera confiable la PNT no es
verificable. Y el orden por PNT porcentual: **este es el primer nivel de
focalización**.

### Etapa 5 — Credibilidad
`etapa5_credibilidad.pdf`

**La etapa que evita el error más caro del proyecto.** Antes de mandar
cuadrillas, verifica que el número sea creíble: transferencias de carga no
reportadas, clientes faltantes e incoherencias.

**Qué mirar:** el **porcentaje de energía vinculada** (97,2 %). Es el techo de
calidad del balance: ninguna PNT por debajo del 2,8 % es distinguible del ruido
de vinculación.

### Etapa 6 — Validación contra multados
`etapa6_validacion.pdf`

La única etapa que mide precisión **real** y no simulada: contra clientes que la
distribuidora ya multó en campo.

**Qué mirar:** el lift. Y la advertencia metodológica: los no multados **no son
negativos confiables** (solo significan «no detectado todavía»), por eso se usa
aprendizaje PU y una fecha de corte contra fuga de información.

### Etapa 7 — Focalización
`etapa7_focalizacion.pdf`

Prioriza los siete niveles por separado y explica cuándo sirve cada uno.

**Qué mirar:** los identificadores. `GYE-09/TS3` está cualificado con su
alimentador, porque dos alimentadores tienen su propio `TS3` y una orden emitida
para `TS3` a secas sería ambigua.

### Etapa 8 — Plan de campo
`etapa8_plan_campo.pdf`

Órdenes de trabajo ordenadas por **rendimiento por visita**, no por probabilidad.

**Qué mirar:** el plan mezcla niveles. Que uno domine no es un defecto —
significa que ahí está concentrada la sospecha. Si la mezcla fuera siempre la
misma sin importar los datos, **eso** sí sería un defecto.

### Etapa 9 — Resumen ejecutivo
`etapa0_resumen_ejecutivo.pdf` (abre el compendio)

Responde la pregunta directamente, con la tabla de cuándo conviene cada nivel y
la verificación contra la verdad del escenario.

---

## Resultados de la corrida

### Escenario generado

| | |
|---|---|
| Clientes | 20 000 × 36 meses |
| Alimentadores | 12 · 168 rutas comerciales · 264 transformadores |
| Hurtos inyectados | 1 010 (5,05 %), concentrados en periferia (11,2 %) |
| Multados registrados | 555 (55 % de los hurtos reales) |
| Transferencia inyectada | GYE-04 → GYE-05, 2025-12 |
| Clientes sin SIG | 500 |

### Detección

| Tramo | Precisión | Lift | Recall |
|---|---|---|---|
| top 1 % | 100,0 % | 19,8× | 19,8 % |
| top 2 % | 98,8 % | 19,6× | 39,1 % |
| **top 5 %** | **84,4 %** | **16,7×** | **83,6 %** |
| top 10 % | 50,4 % | 10,0× | 99,8 % |

Contra la base de multados: **lift 16,4×**, **AUC 0,981**, posición mediana de
los multados en el **2,6 %** del ranking.

### Balance

12/12 alimentadores convergen, todos con balance **MEDIDO**. PNT del sistema
**6,3 %** (1 726 783 kWh/mes), con dispersión real entre alimentadores:
GYE-08 al 9,00 %, GYE-01 al 4,40 %.

### La respuesta: ¿a qué nivel atacar?

| Nivel | Órdenes | Clientes | kWh por visita |
|---|---|---|---|
| **SECTOR** | 1 | 14 | **87 973** |
| RUTA_COMERCIAL | 13 | 1 601 | 52 045 |
| PUESTO_TRANSFORMACION | 11 | 924 | 50 546 |

**25 órdenes cubren 2 539 clientes y 1 320 568 kWh/mes.**

Los tres primeros de cada nivel:

| Nivel | Entidad | Clientes | kWh/mes |
|---|---|---|---|
| ALIMENTADOR | GYE-08 | 1 633 | 284 598 |
| RAMAL | GYE-09/BT3_0_0 | 44 | 107 554 |
| TRANSFORMADOR | GYE-09/TS3 | 88 | 107 999 |
| SECTOR | SEC-E631.8-N9762.2 | 14 | 87 973 |
| RUTA COMERCIAL | GYE-08-R09 | 103 | 132 963 |

### Verificación contra la verdad inyectada

| Comprobación | Esperado | Obtenido | |
|---|---|---|---|
| Recall top 10 % | > 60 % | 99,8 % | OK |
| Lift top 5 % | > 3× | 16,7× | OK |
| Transferencia: coherente con su piso | 6,0 % < piso 8,0 % → no inventar | 0 falsos positivos | OK |
| Piso de detección declarado | se informa qué se puede cazar | 8,0 % (~179 109 kWh) | OK |
| Clientes faltantes | 500 | 500 | OK |
| Balance cierra | 12 | 12 | OK |
| Identificadores únicos | 1 453 | 1 453 | OK |

#### La transferencia inyectada: por qué «no detectada» es la respuesta correcta

La comprobación anterior decía «detectada / REVISAR» y **no podía pasar por
construcción**. El escenario acota la magnitud de la transferencia para que la
cabecera del origen no caiga por debajo de su facturado —si no, produciría PNT
negativa, que es un artefacto del escenario—, y esa cota la deja en el **6,0 %**
de la cabecera. El piso de detección con 12 alimentadores y 12 meses es del
**8,0 %**. La maniobra es, literalmente, más pequeña que el ruido de la serie.

Una comprobación que solo se puede satisfacer haciendo trampa es peor que no
tenerla. Ahora se verifica lo que sí es cierto y sí importa:

* si la maniobra está **por encima** del piso, hay que encontrarla;
* si está **por debajo**, hay que **declararlo** y no inventar nada.

Y el detector se verifica por encima del piso en
`test_detecta_la_transferencia_entre_un_alimentador_grande_y_uno_chico`.

---

### Rendimiento

La etapa 11 mide la corrida real, no un microbanco: lo que interesa no es cuántas
operaciones por segundo hace una función, sino si una unidad de negocio cabe en
la ventana de la madrugada.

| Indicador | Valor |
|---|---|
| Tiempo total | **51 s** (20 000 clientes × 36 meses = 720 000 lecturas) |
| Por cliente | 2,54 ms, incluidos análisis, red, focalización e informes |
| Memoria pico | 435 MB en un solo proceso |
| Generación de PDF | 5 s (11 %) — es Chromium, no el cálculo |

**Dónde se va el tiempo**, que es lo que evita optimizar la etapa equivocada:

| Etapa | Segundos | % |
|---|---:|---:|
| 1. Generación del escenario y calidad de la ingesta | 37,3 | **71 %** |
| 7. Focalización | 7,2 | 14 % |
| 4. Red y balance por alimentador | 3,1 | 6 % |
| El resto (7 etapas) | ~4,7 | 9 % |

Los 37 s de la etapa 1 son **generar 720 000 lecturas sintéticas y escribirlas a
CSV**: en producción ese tiempo no existe, porque los datos vienen de la base. El
análisis propiamente dicho son unos 14 s.

**Proyección a la escala real** (lineal, y por eso conservadora — los
alimentadores se procesan en paralelo):

| Escala | Tiempo estimado |
|---|---|
| 20 000 (esta prueba) | 51 s |
| 100 000 (una UN mediana) | 4 min |
| 500 000 (una UN grande) | 22 min |
| 2 000 000 (11 UN) | **1,5 h** |

**El paralelismo, medido en la misma corrida** — 24 alimentadores en el equipo de
prueba (4 núcleos):

| Trabajadores | Tiempo | Procesados | Espera máxima en cola |
|---:|---:|---:|---:|
| 1 | 0,36 s | 24/24 | 0,35 s |
| 2 | 0,25 s | 24/24 | 0,22 s |
| 4 | **0,18 s** | 24/24 | 0,13 s |

Las 24 se procesan siempre; lo que cambia es cuántas a la vez y cuánto espera la
última. Esa espera es el argumento **con número** para pedir más máquina.

**Nada de esto obliga a comprar un servidor.** A esta escala el proceso entero
cabe en un equipo de oficina. Lo que sí conviene medir antes de producción es el
coste de *un alimentador urbano real* con `ptnt recursos --medir`: el sintético
consume menos, y ese número es el que decide cuántos caben a la vez.

---

## Cuándo conviene cada nivel

| Nivel | Cuándo | No sirve para |
|---|---|---|
| **ALIMENTADOR** | Priorizar el esfuerzo del mes y decidir dónde medir | Emitir una orden de trabajo: son miles de clientes |
| **RAMAL** | La sospecha está concentrada en un tramo de calle | Sospecha dispersa: la cuadrilla recorre en vano |
| **TRANSFORMADOR** | Hay totalizador o se puede hacer censo de carga | Puestos con clientes dispersos geográficamente |
| **SECTOR** | Muchos sospechosos juntos: barrido casa por casa | Sospechosos aislados a kilómetros entre sí |
| **RUTA COMERCIAL** | Reusar la logística: el lector ya pasa por ahí | Rutas que cruzan varios alimentadores o zonas |
| **CLIENTE** | Grandes clientes y evidencia individual fuerte | Barrido masivo: no rinde por visita |

---

## Defectos encontrados y corregidos gracias a esta prueba

La escala expuso cinco problemas que el escenario de 1 200 clientes no revelaba.

**0. La simetría del detector de transferencias descartaba el par real.**
Descontado el movimiento común, la maniobra era **visible en el residuo**: el
origen perdía 56 100 kWh y el destino ganaba 122 342. Pero la simetría se medía
sobre kWh absolutos, daba 0,46 contra el 0,60 exigido, y el par se tiraba. La
causa: un alimentador de 2,8 GWh se desvía del patrón común por su cuenta en
cientos de miles de kWh, y uno de 1 GWh en decenas de miles — **exigirles
proporcionalidad estricta descarta justo el caso más habitual**, que es descargar
el alimentador saturado sobre el que tiene margen.

Se corrigió con tres cambios: la simetría admite una holgura del tamaño del ruido
propio de cada alimentador; se exige además un **escalón sostenido en la razón
B/A** —que cancela la estacionalidad, porque los dos suben y bajan juntos— para
que la holgura no abra la puerta a falsos positivos; y la magnitud se estima
**ponderando cada alimentador por su propio ruido** en vez de promediarlos a
partes iguales. Sobre el caso de 62 600 kWh reales: la media daba 89 200, el
mínimo 56 100, y la ponderación **59 000**.

La prueba unitaria que lo fija usa alimentadores de tamaños muy distintos con
estacionalidad y ruido. El fixture anterior usaba alimentadores parecidos y sin
estacionalidad — por eso el defecto sobrevivió tanto tiempo.

**1. La detección de transferencias fallaba con muchos alimentadores.**
En una misma zona todos suben y bajan juntos por estacionalidad. Buscando pares
sobre la variación bruta, el alimentador que bajaba aparecía emparejado con
*todos* los que subían: **3 pares espurios y la transferencia real perdida**.
Ahora se descuenta el movimiento común del sistema con la mediana de las
variaciones relativas (robusta: si un par transfiere, son 2 de 12 series y no
mueven la mediana). Resultado: **1 candidato, exactamente el inyectado**.

**2. Los identificadores de objetivo colisionaban entre alimentadores.**
`BT3_0_0` y `TS9` solo son únicos *dentro* de un alimentador. El plan mostraba
dos objetivos con el mismo nombre y energías muy distintas; una orden emitida
para `TS9` habría sido ambigua. Ahora se cualifican: `GYE-09/TS3`.

**3. El diagnóstico y el plan se contradecían.**
La etapa 5 recomendaba excluir los alimentadores con transferencia, pero el plan
seguía emitiendo órdenes para ellos. Ahora `build_survey_plan` recibe
`feeders_con_transferencia` y los marca como problema de datos, lo que los saca
de las órdenes de trabajo sin borrarlos del informe.

**4. Doble conteo de estacionalidad en la cabecera del escenario.**
`head_kwh` se derivaba del consumo del mes final —que ya trae su
estacionalidad— y luego se multiplicaba otra vez por el factor del mes. En los
meses de valle la cabecera caía por debajo del facturado y el alimentador salía
con PNT negativa: un artefacto del escenario, no un hallazgo. Lo detectó
`test_cabecera_cubre_facturado_mas_perdidas`.

---

## Entregables

En `outputs/costa20k/`:

| Archivo | Contenido |
|---|---|
| `INFORME_COMPLETO_costa20k.pdf` | Compendio de las 9 etapas con índice (≈ 830 KB) |
| `etapa0_resumen_ejecutivo.pdf` | La respuesta: a qué nivel atacar |
| `etapa1..8_*.pdf` / `.html` | Un informe por etapa |
| `plan_levantamientos.csv` | Los 1 453 objetivos priorizados |
| `ordenes_levantamiento.csv` | Las 25 órdenes de trabajo |
| `reporte_focalizacion.html` | Reporte interactivo de focalización |
| `ubicaciones.json` | Registro persistente de ubicaciones |

Los informes HTML son **autocontenidos** —CSS embebido, gráficos SVG en línea,
sin recursos externos— para poder enviarse por correo, archivarse como evidencia
o imprimirse sin depender de la red. El contenido que viene de la base de origen
se escapa antes de insertarse, de modo que un identificador con marcado no pueda
inyectar nada en el informe que después circula.

---

## Limitación honesta

Los números son sobre **datos sintéticos**. El escenario reproduce la estructura
que el método explota —correlación clase↔geografía, concentración de PNT en
periferia, estacionalidad costera— pero los hurtos inyectados son más limpios
que los reales. **Con la base real el lift será menor.** Lo que esta prueba
demuestra no es la magnitud del lift, sino que el proceso corre de punta a punta
a escala, en menos de un minuto, y que cada etapa entrega lo que promete.

---

Ver también: [Guía de operación](GUIA_OPERACION.md) ·
[Segmentación](SEGMENTACION.md) · [Focalización](FOCALIZACION.md) ·
[Diagnóstico](DIAGNOSTICO.md) · [Pruebas](PRUEBAS.md)
