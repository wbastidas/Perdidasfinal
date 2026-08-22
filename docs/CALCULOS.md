# Los cálculos, y en qué se apoya cada uno

Este documento responde a una sola pregunta, cálculo por cálculo: **¿por qué así
y no de otra manera?** Cada fórmula lleva su origen —norma, libro de texto o
artículo revisado por pares— y, cuando la decisión fue nuestra, se dice
explícitamente que lo fue y con qué razonamiento.

Existe porque un número de pérdidas no técnicas se acaba usando para sancionar a
un cliente o para pedir un presupuesto. Quien lo firma tiene derecho a saber de
dónde sale, y quien lo discute tiene derecho a poder atacarlo.

> Convenio: las referencias van como `[Autor, año]` y se listan al final. Los
> parámetros configurables se nombran con su ruta en `config/base.yaml`.

---

## Índice

1. [Promedio del consumo](#1-promedio-del-consumo)
2. [Demanda máxima por cliente](#2-demanda-máxima-por-cliente)
3. [Coincidencia y agregación](#3-coincidencia-y-agregación)
4. [Potencia reactiva, aparente y corriente](#4-potencia-reactiva-aparente-y-corriente)
5. [Factor de pérdidas](#5-factor-de-pérdidas)
6. [Pérdidas en conductores](#6-pérdidas-en-conductores)
7. [Pérdidas en transformadores](#7-pérdidas-en-transformadores)
8. [Capacidad del banco](#8-capacidad-del-banco-de-transformación)
9. [Pérdidas en medidores](#9-pérdidas-en-medidores)
10. [Alumbrado público](#10-alumbrado-público)
11. [Flujo de potencia](#11-flujo-de-potencia)
12. [Balance de energía y PNT](#12-balance-de-energía-y-pnt)
13. [Incertidumbre: Monte Carlo](#13-incertidumbre-monte-carlo)
14. [Señales de hurto](#14-señales-de-hurto-s1s9)
15. [Grupo par y segmentación](#15-grupo-par-y-segmentación)
16. [Ranking por consenso](#16-ranking-por-consenso)
17. [Energía recuperable](#17-energía-recuperable)
18. [Validación contra multados](#18-validación-contra-la-base-de-multados)
19. [Focalización](#19-focalización-dónde-inspeccionar)
20. [Detección de transferencias](#20-detección-de-transferencias-no-reportadas)
21. [Lo que NO se calcula](#21-lo-que-no-se-calcula-y-por-qué)

---

## 1. Promedio del consumo

**Qué hace.** Reduce hasta 36 meses de consumo de cada cliente a un valor
representativo.

**Cómo.** Métodos disponibles (`promedio.metodo`): media aritmética, **media
recortada**, mediana, ponderada por recencia (decaimiento exponencial con
`half_life`) y estacional.

**Por qué no el último mes.** Es el error que corrige el sistema. El SIG calcula
la demanda con el consumo del último mes disponible; un mes atípico —vacaciones,
una lectura estimada, un mes de 28 días— arrastra el cálculo entero. La media
recortada al 10 % descarta los extremos y es un estimador robusto clásico
[Huber & Ronchetti, 2009, cap. 1]: mantiene casi toda la eficiencia de la media
cuando los datos son limpios y no se desploma cuando no lo son.

**Ponderación por recencia.** `w_i = 2^(−Δt_i / half_life)`. La forma
exponencial es el suavizado exponencial estándar en previsión de demanda
[Hyndman & Athanasopoulos, 2021, cap. 8]; el `half_life` se expresa en meses
porque es como razona un operador («que pese más el último semestre»).

**Decisión propia.** Los ceros de un servicio **suspendido** se excluyen del
promedio, mientras que los ceros con servicio activo **se conservan** — son
justamente la señal S4. Promediarlos juntos borraría la evidencia.

---

## 2. Demanda máxima por cliente

**Método A — factor de carga.** `P_max = P_media / F_c`, con
`P_media = E / t`. Es la definición del factor de carga
[Gönen, 2014, §2.4; Willis, 2004, cap. 2].

**Método B — Velander (por defecto).**

```
P_max = a·E + b·√E          [kW],  E en kWh/mes
```

La fórmula de Velander es el estándar nórdico para estimar demanda máxima a
partir de energía en redes de distribución [Velander, 1952; Willis, 2004,
cap. 3]. El término lineal `a·E` recoge la parte proporcional al consumo, y el
término `b·√E` la dispersión: la desviación típica de la demanda crece con la
raíz del consumo, que es la consecuencia natural de agregar cargas
independientes.

**Por qué Velander y no el factor de carga.** El factor de carga fijo supone que
todos los clientes de una clase tienen la misma forma de curva. Velander captura
la **no linealidad**: un cliente que consume diez veces más no tiene diez veces
la demanda máxima. La diferencia importa sobre todo en no residenciales, que en
un padrón típico son el 2–3 % de los clientes y el 85 % de la energía.

Los parámetros `a` y `b` son **por clase tarifaria** (`carga.clases`), porque un
taller y un departamento no comparten curva. No están escritos en el código.

---

## 3. Coincidencia y agregación

```
FC(n) = A + B/√n
```

con `A + B = 1` **impuesto por validación**, de modo que `FC(1) = 1`: un solo
cliente coincide consigo mismo. Es la forma clásica del factor de coincidencia
[Gönen, 2014, §2.6; Sarfi et al., 1995]. Es monótona decreciente y está acotada
inferiormente por `carga.coincidencia.minimo` para que agregar mil clientes no
produzca un factor absurdamente pequeño.

La demanda agregada de un nodo con *n* clientes es `Σ P_max,i · FC(n)`.

**Propiedades verificadas con pruebas de propiedad** (hypothesis): `FC(1) = 1`,
monotonía y cota inferior. No es un comentario: si alguien cambia `A` o `B` en el
YAML de forma que `A + B ≠ 1`, el arranque falla.

---

## 4. Potencia reactiva, aparente y corriente

```
Q = P · tan(arccos φ)
S = √(P² + Q²) = P / cos φ
```

Definiciones de la potencia en régimen sinusoidal permanente [IEEE Std 1459-2010].
El `cos φ` es **por clase de consumo**, no global.

Corriente, según la configuración de fases — y este detalle es una fuente
habitual de error de factor √3:

| Conexión | Corriente |
|---|---|
| Monofásica (F-N) | `I = S / V_FN` |
| Bifásica (F-F) | `I = S / V_FF` |
| Trifásica | `I = S / (√3 · V_FF)` |

**Error del SIG que esto corrige.** Aplicar la fórmula trifásica a clientes
monofásicos, o el `cos φ` residencial a un industrial, produce desviaciones del
orden del 40 % en la corriente estimada, y de ahí en las pérdidas I²R.

---

## 5. Factor de pérdidas

```
F_p = k·F_c + (1 − k)·F_c²
```

Es la relación empírica de Buller–Woodrow entre factor de carga y factor de
pérdidas [Buller & Woodrow, 1928], que sigue siendo la referencia práctica en
distribución [Gustafson & Baylor, 1988; Gönen, 2014, §2.8].

**Propiedad física garantizada: `F_c² ≤ F_p ≤ F_c`** para `k ∈ [0,1]`. Los dos
extremos tienen significado:

- `k = 1` → `F_p = F_c`: carga plana todo el período.
- `k = 0` → `F_p = F_c²`: pico agudo sobre una base constante.

`k` se configura **por tipo de alimentador**
(`perdidas.k_por_tipo_alimentador`): urbano 0,30 y rural 0,20 por defecto, con
0,25 como respaldo. Un alimentador residencial no tiene la misma forma de curva
que uno industrial. Gustafson & Baylor obtuvieron valores del orden de 0,08–0,3
para sistemas reales; los configurados parten de ahí y **deben recalibrarse**
contra la medición de cabecera de la propia empresa en cuanto se disponga de
ella. Mientras no se recalibren, son un supuesto razonable, no una medición.

---

## 6. Pérdidas en conductores

Potencia de pérdida en el pico, por tramo: `P = 3·I²·R` (trifásico) o `I²·R`
(monofásico), con `R` corregida por temperatura:

```
R(T) = R_20 · [1 + α·(T − 20)]
```

`α = 0,00403 /°C` para aluminio (`perdidas.alpha_aluminio`), valor estándar de
tablas de conductores [Kersting, 2017, cap. 4]. La temperatura de operación es
configurable porque un conductor en la costa ecuatoriana no trabaja a la
temperatura de una tabla europea.

Energía perdida en el período:

```
E = P_pico · t · F_p
```

**Las impedancias no se inventan.** Se derivan de los 415 códigos de conductor
del catálogo CATALOGOESTRUCTURA de la empresa, con la resistencia de fabricante
verificada a menos del 3 % de desviación (prueba `test_survey.py`). Un catálogo
inventado habría dado números plausibles y falsos.

---

## 7. Pérdidas en transformadores

```
E = P0 · t                                   (vacío)
  + Pk · (S_max/S_nom)² · t · F_p · F_desb   (carga)
```

Modelo de dos componentes de la norma de ensayo [IEC 60076-1]: pérdidas en el
hierro (vacío), constantes mientras el transformador esté energizado, y pérdidas
en el cobre (carga), proporcionales al cuadrado de la carga.

> ### **P0 NO se multiplica por el factor de pérdidas**
>
> Es la regla de negocio nº 1 del sistema y el error más frecuente en cálculos de
> pérdidas de distribución. La pérdida en vacío depende de la **excitación**, no
> de la corriente de carga: el transformador la consume igual a las tres de la
> mañana con la red vacía. Multiplicarla por `F_p` (típicamente 0,2–0,3) la
> reduce a un tercio y **subestima sistemáticamente** la pérdida de
> transformación, que en una red de distribución con muchos transformadores poco
> cargados es la componente dominante.
>
> Hay una prueba dedicada que ejecuta el cálculo con la opción incorrecta
> activada, solo para demostrar cuánto cambia el resultado.

`F_desb` penaliza el desbalance entre fases: las pérdidas de un sistema
desbalanceado superan a las del equivalente balanceado de la misma energía,
porque `I²` es convexa.

---

## 8. Capacidad del banco de transformación

Un puesto de transformación CNEL puede tener varias unidades, y **la capacidad
del conjunto no es la suma**:

| Configuración | Capacidad trifásica | Fundamento |
|---|---|---|
| Unidad simple | `kVA₁` | — |
| Banco de 3 iguales | `3 · kVA_u` | Banco trifásico completo |
| **Delta abierto (V-V)** | **`√3 · kVA_u ≈ 1,732 · kVA_u`** | [Kersting, 2017, §8.6] |
| Banco desigual | `min(kVA) · n` | La menor limita el conjunto |
| Delta 4 hilos | `Σ kVA − 5 % · max(kVA)` | Derating de la unidad de mayor carga |

**El delta abierto es el caso que más se equivoca.** Dos unidades en V-V no dan
`2·kVA_u` sino `√3·kVA_u`: **un 13,4 % menos** de lo que sugiere la intuición.
Tomar la suma sobreestima la capacidad, hace parecer que el puesto va holgado y
oculta una sobrecarga real. De ahí también que un banco en delta abierto solo
admita las combinaciones de fase AB, BC o CA — ABC es físicamente imposible, y
el formulario de campo ni siquiera lo ofrece.

---

## 9. Pérdidas en medidores

```
E = Σ W_i · t / 1000
```

Consumo propio del medidor, por tipo (`perdidas.watts_medidor`):
electromecánico 1,8 W y electrónico 0,8 W en la configuración actual. Es una
componente pequeña pero
sistemática: con 20 000 medidores a 1 W son 14,4 MWh al mes, que sin contabilizar
aparecen como pérdida no técnica y mandan cuadrillas a buscar un hurto que no
existe.

---

## 10. Alumbrado público

```
E = (P_lámpara + P_balasto) · horas/noche · días/mes
```

Consumo declarado, no medido, según la potencia de catálogo. Se incluyen
**semáforos y cámaras de vigilancia** por la misma vía: son carga real no
medida, y si no se contabilizan su energía queda como PNT falsa.

El balasto sale del catálogo CATALOGOESTRUCTURA, no de un supuesto.

---

## 11. Flujo de potencia

**Método: barrido iterativo hacia atrás/adelante** (*backward-forward sweep*),
el algoritmo estándar para redes radiales de distribución [Shirmohammadi et al.,
1988; Kersting, 2017, cap. 10]. Se elige frente a Newton-Raphson porque las redes
de distribución son **radiales y mal condicionadas** (relación R/X alta), donde
Newton converge mal, y el barrido aprovecha la topología de árbol para converger
en pocas iteraciones sin invertir matrices.

Dos motores:

- **Monofásico equivalente**: rápido, válido si el desbalance es pequeño.
- **Trifásico con neutro explícito** (por defecto): calcula corriente y pérdida
  de neutro, e informa el desbalance máximo. En redes de distribución
  latinoamericanas, con mucha carga monofásica repartida, el desbalance es la
  norma y el equivalente monofásico subestima las pérdidas.

**Validación.** El motor se contrasta contra casos analíticos resueltos a mano y
contra **OpenDSS** (EPRI), con desviación < 2 % en pérdidas de línea, y ~0 % en
un caso de media tensión controlado.

---

## 12. Balance de energía y PNT

```
E_entrada + E_transferida
  − E_facturada − E_alumbrado_no_medido − E_consumos_propios − E_no_suministrada
  = Pérdida total

PNT = Pérdida total − Pérdida técnica
```

Es la ecuación de balance de la metodología de referencia del sector
[CIER, 2016; World Bank / Antmann, 2009], donde la pérdida no técnica se obtiene
**por diferencia**: se mide lo que entra, se cuenta lo que se factura, se calcula
lo técnico, y lo que sobra es lo que se está perdiendo sin explicación técnica.

### Los tipos de balance no son un adorno

| Tipo | Cuándo | Qué se puede hacer con el número |
|---|---|---|
| `MEDIDO` | Hay medición de cabecera | Contrastable. Se puede declarar |
| `INDICATIVO` | No la hay: la entrada se estima | Orienta el trabajo. **No se declara como pérdida** |
| `PARCIAL` | Falta parte del universo | Consolidado incompleto, marcado como tal |

**El peor tipo manda hacia arriba.** Si un solo alimentador de una subestación es
`INDICATIVO`, el balance de la subestación es `INDICATIVO`. La energía se suma;
la garantía de que el número es verificable, no. Presentar el consolidado de una
unidad de negocio como «medido» cuando el 30 % de sus alimentadores es estimado
es el fallo de credibilidad más caro de este tipo de proyecto.

**Los porcentajes se recalculan, nunca se promedian.** Promediar el 5 % de un
alimentador de 1 000 000 kWh con el 50 % de uno de 20 000 kWh da 27,5 %, y la
pérdida real del conjunto es 5,9 %. Se recalcula siempre sobre los totales.

### Controles de coherencia C01–C06

Antes de que un balance salga a ninguna parte:

| Control | Salta cuando | Qué suele significar |
|---|---|---|
| C01 | PNT negativa | Transferencia no reportada, AP sobreestimado o clientes mal asignados |
| C02 | PNT > umbral (60 %) | Casi siempre un error de datos, no un hurto masivo |
| C04 | Pérdida técnica > 20 % | Parámetros de red mal cargados |
| C05 | Cobertura de clientes baja | Falta padrón |
| C06 | Cobertura de energía < 70 % | El balance no representa al alimentador |

Una PNT negativa **bloquea la publicación**. Es un resultado imposible —los
clientes no devuelven energía a la red— y publicarlo destruiría la credibilidad
de todo lo demás.

---

## 13. Incertidumbre: Monte Carlo

La pérdida técnica no es un número, es un rango: depende de parámetros que se
conocen con incertidumbre (`k`, temperatura, `cos φ`, impedancias). Se propaga
por **simulación de Monte Carlo** [Metropolis & Ulam, 1949] muestreando los
parámetros en sus rangos y se reportan **P10 / P50 / P90**.

**Por qué importa.** Si la pérdida técnica está entre 1 200 y 1 680 kWh, la PNT
hereda esa banda. Dar la PNT como un número exacto cuando su insumo tiene un 30 %
de incertidumbre invita a decisiones que la evidencia no sostiene.

---

## 14. Señales de hurto (S1–S9)

Cada señal produce una **intensidad en [0, 1]**, no un sí/no. Las nueve están en
`ntl/signals.py`.

| Señal | Qué detecta | Fundamento |
|---|---|---|
| **S1** | Caída sostenida ≥X % durante ≥N meses **seguida de recuperación** | Patrón canónico de manipulación temporal [Jokar et al., 2016] |
| **S3** | Ruptura de nivel permanente sin causa comercial | Detección de puntos de cambio (`ruptures`, [Truong et al., 2020]) |
| **S4** | Consumo cero con servicio activo y sin suspensión | Regla del sector: un servicio activo consume |
| **S5** | Muy por debajo de su **grupo par** | Comparación con clientes equivalentes [Nagi et al., 2010] |
| **S7** | Planitud anómala (CV muy bajo) | Consumo declarado fijo, típico de medidor frenado |
| **S8** | Muy por debajo de sus vecinos del **mismo transformador** | Rasgos de vecindad [Glauner et al., 2016] |
| **S9** | Déficit contra **su propia** historia | Detecta el cliente que siempre consumió poco y ahora menos |

**La distinción que sostiene S5 y S8.** Glauner et al. demostraron que los
rasgos de vecindad —comparar a un cliente con los de su mismo transformador—
mejoran sustancialmente la detección de PNT frente a mirar solo su serie
individual, porque controlan por factores locales (clima, nivel
socioeconómico, tipo de vivienda) que afectan a todos por igual.

**S9 existe porque S5 no basta.** Un cliente que siempre consumió poco no se
sale de su grupo par aunque robe: su referencia es él mismo.

---

## 15. Grupo par y segmentación

El grupo par se construye jerárquicamente:

```
clase de consumo × nivel de tensión × nº de fases × CLIRLSCOD (ruta de lectura)
```

y **degrada de forma controlada** cuando no hay suficientes miembros
(`segmentacion.minimo_pares`), quitando el nivel más fino primero. Cada
degradación reduce un **factor de confianza** que pondera S5.

> ### El estrato de consumo NO entra en la clave del grupo par
>
> Es la regresión más grave que se ha corregido en este sistema. Si el estrato
> —que se deriva del propio consumo— formara parte de la clave, se estaría
> comparando a cada cliente contra otros que consumen lo mismo que él, y por
> construcción nadie destacaría. Con el estrato dentro, el lift de S5 caía de
> **10,0× a 2,4×**. Es circularidad pura, y hay una prueba dedicada que impide
> reintroducirla.

**Sin grupo par se reporta, no se inventa.** Un cliente sin comparables sale
marcado como tal en vez de compararse contra una mediana global que mezclaría
industriales con residenciales.

---

## 16. Ranking por consenso

El score es el **promedio de los rangos percentiles** de las señales activas,
más un score no supervisado (Isolation Forest) cuando está disponible.

**Por qué rangos y no valores.** Las señales tienen escalas y distribuciones
incomparables: un CV de 0,05 y una caída del 60 % no se pueden sumar. Pasar a
rangos percentiles las hace conmensurables y hace el consenso **robusto a
valores atípicos** de cualquier señal individual — es la lógica de la agregación
de rangos [Dwork et al., 2001].

**Por qué consenso y no un clasificador entrenado.** Porque al empezar no hay
etiquetas fiables (ver §18). Cuando la empresa acumula casos confirmados, los
pesos **se recalibran** contra ellos.

Cada cliente sale con sus **razones en lenguaje operativo** (las tres señales más
fuertes). Una cuadrilla que recibe «score 0,87» no sabe qué mirar; una que recibe
«consumo cero con servicio activo desde marzo» sí.

---

## 17. Energía recuperable

Estimación de kWh/mes que se recuperarían al corregir el hurto:

```
recuperable = min( consumo_esperado_del_grupo_par − consumo_actual ,
                   tope por el segmento )
```

**Se calcula dentro del segmento del cliente**, no contra una mediana global. La
diferencia no es sutil: la mediana global **subestima gravemente al industrial**
—donde está la energía— y **le inventa recuperable al pequeño residencial**. Se
toma el máximo de dos estimadores y se usa mediana robusta.

Es lo que permite ordenar por **rendimiento por visita** en lugar de por score:
una visita industrial puede rendir 49 veces más que una residencial, y una
cuadrilla tiene ocho horas.

---

## 18. Validación contra la base de multados

Métricas: **lift** por decil, **AUC** y precisión.

> ### Los no multados NO son negativos confiables
>
> Que un cliente no haya sido multado no significa que no robe: significa que
> nadie fue a mirar. Entrenar un clasificador tratando a los no multados como
> negativos enseña al modelo a reproducir **dónde ha inspeccionado la empresa
> históricamente**, no dónde hay hurto.
>
> Por eso se usa **aprendizaje PU** (positive-unlabeled) con el estimador de
> [Elkan & Noto, 2008]: hay positivos etiquetados y un conjunto **sin etiquetar**,
> no negativos.

**Fecha de corte contra la fuga de información.** Las señales de un cliente
multado se calculan **solo con meses anteriores a la multa**. Sin este corte, el
modelo «predice» algo que ya había ocurrido y el lift sale inflado — el error
metodológico más común al validar detectores de PNT.

**Control negativo.** Se compara siempre contra un ranking aleatorio. Un
detector cuyo lift no supera claramente al azar no sirve, por bonita que sea su
AUC.

**Y un predio cerrado no es un cliente inocente.** De los once hallazgos de
campo, solo dos grupos son etiquetas utilizables. `PREDIO_CERRADO` y
`ACCESO_NEGADO` no son negativos: meterlos como tales enseñaría al modelo que un
cliente sospechoso al que nadie pudo entrar está limpio.

Resultados medidos sobre escenario sintético con verdad conocida: **lift 16,7× y
AUC 0,981** con 20 000 clientes; **lift 12,6× y AUC 0,861** con 1 200.

---

## 19. Focalización: dónde inspeccionar

Se ordena por **energía recuperable por visita**, no por score:

```
prioridad = recuperable_kWh_mes / visitas_necesarias
```

en siete niveles: alimentador, zona de protección, ramal, transformador, sector
geográfico, ruta comercial (`CLIRLSCOD`) y cliente.

**El sector geográfico se deriva de la coordenada**, no del orden del cálculo.
Así una orden emitida hoy sigue apuntando al mismo sitio físico después de cargar
el mes siguiente o de modificar la topología. Un identificador derivado del orden
haría que las cuadrillas fueran a otro lado tras cada recálculo.

**Baja confiabilidad ≠ prioridad.** Un objetivo con score alto en una zona con
datos malos se marca como **problema de datos**: hay que arreglar el dato antes
de mandar a nadie.

---

## 20. Detección de transferencias no reportadas

Cuando se abre un enlace y parte de la carga de un alimentador pasa a otro sin
que nadie lo registre, aparece una PNT falsa en uno y negativa en el otro.

Se buscan **pares de alimentadores con escalones simultáneos y opuestos** en la
energía de cabecera, tras **descontar el movimiento común del sistema** (si todo
el sistema sube un 8 % por calor, no es una transferencia).

- La magnitud se pondera por **inverso de la varianza**: un escalón de 500 kWh en
  un alimentador estable pesa más que el mismo escalón en uno ruidoso.
- La simetría se evalúa sobre la **razón** del escalón, no sobre kWh absolutos.
  Dos alimentadores de tamaños muy distintos nunca darían simetría en kWh
  absolutos, y el par real se descartaría — un defecto que tuvimos y corregimos.
- **Con menos de 3 períodos se devuelve `NO_APLICABLE_POR_DATOS`**, no
  candidatos. Dos puntos siempre definen un escalón; con eso se inventarían
  transferencias sobre ruido.

---

## 21. Lo que NO se calcula, y por qué

Ser explícito sobre los límites es parte de la honestidad del número.

| No se hace | Por qué |
|---|---|
| **Clasificador supervisado de hurto como motor principal** | Requiere etiquetas fiables que la mayoría de distribuidoras no tiene. Se usa consenso de señales y se recalibra con los casos que van apareciendo |
| **Curvas de carga horarias** | El insumo es facturación mensual. Inventar una curva horaria daría una precisión aparente sin respaldo |
| **Salto de tensión del transformador (MT→BT) dentro del barrido** | Pendiente declarado. Afecta a la comparación con OpenDSS por alimentador completo |
| **Reproducción IEEE 13/34/123 completa** | Pendiente declarado. El esquema de base ya soporta los resultados |
| **Expresiones calculadas en el formulario móvil** | El score lo produce el backend con la historia completa; una fórmula en el teléfono daría otro número que el informe |
| **Atribuir causa a un cliente concreto** | El sistema **prioriza dónde mirar**. Quien determina si hay hurto es la inspección, y ninguna decisión sancionatoria se toma con un score |

---

## Referencias

**Pérdidas no técnicas y detección de hurto**

- Glauner, P., Meira, J. A., Dolberg, L., State, R., Bettinger, F., Rangoni, Y.
  (2016). *Neighborhood Features Help Detecting Non-Technical Losses in Big Data
  Sets*. IEEE/ACM Int. Conf. on Big Data Computing, Applications and
  Technologies (BDCAT).
- Glauner, P., Meira, J. A., Valtchev, P., State, R., Bettinger, F. (2017).
  *The Challenge of Non-Technical Loss Detection Using Artificial Intelligence:
  A Survey*. Int. Journal of Computational Intelligence Systems, 10(1), 760–775.
- Buzau, M. M., Tejedor-Aguilera, J., Cruz-Romero, P., Gómez-Expósito, A.
  (2019). *Hybrid Deep Neural Networks for Detection of Non-Technical Losses in
  Electricity Smart Meters*. IEEE Transactions on Power Systems, 34(2),
  1254–1263.
- Zheng, Z., Yang, Y., Niu, X., Dai, H.-N., Zhou, Y. (2018). *Wide and Deep
  Convolutional Neural Networks for Electricity-Theft Detection*. IEEE
  Transactions on Industrial Informatics, 14(4), 1606–1615.
- Jokar, P., Arianpoo, N., Leung, V. C. M. (2016). *Electricity Theft Detection
  in AMI Using Customers' Consumption Patterns*. IEEE Transactions on Smart
  Grid, 7(1), 216–226.
- Messinis, G. M., Hatziargyriou, N. D. (2018). *Review of Non-Technical Loss
  Detection Methods*. Electric Power Systems Research, 158, 250–266.
- Nagi, J., Yap, K. S., Tiong, S. K., Ahmed, S. K., Mohamad, M. (2010).
  *Nontechnical Loss Detection for Metered Customers Using Support Vector
  Machines*. IEEE Transactions on Power Delivery, 25(2), 1162–1171.
- Antmann, P. (2009). *Reducing Technical and Non-Technical Losses in the Power
  Sector*. World Bank Background Paper for the Energy Sector Strategy.
- CIER (2016). *Metodología de cálculo y control de pérdidas de energía
  eléctrica*. Comisión de Integración Energética Regional.

**Ingeniería de distribución**

- Kersting, W. H. (2017). *Distribution System Modeling and Analysis*, 4ª ed.
  CRC Press.
- Gönen, T. (2014). *Electric Power Distribution Engineering*, 3ª ed. CRC Press.
- Willis, H. L. (2004). *Power Distribution Planning Reference Book*, 2ª ed.
  Marcel Dekker.
- Shirmohammadi, D., Hong, H. W., Semlyen, A., Luo, G. X. (1988). *A
  Compensation-Based Power Flow Method for Weakly Meshed Distribution and
  Transmission Networks*. IEEE Transactions on Power Systems, 3(2), 753–762.
- Buller, F. H., Woodrow, C. A. (1928). *Load Factor–Equivalent Hours Values
  Compared*. Electrical World, 92(2), 59–60.
- Gustafson, M. W., Baylor, J. S. (1988). *Approximating the System Losses
  Equation*. IEEE Transactions on Power Systems, 3(3), 850–855.
- Velander, S. (1952). *Estimation of Distribution Transformer Load*.
  Elteknik (Suecia).
- Sarfi, R. J., Salama, M. M. A., Chikhani, A. Y. (1995). *Distribution System
  Reconfiguration for Loss Reduction: A Review*. Electric Power Systems
  Research.

**Normas**

- IEC 60076-1: *Power transformers — General* (ensayo de pérdidas en vacío y
  con carga).
- IEEE Std 1459-2010: *Definitions for the Measurement of Electric Power
  Quantities*.

**Métodos estadísticos**

- Elkan, C., Noto, K. (2008). *Learning Classifiers from Only Positive and
  Unlabeled Data*. KDD '08.
- Truong, C., Oudre, L., Vayatis, N. (2020). *Selective Review of Offline Change
  Point Detection Methods*. Signal Processing, 167.
- Huber, P. J., Ronchetti, E. M. (2009). *Robust Statistics*, 2ª ed. Wiley.
- Hyndman, R. J., Athanasopoulos, G. (2021). *Forecasting: Principles and
  Practice*, 3ª ed. OTexts.
- Dwork, C., Kumar, R., Naor, M., Sivakumar, D. (2001). *Rank Aggregation
  Methods for the Web*. WWW '10.
- Metropolis, N., Ulam, S. (1949). *The Monte Carlo Method*. Journal of the
  American Statistical Association, 44(247), 335–341.
- Liu, F. T., Ting, K. M., Zhou, Z.-H. (2008). *Isolation Forest*. ICDM '08.

---

## Dónde está cada cálculo en el código

| Cálculo | Archivo |
|---|---|
| Promedio multi-mes | `src/ptnt/load/averaging.py` |
| Demanda, coincidencia, Q/S/I | `src/ptnt/load/demand.py` |
| Factor de pérdidas | `src/ptnt/losses/factors.py` |
| Conductores | `src/ptnt/losses/conductors.py` |
| Transformadores y bancos | `src/ptnt/losses/transformers.py` |
| Medidores | `src/ptnt/losses/meters.py` |
| Alumbrado | `src/ptnt/lighting/` |
| Flujo 1φ y 3φ | `src/ptnt/powerflow/` |
| Balance y controles | `src/ptnt/balance/energy_balance.py` |
| Monte Carlo | `src/ptnt/losses/montecarlo.py` |
| Señales S1–S9 | `src/ptnt/ntl/signals.py` |
| Señales de red N1/N3/N4 | `src/ptnt/ntl/network_signals.py` |
| Scoring y recuperable | `src/ptnt/ntl/scoring.py` |
| Grupo par | `src/ptnt/segment/` |
| Multados y PU learning | `src/ptnt/ntl/confirmed.py` |
| Focalización | `src/ptnt/survey/` |
| Transferencias | `src/ptnt/anomalies/transfers.py` |

Cada uno tiene pruebas que fijan sus propiedades: ver
[`PRUEBAS.md`](PRUEBAS.md).
