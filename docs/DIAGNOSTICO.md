# Diagnóstico de credibilidad y validación con casos reales

Antes de reportar PNT como hurto hay que descartar lo que **no es hurto**. Este
diagnóstico reúne las tres familias de anomalías que explican la mayoría de los
balances que no cierran, más la validación del detector contra los casos
confirmados.

```bash
ptnt diagnostico --csv consumos_36m.csv --cabecera cabecera.csv --multados multados.csv
```

## 1. Transferencias entre alimentadores no reportadas (§10.4)

Es la **causa número uno de balances que no cierran**: una maniobra no registrada
pasa carga de A a B y produce PNT falsamente alta en A y negativa en B.

**Método:** se buscan pares de alimentadores con cambios **abruptos, de signo
opuesto, magnitud similar y sostenidos** en el mismo mes. Un cambio que revierte al
mes siguiente es un pico, no una transferencia.

**Restricción honesta:** con **un solo mes** de cabecera la detección por serie
temporal **no es posible**. El sistema devuelve `NO_APLICABLE_POR_DATOS` en vez de
inventar candidatos — y esa es la razón de peso para conseguir los 8 meses.

Los alimentadores implicados se **excluyen del ranking de sospecha** hasta aclarar.

## 2. Clientes faltantes (vinculación comercial ↔ SIG)

| Dirección | Qué significa | Efecto |
|---|---|---|
| **CSV_SIN_SIG** | Factura pero no está en la red | Su energía no se asigna: **infla la PNT** de la zona |
| **SIG_SIN_CSV** | Está en la red pero no factura | Cliente sin activar, retiro no depurado o **consumo no facturado** |

La métrica rectora es el **% de energía vinculada** (no el % de cuentas): es el
techo de calidad del balance. Por debajo del umbral el balance se degrada a
INDICATIVO, porque la PNT sería indistinguible del hueco sin ubicar.

Los faltantes se agrupan además **por ruta comercial**, porque suelen concentrarse
en la misma ruta — y eso apunta a un problema de gestión, no a hurto disperso.

## 3. Alimentadores con valores incoherentes

Consolida en una vista todo lo que hace **no creíble** un resultado, con su causa
probable ordenada por sensibilidad:

| Código | Incoherencia | Severidad |
|---|---|---|
| C01 | PNT negativa | **BLOQUEANTE** — no se publica |
| C03 | Facturado > entrada | **BLOQUEANTE** |
| C02 | PNT improbablemente alta | ALTA — excluir del ranking |
| C04 | Pérdidas técnicas excesivas | ALTA |
| C06 | Cobertura de energía insuficiente | ALTA — degradar a INDICATIVO |
| TRANSFER | Transferencia probable no reportada | ALTA — excluir del ranking |
| VINCULACION | Energía sin vincular sobre el umbral | ALTA |
| CONFIABILIDAD | Índice de confiabilidad bajo | ALTA |

Para C01 la causa se reporta ordenada: *1) transferencia no registrada;
2) sobreestimación del alumbrado público; 3) error de asignación de clientes*.

## 4. Base de clientes multados: la validación que faltaba

La especificación asumía que **no** existía histórico de hurto confirmado. Si la
distribuidora **sí lo tiene**, es el activo de mayor valor después de la cabecera:

**a) Medición honesta de precisión.** Sin etiquetas, "el detector funciona" no tiene
respaldo. Con ellas se obtiene precisión, recall, **lift** y aciertos por decil.

```
Precisión en el top 1%   73.3%
Precisión en el top 5%   36.0%
Recall en el top 10%     58.7%
Lift en el top 5%        11.7×      ← 11,7 veces más hurtos que inspeccionar al azar
AUC                      0.828
Posición mediana          2.4%      del ranking
```

**b) Calibración de señales.** Se mide qué señal discrimina de verdad:

```
S7 (planitud)      lift 56.9  → Aumentar peso
S4 (cero activo)   lift 40.2  → Aumentar peso
S5 (grupo par)     lift  2.4  → Aumentar peso
S1 (caída/recup.)  lift  0.7  → Revisar umbral o desactivar (no discrimina)
```

**c) Aprendizaje PU (Positivo–No etiquetado).** Es el método correcto: los multados
son positivos confiables, pero **los no multados NO son negativos** — solo son no
inspeccionados o no detectados. Tratarlos como negativos es el error que arruina
estos proyectos. Se usa el estimador de **Elkan–Noto**, que corrige la probabilidad
dividiendo por la propensión `c = P(etiquetado | positivo)`.

**Fuga temporal:** las multas tienen fecha. Una señal calculada con meses
posteriores a la multa "predice" algo que ya pasó, así que las etiquetas se filtran
por fecha de corte y se avisa si no hay fechas.

**Formato esperado** (`--multados`): CSV con `contract_account`, `fecha_multa` y,
opcionalmente, `kwh_recuperado` y `tipo_hallazgo`.

## 5. Ruta comercial (CLIRLSCOD) como unidad de levantamiento

La ruta de lectura es la unidad con la que la distribuidora **ya** organiza el
trabajo de campo: un lector la recorre completa, en un orden establecido. Por eso
es el mecanismo natural para seleccionar qué levantar — no hay que inventar un
recorrido, ya existe.

Se selecciona por **dos criterios**, y basta con uno:

* **Sospecha** — concentración de clientes con señales de PNT en la ruta.
* **Incoherencia** — rachas de ceros, exceso de lecturas estimadas, consumo medio
  colapsado frente a rutas comparables, o clientes que facturan sin estar en el SIG
  concentrados en la misma ruta. Una ruta incoherente suele señalar un **problema
  del lector o del proceso**, y ese hallazgo vale tanto como el hurto.

`RUTA_COMERCIAL` es un nivel más del plan de levantamientos, con su acción propia:
*"Relectura y verificación de toda la ruta comercial"*.

## Salidas

| Archivo | Contenido |
|---|---|
| `diagnostico.json` | Resumen consolidado de las cuatro secciones |
| `transferencias.csv` | Pares con transferencia probable |
| `clientes_csv_sin_sig.csv` | Facturan sin estar en el SIG (ordenados por energía) |
| `clientes_sig_sin_csv.csv` | En la red sin facturación |
| `alimentadores_incoherentes.csv` | Incoherencias con causa probable |
| `validacion_por_decil.csv` | Curva de ganancia contra casos confirmados |
| `calibracion_senales.csv` | Lift por señal y recomendación |
