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
| `test_campo_reconexion.py` | **Reconexión de consumidor** (la conexión es editable en campo y llega así al móvil, reconectar es topológico y no un atributo, **obliga a recalcular las dos zonas** —origen y destino—, sin origen o destino bloquea el lote, reconectar al mismo transformador solo advierte, un cambio rechazado no arrastra recálculo), **definición del trabajo** (un censo no se evalúa por energía, bloques geográficamente compactos, las cuentas ausentes del padrón se reportan, selección por área dibujada, unir campañas sin códigos repetidos) |
| `test_campo_subtipos.py` | **Subtipos** (un banco de dos unidades no puede servir tres fases, la tarifa depende de la clase de servicio, la tensión depende de si es media o baja), **cambio de subtipo** (lo válido se respeta, lo que deja de valer cae al defecto avisando, sin defecto se limpia y se pide volver a elegir, los campos que no aplican se ocultan y se vacían, el defecto no pisa lo capturado), **contingencias** (el calibre depende del tipo de acometida y **gana al subtipo**), **rango** (rechaza el error de tecleo; la torre metálica sí llega a 40 m), **validación en el servidor** (nombra el subtipo en el mensaje), **el conductor no se acota con un catálogo inventado** sino con CATALOGOESTRUCTURA, **las tarifas clasifican donde el análisis espera** con el mismo clasificador, **sin subtipo no se supone ninguno**, el número que vuelve de SQLite encaja en el dominio, y el **contrato del manifiesto con Kotlin** |
| `test_aprendizaje.py` | **Bucle campo → modelo** (**un predio cerrado no es un cliente inocente**, el cliente que nadie visitó no entra, los confirmados llegan con la fecha de la inspección, la fecha de corte sigue funcionando, los negativos verificados se guardan aparte, reprocesar no duplica, el mismo cliente en dos campañas son dos datos, un hallazgo desconocido avisa y no se usa, **la precisión no se hunde por los predios cerrados**, el contraste avisa si no hay grupo de comparación), **grupo par de agregados** (**un rural con pérdidas altas no es un hallazgo**, el que se sale de su grupo sí aparece, **el perfil no puede contener la métrica evaluada**, la mediana aguanta que haya varios malos, cada unidad de negocio se juzga con su propio nivel, sin grupo par se reporta en vez de inventar, rutas parecidas consultables, los niveles se reportan por separado) |
| `test_recursos.py` | **Presupuesto** (la memoria manda sobre los núcleos, con memoria de sobra manda la CPU, siempre cabe al menos una, la reserva del sistema no se toca, el tope de configuración gana, **la espera de red no se limita por núcleos**), **ejecutor** (procesa todo aunque haya más tareas que trabajadores, **un fallo no cancela el lote**, sin paralelismo no se arranca ningún proceso, la espera en cola queda medida, las tareas se consumen de forma perezosa), **control de admisión** (solo pasan los que caben y el resto espera, la cola llena responde «reintente», **el turno se libera aunque el bloque falle**, esperar demasiado también se rechaza, el reintento escala con la cola) |
| `test_ciclo_campo_completo.py` *(integración)* | **Contrato móvil ↔ backend** verificado con el simulador: lo que la app escribe el backend lo lee y lo entiende (autor, precisión, secuencia sin huecos), **mover arrastra la acometida y llega marcado como propagado**, una reconexión recorre el ciclo hasta el recálculo de las dos zonas, **dos jornadas no duplican el histórico**, y eliminar un puesto con clientes se impide en el dispositivo |
| `test_locations_versioning.py` | **Identidad geográfica estable** (código determinista, agrupa coordenadas cercanas, sectores conservan ubicación al cargar datos nuevos y ante reordenamiento), **registro persistente** (acumula priorizaciones sin inflar por re-ejecución, reincidencia, cierre por inspección, persistencia en disco), **versionado de topología** (alta, sin cambio, cada hash reacciona solo a su dominio, invalidación selectiva por tipo de cambio, historial, **las ubicaciones sobreviven al cambio de red**) |

| `test_escenarios.py` | **Alcance por unidad de negocio** (un usuario sin unidad no ve nada, cada unidad lo suyo, la matriz todas, **lo que no está en el catálogo no se entrega**, subestación repartida entre dos unidades se bloquea, **un conjunto sin columna de unidad se devuelve vacío**, el administrador ve todas sin declararlo, el alcance sobrevive al guardado, un archivo de usuarios anterior sigue abriendo), **escenarios** (acumular no toca el modelo, **cada evaluación es una iteración que se conserva**, la comparación avisa si cambió la topología, un escenario aplicado no admite cambios, el listado respeta el alcance, la evolución de una entidad cruza escenarios), **evaluación** (se aplica sobre una copia, el elemento ausente se reporta, los clientes se encuentran dentro de listas, **CREAR/ELIMINAR no se evalúan a medias**, **el porcentaje se recalcula y no se promedia**, lo no aplicado sale en la lectura, **el tipo de balance viaja con la iteración y los controles llegan al usuario**, **la cabecera incompleta no pasa por medida**, la comparación avisa si cambió el tipo de balance, lectura del CSV de cabecera) |

| `test_jobs.py` | **Ejecución** (los pasos se ordenan solos, un paso inexistente se dice con los que hay, **lo que el plan da por hecho es aviso y no error**, todos los planes preparados son ejecutables, una bandera booleana se pone o no se pone, **un fallo detiene la cadena**, **la bitácora se escribe mientras corre**, el resumen deja fuera las trazas de registro, un paso que no se puede lanzar no tumba la corrida), **programación** (una tarea sin pasos no se acepta, **el día 29 al 31 se rechaza**, la hora mal escrita se dice con el formato, cuándo toca la próxima vez, **una tarea recién creada no se saltó nada**, una que de verdad se saltó se reporta, **las órdenes generadas llevan rutas absolutas**, el día de la semana llega bien a cada planificador, las tareas sobreviven al reinicio, una tarea corrupta no impide leer las demás) |

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

También cubre los **límites de recursos**: la API acota las descargas
simultáneas —seis técnicos a la vez, nunca más de dos en curso, ninguno
rechazado— y, con la cola llena, **el trabajo del técnico no se pierde**: el
paquete se guarda antes de pedir turno y se responde 503 con `Retry-After`.
La invariante que se comprueba vale gane quien gane la carrera:
`atendidas + rechazadas == intentos`, y los tres paquetes acaban en disco.

También cubre el **trabajo de varios días**: sincronizar cierra solo lo que el
técnico marcó completado, las órdenes empezadas suman jornada y siguen abiertas,
las que nadie abrió no cuentan avance, y lo ya enviado no se reprocesa al día
siguiente. Y la **subida simultánea** de los tres: cada lote llega entero, con su
identificador y a nombre de quien lo hizo.

Esta prueba destapó un defecto que no habría dado la cara en desarrollo:
`from __future__ import annotations` hacía que FastAPI recibiera `UploadFile`
como una cadena sin resolver, así que `/movil/sincronizar` devolvía 500 con
cualquier paquete real. Corregido.

El recorrido completo, con reparto, paquetes en lote y verificación final, está en
`scripts/demo_campo_multiusuario.py` (24 órdenes, 3 cuadrillas, 24/24 correctas).

## El ciclo entero, de punta a punta

`scripts/demo_ciclo_completo.py` recorre **las 15 etapas** sin datos preparados:
cada una consume lo que produjo la anterior.

```bash
python scripts/demo_ciclo_completo.py      # ~40 s, 22 comprobaciones
```

| Etapa | Qué demuestra | Verificación |
|---|---|---|
| 1–2 | Escenario sintético y análisis comercial | 900 clientes × 36 meses, 100 % clasificado |
| 3 | Balance y PNT | flujo 3φ converge, PNT ≥ 0 |
| 4 | Credibilidad | detecta **la** transferencia inyectada y **los 27** clientes sin SIG |
| 5 | Focalización | 298 objetivos en 7 niveles; ubicaciones con identidad estable |
| 6 | Trabajo definido a mano | un censo lleva 0 en recuperable, a propósito |
| 7 | Reparto entre 3 cuadrillas | desbalance 8 %, dispersión 1,9 km |
| 8–9 | Paquetes y descarga simultánea | 20/20 órdenes, cada técnico ve **solo lo suyo** |
| 10 | **La jornada de campo** | editar, mover con propagación, reconectar, fotografiar |
| 11 | Subida simultánea | 3 lotes íntegros, órdenes empezadas **siguen abiertas** |
| 12 | Revisión granular | acepta unos cambios y rechaza otros del mismo lote |
| 13 | Invalidación selectiva | la reconexión obliga a rehacer topología, balance y ranking |
| 14 | **Segundo día** | lo enviado ayer no se reprocesa; el avance quedó anotado |
| 15 | Coherencia final | ninguna orden perdida en todo el ciclo |

El guion **termina con código de salida distinto de cero** si alguna comprobación
falla: una demostración que solo imprime no demuestra nada.

Ejecutarlo destapó dos incoherencias reales que las pruebas por módulo no veían:
el simulador no marcaba lo subido —así que el día 2 reenviaba el día 1— y una
orden abierta en el dispositivo figuraba como «descargada» en el backend, de modo
que el tablero del supervisor mostraba trabajo sin empezar donde había una
cuadrilla trabajando. Las dos están corregidas y con prueba.

Al activar la validación de dominios volvió a pasar lo mismo, y por eso conviene
correrlo: encontró **datos inventados que ya estaban en el repositorio** —un
hallazgo `"NORMAL"` que ningún reporte agrupaba con `SIN_NOVEDAD`— y dos defectos
propios que solo aparecen con datos reales: un campo `REAL` vuelve de SQLite como
`220.0` contra un dominio que dice `"220"`, y suponer el primer subtipo cuando el
SIG no trae el campo aplicaba reglas que nadie eligió.

## Una jornada de campo sin dispositivo

```bash
ptnt campo-simular --paquete outputs/campo/paquetes/jperez.gpkg
```

Escribe sobre el **GeoPackage real** con las mismas reglas que la aplicación
—diario con secuencia, subtipos con sus dominios, snap topológico, fotos con
ubicación y hora—, así que el paquete que sale es indistinguible del que subiría
un teléfono. Es lo que permite verificar el ciclo completo desde el servidor y
dejarlo en el programador de tareas.

Lo que **no** sustituye: el render del mapa, los permisos y el GPS bajo cobertura
real. Eso solo se comprueba en la calle, y sigue pendiente. El paso a paso
—simulador, emulador y teléfono— está en
[Probar la app de campo desde Windows](PRUEBAS_CAMPO_WINDOWS.md).

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

### El tablero, ejecutado de verdad

`tests/integration/test_tablero.py` ejecuta el tablero con `AppTest`, que corre
el guion igual que lo haría un navegador. Comprobar que el servidor responde
HTTP 200 no prueba nada: Streamlit ejecuta el script al conectarse, y es ahí
donde revienta.

Seis pruebas fijan lo que más importa: que **arranca sin excepción**, que una
credencial mala no deja pasar, que **cada analista ve solo su unidad de negocio**
(los resultados traen todas dentro y el tablero los lee tal cual), que la matriz
las ve todas y puede acotar a una, que un usuario **sin unidad no ve nada y se le
explica por qué**, y que sin resultados calculados se ofrece el botón de empezar
en vez de un comando.

Dos defectos reales aparecieron al escribirlas: el tablero **reventaba con
`IndexError`** cuando el ranking filtrado quedaba vacío —un analista cuya unidad
aún no se ha cargado tumbaba la aplicación entera—, y las **razones de sospecha
nunca se mostraban**, porque llegan del CSV como texto y se comprobaba si eran
una lista.

### Escenarios de trabajo y alcance por unidad

`scripts/demo_escenarios.py` recorre **12 etapas y 20 comprobaciones** usando la
CLI real contra una fuente sintética de cinco alimentadores en tres unidades de
negocio:

```bash
python scripts/demo_escenarios.py          # ~25 s
```

Muestra el ciclo completo —abrir, acumular, evaluar, iterar, comparar— y, en las
tres últimas etapas, lo que un usuario de otra unidad **no** puede hacer: no
puede abrir un escenario sobre un alimentador ajeno, no puede evaluarlo, y no lo
ve siquiera en el listado. La matriz sí ve los de todas y puede trabajar sobre
cualquiera.

Las etapas 6 y 7 son el mismo escenario evaluado sin y con energía de cabecera:
la PNT pasa de `INDICATIVO` (estimada, con `C01` saltando por PNT negativa) a
`MEDIDO`, y la comparación entre ambas iteraciones advierte de que se están
restando dos números de garantía distinta.

## Estado actual

```
517 passed
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
