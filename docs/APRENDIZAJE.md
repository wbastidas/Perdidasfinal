# Aprendizaje: que el sistema mejore con el uso

Dos mecanismos para que la plataforma no se quede en la calibración del primer
día.

```bash
ptnt campo-aprender --paquete outputs/campo/paquetes/jperez.gpkg
ptnt pares --entrada alimentadores.csv --nivel ALIMENTADOR
```

---

## 1. Lo que la cuadrilla encuentra vuelve al modelo

Hasta aquí el ciclo se cortaba en la revisión. El técnico capturaba
`hallazgo = MEDIDOR_MANIPULADO`, el supervisor lo aceptaba, el cambio entraba al
SIG… y el detector seguía calibrándose contra la base de multados heredada. **El
sistema mandaba cuadrillas y no aprendía de lo que las cuadrillas encontraban**,
que es la información más cara y más limpia que produce la empresa.

### La decisión que más pesa: no todo lo que no es hurto es inocencia

De los once hallazgos del formulario, solo dos grupos son etiquetas utilizables:

| Hallazgo | Veredicto | Qué se puede afirmar |
|---|---|---|
| Conexión directa, medidor manipulado, puente, sellos violentados, medidor invertido, medidor frenado | **CONFIRMADO** | Hubo hurto |
| Sin novedad | **DESCARTADO** | Fue alguien, miró, y no había nada |
| Predio cerrado, acceso negado | **NO CONCLUYENTE** | *Nada.* Trabajo a rehacer |
| Cliente no existe, error de datos | **PROBLEMA DE DATOS** | El padrón está mal, no el cliente |

`PREDIO_CERRADO` y `ACCESO_NEGADO` **no son negativos**. Meterlos como tales
enseñaría al modelo que un cliente sospechoso al que nadie pudo entrar es un
cliente limpio, y dejaría de señalar justo el caso donde más vale la pena
insistir. Se guardan aparte y salen en el informe como trabajo pendiente.

Y el cliente que viajó en el paquete pero **nadie visitó** no entra: contarlo
como «sin novedad» inventaría un negativo que nadie verificó.

### Los negativos verificados son el dato más escaso del problema

Un cliente donde *fue* un técnico, *miró* y *no había nada*. La literatura de PNT
usa aprendizaje PU (Elkan–Noto) precisamente porque los negativos no existen: no
inspeccionado ≠ inocente. Aquí, por primera vez, **algunos negativos sí existen**,
y se guardan por separado. Permiten medir la tasa de falsos positivos en vez de
estimarla.

### La fecha es la de la inspección

No la de la carga. Es la que `fecha_corte` usa para descartar fuga temporal: con
la fecha del proceso, una señal calculada con meses posteriores a la visita
«predeciría» algo que ya había ocurrido, y el lift saldría inflado.

### Qué mide

```
            Lo que encontró la cuadrilla
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Veredicto                              ┃ Visitas ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Hurto confirmado                       │       1 │
│ Verificado y limpio                    │       2 │
│ Sin concluir (cerrado / acceso negado) │       0 │
│ Problema de datos                      │       0 │
└────────────────────────────────────────┴─────────┘
```

Dos indicadores, y separarlos importa:

* **Precisión en campo** — de cada 100 visitas *con conclusión*, cuántas
  encontraron hurto. Es el número que justifica el presupuesto del programa.
* **Cobertura** — qué porcentaje de las visitas dejó conclusión.

Se calculan por separado a propósito. Si el 30 % de los predios estaba cerrado,
incluirlos hundiría la precisión y el equipo parecería estar apuntando mal cuando
lo que tiene es un problema de acceso — y la reacción correcta es cambiar el
horario de las visitas, no tocar el detector.

### El contraste con el ranking

`--ranking` compara lo que el modelo predijo con lo que la cuadrilla encontró. Es
**la única medición de calidad que no depende de supuestos**: no contra una base
heredada de procedencia desconocida, sino contra visitas que esta empresa hizo,
en estas calles, este año.

Con dos avisos que el informe da solo:

* Con menos de 20 visitas verificadas en el top, el lift todavía es ruido.
* Si **todas** las visitas salieron del top del ranking, no hay grupo de
  comparación y el lift queda sobreestimado. Conviene reservar una fracción de
  las visitas a clientes tomados al azar — cuesta unas pocas órdenes al mes y es
  lo único que permite saber si el detector sirve.

### Cómo se usa

```bash
# 1. Vuelve el trabajo del día y se incorpora
ptnt campo-aprender --paquete outputs/campo/paquetes/jperez.gpkg \
                    --multados data/multados_historico.csv \
                    --ranking outputs/ranking_hurto.csv

# 2. El detector se recalibra con lo aprendido
ptnt diagnostico --multados outputs/aprendizaje/confirmados.csv
```

La base propia y la heredada **se unen, no se reemplazan**: la heredada es peor
—no se sabe cómo se eligieron esos casos— pero mientras la propia sea pequeña,
tirarla sería perder señal. Con el tiempo, la propia domina.

El registro es idempotente: reprocesar el mismo lote no duplica visitas, porque
si no el lift mejoraría solo por haber cargado dos veces. Y un mismo cliente
inspeccionado en dos campañas son **dos datos**, no uno: quedarse con la última
visita perdería la historia justo de los reincidentes.

---

## 2. Cada entidad contra las que se le parecen

La pregunta que un jefe de pérdidas hace mirando un tablero: *«este alimentador
tiene 9 % de PNT… ¿es mucho?»*. **Sin referencia no se puede contestar.**

Un alimentador rural largo, con pocos clientes y mucha red, pierde más por
física. Uno urbano compacto con la misma cifra tiene un problema. Compararlos
contra el promedio de la empresa mezcla los dos casos y produce lo peor que puede
pasar: cuadrillas mandadas a alimentadores que están como deben estar, y
alimentadores realmente malos que pasan por normales porque el promedio los tapa.

Es la misma idea del grupo par de clientes —que en este proyecto ya está probada—
subida un piso.

### La regla que impide que sea circular

**El perfil que define quién se parece a quién tiene que ser exógeno a la métrica
evaluada.** Si se buscaran parecidos por PNT y después se evaluara la PNT, todos
serían normales dentro de su grupo por construcción.

No es una precaución teórica: es exactamente el error que en la segmentación de
clientes hizo caer el lift de S5 de **10,0× a 2,4×** cuando el estrato de consumo
entraba en la clave del grupo par. Si alguien mete la métrica en el perfil, el
módulo la excluye **y lo dice en el informe** — corregirlo en silencio produciría
un informe que parece correcto y dice que todo es normal.

| Nivel | Perfil (estructura) | Bloque |
|---|---|---|
| Alimentador | clientes, energía, km de red, transformadores, kVA, % residencial, kWh/cliente | unidad de negocio |
| Ramal | clientes, energía, km de red, kWh/cliente | alimentador |
| Puesto de transformación | clientes, energía, kVA, kWh/cliente, % residencial | alimentador |
| Ruta comercial | clientes, energía, kWh/cliente, % residencial | unidad de negocio |
| Sector | clientes, energía, kWh/cliente, radio | alimentador |

### Que cada unidad de negocio se comporte distinto, sin estimar nada

El **bloque** es la forma honesta de reconocerlo: entidades de unidades distintas
sencillamente **no se comparan entre sí**. En vez de estimar un efecto por unidad
con pocos datos —que con 11 unidades y unos pocos alimentadores cada una sería
más ruido que señal—, se corta la comparación.

Una unidad entera con PNT alta no puede así marcar a todos sus alimentadores, que
es lo que pasaría con un umbral global. Si una unidad queda con menos de cinco
entidades, se cae al universo completo avisando: un grupo par de dos no es un
grupo par.

### Tres detalles que decidieron si esto servía o no

**Mediana y desviación absoluta mediana, no media y desviación típica.** Si en un
grupo de ocho hay dos con hurto grave, la media se va con ellos y los dos dejan
de parecer atípicos — justo los que se buscaban.

**Los vecinos tienen que parecerse de verdad.** Pedir «los 8 más cercanos» en un
universo con 6 urbanos y 6 rurales le mete 3 rurales al grupo de un urbano,
porque son los que quedan. Se descartan los que superan el triple de la distancia
de referencia; sin ese corte, los dos alimentadores urbanos realmente malos
dejaban de aparecer.

**Un suelo de dispersión del 10 % del nivel del grupo.** En un grupo de seis muy
parecidos la desviación mediana puede salir casi cero, y entonces media décima de
punto de PNT se convierte en «cuatro desviaciones». Estadísticamente cierto e
inútil: nadie manda una cuadrilla por 0,4 puntos.

### Lo que sale

```
           ALIMENTADOR: se apartan de lo que su estructura explicaría
┏━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Entidad ┃ Observado ┃ Esperado ┃ Desviación ┃     z ┃ Severidad  ┃ Se comparó┃
┡━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ URB-03  │      11.0 │      4.8 │       +6.2 │ +12.8 │ MUY_ATIPICA│ URB-02,…  │
└─────────┴───────────┴──────────┴────────────┴───────┴────────────┴───────────┘
```

Y la pregunta inversa, que es la de la reunión — *«esta ruta va mal, ¿cómo están
las que se le parecen?»*:

```bash
ptnt pares --entrada alimentadores.csv --nivel ALIMENTADOR --parecidas-a URB-03
```

Ver las cinco parecidas con sus números al lado convence más que un z-score:

| Entidad | Clientes | Km red | kWh/cliente | PNT % |
|---|---:|---:|---:|---:|
| **URB-03** | 1 880 | 19,4 | 474,7 | **11,0** |
| URB-02 | 1 887 | 20,1 | 473,1 | 4,8 |
| URB-00 | 1 977 | 22,6 | 490,9 | 4,6 |
| URB-04 | 1 777 | 21,6 | 499,0 | 5,0 |
| URB-01 | 1 965 | 24,7 | 509,8 | 5,1 |

### Qué **no** significa

Que una entidad se salga de su grupo **no prueba hurto**: prueba que se comporta
distinto de lo que explicaría su estructura. Puede ser hurto, un transformador
mal asignado, un cliente no vinculado o una maniobra no reportada. Es una
pregunta bien hecha, no una respuesta — y el informe lo dice cada vez.

---

## Por qué estos dos y no aprendizaje profundo

La mayor parte de la literatura reciente de PNT con redes neuronales asume
**medición inteligente**: curvas de carga cada 15 minutos. Buzau et al.
(*IEEE Trans. Power Systems*, 2019), Zheng et al. (*IEEE TII*, 2018) y Jokar et
al. (*IEEE TSG*, 2016) parten todos de AMI.

Aquí hay **facturación mensual de 36 meses**. Con 36 puntos por cliente, una red
profunda no tiene de dónde sacar ventaja: se sobreajusta y se pierde la
interpretabilidad, que es lo que sostiene la decisión de mandar una cuadrilla a
casa de alguien.

Lo que sí está documentado y sí aplica a datos mensuales es esto: comparación
contra pares, contexto estructural y **cerrar el bucle con las inspecciones**.
Glauner et al. (*BDCAT* 2016; survey de 2017) mostraron además que las
características de vecindad ayudan, y —más importante— que el conjunto inspeccionado
está **sesgado**, porque se inspecciona donde el modelo ya apunta. Ese sesgo se
puede corregir, pero solo cuando hay inspecciones propias registradas: por eso
este paso va primero.

## Qué sigue, y por qué no ahora

| | Por qué esperar |
|---|---|
| Características de vecindad espacial | Cambia el ranking; necesita casos propios para validarse contra lift |
| Corrección de sesgo de selección | Necesita varias campañas registradas para tener con qué reponderar |
| Vigilancia de deriva del modelo | Necesita histórico de campañas |

Los tres se apoyan en el registro de inspecciones. Implementarlos antes sería
afinar contra una base heredada cuyo sesgo se desconoce.

**La regla para cuando llegue el momento:** toda señal nueva entra como una señal
más del consenso, con su peso calibrado contra casos confirmados, y **solo se
activa si el lift medido mejora**. Ese banco de pruebas ya existe
(`validate_against_confirmed`) y es la red de seguridad que permite intentar sin
arriesgar lo que funciona.

---

Ver también: [Segmentación](SEGMENTACION.md) · [Diagnóstico](DIAGNOSTICO.md) ·
[Focalización](FOCALIZACION.md) · [Aplicación móvil](APLICACION_MOVIL.md) ·
[Pruebas](PRUEBAS.md)
