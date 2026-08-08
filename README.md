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
5. **Identifica clientes con posible hurto** (pérdidas no técnicas) mediante
   señales de comportamiento sobre la serie de 36 meses (S1–S10), grupos par por
   **CLIRLSCOD**, detección no supervisada y un ranking de consenso con la
   energía recuperable estimada y las razones en lenguaje operativo.
6. **Dice dónde ir a hacer el levantamiento**: rankings por alimentador, zona,
   ramal, transformador, **sector geográfico** y cliente, con órdenes de trabajo
   priorizadas por rendimiento por visita, exportables y reportables.
7. **Publica los resultados en dos interfaces web**: un tablero de análisis para
   escritorio (Streamlit) y un visor de solo lectura (FastAPI) para consulta por
   terceros.

> Documentación completa en [`docs/`](docs/): [Arquitectura](docs/ARQUITECTURA.md) ·
> [Instalación en Windows](docs/INSTALACION_WINDOWS.md) ·
> [Focalización de levantamientos](docs/FOCALIZACION.md) ·
> [Proceso](docs/PROCESO.md) · [Seguridad](docs/SEGURIDAD.md) ·
> [Pruebas](docs/PRUEBAS.md).

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
| `ptnt focalizar` | **Dónde ir a hacer el levantamiento**: rankings por alimentador/zona/ramal/transformador/sector/cliente + órdenes de trabajo, XLSX y reporte HTML |
| `ptnt validar-flujo` | Valida el flujo contra casos analíticos y contra **OpenDSS** (si está instalado) |
| `ptnt dashboard` | Tablero de análisis (Streamlit) |
| `ptnt servir-visor` | Visor web de solo lectura (FastAPI) |
| `ptnt crear-usuario` | Alta de usuario para las interfaces (solo hash) |

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
├── ntl/
│   ├── signals.py             # señales S1–S10 de hurto
│   ├── network_signals.py     # N1/N3/N4 (residuo zona, totalizador, cargabilidad)
│   └── scoring.py             # consenso + ranking
├── canonical/    # decodificadores de dominio (fase bitmask, kVA) + field_map (§4)
├── ref/          # catálogos: conductores, transformadores, CATALOGOESTRUCTURA (Excel)
├── topology/     # grafo radial + trazas + zonas (E4)
├── powerflow/    # flujo BFS 1φ + 3φ con neutro + OpenDSS (export/run) + validación (E8.1)
├── losses/       # pérdidas técnicas + Monte Carlo P10/P50/P90 (E8)
├── lighting/     # alumbrado público (E7)
├── balance/      # balance jerárquico y PNT + controles C01-C06 (E9)
├── grid/         # cargabilidad, desbalance, riesgo multinivel, agregación LV al trafo (E10)
├── survey/       # focalización: dónde inspeccionar + sectores + órdenes + reporte (§11.5)
├── quality/      # reconciliación (§6.1) + motor de reglas R05/R09/R11/R12/R15/R22/R24/P01/P09 (E5)
├── store/        # DuckDB (schema.sql) + persistencia
├── io/migration.py  # migración origen → modelo canónico (§4.3, round-trip)
├── security/     # auth (hash), secretos por entorno
├── synth/        # generador de datos (comercial + red radial) con hurtos
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

Queda como evolución del motor: modelar el **salto de tensión del transformador**
(MT→BT) dentro del barrido para la comparación OpenDSS por alimentador completo, y
la reproducción **IEEE 13/34/123** completa. El esquema de BD ya soporta los
resultados.
