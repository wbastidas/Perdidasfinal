# Escenarios de trabajo y alcance por unidad de negocio

Dos cosas que hasta ahora faltaban:

1. Un analista hace cambios sobre un alimentador y quiere ver **en ese momento**
   cómo sale el balance, sin publicar nada.
2. Cada usuario trabaja **su** unidad de negocio; la matriz ve todas y analiza la
   que quiera.

```bash
ptnt escenario-abrir "Rebalanceo GYE-01" --usuario ana --entidad GYE-01
ptnt escenario-cambiar 40439679 --capa ptnt_puesto_transformacion \
     --elemento TS2 --campo kva --valor 150 --motivo "Puesto sobrecargado"
ptnt escenario-evaluar 40439679 --usuario ana --cabecera datos/cabecera.csv
ptnt escenario-evolucion --entidad GYE-01 --usuario ana
```

Recorrido completo, con datos ficticios y sin instalar nada más:

```bash
python scripts/demo_escenarios.py
```

---

## 1. Un escenario es un lugar donde equivocarse sin consecuencias

El analista **define su alcance** —un alimentador o una subestación entera—,
acumula cuantos cambios quiera y evalúa cuando le parece. El modelo oficial no se
toca en ningún momento: los cambios se aplican sobre una copia profunda que vive
lo que dura el cálculo.

Eso cuesta memoria y es exactamente lo que se está comprando. Probar una
configuración de banco que resulta un disparate tiene que ser barato; si probar
implica publicar, nadie prueba.

| Estado | Qué significa |
|---|---|
| `ABIERTO` | Se pueden acumular cambios |
| `EVALUADO` | Tiene al menos una iteración calculada |
| `APLICADO` | Sus cambios se llevaron al modelo oficial — **ya no admite cambios nuevos** |
| `DESCARTADO` | Se cerró sin aplicar |

Un escenario aplicado que siguiera admitiendo cambios produciría un historial que
dice una cosa y un modelo que dice otra.

## 2. Las iteraciones no se borran: son la evolución

Cada `escenario-evaluar` deja una **iteración**, numerada y con fecha, que nunca
se sobrescribe. Es lo que permite ver el alimentador cambiar en el tiempo en vez
de solo su estado actual.

```
ptnt escenario-evolucion --entidad GYE-01 --usuario ana
```

Con `--entidad` la historia **cruza todos los escenarios** de ese alimentador, no
solo uno. Tiene que ser así: cada vez que un escenario se aplica se abre otro, de
modo que la vida de un alimentador está repartida entre varios.

### Comparar dos iteraciones

```
ptnt escenario-comparar 40439679 --desde 1 --hasta 2
```

Avisa en dos casos donde la diferencia **no** es efecto de los cambios del
analista:

- **Cambió el hash de topología** entre las dos evaluaciones — hubo una carga del
  SIG en medio. Sin el aviso, esa carga se atribuiría a la idea que se estaba
  probando.
- **Cambió el tipo de balance** (`INDICATIVO` → `MEDIDO`). Restar una PNT
  estimada de una medida da un número, no una conclusión.

## 3. La PNT dice de qué clase es

| Tipo | Cuándo | Qué se puede hacer con ella |
|---|---|---|
| `MEDIDO` | Hay energía de cabecera (`--cabecera`) | Contrastable. Se puede declarar |
| `INDICATIVO` | No hay medición de cabecera | Estimada. Sirve para **comparar iteraciones entre sí** —la estimación es la misma en ambas—, no para declararla como pérdida real |

El CSV de cabecera es `feeder_code,period,kwh_delivered`; varios períodos del
mismo alimentador se suman.

Dos reglas que evitan un consolidado engañoso:

- **El peor tipo manda en el conjunto.** Si un solo alimentador de la subestación
  es `INDICATIVO`, el balance de la subestación es `INDICATIVO`. La energía se
  suma; la garantía de que es contrastable, no.
- **Cabecera incompleta se declara.** Si se entrega medición de algunos
  alimentadores y no de otros, sale advertido por nombre. Medir la mitad y
  presentarla como medida es peor que no medir: nadie sabría qué mitad creerse.

Los controles del balance que saltan (`C01` PNT negativa, `C02` PNT máxima, …)
llegan al usuario en la evaluación, no se quedan en el cálculo.

## 4. El porcentaje se recalcula, no se promedia

En un escenario de subestación, la PNT del conjunto se calcula sobre los totales:

```
pnt_pct = 100 · Σ pnt_kwh / Σ e_input_kwh
```

Promediar los porcentajes de alimentadores de tamaños distintos da un número que
no es el de nadie: uno de 1 000 000 kWh al 5 % y uno de 20 000 kWh al 50 %
promedian 27,5 %, y la pérdida real del conjunto es 5,9 %.

## 5. Un cambio que no se pudo aplicar se dice

Si el elemento ya no está en la red —lo eliminó una carga posterior, o el guid
venía de otra versión— el usuario tiene que enterarse. Callarlo sería peor que
fallar: creería que probó algo que en realidad no se probó, y decidiría sobre
eso.

`ResultadoEvaluacion.confiable` es falso en cuanto queda un cambio sin aplicar, y
la lectura lo dice en la misma frase que da el número.

**Crear y eliminar elementos no se evalúan en un escenario.** Cambian la
topología y exigen rehacer el grafo; se rechazan explícitamente en vez de
simularlos a medias. Van por la vía de revisión de campo y una nueva migración.

## 6. Alcance: cada unidad la suya, la matriz todas

```bash
ptnt crear-usuario ana --rol analyst --unidad GUAYAQUIL
ptnt crear-usuario central --rol admin --matriz
ptnt usuario-unidad ana --unidad "GUAYAQUIL,LOS_RIOS"
ptnt usuarios
```

Tres decisiones que gobiernan el módulo:

**El control está donde se leen los datos, no en la interfaz.** `Alcance.filtrar`
se aplica en la consulta —`escenario-listar` filtra en el propio SQL—, de modo
que una pantalla nueva que se olvide de comprobar no abre un agujero.

**Falla cerrado.** Un usuario sin unidad asignada no ve *nada*, no lo ve *todo*.
Lo contrario convertiría un alta a medias en el padrón de otra unidad en manos de
quien no debe. Por lo mismo, un conjunto de datos sin columna de unidad se
devuelve vacío: no es «de todos», es uno que no se puede filtrar.

**La matriz elige, no acumula.** Ve todas las unidades y analiza la que quiera;
no es la suma de las unidades sino la capacidad de entrar en cualquiera.

Casos límite que están resueltos:

- Una entidad **fuera del catálogo organizacional** no se entrega: si no se puede
  demostrar de quién es, no se entrega. La matriz sí pasa, pero enterada de que
  no está catalogada — suele ser justo lo que hay que arreglar.
- Una **subestación repartida entre dos unidades** se bloquea. Es un error del
  catálogo, no un caso a resolver en silencio: dejarlo pasar haría que un usuario
  de una unidad analizara alimentadores de otra.
- **El administrador ve todas sin declararlo.** Puede crear usuarios, así que
  podría asignarse cualquier unidad; negarle los datos sería teatro.
- La jerarquía usada para el alcance **no se infiere** del prefijo del código,
  aunque `organizacion.inferir_si_falta` esté activo para los consolidados. Una
  unidad adivinada daría o negaría acceso por una coincidencia de texto.

## 7. Traer los cambios de un paquete de campo

Lo que la cuadrilla capturó se puede probar antes de aceptarlo:

```bash
ptnt escenario-cambiar 40439679 --desde-paquete outputs/campo/paquetes/jperez.gpkg
```

Toma el diario de cambios del GeoPackage y acumula los evaluables
(`MODIFICAR`, `RECONECTAR`). Permite responder «¿cuánto mueve la aguja este
levantamiento?» sin haberlo publicado todavía.

---

## Dónde vive

| Pieza | Archivo |
|---|---|
| Alcance por unidad de negocio | `src/ptnt/security/scope.py` |
| Usuarios con unidades y matriz | `src/ptnt/security/auth.py` |
| Escenarios, cambios e iteraciones | `src/ptnt/workspace/escenarios.py` |
| Evaluación sobre copia | `src/ptnt/workspace/evaluacion.py` |
| Comandos | `src/ptnt/cli.py` (`escenario-*`, `usuarios`, `usuario-unidad`) |
| Pruebas | `tests/unit/test_escenarios.py` (27) |
| Recorrido completo | `scripts/demo_escenarios.py` |

Los escenarios se guardan en `<salidas>/escenarios/escenarios.db` (SQLite en modo
WAL, con `BEGIN IMMEDIATE` en las escrituras): varios analistas pueden trabajar a
la vez sobre el mismo archivo sin pisarse.
