# Guía de operación paso a paso

Cómo ejecutar el sistema, **qué significa cada resultado**, cómo ir cargando
información nueva y qué ocurre cuando cambia la topología de la red.

> Para ver todo el proceso funcionando con datos ficticios:
> ```bash
> python scripts/demo_completa.py
> ```
> Ejecuta las 9 etapas y muestra los resultados de cada una.

---

## 0. Preparación (una sola vez)

```bash
pip install -e ".[all]"
ptnt verificar-config -c config/base.yaml     # falla si falta un parámetro obligatorio
ptnt probar-fuentes                            # verifica conectividad de cada origen
ptnt crear-usuario analista --rol analyst      # tablero
ptnt crear-usuario jefatura --rol viewer       # visor de solo lectura
```

Las credenciales de las bases **no van en el YAML**: se declaran como variables de
entorno (ver [SEGURIDAD.md](SEGURIDAD.md)).

---

## 1. Cargar la red y versionarla

```bash
ptnt migrar --feeder F001        # lee de la fuente configurada (FGDB / Oracle-ArcSDE / SQL)
```

**Qué hace:** convierte la red del origen al modelo canónico y la registra en el
versionado. La primera vez es un **ALTA**.

**Qué obtienes:**
```
Acción: ALTA · versión 1
Alimentador F001 dado de alta: 337 tramos, 164 clientes.
Inventario: {tramos: 337, puestos_transformacion: 8, luminarias: 16,
             semaforos_camaras: 2, seccionadores: 8, capacitores: 1, postes: 8}
```
Si el inventario no cuadra con lo que esperas, el problema está en el origen, no
en el análisis. **Revísalo antes de seguir.**

---

## 2. Analizar el consumo comercial

```bash
ptnt analizar --csv consumos_36m.csv
```

**Cómo leer el resultado:**

| Salida | Qué significa | Qué hacer |
|---|---|---|
| `Método de promedio: media_recortada (12 meses)` | El consumo base **no** es el último mes, sino un promedio robusto de 12 | Si tu operación tiene estacionalidad fuerte, prueba `estacional` en el YAML |
| `Δ Potencia SIG→corregido: −219.735 kW (−91,0 %)` | Cuánto se aparta la potencia del SIG de la recalculada | Un delta enorme confirma que el cálculo del SIG estaba mal; revisa el informe de reconciliación por causa |
| `Clientes sospechosos: 60` | Clientes sobre el umbral de consenso de señales | Todavía **no** son objetivos de campo: falta cruzarlos con la red (paso 5) |

⚠️ El ranking de clientes por sí solo **no es un plan de trabajo**. Un cliente con
score alto en una zona cuyo balance cierra bien es más probablemente un problema de
datos. Por eso el paso 5 es el que manda a campo.

---

## 3. Analizar la red: balance y pérdidas

```bash
ptnt analizar-red --trifasico --opendss
```

**Cómo leer el balance:**

```
BALANCE (MEDIDO)                    ← si dice INDICATIVO, la PNT NO es verificable
  Entrada (cabecera)    :   45.668 kWh
  − Facturado           :   42.015 kWh
  − Alumbrado no medido :      623 kWh   (incluye semáforos y cámaras)
  − Consumos propios    :       42 kWh
  = Pérdidas totales    :    2.989 kWh
  − Pérdidas técnicas   :    1.922 kWh   (P10–P90: 1.616–2.242)
  = PNT                 :    1.067 kWh (2,3 %)  [P10–P90: 769–1.377]
```

**Las tres cosas que hay que mirar siempre:**

1. **`MEDIDO` vs `INDICATIVO`.** Sin medición de cabecera no hay balance, hay
   estimación. Un número indicativo presentado como medido es el fallo de
   credibilidad más caro de este tipo de proyecto.
2. **La banda P10–P90.** Si la PNT es 1.067 kWh con banda 769–1.377, el número
   central no es un dato duro. Reporta siempre la banda.
3. **Los controles C01–C06.** Si alguno se dispara, el resultado **no es
   publicable** hasta aclararlo (ver paso 4).

**Pérdidas por componente:** en redes con transformadores subutilizados la pérdida
en **vacío** domina. Eso no es hurto ni error: es física (P0 es constante y no se
afecta por el factor de pérdidas). Es un argumento para reubicar transformadores.

**Desbalance máximo:** se calcula solo sobre tramos **trifásicos y cargados**. Una
acometida monofásica marca siempre 200 % (el máximo teórico) y un tramo trifásico
de cola que alimenta a un cliente monofásico, también; si se los incluyera, el
indicador diría 200 % en cualquier red real y no señalaría nada. Al filtrarlos, el
número apunta al tramo donde **rebalancear sí reduce pérdidas** (el ahorro escala
con I²). El tramo concreto queda en `metrics["imbalance_segment"]`.

---

## 4. Diagnóstico de credibilidad — **antes** de mandar cuadrillas

```bash
ptnt diagnostico --csv consumos_36m.csv --cabecera cabecera.csv --multados multados.csv
```

Este paso descarta lo que **no es hurto**. Cuatro secciones:

### 4a. Transferencias entre alimentadores

```
F002 → F003  2026-02-01  73.887 kWh  simetría 0,90  confianza 0,79
```
**Qué significa:** una maniobra no registrada pasó carga de F002 a F003. Eso
produce PNT falsamente alta en uno y negativa en el otro.
**Qué hacer:** confirmar la maniobra con operación. Ambos alimentadores quedan
**excluidos del ranking** hasta aclarar.

Si ves `NO_APLICABLE_POR_DATOS`, es porque tienes menos de 3 meses de cabecera: la
detección por serie temporal es imposible con ese dato. No es una falla del
sistema, es una limitación real — y el argumento más fuerte para conseguir más
meses de cabecera.

### 4b. Clientes faltantes

```
ENERGÍA vinculada: 96,65 %          ← el techo de calidad de tu balance
CSV sin SIG: 36 cuentas (1.477.670 kWh sin ubicar)
SIG sin facturación: 5 clientes
```
**Lo importante es el % de ENERGÍA, no el % de cuentas.** Si es 85 %, tu PNT es
indistinguible de ese 15 % sin ubicar. Por debajo del umbral el balance se degrada
a INDICATIVO automáticamente.

**Qué hacer:** los `CSV_SIN_SIG` son trabajo de **geo-referenciación**, no de
cuadrilla de hurto. Los `SIG_SIN_CSV` pueden ser consumo no facturado (sí es PNT).

### 4c. Alimentadores incoherentes

| Código | Qué pasó | Severidad |
|---|---|---|
| C01 | PNT negativa | **BLOQUEANTE** — no se publica |
| C03 | Facturado > entrada | **BLOQUEANTE** |
| C02 | PNT improbablemente alta | Excluir del ranking |
| TRANSFER | Transferencia probable | Excluir del ranking |

Para C01 el sistema da la causa ordenada por sensibilidad: *1) transferencia no
registrada; 2) sobreestimación del alumbrado; 3) error de asignación de clientes*.

### 4d. Validación contra la base de multados

```
LIFT en el top 5 %: 12,63×     ← 12,6 veces más hurtos que inspeccionar al azar
AUC: 0,861 · Recall top 10 %: 65,8 % · Posición mediana de los hurtos: 3,0 %
```

**Este es el número que justifica el proyecto ante la gerencia.** Sin la base de
multados, "el detector funciona" es una afirmación sin respaldo.

Y la calibración te dice qué señal sirve **en tu red**:
```
S7 (planitud)      lift 62,9  → Aumentar peso
S4 (cero activo)   lift 33,8  → Aumentar peso
S1 (caída/recup.)  lift  1,3  → Mantener
```

---

## 5. Focalizar: **dónde ir a hacer el levantamiento**

```bash
ptnt focalizar --ordenes 25
```

**Los 7 niveles y qué hacer en cada uno:**

| Nivel | Acción de campo |
|---|---|
| Alimentador | Campaña y verificación de cabecera |
| Zona de protección | Instalar medición de frontera y seccionalizar |
| Ramal | Recorrido del ramal con medición de frontera |
| Puesto de transformación | Censo de carga y revisión de acometidas |
| **Ruta comercial (CLIRLSCOD)** | Relectura y verificación de toda la ruta |
| **Sector** | Recorrido casa por casa |
| Cliente | Inspección de acometida y medidor |

**Las órdenes de trabajo** se ordenan por **rendimiento por visita**, no por score:

```
OT-0001 SECTOR SEC-E621.1-N9756.5  19 clientes  51.263 kWh/mes
OT-0007 PUESTO TS2                 24 clientes  18.658 kWh/mes
OT-0010 RUTA   F003-R03            29 clientes  14.124 kWh/mes
→ 171 clientes cubiertos en 10 visitas · 270.113 kWh/mes en juego
```

Un sector que agrupa 19 clientes rinde más que 19 visitas sueltas. Esa es la
decisión logística correcta, y por eso no se ordena por el score del cliente.

**Salidas:** `reporte_focalizacion.html` (para reunión), `focalizacion.xlsx` (una
hoja por nivel), `ordenes_levantamiento.csv` (para el sistema de campo).

---

## 6. Ver los resultados

```bash
ptnt dashboard        # analista → http://127.0.0.1:8501
ptnt servir-visor     # jefaturas/cuadrillas → http://127.0.0.1:8080
```

El tablero abre en la pestaña **"📍 Dónde inspeccionar"**. El visor es de **solo
lectura** y expone `GET /api/focalizacion?nivel=SECTOR` y `GET /api/ordenes` para
integrarlos con el sistema de gestión de campo (ambos autenticados: los objetivos
identifican predios concretos).

---

## 7. Cargar información nueva (mes a mes)

**El ciclo normal es: llega el mes nuevo → se reejecuta → se compara.**

```bash
# 1. Sustituye el CSV de consumos por el que trae el mes nuevo
ptnt analizar --csv consumos_36m_actualizado.csv
# 2. Agrega el mes a la cabecera y vuelve a diagnosticar
ptnt diagnostico --cabecera cabecera_actualizada.csv --multados multados.csv
# 3. Refocaliza
ptnt focalizar
```

### Qué se conserva y qué cambia

| Elemento | Al cargar datos nuevos |
|---|---|
| **Identificador de sector** | **Se conserva** — deriva de la coordenada (`SEC-E621.1-N9756.5`) |
| Identificador de transformador / cliente | Se conserva (GLOBALID / cuenta contrato) |
| Registro de ubicaciones | **Acumula** primera y última detección y nº de veces priorizada |
| Scores y prioridades | Se recalculan con el dato nuevo |
| Sectores nuevos | Aparecen con su propio identificador geográfico |

**Por qué importa:** una orden de trabajo emitida para `SEC-E621.1-N9756.5` sigue
apuntando **al mismo sitio físico** el mes que viene. Verificado en el paso 8 de la
demo: los 11 sectores conservaron su identificador tras una carga nueva.

### Reincidencia

El registro marca las ubicaciones **priorizadas 3 o más veces sin inspeccionar**.
Un sector que aparece seis meses seguidos y nunca se visitó es una señal de gestión,
no de análisis:

```python
from ptnt.survey.locations import LocationRegistry
reg = LocationRegistry("outputs/ubicaciones.json")
for r in reg.reincidentes():
    print(r.location_id, r.veces_priorizado, r.primera_deteccion)
```

### Cerrar el ciclo con el resultado de campo

```python
reg.marcar_inspeccionado("SEC-E621.1-N9756.5", "Hurto confirmado: 3 conexiones directas")
reg.save()
```
Esos resultados alimentan la base de multados del próximo ciclo, que recalibra el
detector. **El sistema mejora con cada campaña.**

---

## 8. Cuando cambia la topología de la red

Al reejecutar `ptnt migrar`, el sistema compara **tres hashes independientes** y
recalcula solo lo afectado:

| Cambio | Hash que cambia | Qué se recalcula |
|---|---|---|
| Se vuelve a cargar la misma red | ninguno | **nada** (`SIN_CAMBIO`) |
| Repotenciación de un tramo (cambia el conductor) | `attribute_hash` | calidad, flujo, pérdidas, balance |
| Ampliación (nuevo ramal con clientes) | `topology_hash` + `attribute_hash` | + topología y focalización |
| Maniobra (se abre un seccionador) | `switch_state_hash` | topología dinámica y balance |

Salida real de la demo:
```
CONDUCTOR    → ACTUALIZACION v2 · attribute_hash    · recalcula: balance, calidad, flujo, perdidas
NUEVO_RAMAL  → ACTUALIZACION v2 · topology_hash+attr · recalcula: + topologia, focalizacion
             → Cambio de inventario: {tramos: +6, clientes: +3}
MANIOBRA     → ACTUALIZACION v2 · switch_state_hash · recalcula: balance, topologia_dinamica
```

### **Las ubicaciones se conservan siempre**

Este es el punto crítico para campo. Aunque cambie la topología:

- El **identificador de sector** no cambia: viene de la coordenada, no de la red.
- El **registro de ubicaciones** sobrevive: mantiene primera detección, veces
  priorizada y resultado de inspección.
- Las **versiones anteriores** se conservan (`is_current = False`) para auditar qué
  cambió y cuándo.

Una cuadrilla con una orden emitida antes del cambio **sigue yendo al mismo lugar**.
Lo que cambia es la prioridad, no la ubicación.

---

## Resumen del flujo

```
migrar ──► analizar ──► analizar-red ──► diagnostico ──► focalizar ──► dashboard/visor
  │           │              │                │              │
versión    consumo y      balance y      credibilidad    órdenes de
de red      hurto          pérdidas      (¿es creíble?)  levantamiento
                                                              │
                                                    campo ────┘
                                                      │
                                            resultados ──► base de multados
                                                              │
                                                    recalibra el detector
```
