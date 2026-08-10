# Segmentación de clientes

## Por qué el comportamiento es distinto y por qué importa

Un taller metalmecánico, un supermercado y un departamento no se parecen en nada:
distinto nivel de consumo, distinta estacionalidad, distinta variabilidad y
distinta forma de hurtar. Comparar a todos contra la misma referencia produce dos
errores caros **a la vez**:

| Error | A quién afecta | Consecuencia |
|---|---|---|
| **Falso positivo** | residenciales pequeños | Un cliente de 40 kWh/mes queda "muy por debajo" de una mediana global inflada por los grandes, y se le manda cuadrilla sin motivo |
| **Falso negativo** | comerciales e industriales | Un cliente de 20 000 kWh/mes que hurta el 20 % sigue muy por encima de la mediana global: **ninguna señal de consumo bajo se activa** y nunca se lo visita |

El segundo error es el caro. Es donde está la energía: un solo industrial
recuperado vale lo que cientos de residenciales.

## Los cuatro ejes

Extraídos de `ATRIBUTOSCONSUMIDOR` (`DESTARI` / `TIPOTARIFA`, `CDAFAS`,
`CLIRLSCOD`):

1. **Clase tarifaria** — residencial, comercial, industrial, oficial, asistencia
   social, bombeo de agua, alumbrado público. Primer corte, porque define el
   patrón de uso.
2. **Nivel de tensión** — BT / MT / AT. Un cliente de media tensión nunca se
   compara con uno de baja: son pocos, enormes y medidos de otra forma.
3. **Modalidad de medición** — con o sin demanda facturada.
4. **Estrato de consumo** — bloques de kWh/mes dentro de cada clase.

La clase y la tensión se deducen del **texto** de la tarifa, no de una igualdad
exacta: `DESTARI` trae `"INDUSTRIAL CON DEMANDA MEDIA TENSION"`, no
`"MT Industrial"`. Cuando el texto no es reconocible, el cliente queda
`NO_CLASIFICADO` y se reporta — **nunca se adivina "residencial" por defecto**,
porque clasificar mal a un industrial como residencial es justo el error a evitar.

## La lección que costó medir: el estrato NO va en el grupo par

La intuición dice que el grupo par ideal incluye el estrato de consumo: comparar
clientes de tamaño parecido. **Es exactamente al revés.**

Estratificar por consumo para comparar consumos es **circular**. Un cliente que
hurta durante toda la ventana tiene su nivel base deprimido, cae en un estrato
bajo y termina comparado contra clientes genuinamente pequeños — precisamente
donde deja de destacar.

Medido sobre el escenario de prueba:

| Clave del grupo par | Lift de la señal S5 |
|---|---|
| clase × tensión × **estrato** × ruta | **2,4×** |
| clase × tensión × ruta | 10,0× |
| clase × tensión × fases × ruta | **10,0×** ← implementado |

Incluir el estrato **vuelve la señal casi inútil**. Por eso el grupo par se arma
solo con claves **exógenas al consumo**, y el estrato se conserva para los dos
usos donde sí es correcto y valioso: reportar dónde está la energía y estimar el
recuperable.

## Grupo par jerárquico con degradación

Cuanto más específico el grupo, más significativo es quedar por debajo de él —
pero los grupos se vacían. Se intenta el más fino y se retrocede:

| Nivel | Clave | Confianza |
|---|---|---|
| 1 | clase × tensión × fases × ruta | 1,00 |
| 2 | clase × tensión × ruta | 0,90 |
| 3 | clase × tensión × fases | 0,70 |
| 4 | clase × tensión | 0,60 |
| 5 | clase | 0,40 |

La **confianza pondera la intensidad de S5**. Quedar bajo pares de la misma ruta
es evidencia fuerte; quedar bajo "toda la clase residencial" es apenas un indicio,
porque ese grupo mezcla departamentos con casas grandes. Sin ponderar, las
comparaciones gruesas —que son muchas más— ahogarían a las finas en el ranking.

Cada cliente sale con `grupo_par_id`, `grupo_par_nivel` y `grupo_par_confianza`,
para poder responder **"¿cuánto vale la señal S5 en esta corrida?"**. Si la mayoría
quedó en el nivel más general, conviene revisar la calidad de `DESTARI` y
`CLIRLSCOD` antes que ajustar umbrales.

## S9 — la señal para quien no tiene pares

En industrial y oficial hay pocos clientes y son enormemente heterogéneos:
comparar una fábrica de hielo contra una imprenta no dice nada. Para ellos la
única referencia válida es **su propia historia**, y eso mide S9: el déficit
sostenido contra el nivel base propio (percentil alto de los 36 meses).

Se usa un percentil alto y no la media porque responde "¿de qué tamaño es este
cliente?" y no "¿cuánto consumió?": un cliente que hurta parte de la ventana
conserva meses en su nivel real.

## Energía recuperable: el defecto que esto corrige

Antes, el recuperable se estimaba contra una **mediana global** de todo el padrón:

```
recuperable = max(mediana_global − consumo_actual, 0)
```

Con una mediana global de ~150 kWh:

* Un **industrial** de 20 000 kWh/mes que hurta el 50 % sigue estando muy por
  encima de 150 → su recuperable sale **cero**. Invisible.
* Un **residencial** honesto de 60 kWh/mes queda por debajo → se le atribuye un
  recuperable **inventado** de 90 kWh.

Ahora se toma el **máximo de dos estimadores dentro del segmento**:

```
recuperable = max( base_propia − actual ,  mediana_del_grupo_par − actual ,  0 )
```

que cubren las dos formas de hurto: la **caída respecto de sí mismo** (consumía
800, ahora marca 300) y el **déficit respecto de sus pares** (siempre marcó bajo,
no hay caída que detectar). La mediana del grupo, y no la media, porque el propio
grupo puede contener otros hurtos.

## Resultado medido

Sobre el escenario de prueba (3 000 clientes, 36 meses, 5 % de hurto inyectado),
comparando el mismo pipeline con y sin segmentación:

| Métrica (top 5 % del ranking) | Sin segmentar | Segmentado |
|---|---|---|
| Lift de detección | 12,20× | **12,20×** |
| Recall (top 10 %) | 73,8 % | **76,6 %** |
| **Energía recuperable priorizada** | 10 898 kWh/mes | **284 425 kWh/mes** |

**No se pierde capacidad de detectar y se prioriza 26× más energía con el mismo
número de visitas.** Ese es todo el argumento.

## A quién conviene ir a revisar

`ptnt analizar` publica la tabla de **rendimiento esperado por visita**, calculada
*dentro* de cada clase y no sobre el ranking global (si se tomara el top global,
los residenciales —que son la enorme mayoría— coparían la lista):

```
Clase          Visitas   kWh recuperables   kWh por visita
INDUSTRIAL           2            6 888,9          3 444,5
COMERCIAL           12            6 699,4            558,3
RESIDENCIAL         47            3 283,3             69,9

→ Una visita en 'INDUSTRIAL' rinde 49× más energía que una en 'RESIDENCIAL'.
→ Los no residenciales son el 22 % de los clientes pero concentran el 75,4 % de
  la energía recuperable: conviene una cuadrilla dedicada a ellos, separada del
  barrido masivo residencial.
```

Y una lista aparte de **grandes clientes con indicios**, para revisión
**individual**. Se listan fuera del ranking por una razón económica: su posición
relativa los esconde. Un industrial con score 0,55 queda en la mitad de una lista
de miles y nunca se visita, pero un desvío del 10 % en él equivale a cientos de
residenciales completos. La regla operativa estándar es revisar el universo de
grandes clientes de forma **censal y periódica**, no por ranking.

## Configuración

```yaml
segmentacion:
  habilitada: true
  columna_tarifa: "tariff_description"   # DESTARI / TIPOTARIFA
  percentil_consumo_base: 75.0
  min_pares: 8
  cortes_residencial_kwh: [50, 100, 130, 200, 300, 500, 1000]
  cortes_no_residencial_kwh: [200, 1000, 5000, 20000, 100000]
  umbral_gran_cliente_kwh_mes: 5000.0
```

El corte de 130 kWh corresponde al límite de la **Tarifa Dignidad** en
Costa/Oriente/Insular (110 en la Sierra). **Los cortes deben ajustarse contra el
histograma real del padrón antes de producción**: los valores por defecto siguen
los bloques del pliego tarifario, pero la distribución real de cada distribuidora
manda.

## Efecto lateral corregido: la clase tarifaria del cálculo de potencia

Al implementar esto salió a la luz un defecto que también afectaba a la base real.
El catálogo de clases (`carga.clases`) se buscaba por **igualdad exacta**:

```python
clase = cfg.clases.get(tarifa_desc, default)   # "BT Residencial"
```

Como `DESTARI` trae texto libre, **ninguna descripción real coincidía** y todos los
clientes caían silenciosamente a la clase por defecto — asignando coeficientes de
Velander y `cosφ` residenciales a industriales de media tensión. El cálculo no
fallaba: simplemente daba mal.

Ahora la resolución es semántica (`resolver_clave_config`): se clasifican tanto la
descripción como las claves del catálogo y se emparejan por (clase, tensión),
degradando a solo clase. La misma corrección se aplicó a la validación de rangos
plausibles del parser, cuyas máscaras quedaban siempre vacías y por tanto nunca
advertían de un error de separador de miles.

## Limitación honesta

Si el hurto lleva activo **toda** la ventana de 36 meses, el nivel base propio
también está deprimido: S9 no lo verá y el cliente parecerá pequeño. Ese caso lo
cubren las señales de **red** (N1 residuo de zona, N3 balance de totalizador) y la
comparación contra el grupo par geográfico — no la historia propia. Es la razón
de fondo para no depender de una sola familia de señales.

---

Ver también: [Focalización](FOCALIZACION.md) · [Diagnóstico](DIAGNOSTICO.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Pruebas](PRUEBAS.md)
