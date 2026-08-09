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
13. **Publica los resultados en dos interfaces web**: un tablero de análisis para
    escritorio (Streamlit, 9 pestañas) y un visor de solo lectura (FastAPI).

> **[Especificación completa](docs/ESPECIFICACION_COMPLETA.md)** — requerimientos,
> campos y arquitectura de información para replicar el sistema.
>
> Documentación en [`docs/`](docs/): [Arquitectura](docs/ARQUITECTURA.md) ·
> [Instalación en Windows](docs/INSTALACION_WINDOWS.md) ·
> **[Guía de operación paso a paso](docs/GUIA_OPERACION.md)** ·
> [Segmentación de clientes](docs/SEGMENTACION.md) ·
> **[Prueba a escala — 20 000 clientes](docs/PRUEBA_COSTA_20K.md)** ·
> **[Aplicación móvil de campo](docs/APLICACION_MOVIL.md)** ·
> [Focalización de levantamientos](docs/FOCALIZACION.md) ·
> [Diagnóstico y validación](docs/DIAGNOSTICO.md) ·
> [Proceso](docs/PROCESO.md) · [Seguridad](docs/SEGURIDAD.md) ·
> [Pruebas](docs/PRUEBAS.md).

## Demostración completa con datos ficticios

Antes de conectar la base real, se puede ver **todo el proceso funcionando** sobre
un escenario ficticio con verdad conocida (hurtos, transferencia entre
alimentadores y clientes faltantes inyectados a propósito):

```bash
python scripts/demo_completa.py
```

Recorre los 9 pasos —generación del escenario, versionado de la red, análisis
comercial, balance y PNT, diagnóstico de credibilidad, validación contra la base
de multados, focalización, **carga de un mes nuevo** y **modificación de
topología**— y contrasta cada resultado contra lo inyectado. El paso a paso y la
interpretación de cada salida están en
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
| `ptnt campo-usuario` | **Crea un usuario de la aplicación móvil** |
| `ptnt campo-asignar` | **Asigna órdenes de levantamiento a un técnico** |
| `ptnt campo-repartir` | **Reparte la jornada entre varias cuadrillas**: carga pareja y grupos compactos en el territorio |
| `ptnt campo-paquete` | **Genera el GeoPackage descargable** con la red del área y la cartografía offline |
| `ptnt campo-paquetes` | **Genera de una vez el paquete de cada técnico** con trabajo pendiente |
| `ptnt campo-servir` | **API de sincronización** para la aplicación móvil |
| `ptnt campo-revisar` | **Revisa los cambios de campo** y determina qué recalcular |

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
├── security/     # auth (hash), secretos por entorno
├── synth/        # generador de datos (comercial + red radial) con hurtos
│                 # + scenario.py: escenario ficticio completo con verdad conocida
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

**359 pruebas** en verde (más las pruebas JVM de la app Android, en `mobile/`). Detalle de qué cubre cada archivo y los resultados de la
demostración extremo a extremo en [`docs/PRUEBAS.md`](docs/PRUEBAS.md).

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

Queda como evolución del motor: modelar el **salto de tensión del transformador**
(MT→BT) dentro del barrido para la comparación OpenDSS por alimentador completo, y
la reproducción **IEEE 13/34/123** completa. El esquema de BD ya soporta los
resultados.
