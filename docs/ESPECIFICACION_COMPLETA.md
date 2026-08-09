# PTNT-BAL — Especificación completa

**Documento de replicación.** Contiene todo lo necesario para reconstruir el
sistema desde cero: requerimientos trazados a su origen, arquitectura de
información, entidades y campos, reglas de negocio con su justificación,
interfaces, y la trazabilidad requerimiento → módulo → prueba.

| | |
|---|---|
| Sistema | PTNT-BAL — Pérdidas No Técnicas y Balance Energético |
| Ámbito | Distribuidora eléctrica (modelo de datos CNEL EP, esquema Puesto → Unidad) |
| Stack | Python 3.11+, pydantic v2, pandas/numpy, DuckDB, typer, Streamlit, FastAPI |
| Estado | 290 pruebas en verde |

---

# 1. Objetivo y alcance

Determinar **cuánta energía se pierde, dónde, por qué causa, y a dónde conviene
mandar las cuadrillas**, a partir de la información comercial de consumo y del
modelo de datos de la red.

El sistema responde cuatro preguntas, en este orden:

1. **¿Cuánto se pierde?** — balance energético con separación entre pérdidas
   técnicas (calculadas con física) y no técnicas (el residuo).
2. **¿Es creíble ese número?** — transferencias de carga no reportadas, clientes
   sin vincular, incoherencias. *Antes* de mandar a nadie.
3. **¿Quién y dónde?** — ranking de sospecha y focalización en ocho niveles.
4. **¿Rinde ir?** — órdenes de trabajo ordenadas por energía recuperable por
   visita.

**Fuera de alcance:** facturación, gestión de órdenes en campo (se exporta a
CSV/XLSX para el sistema que las gestione), telemedida en tiempo real.

---

# 2. Requerimientos

Cada requerimiento indica su **origen** (petición del cliente o derivado del
dominio), el **módulo** que lo implementa y la **prueba** que lo verifica.

## 2.1 Ingesta y fuentes

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-01 | Leer desde **múltiples bases de origen** intercambiables: CSV, SQL Server, PostgreSQL, Oracle, MySQL, DuckDB/Parquet | `io/sources/` | `test_config` |
| RF-02 | Leer **File Geodatabase de ArcGIS** con red geométrica y ST_Geometry, verificando EPSG:32717 | `io/sources/fgdb_source.py` | `test_catalog_lv` |
| RF-03 | Leer **Oracle 11gR2 + ArcSDE**, envolviendo geometrías con `SDE.ST_AsBinary` | `io/sources/sql_source.py` | `test_security` |
| RF-04 | Parsear el CSV comercial con **separadores por columna**: en `KWH_*` el punto es separador de miles (`"1.302"`→1302), en coordenadas es decimal | `io/commercial_parser.py` | `test_commercial_parser` |
| RF-05 | **Verificar la orientación temporal** de `KWH_1..KWH_36` contra `CLIULTCONM` y **abortar** si está invertida | `io/commercial_parser.py` | `test_commercial_parser` |
| RF-06 | Validar rangos plausibles de consumo por clase para detectar errores de separador de miles | `io/commercial_parser.py` | `test_commercial_parser` |
| RF-07 | **Migrar** de la base de origen al modelo canónico con mapa de campos configurable y round-trip verificable | `io/migration.py` | `test_3ph_migration` |
| RF-08 | **Carga parcial** con alcance declarado por insumo y alimentador; cobertura y pendientes | `ingest/partial.py` | `test_org_carga_historico` |

> **RF-05 — por qué aborta en vez de corregir.** Un eje temporal invertido
> convierte toda caída de consumo en una subida: el detector buscaría hurto
> exactamente donde no está. Corregirlo en silencio esconde un problema de
> configuración que volverá a aparecer en la siguiente carga.

## 2.2 Consumo y demanda

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-10 | **Promedio multi-mes** con métodos robustos: media, media recortada, mediana, ponderada por recencia, estacional | `load/averaging.py` | `test_averaging` |
| RF-11 | Excluir del promedio los ceros de clientes **suspendidos** (no son anomalía) | `load/averaging.py` | `test_averaging` |
| RF-12 | Marcar como **no confiable** el promedio con menos de N meses válidos | `load/averaging.py` | `test_averaging` |
| RF-13 | Recalcular **P, Q, S, I** por cliente: Velander `P_max = a·E + b·√E`, coincidencia `FC(n) = A + B/√n`, `cosφ` por clase | `load/demand.py` | `test_demand` |
| RF-14 | Corriente **según configuración de fase real** (`CDAFAS`), no √3 por defecto | `load/demand.py` | `test_demand` |
| RF-15 | **Informe de reconciliación** SIG vs corregido, con descomposición de la diferencia por causa | `quality/reconciliation.py` | `test_pipeline` |
| RF-16 | Resolver la clase tarifaria **semánticamente** desde el texto de `DESTARI`, no por igualdad exacta | `segment/classification.py` | `test_pipeline` |

> **Origen de RF-10/RF-13 (petición explícita):** *"el consumo es de varios meses
> por lo que se debe obtener un mecanismo de promedio"* y *"el consumo también
> está en esa tabla del último mes y con ese se calcula la potencia activa y
> reactiva, lo cual no es correcto para los clientes no residenciales"*.
>
> **RF-16 — el defecto que corrige.** El catálogo usa nombres cortos
> (`"MT Industrial"`) pero `DESTARI` trae texto libre
> (`"INDUSTRIAL CON DEMANDA MEDIA TENSION"`). Con búsqueda por igualdad, *ninguna*
> descripción real coincide y todos los clientes caen a la clase por defecto:
> coeficientes residenciales aplicados a industriales de media tensión. El
> cálculo no falla, simplemente da mal.

## 2.3 Segmentación de clientes

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-20 | Clasificar por **clase tarifaria** (residencial, comercial, industrial, oficial, asistencia social, bombeo, alumbrado) desde el texto de la tarifa | `segment/classification.py` | `test_segmentacion` |
| RF-21 | Clasificar por **nivel de tensión** (BT/MT/AT) y **modalidad** (con/sin demanda) | `segment/classification.py` | `test_segmentacion` |
| RF-22 | **Estrato de consumo** por bloques configurables, calculado sobre el nivel base propio (percentil alto de la historia) | `segment/classification.py` | `test_segmentacion` |
| RF-23 | **Grupo par jerárquico** con degradación controlada y factor de confianza | `segment/peers.py` | `test_segmentacion` |
| RF-24 | Marcar **grandes clientes** para revisión individual, fuera del ranking relativo | `segment/report.py` | `test_segmentacion` |
| RF-25 | **Rendimiento por visita** calculado dentro de cada clase | `segment/report.py` | `test_segmentacion` |

> **Origen (petición explícita):** *"por qué los comportamientos son diferentes y
> quizás es más beneficioso revisar posibles clientes comerciales o industriales
> y los residenciales agruparlos... crea grupos según las recomendaciones de las
> mejores prácticas"*.
>
> **RF-23 — regla no negociable: el estrato de consumo NO entra en la clave del
> grupo par.** Estratificar por consumo para comparar consumos es circular: un
> cliente que hurta toda la ventana cae en un estrato bajo y termina comparado
> contra clientes genuinamente pequeños. Medido: incluirlo baja el lift de la
> señal S5 de **10,0× a 2,4×**. Todas las claves deben ser **exógenas al
> consumo** (clase, tensión, fases, ruta).

## 2.4 Detección de pérdidas no técnicas

| ID | Señal | Qué detecta | Módulo |
|---|---|---|---|
| S1 | Caída y recuperación | Manipulación temporal del medidor | `ntl/signals.py` |
| S3 | Ruptura de nivel | Cambio de escalón sin causa comercial | `ntl/signals.py` |
| S4 | Cero con servicio activo | Consumo nulo sin suspensión | `ntl/signals.py` |
| S5 | Divergencia del grupo par | Muy por debajo de clientes equivalentes | `ntl/signals.py` |
| S7 | Planitud anómala | Consumo fijo declarado | `ntl/signals.py` |
| S8 | Dispersión intra-puesto | Muy por debajo de sus vecinos del transformador | `ntl/signals.py` |
| S9 | Déficit contra base propia | Caída respecto de **su propia** historia | `ntl/signals.py` |
| N1 | Residuo de zona | Energía que no cierra en una zona de protección | `ntl/network_signals.py` |
| N3 | Balance de totalizador | Totalizador vs suma de individuales | `ntl/network_signals.py` |
| N4 | Cargabilidad incoherente | Transformador cargado sin consumo facturado | `ntl/network_signals.py` |

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-30 | Ranking por **consenso de rangos** de las señales activas + detección no supervisada | `ntl/scoring.py` | `test_pipeline` |
| RF-31 | **Energía recuperable** estimada dentro del segmento: máximo entre caída propia y déficit del grupo | `segment/peers.py` | `test_segmentacion` |
| RF-32 | **Razones en lenguaje operativo** por cliente (top-3 señales) | `ntl/scoring.py` | `test_pipeline` |
| RF-33 | Validar contra la **base de clientes multados**: lift, AUC, precisión por decil | `ntl/confirmed.py` | `test_anomalies_confirmed` |
| RF-34 | **Fecha de corte** en la base de multados, contra fuga de información | `ntl/confirmed.py` | `test_anomalies_confirmed` |
| RF-35 | **PU learning** (Elkan–Noto): los no multados no son negativos confiables | `ntl/confirmed.py` | `test_anomalies_confirmed` |
| RF-36 | **Calibrar** el peso de cada señal con los casos reales de la distribuidora | `ntl/confirmed.py` | `test_anomalies_confirmed` |

> **S9 — por qué existe.** En industrial y oficial hay pocos clientes y son
> enormemente heterogéneos: comparar una fábrica de hielo contra una imprenta no
> dice nada. Para ellos la única referencia válida es su propia historia.
>
> **RF-31 — el defecto que corrige.** Con una mediana **global** de todo el
> padrón (~150 kWh), un industrial de 20 000 kWh/mes que hurta el 50 % sigue muy
> por encima de la mediana → recuperable **cero**, invisible. Y a un residencial
> honesto de 60 kWh se le atribuye recuperable inventado. Ambos errores
> desaparecen comparando dentro del segmento.

## 2.5 Red eléctrica y balance

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-40 | Reconstruir la **topología** en grafo radial con trazas aguas arriba/abajo, zonas de protección y detección de ciclos e islas | `topology/graph.py` | `test_topology` |
| RF-41 | **Flujo de potencia** monofásico equivalente (backward-forward sweep) | `powerflow/bfs.py` | `test_powerflow` |
| RF-42 | **Flujo trifásico desbalanceado de 4 hilos** con corriente y pérdida de neutro | `powerflow/bfs3ph.py` | `test_3ph_migration` |
| RF-43 | **Exportar y ejecutar OpenDSS**, comparar pérdidas de línea | `powerflow/opendss_*.py` | `test_advanced` |
| RF-44 | **Validar el flujo** contra casos analíticos de solución cerrada | `powerflow/validation.py` | `test_advanced` |
| RF-45 | Pérdidas en **conductores** con corrección de temperatura, **transformadores** (vacío + carga) y **medidores** | `losses/` | `test_losses` |
| RF-46 | **Capacidad de banco** según configuración (delta abierto = √3·kVA, banco de 3, desigual, delta 4 hilos) | `losses/transformers.py` | `test_losses` |
| RF-47 | La pérdida en **vacío (P0) NO se multiplica** por el factor de pérdidas | `losses/transformers.py` | `test_losses` |
| RF-48 | **Monte Carlo** de pérdidas técnicas y PNT: P10/P50/P90 | `losses/montecarlo.py` | `test_advanced` |
| RF-49 | **Alumbrado público**, incluyendo **semáforos y cámaras** como no medido (regulación) | `lighting/streetlight.py` | `test_catalog_lv` |
| RF-50 | **Agregar la baja tensión al transformador** por `PARENTCIRCUITSOURCEGUID`, sin doble conteo | `grid/lv_aggregation.py` | `test_catalog_lv` |
| RF-51 | **TOTALIZADOR**: los medidores individuales bajo él no se re-suman | `grid/lv_aggregation.py` | `test_catalog_lv` |
| RF-52 | **Balance jerárquico** MEDIDO/INDICATIVO + controles C01–C06 | `balance/energy_balance.py` | `test_balance_lighting_grid` |
| RF-53 | **Cargabilidad** de transformadores y **desbalance de fases** | `grid/loadability.py` | `test_balance_lighting_grid` |
| RF-54 | **Motor de reglas de calidad** (R05/R09/R11/R12/R15/R22/R24/P01/P09) | `quality/rules.py` | `test_advanced` |
| RF-55 | **Versionado de topología**: tres hashes independientes con invalidación selectiva | `topology/versioning.py` | `test_locations_versioning` |

> **RF-47 — por qué.** La pérdida en vacío es **constante**: el transformador la
> tiene energizado esté cargado o no. Multiplicarla por el factor de pérdidas
> (que modela la variación de la carga) la subestima sistemáticamente. En redes
> subutilizadas es la fracción dominante de la pérdida de transformación.
>
> **RF-52 — MEDIDO vs INDICATIVO.** Sin medición de cabecera confiable no hay
> balance, hay estimación. Presentar un número indicativo como medido es el fallo
> de credibilidad más caro de este tipo de proyecto.

## 2.6 Diagnóstico de credibilidad

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-60 | Detectar **transferencias de carga no reportadas** entre alimentadores | `anomalies/transfers.py` | `test_anomalies_confirmed` |
| RF-61 | Con menos de 3 períodos, devolver `NO_APLICABLE_POR_DATOS` en vez de inventar candidatos | `anomalies/transfers.py` | `test_anomalies_confirmed` |
| RF-62 | **Descontar el movimiento común** del sistema antes de buscar pares | `anomalies/transfers.py` | `test_anomalies_confirmed` |
| RF-63 | **Clientes faltantes** en ambos sentidos (facturan sin SIG / SIG sin facturación) con **% de energía vinculada** | `anomalies/unmatched.py` | `test_anomalies_confirmed` |
| RF-64 | **Alimentadores incoherentes** (PNT negativa o fuera de rango) que **bloquean la publicación** | `anomalies/coherence.py` | `test_anomalies_confirmed` |
| RF-65 | Los alimentadores con transferencia probable **no reciben órdenes de trabajo** | `survey/targeting.py` | `test_survey` |

> **RF-62 — el defecto que corrige.** En una misma zona todos los alimentadores
> suben y bajan juntos por estacionalidad. Buscando pares sobre la variación
> bruta, el que baja aparece emparejado con *todos* los que suben: sobre 12
> alimentadores producía **3 pares espurios y perdía la transferencia real**. Se
> descuenta con la **mediana** de las variaciones relativas (robusta: si un par
> transfiere, son 2 de N series y no mueven la mediana).

## 2.7 Focalización de levantamientos

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-70 | Priorizar en **ocho niveles**: unidad de negocio, subestación, alimentador, zona de protección, ramal, transformador, ruta comercial, sector, cliente | `survey/targeting.py` | `test_survey` |
| RF-71 | Un **ramal** es el tramo entre bifurcaciones; las acometidas de cliente **no** cuentan como bifurcación | `topology/graph.py` | `test_survey` |
| RF-72 | **Sectores geográficos** por clustering espacial (HDBSCAN con respaldo de grilla) | `survey/sectors.py` | `test_survey` |
| RF-73 | **Ruta comercial (`CLIRLSCOD`)** como nivel de focalización | `survey/routes.py` | `test_anomalies_confirmed` |
| RF-74 | **Órdenes de trabajo** ordenadas por energía recuperable **por visita**, no por probabilidad | `survey/targeting.py` | `test_survey_e2e` |
| RF-75 | Identificadores de objetivo **únicos en todo el sistema** (cualificados con el alimentador) | `survey/targeting.py` | `test_survey` |
| RF-76 | **Identidad geográfica estable**: el id del sector se deriva de la coordenada, no del orden del cálculo | `survey/locations.py` | `test_locations_versioning` |
| RF-77 | **Registro persistente de ubicaciones** con reincidencia y cierre por resultado de campo | `survey/locations.py` | `test_locations_versioning` |
| RF-78 | Resultado **exportable** (CSV/XLSX) y **reportable** (HTML/PDF) | `survey/report.py`, `report/` | `test_survey_e2e` |

> **Origen (petición explícita):** *"análisis de dónde toca ir a hacer
> levantamientos, ya sea por alimentador, ramal, transformador o sectores, y su
> resultado pueda ser visible y reportable"* y *"es clave que las ubicaciones se
> mantengan para los análisis en campo"*.
>
> **RF-75 — el defecto que corrige.** `TS9` y `BT3_0_0` solo son únicos *dentro*
> de un alimentador. El plan mostraba dos objetivos con el mismo nombre y
> energías muy distintas: una orden emitida para `TS9` era ambigua.
>
> **RF-76 — el defecto que corrige.** Los `sector_id` eran correlativos del
> clustering. Al cargar datos nuevos, **los sectores cambiaban de lugar**: una
> orden emitida para `SEC-0000` mandaba la cuadrilla a otro sitio.

## 2.8 Jerarquía organizacional

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-80 | Modelar **Unidad de Negocio → Subestación → Alimentador** desde un catálogo | `org/hierarchy.py` | `test_org_carga_historico` |
| RF-81 | **Consolidar el balance** hacia subestación y unidad de negocio | `org/hierarchy.py` | `test_org_carga_historico` |
| RF-82 | Los **porcentajes se recalculan** sobre los totales, nunca se promedian | `org/hierarchy.py` | `test_org_carga_historico` |
| RF-83 | El **tipo de balance del padre es el peor de sus hijos** | `org/hierarchy.py` | `test_org_carga_historico` |
| RF-84 | Sin catálogo, inferir la UN del prefijo y **advertir** que la subestación no es utilizable | `org/hierarchy.py` | `test_org_carga_historico` |

> **RF-83 — la regla central del módulo.** La energía se puede sumar; la garantía
> de que ese número es verificable, no. Basta **un** alimentador `INDICATIVO`
> para que el consolidado de su unidad de negocio no pueda presentarse como
> medido.
>
> **RF-82 — por qué importa.** Un alimentador de 700 MWh al 4 % y uno de
> 4 400 MWh al 6 % no dan 5 % en conjunto. Promediar porcentajes de entidades de
> tamaños distintos produce un número que no corresponde a ninguna realidad
> física.

## 2.9 Histórico y evolución

| ID | Requerimiento | Módulo | Prueba |
|---|---|---|---|
| RF-90 | **Serie histórica** del balance por entidad y período | `store/history.py` | `test_org_carga_historico` |
| RF-91 | Re-registrar un período lo **reemplaza**, no lo duplica | `store/history.py` | `test_org_carga_historico` |
| RF-92 | **Comparar dos períodos** con tendencia (MEJORA/ESTABLE/EMPEORA) | `store/history.py` | `test_org_carga_historico` |
| RF-93 | Guardar el **hash de configuración** y marcar como **no comparables** los puntos calculados con configuraciones distintas | `store/history.py` | `test_org_carga_historico` |
| RF-94 | Advertir de puntos **no MEDIDO** y de baja cobertura en el histórico | `store/history.py` | `test_org_carga_historico` |

> **RF-93 — por qué.** Atribuir a la red una variación que fue de parámetros es
> una forma silenciosa de mentir con un gráfico. Dos valores calculados con
> configuraciones distintas no son una tendencia.

## 2.10 Interfaces

| ID | Requerimiento | Módulo |
|---|---|---|
| RF-100 | **CLI** completa (23 comandos) | `cli.py` |
| RF-101 | **Tablero de escritorio** (Streamlit) con 8 pestañas | `dashboard/app.py` |
| RF-102 | **Visor web de solo lectura** (FastAPI) para consulta por terceros | `webviewer/app.py` |
| RF-103 | Pestaña de **unidad de negocio y subestación** | `dashboard/app.py` |
| RF-104 | Pestaña de **histórico** con comparación de períodos | `dashboard/app.py` |
| RF-105 | Pestaña de **carga de datos** con registro de alcance y pendientes | `dashboard/app.py` |
| RF-106 | **Informes HTML + PDF por etapa**, autocontenidos | `report/` |
| RF-107 | Pestaña de **trabajo de campo**: técnicos, reparto, paquetes y revisión | `dashboard/app.py` |

## 2.12 Trabajo de campo y aplicación móvil

| ID | Requerimiento | Módulo |
|---|---|---|
| RF-200 | **Usuarios móviles creados en el backend**, nunca en el dispositivo | `field/workorders.py` |
| RF-201 | Un usuario, **un dispositivo**: token revocable sin tocar la cuenta | `field/workorders.py` |
| RF-202 | **Reparto entre varias cuadrillas** equilibrando carga y compacidad geográfica | `field/distribute.py` |
| RF-203 | **Definición de trabajo** por alimentador, sector, área o lista de cuentas, con 7 tipos de campaña | `field/workdef.py` |
| RF-204 | **Paquete GeoPackage OGC** autocontenido: red recortada, órdenes, cartografía offline y esquema de formularios | `field/package.py` |
| RF-205 | **Un paquete por técnico**, con todas sus órdenes; nunca uno por orden | `field/package.py` |
| RF-206 | Generación de **paquetes en lote** para todo el equipo | `field/package.py` |
| RF-207 | Edición en campo: **crear, modificar, mover y eliminar** sin conexión | `mobile/` |
| RF-208 | **Snap y propagación topológica**: mover un cliente arrastra su acometida | `field/topology_edit.py` |
| RF-209 | La relación **eléctrica no propaga geometría**: mover un transformador no mueve el barrio | `field/topology_edit.py` |
| RF-210 | **Reconexión del consumidor** (Puesto→Unidad) con recálculo de las dos zonas | `field/schema.py`, `field/sync.py` |
| RF-211 | **Varias fotografías por elemento** con ubicación, hora, autor y hash | `field/schema.py`, `mobile/` |
| RF-212 | **Diario de cambios** con antes/después, autor, posición y precisión GPS | `field/schema.py` |
| RF-213 | **Descarga y subida simultáneas** de varios técnicos sin pérdida de actualizaciones | `field/store.py` |
| RF-214 | **Trabajos de varios días**: avance parcial sin cerrar la orden, sin reprocesar lo enviado | `field/api.py`, `field/store.py` |
| RF-215 | **Revisión granular** por el supervisor: aceptar y rechazar cambio a cambio | `field/sync.py` |
| RF-216 | **Invalidación selectiva**: cada cambio dice qué etapas hay que recalcular | `field/sync.py` |
| RF-217 | **Histórico permanente** de modificaciones, de archivo y de móvil | `field/sync.py` |
| RF-218 | Interfaz **adaptativa**: tablet lado a lado, teléfono con hoja deslizante | `mobile/ui/` |
| RF-219 | Cartografía **offline** desde MBTiles libres o caché de ArcGIS Server, **sin licencias** | `field/package.py`, `mobile/geo/` |
| RF-220 | Formularios construidos **desde el esquema del paquete**, sin publicar versión de la app | `field/schema.py`, `mobile/data/` |
| RF-221 | Soporte de **equipos de gama baja**: consulta por ventana, umbral de zoom, tope de elementos | `mobile/ui/MapaCampo.kt` |
| RF-222 | **Simulador de jornada** para probar el contrato móvil↔backend sin dispositivo | `field/simulator.py` |

## 2.11 Requerimientos no funcionales

| ID | Requerimiento | Verificación |
|---|---|---|
| RNF-01 | **Servidor único**, sin dependencias de nube | Instalación por capas (extras) |
| RNF-02 | **Núcleo mínimo**: solo numpy/pandas/pydantic | `pyproject.toml` extras |
| RNF-03 | Las **credenciales nunca** en YAML ni código: solo nombres de variables de entorno | `test_security` |
| RNF-04 | Contraseñas solo como **hash** (bcrypt/PBKDF2) | `test_security` |
| RNF-05 | SQL **parametrizado** (`URL.create`), sin concatenar credenciales | `test_security` |
| RNF-06 | Visor con **autenticación** y restricción por red (CIDR) | `test_security` |
| RNF-07 | Configuración **estricta**: clave desconocida = fallo de arranque | `test_config` |
| RNF-08 | Todo parámetro numérico en **configuración**, ninguno en el código | `test_config` |
| RNF-09 | Informes **autocontenidos**, sin recursos externos, con escape de contenido | `test_escenario_costa_reportes` |
| RNF-10 | Rendimiento: 20 000 clientes × 36 meses en **< 60 s** | `scripts/prueba_costa_20k.py` |
| RNF-11 | El paquete de campo **no depende de GDAL**: GeoPackage escrito sobre `sqlite3` puro | `field/gpkg.py` |
| RNF-12 | La app móvil **no usa librerías con licencia** (MapLibre, no ArcGIS) | `mobile/app/build.gradle.kts` |
| RNF-13 | Android **API 24+**: cubre el parque real de equipos entregados a cuadrillas | `mobile/app/build.gradle.kts` |
| RNF-14 | Token del dispositivo cifrado en **Keystore** | `mobile/sync/AlmacenSesionCifrado.kt` |
| RNF-15 | Escrituras concurrentes **transaccionales**: `BEGIN IMMEDIATE` + compare-and-set | `field/store.py` |
| RNF-16 | Proyección UTM⇄WGS84 con error **< 1 mm**; códec de geometría **byte a byte** idéntico entre Python y Kotlin | `mobile/geo/Utm.kt`, `field/gpkg.py` |

---

# 3. Arquitectura de información

## 3.1 Jerarquías

**Organizacional (gestión):**

```
Unidad de Negocio
  └── Subestación
        └── Alimentador
```

**Eléctrica (análisis):**

```
Alimentador  (cabecera: PuestoProteccionDinamico con CircuitSource,
              tipo "cabecera de alimentador")
  └── Zona de protección   (delimitada por seccionadores)
        └── Ramal          (tramo entre bifurcaciones)
              └── Puesto de transformación
                    └── Unidad de transformación  (1..3, una por fase)
                          └── Punto de carga
                                └── Conexión consumidor (cliente/medidor)
```

**Comercial (logística de lectura):**

```
División
  └── Ruta comercial (CLIRLSCOD)
        └── Cuenta contrato
```

**Geográfica (campo):**

```
Sector  (agrupación espacial, id derivado de la coordenada)
  └── Cliente
```

Las cuatro coexisten: un cliente pertenece simultáneamente a un transformador, a
una ruta comercial y a un sector. La focalización explota las tres.

## 3.2 Entidades y campos del modelo de origen

### ATRIBUTOSCONSUMIDOR (1:1 con la conexión del consumidor)

| Campo | Tipo | Uso en el sistema |
|---|---|---|
| `CUENTACONTRATO` | texto | Clave del cliente; join con el padrón comercial |
| `CLIRLSCOD` | texto | **Ruta comercial**: nivel de focalización y clave del grupo par |
| `DESTARI` / `TIPOTARIFA` | texto | **Clase tarifaria y nivel de tensión** (resolución semántica) |
| `CLIULTCONM` | numérico | Consumo del último mes (verificación de orientación temporal) |
| `POTENCIAACTIVA` | numérico | Potencia previa del SIG (para la reconciliación) |
| `POTENCIAREACTIVA` | numérico | Reactiva previa del SIG |
| `CDAFAS` | entero | **Número de fases**: fórmula de corriente y clave del grupo par |
| `SECUENCIAFASE` | bitmask | Fase conectada (A=4, B=2, C=1) |
| `EDCCOD` | texto | Estado del servicio (ACT/SUSP): S4 y exclusión de ceros |
| `BAJOMEDICION` | booleano | Excluir del cómputo de alumbrado medido |
| `TOTALIZADOR` | booleano | **No sumar los individuales** bajo él |

### Padrón comercial (CSV)

| Campo | Formato | Trampa |
|---|---|---|
| `KWH_1..KWH_36` | texto | Punto = **separador de miles** (`"1.302"` → 1302) |
| `ZZUTM_X`, `ZZUTM_Y` | texto | Punto = **separador decimal** (UTM 17S, EPSG:32717) |
| `DIVISION` | texto | Unidad de negocio / división comercial |
| `NOMBRE` | texto | Solo para el informe de campo |

> El mismo carácter `.` significa cosas opuestas según la columna. Por eso el
> parser usa **conversores por columna**, nunca un `thousands=` global.

### Red (FGDB / SQL)

| Entidad | Campos clave |
|---|---|
| `TRAMODISTRIBUCIONAEREO/SUBTERRANEO` | `CODIGOESTRUCTURA`, `SHAPE_Length`, `CIRCUITSOURCEGUID`, `PARENTCIRCUITSOURCEGUID`, `SECUENCIAFASE` |
| `PUESTOTRANSFDISTRIBUCION` | `CODIGO`, `POTENCIANOMINAL`, `CODIGOESTRUCTURA`, `ANCILLARYROLE` |
| `UNIDADTRANSFDISTRIBUCION` | kVA por unidad (1..3 por puesto), configuración de banco |
| `PUNTOCARGA` | Puesto del cliente |
| `PUESTOPROTECCIONDINAMICO` | Seccionadores; la **cabecera** tiene `ANCILLARYROLE=Source` |
| `PUESTOCAPACITOR` | Bancos de capacitores (kVAR) |
| `ESTRUCTURASOPORTE` | Postes |
| `LUMINARIA` | Alumbrado; potencia + **pérdidas de balastro** |
| `CIRCUITOFUENTE` | Cabecera: `CODIGOALIMENTADOR`, `TIPOALIMENTADOR`, `DEMANDAMAXIMA`, `FACTORDECARGA`, `FACTORDEPERDIDA` |

### CATALOGOESTRUCTURA (5 661 códigos)

Clasificación por **prefijo del código**:

| Prefijo | Elemento |
|---|---|
| `APO`, `AOD`, `AOC` | Alumbrado público |
| `CSP` | Semáforos y cámaras (→ alumbrado **no medido** por regulación) |
| `TR*`, `TU*` | Transformadores (kVA + configuración de banco) |
| `COO` | Conductores (415 códigos) |
| `ECT`, `ECR` | Capacitores |
| `SP*`, `SS*` | Seccionadores |
| `MED*` | Medidores |
| `POO`, `TOO` | Postes |
| `SD*` | Generación distribuida |

### Catálogos derivados

| Catálogo | Contenido | Origen |
|---|---|---|
| Conductores | R, X, ampacidad de los 415 códigos `COO` | **Derivados**: fórmula AWG `d = 0,127·92^((36−n)/39)`, kcmil→mm² ×0,5067074, resistividad IACS, `X = 2πf·2e-4·ln(GMD/GMR)`. Error < 2 % vs fabricante |
| Transformadores | P0, Pk, %Z por kVA | YAML parametrizado (no vienen en el Excel) |
| Clases tarifarias | a, b, A, B, cosφ, factor de carga, k | YAML |
| Jerarquía | alimentador → subestación → UN | CSV externo |

## 3.3 Modelo de resultados (DuckDB)

Cuatro esquemas:

| Esquema | Contenido | Tablas principales |
|---|---|---|
| `meta` | Trazabilidad | `run`, `feeder_version`, `checkpoint`, `ingest_summary`, `data_lineage` |
| `ref` | Catálogos | `conductor`, `transformer_catalog`, `streetlight_catalog`, `tariff_class`, `load_profile`, `calibration` |
| `silver` | Datos limpios | `customer_consumption`, `feeder_head_energy`, `customer_link`, `customer_power` |
| `gold` | Resultados | `energy_balance`, `technical_loss_component`, `ntl_score`, `ntl_signal`, `suspect_area`, `quality_finding`, `reconciliation_report`, `unmatched_customer`, `field_inspection` |

**Campos obligatorios en toda vista de balance** (`gold.energy_balance`):
`balance_type` (MEDIDO/INDICATIVO/PARCIAL), `model_confidence_index`,
`energy_linked_pct`, `closure_residual_pct`. Sin ellos, un consumidor del dato no
puede saber cuánto vale.

## 3.4 Artefactos de estado (fuera de la base)

| Archivo | Contenido | Módulo |
|---|---|---|
| `alcance_carga.json` | Qué insumo se cargó, de qué alimentadores, cuándo | `ingest/partial.py` |
| `versiones_red.json` | Versiones de topología con sus tres hashes | `topology/versioning.py` |
| `ubicaciones.json` | Registro de ubicaciones priorizadas y su historia | `survey/locations.py` |
| `historico_balance.parquet` | Serie temporal del balance por entidad | `store/history.py` |
| `campo/registro.db` | Usuarios móviles, asignaciones y bitácora (SQLite transaccional) | `field/store.py` |
| `campo/paquetes/{usuario}.gpkg` | Paquete descargable de cada técnico | `field/package.py` |
| `campo/entrantes/*.gpkg` | Paquetes de retorno tal como llegaron | `field/api.py` |
| `campo/lotes/*.json` | Lotes en revisión, con sus hallazgos | `field/api.py` |
| `campo/historico_cambios.parquet` | Histórico permanente de modificaciones de red | `field/sync.py` |

Están fuera de la base a propósito: sobreviven a un borrado de DuckDB y se pueden
versionar con el proyecto.

---

# 4. Arquitectura de software

## 4.1 Módulos

```
src/ptnt/
├── config/       Modelos pydantic + carga YAML (validación estricta)
├── io/
│   ├── sources/  Conectores multi-origen (csv, sql, duckdb, FGDB, Oracle/ArcSDE)
│   ├── commercial_parser.py   Parseo con conversores por columna
│   ├── migration.py           Origen → modelo canónico
│   └── exporters.py           XLSX/CSV
├── ingest/       Carga parcial: alcance declarado y cobertura
├── load/         Promedio multi-mes + demanda (P, Q, S, I)
├── segment/      Clase, tensión, estrato, grupo par jerárquico
├── ntl/          Señales S1–S9, N1/N3/N4, scoring, base de multados
├── canonical/    Decodificadores de dominio (bitmask, kVA, longitud)
├── ref/          Catálogos: conductores derivados, transformadores, estructuras
├── topology/     Grafo radial, trazas, zonas + versionado
├── powerflow/    BFS 1φ y 3φ con neutro, OpenDSS, validación
├── losses/       Conductores, transformadores, medidores, Monte Carlo
├── lighting/     Alumbrado público (incl. semáforos/cámaras)
├── balance/      Balance jerárquico y PNT + controles C01–C06
├── grid/         Cargabilidad, desbalance, riesgo, agregación LV
├── anomalies/    Transferencias, clientes faltantes, incoherencias
├── org/          Unidad de negocio → subestación → alimentador
├── survey/       Focalización, sectores, rutas, ubicaciones, órdenes
├── quality/      Reconciliación + motor de reglas
├── report/       Informes HTML autocontenidos + gráficos SVG + PDF
├── store/        DuckDB + histórico de balance
├── security/     Auth (hash), secretos por entorno
├── field/        Trabajo de campo (ver abajo)
├── synth/        Generadores de escenario con verdad conocida
├── dashboard/    Tablero Streamlit (8 pestañas)
├── webviewer/    Visor FastAPI de solo lectura
├── pipeline.py       Orquestador comercial
├── grid_pipeline.py  Orquestador de red
└── cli.py        CLI typer
```

El módulo de campo, en detalle:

```
src/ptnt/field/
├── gpkg.py           GeoPackage OGC nativo sobre sqlite3 (sin GDAL)
├── schema.py         13 capas, dominios y esquema que consume el móvil
├── workdef.py        Definición del trabajo: alimentador, sector, área, lista
├── workorders.py     Usuarios, asignaciones y máquina de estados
├── store.py          Persistencia transaccional (concurrencia real)
├── distribute.py     Reparto entre cuadrillas: carga pareja y compacidad
├── package.py        Armado del paquete: recorte, contexto topológico, teselas
├── topology_edit.py  Snap y propagación (4 reglas de relación)
├── simulator.py      Jornada de campo simulada sobre el paquete real
├── sync.py           Recepción, validación, revisión e invalidación selectiva
├── api.py            API móvil (FastAPI, 5 endpoints)
└── demo_red.py       Red de demostración para probar el ciclo sin SIG
```

Y la aplicación Android:

```
mobile/app/src/main/java/ec/cnel/ptnt/field/
├── MainActivity.kt   Actividad única, permisos y cámara del sistema
├── data/             GeoPackage directo, formularios del manifiesto, repositorio
├── domain/           Editor topológico y captura de fotos con metadatos
├── geo/              UTM⇄WGS84, ubicación, servidor local de teselas
├── sync/             Cliente HTTP y sesión cifrada en Keystore
├── work/             Subida diferida al recuperar señal
└── ui/               Compose: vinculación, órdenes, mapa y atributos
```

## 4.2 Flujo de proceso

```
     ┌─ padrón comercial (CSV/SQL)
     ├─ red (FGDB / Oracle ArcSDE)          ← carga parcial con alcance declarado
     ├─ cabecera (energía medida)
     ├─ base de multados
     └─ catálogo organizacional
              │
              ▼
   [E1] Ingesta + verificación de orientación temporal
              │
   [E2] Promedio multi-mes robusto
              │
   [E3] Segmentación: clase × tensión × fases × ruta → grupo par
              │
   [E4] Recálculo de potencia (Velander) → reconciliación vs SIG
              │
   [E5] Señales S1–S9 → scoring → ranking + recuperable segmentado
              │
   [E6] Topología → flujo de potencia → pérdidas técnicas (Monte Carlo)
              │
   [E7] Balance MEDIDO/INDICATIVO → PNT = pérdidas − técnicas
              │
   [E8] Diagnóstico de credibilidad ──► ¿publicable?  ── NO ─► corregir datos
              │ SÍ
   [E9] Focalización en 8 niveles → órdenes por rendimiento por visita
              │
   [E10] Consolidado UN/subestación + instantánea al histórico
              │
              ▼
    CLI · Tablero · Visor · Informes PDF · CSV/XLSX
```

## 4.3 Comandos

| Comando | Función |
|---|---|
| `ptnt verificar-config` | Valida el YAML; falla nombrando el parámetro ausente |
| `ptnt probar-fuentes` | Conectividad de todas las bases de origen |
| `ptnt generar-sinteticos` | CSV comercial sintético con hurtos conocidos |
| `ptnt registrar-carga` | **Declara el alcance de una carga parcial** |
| `ptnt migrar` | Origen → modelo canónico |
| `ptnt analizar` | Pipeline comercial completo |
| `ptnt analizar-red` | Topología → flujo → pérdidas → balance |
| `ptnt diagnostico` | Credibilidad + validación contra multados |
| `ptnt focalizar` | Dónde ir a hacer el levantamiento |
| `ptnt consolidar` | **Consolidado por UN y subestación + histórico** |
| `ptnt validar-flujo` | Validación analítica y contra OpenDSS |
| `ptnt dashboard` | Tablero de escritorio |
| `ptnt servir-visor` | Visor de solo lectura |
| `ptnt crear-usuario` | Alta de usuario (solo hash) |
| `ptnt campo-usuario` | Alta de **usuario móvil** |
| `ptnt campo-definir` | **Define trabajo** sin pasar por el ranking (censo, cartografía, listado) |
| `ptnt campo-asignar` | Asigna órdenes a **un** técnico |
| `ptnt campo-repartir` | **Reparte la jornada** entre varias cuadrillas |
| `ptnt campo-paquete` | Genera el paquete de **un** técnico |
| `ptnt campo-paquetes` | Genera **todos** los paquetes de una vez |
| `ptnt campo-servir` | API de sincronización móvil |
| `ptnt campo-revisar` | Revisa un lote y determina qué recalcular |

---

# 5. Reglas de negocio

Las decisiones que **no** son configurables porque cambiarlas produciría números
incorrectos.

| # | Regla | Consecuencia de violarla |
|---|---|---|
| 1 | La pérdida en vacío **no** se multiplica por el factor de pérdidas | Subestima sistemáticamente la pérdida de transformación |
| 2 | Sin cabecera medida, el balance es **INDICATIVO**, nunca MEDIDO | Se presenta una estimación como verificada |
| 3 | El tipo de balance del consolidado es el **peor de sus hijos** | Un consolidado parece medido con componentes estimados |
| 4 | Los porcentajes se **recalculan**, nunca se promedian | Un número que no corresponde a ninguna realidad física |
| 5 | El **estrato de consumo no entra** en la clave del grupo par | Circularidad: lift de S5 de 10,0× a 2,4× |
| 6 | Bajo un **TOTALIZADOR** no se suman los individuales | Doble conteo de energía |
| 7 | Semáforos y cámaras son **alumbrado no medido** | Su energía queda como PNT falsa |
| 8 | Las acometidas de cliente **no** son bifurcación de ramal | Cada cliente sería su propio ramal |
| 9 | Con < 3 períodos **no** se detectan transferencias | Candidatos inventados sobre ruido |
| 10 | Los identificadores de objetivo son **únicos globalmente** | Órdenes de trabajo ambiguas |
| 11 | El id de sector se deriva de la **coordenada** | Las órdenes emitidas cambian de sitio |
| 12 | La base de multados usa **fecha de corte** | Fuga de información: lift inflado |
| 13 | Los no multados **no son negativos** confiables | Modelo entrenado sobre etiquetas falsas |
| 14 | Ante tarifa no reconocible → `NO_CLASIFICADO`, **no** residencial | Industriales tratados como residenciales |
| 15 | Series con distinto **hash de configuración** no son comparables | Se atribuye a la red un cambio de parámetros |
| 16 | La relación **ALIMENTA no propaga geometría** | Un arrastre de dedo reescribe el alimentador entero |
| 17 | Reconectar es **RECONECTAR**, no MODIFICAR | Solo se recalcularía una zona; la otra queda con energía que ya no le corresponde |
| 18 | El vínculo anterior se borra **en la misma transacción** que se crea el nuevo | El consumo se contaría en dos zonas a la vez |
| 19 | Sincronizar **no cierra** las órdenes que el técnico no marcó | Se dan por terminadas visitas que no se hicieron |
| 20 | Lo ya enviado **no se reprocesa** | El histórico cuenta el mismo cambio tantas veces como días duró la orden |
| 21 | Se marca lo enviado **después** de la respuesta del servidor | Una subida fallida a mitad perdería esos cambios en silencio |
| 22 | Un paquete de campo por **técnico**, nunca por orden | Snap roto, elementos duplicados y ediciones en conflicto irresolubles |
| 23 | Una campaña que no persigue energía lleva **0 en recuperable** | Se la evalúa por kWh, parece inútil y deja de hacerse |
| 24 | Nada entra al modelo **sin revisión humana** | El trabajo de campo degradaría el SIG en vez de mejorarlo |

---

# 6. Parámetros de configuración

Secciones del YAML (`config/base.yaml`):

| Sección | Contenido |
|---|---|
| `proyecto` | Nombre, versión, unidad de negocio, zona horaria |
| `rutas` | DuckDB, salidas, capas bronze/silver/gold |
| `fuentes` | Bases de origen (credenciales **solo** por nombre de variable de entorno) |
| `migracion` | Fuente, mapa de campos, tablas, tolerancias |
| `comercial` | Separadores por columna, orientación temporal, `mes_final`, nombres de columna, rangos plausibles |
| `promedio` | Método, ventana, recorte, half-life, mínimos |
| `carga` | Método de demanda, voltajes, clases tarifarias (a, b, A, B, cosφ, FC, k) |
| `senales` | Umbrales S1–S9, contaminación, no supervisado |
| `segmentacion` | Columna de tarifa, percentil base, mínimo de pares, cortes de estrato, umbral de gran cliente |
| `organizacion` | Catálogo de jerarquía, inferencia por prefijo |
| `carga_parcial` | Ruta del alcance, universo de alimentadores, cobertura mínima |
| `historico` | Habilitado, ruta, mínimo de períodos para tendencia |
| `catalogos` | Rutas de conductores y transformadores |
| `perdidas` | k por tipo de alimentador, temperaturas, alfa, watts de medidor, Monte Carlo |
| `alumbrado` | Horas, factores, balastro |
| `balance` | Umbrales de los controles C01–C06 |
| `cargabilidad` | Umbrales de sobrecarga y subutilización |
| `flujo` | Tolerancia, iteraciones, impedancia de neutro |
| `seguridad` | JWT, ruta de usuarios, redes permitidas, intentos de login |
| `dashboard` / `visor` | Puertos y hosts |

El trabajo de campo no añade parámetros al YAML: sus umbrales operativos
—tolerancia de snap, tope de propagación, tamaño de paquete— viajan en el
**manifiesto del paquete**, para que el móvil los reciba sin publicar una versión
nueva de la aplicación.

**Regla:** ningún valor numérico del dominio está escrito en el código. Un
parámetro nuevo se agrega al modelo pydantic **y** al YAML; una clave desconocida
hace fallar el arranque.

---

# 7. Seguridad

| Control | Implementación |
|---|---|
| Credenciales de base | Solo nombres de variables de entorno (`usuario_env`, `password_env`, `dsn_env`) |
| Contraseñas de usuario | Hash bcrypt/PBKDF2; jamás en claro |
| SQL | `URL.create` parametrizado; sin concatenación |
| Visor web | Autenticación + restricción por red (CIDR) |
| Repositorio | Prueba que **falla la CI** si aparece `password:` en el YAML |
| Informes | Escape del contenido de la base de origen antes de insertarlo en HTML |
| Datos sensibles | Los objetivos de campo identifican predios: el visor exige autenticación |
| Usuarios móviles | Se crean **solo** en el backend; no hay registro desde la app |
| Token de dispositivo | Uno por usuario; vincular otro equipo revoca el anterior |
| Pérdida del equipo | Se revoca el token sin tocar la cuenta ni la contraseña del técnico |
| Sesión en el teléfono | `EncryptedSharedPreferences` respaldado por Keystore; si falla, se avisa en vez de fingir cifrado |
| Paquete en el dispositivo | Almacenamiento privado de la app: no aparece en la galería ni para otras aplicaciones |
| Atribución | Todo cambio lleva autor, instante y posición; sin autor el lote se bloquea |
| Evidencia fotográfica | Hash SHA-256 en la captura para detectar sustitución del archivo |

---

# 8. Pruebas

| Suite | Marcador | Cubre |
|---|---|---|
| Unitarias | `unit` | Fórmulas de dominio, catálogos, señales, segmentación, jerarquía, informes |
| Integración | `integration` | Pipeline comercial, de red y focalización de punta a punta |
| Seguridad | `security` | Auth, secretos, visor, ausencia de credenciales |
| Propiedades | hypothesis | `S ≥ P`, `FC(1)=1`, FC monótona y acotada, `F_c² ≤ F_p ≤ F_c` |
| Campo | `unit` / `integration` | GeoPackage, edición topológica, concurrencia, reparto, reconexión, jornadas de varios días |
| Contrato móvil↔backend | `integration` | Lo que la app escribe, el backend lo lee y lo entiende (vía `field/simulator.py`) |

**Escenarios con verdad conocida:**

| Escenario | Tamaño | Uso |
|---|---|---|
| `synth/generator.py` | Configurable | Padrón comercial con hurtos por tipo |
| `synth/scenario.py` | 1 200 clientes | Demo rápida de un alimentador (`demo_completa.py`) |
| `synth/escenario_costa.py` | 20 000 clientes | Prueba a escala, 12 alimentadores, informes PDF |

```bash
pytest                                    # 380 pruebas
python scripts/demo_completa.py           # análisis, 9 pasos
python scripts/demo_ciclo_completo.py     # CICLO ENTERO, 15 etapas, 22 comprobaciones
python scripts/demo_campo_multiusuario.py # despacho a 3 cuadrillas
python scripts/prueba_costa_20k.py        # escala: 20 000 clientes con PDF, ~55 s
cd mobile && ./gradlew test               # proyección y códec de la app Android
```

`demo_ciclo_completo.py` recorre el proceso entero sin datos preparados: cada
etapa consume lo que produjo la anterior, desde generar el padrón sintético hasta
recalcular tras las correcciones que volvieron del campo. **Cada etapa verifica
lo suyo** —una demostración que solo imprime no demuestra nada— y el guion
termina con código de salida distinto de cero si alguna comprobación falla.

---

# 9. Trazabilidad de las peticiones del cliente

| Petición original | Requerimientos | Estado |
|---|---|---|
| Servidor y **diferentes bases de origen** | RF-01…RF-03 | ✅ |
| **Mecanismo de promedio** multi-mes | RF-10…RF-12 | ✅ |
| Identificar clientes con **hurto** | S1–S9, RF-30…RF-32 | ✅ |
| Arquitectura, implementación Windows, proceso | `docs/ARQUITECTURA.md`, `INSTALACION_WINDOWS.md`, `PROCESO.md` | ✅ |
| **Documentado** + pruebas de integración y seguridad | 380 pruebas, 13 documentos | ✅ |
| **Interfaz web** para escritorio y para terceros | RF-101, RF-102 | ✅ |
| Esquema **Puesto → Unidad** | RF-40, §3.1 | ✅ |
| **CLIRLSCOD** como agrupador | RF-23, RF-73 | ✅ |
| Corregir potencia del **último mes** (no residenciales) | RF-13, RF-15 | ✅ |
| Etapas de red E4–E10 | RF-40…RF-55 | ✅ |
| Motor **trifásico con neutro** + **OpenDSS** + migración | RF-42, RF-43, RF-07 | ✅ |
| **FGDB ArcGIS** + red geométrica | RF-02 | ✅ |
| Seccionadores, capacitores, postes, cabecera | RF-40, §3.2 | ✅ |
| **Semáforos y cámaras** como AP no medido | RF-49 | ✅ |
| **TOTALIZADOR** no se suma | RF-51 | ✅ |
| **CATALOGOESTRUCTURA** del Excel + balastro | §3.2, RF-49 | ✅ |
| Agregación LV por circuitsource | RF-50 | ✅ |
| **Oracle 11gR2 + ArcSDE** | RF-03 | ✅ |
| **Impedancias faltantes** del catálogo | §3.2 (derivación) | ✅ |
| **Dónde hacer levantamientos** (alimentador/ramal/trafo/sector) | RF-70…RF-78 | ✅ |
| Balance, técnicas y no técnicas | RF-45…RF-52 | ✅ |
| **Alimentadores incoherentes** | RF-64 | ✅ |
| **Transferencias no reportadas** | RF-60…RF-62 | ✅ |
| **Clientes faltantes** | RF-63 | ✅ |
| **Base de multados** | RF-33…RF-36 | ✅ |
| Datos ficticios y prueba completa | `demo_completa.py`, `prueba_costa_20k.py` | ✅ |
| **Ubicaciones estables** para campo | RF-76, RF-77 | ✅ |
| Qué pasa al **modificar la topología** | RF-55 | ✅ |
| **Clasificación de clientes** por comportamiento | RF-20…RF-25 | ✅ |
| Prueba de **20 000 clientes**, Costa, con PDF | `prueba_costa_20k.py`, RF-106 | ✅ |
| **Carga parcial** de información | RF-08, RF-105 | ✅ |
| **Dashboard histórico** | RF-90…RF-94, RF-104 | ✅ |
| Ver por **unidad de negocio y subestación** | RF-80…RF-84, RF-103 | ✅ |
| **Aplicación móvil** para llevar el trabajo al campo | RF-200…RF-222 | ✅ |
| Órdenes con **múltiples trabajos asignables** | RF-202, RF-206 | ✅ |
| Interfaz web para **asignar a varios usuarios** | RF-107, RF-202 | ✅ |
| Descargar trabajo y cartografía para trabajar **offline** | RF-204, RF-219 | ✅ |
| Crear, modificar (atributos y ubicación) y eliminar en campo | RF-207 | ✅ |
| **Snap**: mover un cliente mueve la red conectada | RF-208, RF-209 | ✅ |
| Modificar **elementos relacionados** (Puesto→Unidad) | RF-210 | ✅ |
| Conocer **todos los cambios** al subir, y recalcular si se aceptan | RF-212, RF-215, RF-216 | ✅ |
| Cartografía **open source o de ArcGIS Server** | RF-219 | ✅ |
| **Histórico** de modificaciones de archivo y de móvil | RF-217 | ✅ |
| Volver a decir **dónde revisar** y recalcular el ranking | RF-216 | ✅ |
| Usuarios móviles generados en el **backend Python** | RF-200 | ✅ |
| Óptimo para **GeoPackage** y equipos de bajo procesamiento | RF-204, RF-221 | ✅ |
| Tablet **gráfico + atributos**; teléfono con transición | RF-218 | ✅ |
| **Varias fotos** por elemento con ubicación, hora y fecha | RF-211 | ✅ |
| **Varios usuarios** descargando y subiendo a la vez | RF-213 | ✅ |
| Trabajo de **varios días** hasta terminarlo | RF-214 | ✅ |
| Definir el trabajo **más allá de lo peor** del ranking | RF-203 | ✅ |
| Editar la **conexión del consumidor** en campo | RF-210 | ✅ |

---

# 10. Limitaciones declaradas

Lo que el sistema **no** hace, dicho explícitamente para que nadie lo suponga:

1. **La comparación con OpenDSS por alimentador completo no es rigurosa.** El
   barrido no modela el salto de tensión MT→BT dentro del flujo; la validación
   rigurosa se reporta sobre el caso MT controlado (0,00 % de diferencia).
2. **Los catálogos de P0/Pk/%Z de transformador son parametrizados**, no vienen
   del Excel del cliente (que aporta kVA, potencias y pérdidas de balastro).
3. **Si el hurto lleva activo toda la ventana de 36 meses**, el nivel base propio
   también está deprimido: S9 no lo verá. Ese caso lo cubren las señales de red
   (N1, N3), no la historia propia.
4. **Los resultados de los escenarios sintéticos no predicen el desempeño real.**
   Los hurtos inyectados son más limpios que los reales; con la base real el lift
   será menor. Lo que los escenarios demuestran es que el proceso corre completo
   y que cada etapa entrega lo que promete.
5. **Sin catálogo organizacional**, la subestación queda `SIN_SUBESTACION` y los
   consolidados por subestación no son utilizables.
6. **La detección de transferencias requiere ≥ 3 períodos** de energía de
   cabecera.
7. **La aplicación Android no tiene pruebas instrumentadas en dispositivo.** El
   render, los permisos y el comportamiento del GPS bajo cobertura real solo se
   comprueban en la calle. Lo que sí está verificado de forma independiente es
   donde un error pasa inadvertido: la proyección (< 1 mm de ida y vuelta) y el
   códec binario (idéntico byte a byte entre Python y Kotlin).
8. **El paquete de campo no sube fotografías como archivo binario**: viajan sus
   metadatos y su hash. Adjuntar los JPEG multiplicaría el tamaño del retorno; el
   archivo se transfiere aparte cuando el caso llega a proceso administrativo.
9. **La aplicación no rastrea el recorrido del técnico.** Es una decisión
   laboral, no técnica, y no corresponde a esta herramienta.

---

Ver también: [Arquitectura](ARQUITECTURA.md) · [Proceso](PROCESO.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Segmentación](SEGMENTACION.md) ·
[Focalización](FOCALIZACION.md) · [Diagnóstico](DIAGNOSTICO.md) ·
[Red eléctrica](RED_ELECTRICA.md) · [Aplicación móvil](APLICACION_MOVIL.md) ·
[Prueba a escala](PRUEBA_COSTA_20K.md) · [Seguridad](SEGURIDAD.md) ·
[Pruebas](PRUEBAS.md) · [Instalación en Windows](INSTALACION_WINDOWS.md)
