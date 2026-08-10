# El ciclo completo, de punta a punta

Del padrón sintético a la red corregida, sin saltarse ninguna pieza y sin datos
preparados de antemano: cada etapa consume lo que produjo la anterior.

```bash
python scripts/demo_ciclo_completo.py      # ~40 s
```

El guion **verifica cada etapa** y termina con código de salida distinto de cero
si alguna comprobación falla. Una demostración que solo imprime no demuestra
nada: imprimiría igual con los números mal.

---

## Qué recorre

```
 1. Escenario ficticio          →  padrón, cabecera, red, multados
 2. Análisis comercial          →  promedio, potencia, segmentación, sospecha
 3. Balance y PNT               →  flujo 3φ, pérdidas técnicas, no técnicas
 4. Diagnóstico                 →  ¿se puede creer este balance?
 5. Focalización                →  dónde ir a inspeccionar
 6. Trabajo definido a mano     →  un censo que el ranking no pide
 7. Cuadrillas y reparto        →  carga pareja, recorrido compacto
 8. Paquetes descargables       →  un .gpkg por técnico
 9. Descarga simultánea         →  API real, tres a la vez
10. LA JORNADA DE CAMPO         →  editar, mover, reconectar, fotografiar
11. Subida y validación         →  tres lotes a la vez
12. Revisión del supervisor     →  aceptar y rechazar, cambio a cambio
13. Qué recalcular              →  invalidación selectiva
14. Segundo día                 →  lo que quedó abierto
15. Verificación final          →  coherencia de todo el ciclo
```

---

## Resultados de una ejecución

### Etapas 1–4: del padrón al balance creíble

| | |
|---|---|
| Clientes × meses | 900 × 36 |
| Hurtos inyectados | 61 (36 con multa histórica, 59 %) |
| Clasificado por tarifa | **100 %** |
| Energía en no residenciales | 79 % |
| Flujo | 3φ desbalanceado con neutro, **converge** |
| Entrada de cabecera | 45 668 kWh |
| Pérdidas técnicas | 1 922 kWh (P10–P90: 1 616–2 242) |
| **PNT** | **1 067 kWh (2,3 %)** |

El diagnóstico se hace **antes** de mandar cuadrillas, y ahí está el sentido:

| Control | Inyectado | Detectado |
|---|---|---|
| Transferencia no reportada | F002 → F003 | **F002 → F003** |
| Clientes sin SIG | 27 | **27** |
| Energía vinculada al SIG | — | 95,97 % ← techo de calidad del balance |
| Incoherencias que impiden publicar | — | 0 |

> La PNT de un alimentador con una transferencia no reportada **no es hurto**: es
> un error de medición. Mandar una cuadrilla ahí es quemar una jornada.

### Etapa 5: dónde inspeccionar

298 objetivos priorizados en 7 niveles:

```
ALIMENTADOR 1 · ZONA_PROTECCION 8 · RAMAL 17 · PUESTO_TRANSFORMACION 8
RUTA_COMERCIAL 55 · SECTOR 9 · CLIENTE 200
```

Las órdenes salen ordenadas **por rendimiento por visita**, no por score: lo que
decide una jornada que se corta a media tarde es cuál se hizo primero.

```
orden_trabajo          nivel            entidad  clientes  kwh_por_visita
      OT-0001         SECTOR SEC-E624.0-N9755.6        28          9014.2
      OT-0002 RUTA_COMERCIAL           F005-R01        13          6888.9
      OT-0003         SECTOR SEC-E620.8-N9756.9         6          6219.3
```

209 ubicaciones quedan registradas con **identidad geográfica estable**: un
sector priorizado hoy sigue siendo el mismo sector el mes que viene, aunque
cambien los datos.

### Etapas 6–7: definir y repartir

Al plan del análisis (12 órdenes de inspección) se le suma un **censo** definido a
mano (8 órdenes). El censo lleva `0` en recuperable **a propósito**: no recupera
kWh, corrige el denominador del balance. Evaluarlo por energía lo haría parecer
inútil y dejaría de hacerse.

```
usuario  ordenes  clientes  recuperable_kwh_mes  dispersion_km
    ana        4        77              13643.5           1.82
   beto        5        95              13530.2           2.03
  carla       11       942              12585.5           1.77
  → desbalance 8,0 %   ·   dispersión media 1,87 km
```

Las dos métricas importan. El desbalance dice si alguien terminará a media tarde
mientras otro no acaba; la dispersión, cuánto va a manejar cada cuadrilla.

### Etapas 8–9: paquetes y descarga

```
usuario  ordenes  elementos  area_km2  tamano_mb
    ana        4        142     14.11       0.33
   beto        5         92     13.44       0.31
  carla       11        292     18.01       0.39
```

Los tres descargan **al mismo tiempo** contra la API real:

- Cada técnico ve **exactamente su parte**, nada más.
- **20/20 órdenes** cambian de estado: ninguna actualización se pierde.

### Etapa 10: la jornada

Se ejecutan sobre el paquete real las mismas operaciones que hace la app Android,
escribiendo el mismo diario de cambios:

```
       cambios  atributos  movimientos  propagados  reconexiones  fotos  cerradas  en_proceso
ana         15         12            1           1             1      3         1           1
beto        15         12            1           1             1      3         1           1
carla       15         12            1           1             1      3         1           1
```

- **Mover un cliente arrastra su acometida** (1 elemento propagado por movimiento).
- **Reconexión**: `07f77ae6 → 8ce5fa36`. Es la corrección que más vale de la
  visita: mueve consumo de una zona de balance a otra.
- Cada técnico cierra una orden y deja otra **empezada**.

### Etapas 11–13: vuelta, revisión y recálculo

Los tres suben a la vez. Cada lote llega íntegro, con su identificador y a nombre
de quien lo hizo:

```
carla  HTTP 200 · 15 cambio(s) · 3 foto(s) · cerradas 1 · en curso 1
beto   HTTP 200 · 15 cambio(s) · 3 foto(s) · cerradas 1 · en curso 1
ana    HTTP 200 · 15 cambio(s) · 3 foto(s) · cerradas 1 · en curso 1
```

El supervisor ve el antes y el después de **cada campo**, y decide uno por uno:

```
 secuencia         capa operacion           campo  antes             despues
         1 ptnt_cliente MODIFICAR        hallazgo    NaN  MEDIDOR_MANIPULADO
         2 ptnt_cliente MODIFICAR   inspeccionado      0                   1
         3 ptnt_cliente MODIFICAR lectura_medidor    NaN             12345.0
```

Y el sistema dice qué queda obsoleto:

```
1 reconexión(es) de consumidor: cambian el balance de la zona que pierde el
cliente y de la que lo gana.
Etapas invalidadas: balance, flujo, focalizacion, perdidas, ranking, topologia
```

Recalcular **todo siempre** haría el ciclo inviable en una unidad de negocio real;
recalcular **de menos** dejaría números que ya no corresponden a la red.

### Etapa 14: el segundo día

```
Diario de ana: 15 → 17 entradas (lo de ayer se conserva, marcado como enviado)
Subida del día 2: 2 cambio(s) nuevos, 15 ya enviados que NO se reprocesan
```

Sin esto, un trabajo de una semana subiría siete veces la jornada del lunes y el
histórico contaría cada cambio siete veces.

### Etapa 15: coherencia final

```
DESCARGADA 14 · COMPLETADA 4 · EN_PROCESO 2

usuario  ordenes  DESCARGADA  EN_PROCESO  COMPLETADA  jornadas
    ana        4           2           0           2         1
   beto        5           3           1           1         1
  carla       11           9           1           1         1
```

Y la bitácora explica quién movió qué:

```
 operacion actor                                  detalle
TRANSICION   ana {"desde": "EN_PROCESO", "hacia": "COMPLETADA"}
    AVANCE  beto        {"estado": "EN_PROCESO", "jornada": 1}
TRANSICION  beto {"desde": "DESCARGADA", "hacia": "EN_PROCESO"}
```

**22 de 22 comprobaciones pasan.**

---

## Lo que destapó ejecutarlo

Recorrer el ciclo entero encontró dos incoherencias que las pruebas por módulo no
veían, porque cada módulo era correcto por separado:

**1. El día 2 reenviaba el día 1.** El simulador de campo —que debe espejar a la
app— no marcaba lo subido después de la respuesta del servidor. El backend hacía
bien su parte (ignorar lo ya sincronizado), pero nada lo marcaba, así que la
protección nunca se activaba. Corregido en `field/simulator.py`, con la misma
regla que el cliente Kotlin: **marcar después del 200, nunca antes** — al revés,
una subida fallida a mitad perdería esos cambios en silencio.

**2. Una orden abierta figuraba como «descargada».** El técnico la abría en el
dispositivo, el backend anotaba la jornada pero no cambiaba el estado. El tablero
del supervisor mostraba trabajo sin empezar donde había una cuadrilla trabajando.
Ahora `anotar_avance` transiciona `DESCARGADA → EN_PROCESO` y sella la fecha de
inicio.

Las dos están fijadas con prueba de regresión en
`tests/integration/test_ciclo_campo_completo.py`.

---

## El simulador de campo

`ptnt.field.simulator.SimuladorCampo` ejecuta en Python las mismas operaciones
que la app Android sobre el mismo GeoPackage: editar atributos, mover con
propagación, reconectar, fotografiar, abrir y cerrar órdenes, y marcar lo enviado.

Existe por una razón concreta: **el contrato entre el móvil y el backend es la
parte del sistema donde un error no se ve**. Un cambio mal numerado o una
geometría con otra envolvente no fallan al escribirse — fallan semanas después,
al sincronizar, cuando el técnico ya no está en el sitio.

No sustituye a las pruebas en dispositivo: no dice nada del render, de los
permisos ni del GPS. Dice que **lo que el móvil escribe, el backend lo entiende**.

---

Ver también: [Guía de operación](GUIA_OPERACION.md) ·
[Aplicación móvil](APLICACION_MOVIL.md) · [Arquitectura](ARQUITECTURA.md) ·
[Especificación completa](ESPECIFICACION_COMPLETA.md) · [Pruebas](PRUEBAS.md)
