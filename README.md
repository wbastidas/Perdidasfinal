# PTNT-BAL — Plataforma de Pérdidas No Técnicas y Balance Energético

Sistema en Python, pensado para un **servidor único**, que a partir de la
información comercial de consumo (hasta 36 meses por cliente) y del modelo de
datos de la red (esquema **Puesto → Unidad** homologado CNEL EP):

1. **Ingiere desde distintas bases de origen** (CSV comercial, SQL Server,
   PostgreSQL, Oracle, MySQL, Parquet/DuckDB y la **File Geodatabase de ArcGIS**
   —red geométrica, ST_Geometry, EPSG:32717— o **Oracle 11gR2 + ArcSDE**)
   mediante conectores intercambiables. Modela transformadores (Puesto→Unidad),
   clientes, luminarias, **semáforos/cámaras**, seccionadores, **bancos de
   capacitores**, postes y la **cabecera** (PuestoProteccionDinamico con
   CircuitSource), y agrega la baja tensión a cada transformador por
   `PARENTCIRCUITSOURCEGUID`.
2. **Promedia el consumo sobre varios meses** con métodos robustos (media,
   media recortada, mediana, ponderada por recencia, estacional). El promedio
   multi-mes reemplaza al “último mes” que usa el SIG, que es frágil.
3. **Recalcula la potencia activa, reactiva, aparente y la corriente** por
   cliente (§6): Velander + coincidencia + `cosφ` por clase + corriente correcta
   por configuración de fase. Corrige el error del SIG de calcular la potencia
   con el consumo del último mes — sesgo grave en clientes **no residenciales**.
4. **Genera el informe de reconciliación** de potencia (SIG vs. corregido) con
   descomposición de la diferencia por causa (energía-vs-demanda, √3, cosφ, etc.).
5. **Segmenta el padrón** por clase tarifaria, nivel de tensión, modalidad de
   medición y estrato de consumo (desde `ATRIBUTOSCONSUMIDOR`), de modo que cada
   cliente se compare solo contra clientes equivalentes. Un taller y un
   departamento no se parecen: comparar a todos contra la misma referencia genera
   falsos positivos en residenciales pequeños y **falsos negativos en comerciales
   e industriales**, que es donde está la energía.
6. **Identifica clientes con posible hurto** (pérdidas no técnicas) mediante
   señales de comportamiento sobre la serie de 36 meses (S1–S9), **grupos par
   jerárquicos** (clase × tensión × fases × **CLIRLSCOD**, con degradación
   controlada y factor de confianza), detección no supervisada y un ranking de
   consenso con la energía recuperable estimada **dentro del segmento** y las
   razones en lenguaje operativo.
7. **Dice dónde ir a hacer el levantamiento**: rankings por alimentador, zona,
   ramal, transformador, **sector geográfico** y cliente, con órdenes de trabajo
   priorizadas por rendimiento por visita, exportables y reportables.
8. **Mantiene la identidad de las ubicaciones entre corridas**: los sectores y
   objetivos se identifican por su **coordenada**, no por el orden del cálculo, de
   modo que una orden emitida hoy sigue apuntando al mismo sitio físico después de
   cargar el mes siguiente o de modificar la topología.
9. **Consolida por unidad de negocio y subestación**, con la regla de que la
   energía se suma hacia arriba pero la **credibilidad no**: basta un alimentador
   sin medición confiable para que el consolidado no pueda presentarse como
   MEDIDO.
10. **Acepta carga parcial** de información con alcance declarado por insumo y
    alimentador, y marca **PARCIAL** todo consolidado incompleto en vez de
    dejar que se lea como el total.
11. **Guarda el histórico** del balance por período y entidad, para ver si un
    plan de reducción de pérdidas está funcionando.
12. **Lleva el trabajo al campo**: asigna órdenes a técnicos, arma un
    **GeoPackage** con la red del área y cartografía offline, y una aplicación
    **Android** permite crear, modificar, mover y eliminar elementos **sin
    señal**, manteniendo el snap topológico —si se mueve un cliente se mueve su
    acometida— y capturando fotos con ubicación y hora. Los cambios vuelven,
    **se revisan** y los aceptados disparan el recálculo del balance y del
    ranking.
13. **Reproduce el comportamiento del modelo de datos del SIG en el formulario**:
    al cambiar el **subtipo** de un elemento cambian los dominios de sus campos,
    sus valores por defecto y cuáles aplican. Un banco en delta abierto tiene dos
    unidades y sus fases posibles son AB, BC o CA: **ABC es imposible y no se
    puede elegir**. Los dominios se revalidan también en el servidor, porque la
    aplicación no es el único que escribe en el paquete.
14. **Se ajusta a los recursos del equipo**: procesa tantos alimentadores como
    caben en la memoria disponible —respetando los límites del contenedor— y
    **encola el resto**; lee las **11 unidades de negocio en paralelo** con tope
    por base; y acota descargas y subidas de las cuadrillas con cola y
    `Retry-After`, sin perder nunca el trabajo del día.
15. **Aprende con el uso**: lo que la cuadrilla encuentra en campo vuelve a la
    base de casos confirmados —distinguiendo hurto verificado, cliente
    verificado y limpio, y visita que no concluyó, porque **un predio cerrado no
    es un cliente inocente**— y el detector se recalibra contra la realidad
    local. Y cada alimentador, ramal, sector o ruta se juzga **contra los que se
    le parecen**: un rural largo pierde más por física, y compararlo contra el
    promedio de la empresa manda cuadrillas donde no hay nada.
16. **Deja probar antes de publicar**: un analista define un alimentador o una
    subestación, **acumula cambios sin tocar el modelo oficial** y ve el balance
    al momento. Cada evaluación queda como una iteración que no se sobrescribe,
    de modo que la evolución de ese alimentador se puede recorrer y comparar en el
    tiempo. Y cada usuario trabaja **su** unidad de negocio —la matriz las ve
    todas—, con el control aplicado al leer los datos, no en la pantalla.
17. **Se opera desde el navegador, sin escribir comandos**: el tablero lanza el
    proceso, sigue su avance, muestra los resultados, prepara el trabajo de campo
    y define las **actualizaciones automáticas** que el Programador de tareas de
    Windows ejecuta de madrugada. La línea de comandos sigue estando, y hace
    exactamente lo mismo: es el único camino de ejecución.
18. **Publica los resultados en dos interfaces web**: un tablero de análisis para
    escritorio (Streamlit, 12 pestañas) y un visor de solo lectura (FastAPI).

> **[Especificación completa](docs/ESPECIFICACION_COMPLETA.md)** — requerimientos,
> campos y arquitectura de información para replicar el sistema.
>
> Documentación en [`docs/`](docs/): [Arquitectura](docs/ARQUITECTURA.md) ·
> **[Instalación paso a paso en Windows](docs/INSTALACION_PASO_A_PASO.md)** ·
> [Instalación en Windows Server](docs/INSTALACION_WINDOWS.md) ·
> **[Guía de operación paso a paso](docs/GUIA_OPERACION.md)** ·
> [Segmentación de clientes](docs/SEGMENTACION.md) ·
> **[Prueba a escala — 20 000 clientes](docs/PRUEBA_COSTA_20K.md)** ·
> **[Aplicación móvil de campo](docs/APLICACION_MOVIL.md)** ·
> **[Probar la app de campo desde Windows](docs/PRUEBAS_CAMPO_WINDOWS.md)** ·
> **[El ciclo completo](docs/CICLO_COMPLETO.md)** ·
> **[Recursos y paralelismo](docs/RECURSOS.md)** ·
> **[Los cálculos y su fundamento científico](docs/CALCULOS.md)** ·
> **[Escenarios de trabajo y alcance por unidad](docs/ESCENARIOS.md)** ·
> **[Ejecución e interfaces de usuario](docs/INTERFACES.md)** ·
> **[Aprendizaje continuo](docs/APRENDIZAJE.md)** ·
> [Focalización de levantamientos](docs/FOCALIZACION.md) ·
> [Diagnóstico y validación](docs/DIAGNOSTICO.md) ·
> [Proceso](docs/PROCESO.md) · [Seguridad](docs/SEGURIDAD.md) ·
> [Pruebas](docs/PRUEBAS.md).

## El ciclo completo con datos ficticios

Antes de conectar la base real se puede ver **todo el proceso funcionando**, del
padrón sintético a la red corregida por las cuadrillas:

```bash
python scripts/demo_ciclo_completo.py     # 15 etapas, ~40 s
```

Recorre el ciclo entero sin datos preparados —cada etapa consume lo que produjo la
anterior—: escenario ficticio, análisis comercial, balance y PNT, diagnóstico de
credibilidad, focalización, definición de trabajo adicional, reparto entre tres
cuadrillas, paquetes descargables, **descarga simultánea desde la API real**, la
jornada de campo (editar, mover con propagación, **reconectar un consumidor**,
fotografiar), subida de los tres a la vez, revisión granular del supervisor,
recálculo selectivo, **segundo día de trabajo** y verificación final.

**22 comprobaciones** validan cada etapa contra lo inyectado, y el guion falla con
código distinto de cero si alguna no pasa. Resultados y lectura en
[`docs/CICLO_COMPLETO.md`](docs/CICLO_COMPLETO.md).

Demostraciones más acotadas:

```bash
python scripts/demo_completa.py            # solo el análisis, 9 pasos
python scripts/demo_campo_multiusuario.py  # solo el despacho a 3 cuadrillas
python scripts/demo_recursos.py            # cómo se ajusta al equipo disponible
python scripts/demo_escenarios.py          # probar cambios sin publicarlos, 12 etapas
```

El paso a paso y la interpretación de cada salida están en
[`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md).

## Prueba a escala con informes PDF

```bash
python scripts/prueba_costa_20k.py      # 20 000 clientes, ~55 s
```

Segundo grupo de prueba independiente: 20 000 clientes de una distribuidora de la
**Costa ecuatoriana** (12 alimentadores, estacionalidad costera, Tarifa Dignidad,
coordenadas UTM 17S de Guayaquil/Durán). Emite **un informe HTML + PDF por etapa**
más un compendio, hasta responder *dónde hay que ir a hacer el levantamiento —
por alimentador, ramal, transformador o sector*. Detalle en
[`docs/PRUEBA_COSTA_20K.md`](docs/PRUEBA_COSTA_20K.md).

---

## Inicio rápido (5 minutos)

```bash
# 1. Instalar (núcleo + almacenamiento + ML + interfaces)
pip install -e ".[store,ml,dashboard,webviewer]"

# 2. Validar la configuración
ptnt verificar-config -c config/base.yaml

# 3. Generar datos sintéticos con hurtos inyectados (para probar sin la BD real)
ptnt generar-sinteticos -o data/entrada/consumos_36m.csv --clientes 2000 --pct-hurto 0.05

# 4. Ejecutar el análisis completo
ptnt analizar --csv data/entrada/consumos_36m.csv --top 15

# 5. Crear un usuario y lanzar las interfaces web
ptnt crear-usuario analista --rol analyst
ptnt dashboard          # tablero de escritorio  -> http://127.0.0.1:8501
ptnt crear-usuario jefatura --rol viewer
ptnt servir-visor       # visor de solo lectura  -> http://127.0.0.1:8080
```

## Comandos

| Comando | Descripción |
|---|---|
| `ptnt verificar-config` | Valida el YAML; falla nombrando el parámetro obligatorio ausente |
| `ptnt probar-fuentes` | Prueba la conectividad de todas las bases de origen |
| `ptnt generar-sinteticos` | Genera un CSV comercial sintético con hurtos conocidos |
| `ptnt analizar` | Pipeline comercial: promedio → potencia → reconciliación → hurto |
| `ptnt migrar` | Migra la red desde la base de origen (FGDB/SQL/DuckDB) al modelo canónico |
| `ptnt analizar-red` | Pipeline de red: topología → flujo (1φ o **3φ desbalanceado con neutro**) → pérdidas (Monte Carlo P10/P50/P90) → balance y PNT; opción `--trifasico` y `--opendss`; genera reporte ejecutivo HTML, `.dss` y reglas de calidad |
| `ptnt diagnostico` | **Credibilidad**: transferencias no reportadas, clientes faltantes, alimentadores incoherentes y **validación contra la base de multados** (lift real del detector) |
| `ptnt focalizar` | **Dónde ir a hacer el levantamiento**: rankings por alimentador/zona/ramal/transformador/sector/cliente + órdenes de trabajo, XLSX y reporte HTML |
| `ptnt validar-flujo` | Valida el flujo contra casos analíticos y contra **OpenDSS** (si está instalado) |
| `ptnt dashboard` | Tablero de análisis (Streamlit) |
| `ptnt servir-visor` | Visor web de solo lectura (FastAPI) |
| `ptnt registrar-carga` | **Carga parcial**: declara qué insumo se cargó de qué alimentadores; reporta cobertura y pendientes |
| `ptnt consolidar` | **Consolidado por unidad de negocio y subestación** + instantánea al histórico |
| `ptnt crear-usuario` | Alta de usuario para las interfaces (solo hash) |
| `ptnt recursos` | **Cuántas tareas caben en este equipo** y medición del coste por alimentador |
| `ptnt campo-usuario` | **Crea un usuario de la aplicación móvil** |
| `ptnt campo-definir` | **Define trabajo sin pasar por el ranking**: censo, actualización cartográfica, verificación de un listado, mantenimiento |
| `ptnt campo-asignar` | **Asigna órdenes de levantamiento a un técnico** |
| `ptnt campo-repartir` | **Reparte la jornada entre varias cuadrillas**: carga pareja y grupos compactos en el territorio |
| `ptnt campo-paquete` | **Genera el GeoPackage descargable** con la red del área y la cartografía offline |
| `ptnt campo-paquetes` | **Genera de una vez el paquete de cada técnico** con trabajo pendiente |
| `ptnt campo-servir` | **API de sincronización** para la aplicación móvil |
| `ptnt campo-simular` | **Hace la jornada del técnico sin dispositivo**: edita el GeoPackage real con las mismas reglas que la app |
| `ptnt campo-revisar` | **Revisa los cambios de campo** y determina qué recalcular |
| `ptnt campo-aprender` | **Incorpora lo que la cuadrilla encontró** a la base de casos confirmados y contrasta el acierto del ranking |
| `ptnt pares` | **Compara cada alimentador, ramal, sector o ruta contra los que se le parecen**, no contra el promedio de la empresa |

## Instalación por capas (extras)

El **núcleo** (parseo, promedio, demanda, señales) depende solo de
numpy/pandas/pydantic, para correr en un servidor mínimo. El resto son extras:

| Extra | Habilita |
|---|---|
| `store` | Persistencia en DuckDB + Parquet |
| `sources` | Conectores SQL Server / PostgreSQL / Oracle / MySQL |
| `ml` | Detección no supervisada (IsolationForest, change points) |
| `dashboard` | Tablero Streamlit |
| `webviewer` | Visor FastAPI |
| `security` | bcrypt / JWT |
| `test` | pytest + hypothesis |

```bash
pip install -e ".[all]"   # todo
```

**En Windows con varios Python instalados** —el de ArcGIS Pro y otros— hay un
instalador que lo deja operativo sin tocar los demás entornos:

```powershell
.\scripts\instalar_ptnt.ps1 -Python "C:\...\envs\su_clon\python.exe"
```

Protege las versiones que ArcGIS fijó (para que `arcpy` siga funcionando),
maneja proxy corporativo e instalación sin internet, y **verifica al final que
quedó operativo**. Paso a paso en
[`docs/INSTALACION_PASO_A_PASO.md`](docs/INSTALACION_PASO_A_PASO.md).

## Estructura

```
src/ptnt/
├── config/       # modelos pydantic + carga YAML (validación estricta)
├── io/
│   ├── sources/  # conectores multi-origen (csv, sql, duckdb, FGDB ArcGIS, Oracle/ArcSDE)
│   └── commercial_parser.py   # parseo §6.4 (separadores por columna)
├── load/
│   ├── averaging.py           # promedio multi-mes (mecanismo clave)
│   └── demand.py              # P, Q, S, I §6 (corrige el √3 y el último-mes)
├── quality/reconciliation.py  # informe SIG vs corregido (§6.1)
├── segment/      # clasificación por tarifa/tensión/estrato + grupo par jerárquico
├── org/          # Unidad de Negocio -> Subestación -> Alimentador
├── ingest/       # carga parcial: alcance declarado y cobertura
├── field/        # trabajo de campo: GeoPackage, órdenes, edición móvil, sync
│   └── domains.py             # subtipos, dominios codificados/rango, contingencias
├── runtime/      # ajuste a los recursos: presupuesto, cola con prioridad, admisión
├── report/       # informes HTML autocontenidos + gráficos SVG + PDF (Chromium)
├── ntl/
│   ├── signals.py             # señales S1–S9 de hurto
│   ├── network_signals.py     # N1/N3/N4 (residuo zona, totalizador, cargabilidad)
│   └── scoring.py             # consenso + ranking
├── canonical/    # decodificadores de dominio (fase bitmask, kVA) + field_map (§4)
├── ref/          # catálogos: conductores, transformadores, CATALOGOESTRUCTURA (Excel)
├── topology/     # grafo radial + trazas + zonas (E4) + versionado de la red (E1.3)
├── powerflow/    # flujo BFS 1φ + 3φ con neutro + OpenDSS (export/run) + validación (E8.1)
├── losses/       # pérdidas técnicas + Monte Carlo P10/P50/P90 (E8)
├── lighting/     # alumbrado público (E7)
├── balance/      # balance jerárquico y PNT + controles C01-C06 (E9)
├── grid/         # cargabilidad, desbalance, riesgo multinivel, agregación LV al trafo (E10)
├── survey/       # focalización: niveles + rutas comerciales (CLIRLSCOD) + sectores + órdenes (§11.5)
│                 # + locations.py: identidad geográfica estable y registro de ubicaciones
├── anomalies/    # transferencias no reportadas, clientes faltantes, incoherencias
├── quality/      # reconciliación (§6.1) + motor de reglas R05/R09/R11/R12/R15/R22/R24/P01/P09 (E5)
├── store/        # DuckDB (schema.sql) + persistencia
├── io/migration.py  # migración origen → modelo canónico (§4.3, round-trip)
├── security/     # auth (hash), secretos por entorno + alcance por unidad de negocio
├── workspace/    # escenarios: acumular cambios, evaluar sobre copia, iteraciones
├── synth/        # generador de datos (comercial + red radial) con hurtos
│                 # + scenario.py: escenario ficticio completo con verdad conocida
│                 # + fuentes.py: fuente con varios alimentadores (subestación entera)
├── dashboard/    # tablero Streamlit (escritorio)
├── webviewer/    # visor FastAPI (solo lectura)
├── pipeline.py       # orquestador comercial
├── grid_pipeline.py  # orquestador de red (E4-E10)
└── cli.py        # CLI typer
```

## Pruebas

```bash
pytest -m unit          # fórmulas + propiedades (hypothesis)
pytest -m integration   # extremo a extremo + recuperación de hurtos
pytest -m security      # auth, secretos, visor
pytest --cov=ptnt       # cobertura
```

**517 pruebas** del backend en verde. Detalle de qué cubre cada archivo y los
resultados de la demostración extremo a extremo en
[`docs/PRUEBAS.md`](docs/PRUEBAS.md).

Las pruebas JVM de la aplicación Android (`mobile/`, incluida `SubtipoTest`, que
fija los **mismos casos** que el backend) están escritas pero **no se han
ejecutado**: hacen falta Gradle y el SDK de Android. Y el ciclo de campo se puede
verificar entero **sin dispositivo**:

```bash
ptnt campo-simular --paquete outputs/campo/paquetes/jperez.gpkg
python scripts/demo_ciclo_completo.py    # 15 etapas, 22 comprobaciones
```

Cómo probar la app desde un Windows de oficina —simulador, emulador y teléfono,
con los errores típicos— en
[`docs/PRUEBAS_CAMPO_WINDOWS.md`](docs/PRUEBAS_CAMPO_WINDOWS.md).

## Alcance de esta entrega

Esta versión implementa:

**Vía comercial** (análisis de consumo y hurto): ingesta multi-origen, promedio
multi-mes, recálculo de potencia con informe de reconciliación, y detección de
hurto (`ptnt analizar`).

**Vía de red eléctrica** (etapas E4–E10, ver [`docs/RED_ELECTRICA.md`](docs/RED_ELECTRICA.md)):
reconstrucción de topología en grafo radial y trazas, flujo de potencia
(backward-forward sweep), pérdidas técnicas por componente (conductores con
corrección de temperatura, transformadores con vacío + carga y capacidad de banco
por configuración, medidores), alumbrado público con exclusión por medición,
balance jerárquico MEDIDO/INDICATIVO con controles C01–C06, cargabilidad y
desbalance de fases (`ptnt analizar-red`).

Incluye además: **motor trifásico desbalanceado con neutro** (corriente y pérdida
de neutro, desbalance), **migración de datos** origen → modelo canónico (§4.3),
**Monte Carlo** de pérdidas y PNT (P10/P50/P90), **motor de reglas de calidad**
(§E5), **exportador y ejecución OpenDSS** con comparación de pérdidas de línea
(validado a ~0% en caso MT controlado), **validación del flujo** contra casos
analíticos, **señales de red** N1/N3/N4, y **exportadores** (XLSX/CSV + reporte
ejecutivo HTML).

**Operación continua**: **versionado de la topología** con tres hashes
independientes (conectividad, atributos y estado de maniobra) que invalidan
**solo** las etapas afectadas por cada cambio, e **identidad geográfica estable**
de las ubicaciones para que las órdenes de campo sobrevivan a las cargas nuevas y
a los cambios de red. Ver [`docs/GUIA_OPERACION.md`](docs/GUIA_OPERACION.md).

**Ciclo de campo completo**: definición del trabajo por alimentador, sector, área
o listado (7 tipos de campaña, no solo «lo peor del ranking»), reparto entre
cuadrillas equilibrando carga y compacidad geográfica, un paquete GeoPackage por
técnico con cartografía offline, edición sin señal con snap topológico,
**subtipos que arrastran sus dominios**, trabajos de varios días con avance
parcial, sincronización simultánea de varias cuadrillas con control de admisión,
revisión humana obligatoria y recálculo selectivo de lo que el cambio afecta.
Ver [`docs/APLICACION_MOVIL.md`](docs/APLICACION_MOVIL.md).

**Escenarios de trabajo y alcance por unidad de negocio**: un analista define un
alimentador o una subestación, **acumula cambios sin publicarlos** y ve el
balance al momento; cada evaluación queda como una iteración que no se
sobrescribe, de modo que la evolución del alimentador en el tiempo se puede
recorrer y comparar. Los usuarios se asignan a unidades de negocio y la matriz
las ve todas: el control se aplica al leer los datos —no en la interfaz— y falla
cerrado. Ver [`docs/ESCENARIOS.md`](docs/ESCENARIOS.md).

**Ajuste a los recursos**: paralelismo acotado por memoria —no solo por
núcleos— respetando los límites del contenedor (cgroup v1/v2), cola con
prioridad, reintento de fallos pasajeros, lectura en paralelo de las 11 unidades
de negocio con tope por base, y control de admisión de la API móvil con
`Retry-After`. Ver [`docs/RECURSOS.md`](docs/RECURSOS.md).

Queda como evolución del motor: modelar el **salto de tensión del transformador**
(MT→BT) dentro del barrido para la comparación OpenDSS por alimentador completo, y
la reproducción **IEEE 13/34/123** completa. El esquema de BD ya soporta los
resultados.

### Lo que no está, dicho de frente

* **Pruebas en dispositivo real.** Render, permisos y GPS bajo cobertura solo se
  comprueban en la calle. Es lo único del ciclo que queda sin verificar.
* **Expresiones en el formulario** (visibilidad tipo Arcade). Los valores
  calculados se dejan fuera a propósito: el score lo produce el backend con la
  historia completa, y una fórmula en el teléfono daría otro número que el
  informe.
* **Reparto entre varias máquinas.** El diseño es de servidor único, como pide la
  especificación.

La lista completa, con el motivo de cada una, está en
[`docs/ESPECIFICACION_COMPLETA.md`](docs/ESPECIFICACION_COMPLETA.md) §10 y en la
comparación con Field Maps de
[`docs/APLICACION_MOVIL.md`](docs/APLICACION_MOVIL.md) §14.bis.
