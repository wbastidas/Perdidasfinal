# Pruebas — PTNT-BAL

El proyecto tiene tres suites, marcadas con `pytest` markers.

```bash
pytest -m unit          # fórmulas de dominio y propiedades
pytest -m integration   # extremo a extremo con datos sintéticos
pytest -m security      # autenticación, secretos, visor
pytest                  # todo
pytest --cov=ptnt --cov-report=term-missing   # con cobertura
```

## Pruebas unitarias (`tests/unit/`)

| Archivo | Cubre |
|---|---|
| `test_commercial_parser.py` | Conversores §6.4 (`1.302`→1302, coordenadas), eje temporal, **aborto por orientación invertida**, parseo completo |
| `test_demand.py` | Casos calculados a mano (P_media, Velander, coincidencia, reactiva, corriente) + **propiedades hypothesis** (`S ≥ P`, `FC(1)=1`, FC monótona, FC acotada) |
| `test_averaging.py` | Cada método de promedio, robustez de la media recortada/mediana frente a un mes atípico, ventana, exclusión de ceros suspendidos, marca de no confiable |
| `test_config.py` | Validación estricta: fallo por **parámetro obligatorio ausente**, clave desconocida, `A+B=1`, hash estable |
| `test_losses.py` | Factor de pérdidas (+ propiedad `F_c² ≤ F_p ≤ F_c`), corrección de temperatura, **capacidad de banco** (delta abierto √3, banco 3, desigual, delta 4h, simple), **P0 no multiplicado por F_p**, conductores I²R, medidores |
| `test_topology.py` | Grafo radial, trazas arriba/abajo, `path_to_source`, subárbol, asignación a transformador, detección de ciclo e isla, fuente inexistente |
| `test_powerflow.py` | Convergencia sin carga (pérdida 0), caída de tensión con carga, monotonía pérdida-carga |
| `test_balance_lighting_grid.py` | Balance MEDIDO/INDICATIVO, controles C01/C03, energía de luminaria, **exclusión BAJOMEDICION**, cargabilidad, desbalance de fases |
| `test_risk.py` | Agregación multinivel del riesgo, penalización por baja confiabilidad, inferencia de configuración de banco |
| `test_advanced.py` | Monte Carlo (percentiles ordenados), **validación del flujo** (<2% error, incl. OpenDSS si está), motor de reglas (detecta R05/R11/R12/R22/R24/R15; red limpia sin hallazgos), exportador OpenDSS, señales de red N1/N3/N4, reporte ejecutivo HTML |
| `test_3ph_migration.py` | Decodificadores (fase bitmask, kVA, cascada de longitud), **motor trifásico con neutro** (balanceado→neutro 0, desbalanceado→neutro y pérdida; 3φ≈1φ balanceado), **migración de datos** round-trip y desde DuckDB, **indicador de desbalance** (ignora acometidas monofásicas y tramos de cola sin carga) |
| `test_survey.py` | **Derivación de impedancias** (AWG/kcmil, R vs fabricante <3%, cobertura 415/415, kcmil mal etiquetado), **focalización** (ramales ignoran acometidas, todos los niveles, ramal sin señales no prioritario, baja confiabilidad = problema de datos, sectores por cercanía, órdenes por rendimiento por visita, exportable/reportable) |
| `test_anomalies_confirmed.py` | **Transferencias** (detecta la inyectada, 1 mes = NO_APLICABLE, pico no es transferencia), **clientes faltantes** (ambos sentidos, umbral de energía, concentración por ruta), **incoherencias** (PNT negativa bloquea publicación, transferencia excluye del ranking), **base de multados** (lift real, control negativo con ranking aleatorio, fecha de corte contra fuga, calibración detecta señal inútil, PU learning), **ruta comercial** (sospecha, incoherencia por ceros/estimadas, nivel del plan) |
| `test_catalog_lv.py` | **Catálogo CATALOGOESTRUCTURA** (clasificación por prefijo, kVA/banco, balastro AP, kVAR, doble nivel), **agregación LV al transformador** sin doble conteo en tronco, **totalizador** (no re-suma individuales), **semáforos/cámaras** como AP no medido, balastro desde catálogo |
| `test_segmentacion.py` | **Clasificación de tarifas reales** (texto libre de DESTARI, no se adivina residencial), **resolución semántica de la clave del catálogo** (evita que los industriales caigan a la clase por defecto), **grupo par jerárquico** (nivel más fino disponible, degradación, sin grupo se reporta, confianza pondera S5), **el estrato NO entra en el grupo par** (regresión crítica: sería circular), **S9** (déficit contra la base propia), **recuperable segmentado** (el global subestima al industrial e inventa al pequeño; máximo de dos estimadores; mediana robusta), **rendimiento por visita** (ordena por valor, no por score) y **grandes clientes** (solo con indicios reales) |
| `test_escenario_costa_reportes.py` | **Escenario costero** (estacionalidad de Costa y no de Sierra, coordenadas UTM 17S, mezcla realista de clases, rutas con vocación, hurto concentrado en periferia, Tarifa Dignidad solo bajo el techo, las redes llevan clientes reales del padrón, alimentadores distintos entre sí, **cabecera cubre el facturado**), **motor de informes** (enteros sin decimales, tabla vacía y filas omitidas, SVG válido y sin datos, **el mapa conserva la escala real del terreno**, HTML autocontenido, **escapa el contenido de la base de origen**, compendio con saltos de página) |
| `test_org_carga_historico.py` | **Jerarquía organizacional** (catálogo, columnas obligatorias, alimentador repetido, inferencia advertida, la energía suma hacia arriba, **los porcentajes se recalculan y no se promedian**, **un solo INDICATIVO degrada todo el consolidado**), **carga parcial** (cobertura y faltantes, cargas acumulables, balance MEDIDO exige padrón+red+cabecera, alimentador fuera del universo, pendientes, persistencia, **consolidado incompleto se marca PARCIAL**), **histórico** (serie, re-registro reemplaza, comparación con tendencia, **cambio de configuración invalida la comparación**, advertencias, persistencia, vacío) |
| `test_campo.py` | **GeoPackage OGC** (geometría ida y vuelta, envolvente, metadatos estándar, **no pierde columnas con filas heterogéneas**, índice espacial, manifiesto interno), **esquema** (Puesto→Unidad editable por separado, guid estable, dominios para el móvil, fotos con ubicación y fecha), **edición topológica** (**mover un cliente arrastra el extremo de su acometida**, mover un puesto arrastra sus unidades, **la relación eléctrica NO arrastra geometría**, tope de propagación, snap, no se elimina un puesto con dependientes, validación), **órdenes** (solo hash, vincular revoca el anterior, asignación masiva, no reasigna en silencio, máquina de estados), **paquete** (recorte al área, manifiesto, aviso por tamaño, huella), **sincronización** (lee el diario, rechaza paquete ajeno, hueco de secuencia bloquea, GPS impreciso advierte, **revisión parcial**, propagación incoherente advierte, **etapas a recalcular**), **histórico** (campo + archivo, historia de un elemento, más editados) |
| `test_campo_multiusuario.py` | **Concurrencia** (tres cuadrillas sincronizando a la vez no pierden ninguna actualización, la segunda escritura sobre el estado ya consumido no surte efecto, una transición perdida se reporta como conflicto, la asignación en conflicto no deja nada escrito, cierre idempotente, el registro sobrevive al proceso, **migra un registro JSON anterior**), **reparto entre cuadrillas** (equilibra la carga, mantiene juntas las órdenes de cada una, el tope de jornada deja fuera lo de menor energía, sin coordenadas sigue siendo parejo, **es determinista**, aplicar el reparto deja a cada técnico con lo suyo, criterio desconocido falla claro), bitácora y carga por usuario |
| `test_locations_versioning.py` | **Identidad geográfica estable** (código determinista, agrupa coordenadas cercanas, sectores conservan ubicación al cargar datos nuevos y ante reordenamiento), **registro persistente** (acumula priorizaciones sin inflar por re-ejecución, reincidencia, cierre por inspección, persistencia en disco), **versionado de topología** (alta, sin cambio, cada hash reacciona solo a su dominio, invalidación selectiva por tipo de cambio, historial, **las ubicaciones sobreviven al cambio de red**) |

**Propiedades verificadas (hypothesis):**
- `S ≥ P` para todo P ≥ 0 y cosφ ∈ [0.5, 1].
- `FC(1) = A + B = 1`; `FC` monótona decreciente; `FC ∈ [mínimo, 1]`.

## Pruebas de integración (`tests/integration/`)

`test_pipeline.py` ejecuta el pipeline **comercial** sobre el CSV sintético y verifica:

- **Extremo a extremo:** 600 cuentas × 36 meses, ranking no vacío y ordenado.
- **Recuperación de hurtos** (criterio E10): la posición **mediana** de los hurtos
  inyectados cae en el tercio superior del ranking, y el **recall en el top-15 %**
  es ≥ 40 %. Es la métrica honesta de calidad del detector.
- **Persistencia DuckDB:** las tablas de resultado y el registro de corrida se
  crean correctamente.
- **Reconciliación:** produce una corrección de potencia medible (el SIG usaba el
  último mes; el sistema, el promedio multi-mes).
- **Segmentación:** con y sin segmentar sobre el mismo padrón, el recall no se
  degrada y la **energía recuperable priorizada en el top 10 % crece más de 3×**.
  Es la prueba que justifica el módulo: mismo esfuerzo de cuadrilla, mucha más
  energía en juego.
- **Clase tarifaria desde texto real:** ninguna descripción de `DESTARI` coincide
  literalmente con las claves del catálogo, y aun así cada clase recibe su propio
  `cosφ` — si la resolución semántica fallara, todas caerían al valor por defecto.

`test_survey_e2e.py` valida el **requerimiento general** de punta a punta: el plan
cubre todos los niveles, cada objetivo es accionable (acción + motivo), las órdenes
agrupan clientes por visita y ordenan por rendimiento, y el resultado es exportable
(CSV/XLSX) y reportable (HTML).

`test_grid_pipeline.py` ejecuta el pipeline **de red** (E4–E10) sobre la red radial
sintética y verifica:

- El flujo de potencia converge y la tensión mínima está en rango.
- **El balance cierra:** `pérdidas_total = entrada − facturado − AP − propios − ENS`
  y `PNT = pérdidas_total − técnicas`.
- La pérdida en vacío es fracción dominante de la pérdida de transformación en una
  red subutilizada (efecto de P0 constante).
- Sin cabecera, el balance es INDICATIVO.

## Pruebas de seguridad (`tests/security/`)

`test_security.py`:

- La contraseña **no** aparece en el archivo de usuarios (solo el hash).
- `verify_password` acepta la correcta y rechaza la incorrecta; rechaza
  contraseñas cortas.
- Usuario inexistente no se distingue por comportamiento.
- Un secreto ausente lanza error; las credenciales SQL se resuelven **desde el
  entorno** y el `repr` no filtra la contraseña.
- **El YAML del repositorio no contiene credenciales embebidas** (falla la CI si
  aparece `password:`).
- El visor web devuelve **401** sin credenciales, **401** con credencial errónea,
  **200** con credencial válida y **403** desde una red no autorizada.
- El conector SQL usa `URL.create` parametrizado (no concatena la contraseña).

## Prueba a escala — 20 000 clientes, Costa ecuatoriana

`scripts/prueba_costa_20k.py` ejecuta el proceso completo sobre un segundo grupo
de prueba independiente y emite **un informe HTML + PDF por etapa** (≈ 55 s).
Detalle completo en [`PRUEBA_COSTA_20K.md`](PRUEBA_COSTA_20K.md).

| Verificación | Esperado | Obtenido |
|---|---|---|
| Recall top 10 % | > 60 % | **99,8 %** |
| Lift top 5 % | > 3× | **16,7×** |
| Transferencia de carga inyectada | GYE-04 → GYE-05 | **detectada** |
| Clientes faltantes | 500 | **500** |
| Balance cierra en todos los alimentadores | 12 | **12** |
| Identificadores de objetivo únicos | 1 453 | **1 453** |

Esta prueba destapó cuatro defectos que la demo de 1 200 clientes no revelaba:
la detección de transferencias confundida por la estacionalidad común, la
colisión de identificadores entre alimentadores, la contradicción entre el
diagnóstico y el plan, y un doble conteo de estacionalidad en el escenario.
Los cuatro están corregidos y con prueba de regresión.

`test_campo_api_multiusuario.py` levanta la **API móvil real** con tres técnicos
y una jornada repartida, y verifica lo que el técnico ve y lo que nadie ve:

- Cada técnico ve **exactamente** su parte al conectarse — nada de otro.
- Los tres descargan su paquete **al mismo tiempo** y las 18 órdenes cambian de
  estado: ninguna actualización se pierde.
- Sin token, o con uno inventado, no se ve nada (401).
- Revocar el equipo de un técnico lo deja fuera **sin tocar** a los demás.

El recorrido completo, con reparto, paquetes en lote y verificación final, está en
`scripts/demo_campo_multiusuario.py` (24 órdenes, 3 cuadrillas, 24/24 correctas).

## Demostración extremo a extremo con datos ficticios

Además de las suites automáticas, `scripts/demo_completa.py` ejecuta el proceso
**completo** sobre un escenario ficticio con verdad conocida (`ptnt.synth.scenario`),
de modo que cada resultado se puede contrastar contra lo que se inyectó:

```bash
python scripts/demo_completa.py            # 9 pasos, ~1 min
```

| Paso | Qué demuestra | Verdad inyectada vs. detectado |
|---|---|---|
| 1 | Generación del escenario | 1 200 clientes, 36 meses, 64 hurtos inyectados, 38 multados (59 % histórico) |
| 2 | Carga y versionado inicial | ALTA v1: 337 tramos, 164 clientes |
| 3 | Análisis comercial y **segmentación** | 100 % clasificado; no residenciales = 2,6 % de clientes y **85 % de la energía**; una visita industrial rinde **49×** más que una residencial |
| 4 | Red y balance con incertidumbre | PNT 1 067 kWh = 2,3 % (P10–P90 769–1 377) |
| 5 | Diagnóstico de credibilidad | transferencia F002→F003 detectada (simetría 0,90); 36/36 clientes faltantes; sin incoherencias |
| 6 | Validación contra multados | **lift 12,6×**, AUC 0,861, mediana de hurtos en el 3 % del ranking |
| 7 | Focalización | 300 objetivos en 7 niveles; 10 órdenes cubren 171 clientes / 270 113 kWh |
| 8 | **Estabilidad de ubicaciones** | 11/11 sectores conservan su identificador tras cargar datos nuevos |
| 9 | Modificación de topología | cada tipo de cambio invalida **solo** sus etapas; v1 se conserva como histórica |

Los pasos 8 y 9 son los que se corresponden con los tests de
`test_locations_versioning.py`: la demo los muestra sobre datos realistas y los
tests los fijan como garantía de regresión.

## Estado actual

```
358 passed
```

Cobertura del núcleo de dominio (objetivo de la especificación ≥ 85 %):
reconciliación 100 %, balance 100 %, factor/capacidad de pérdidas ~95 %,
Monte Carlo y validación ~90 %, topología ~90 %, motor de reglas ~90 %, scoring
90 %, configuración 92 %, señales 85 %; promedio, demanda y flujo ~75 % (las ramas
no cubiertas son variantes de método y guardas de error).

## Integración continua

Añadir a la CI (GitHub Actions / Azure DevOps):

```yaml
- run: pip install -e ".[all]"
- run: pytest -m "unit or security" --cov=ptnt --cov-fail-under=70
- run: pytest -m integration
```
