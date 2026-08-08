# PTNT-BAL — Plataforma de Pérdidas No Técnicas y Balance Energético

Sistema en Python, pensado para un **servidor único**, que a partir de la
información comercial de consumo (hasta 36 meses por cliente) y del modelo de
datos de la red (esquema **Puesto → Unidad** homologado CNEL EP):

1. **Ingiere consumo desde distintas bases de origen** (CSV comercial, SQL
   Server, PostgreSQL, Oracle, MySQL o Parquet/DuckDB) mediante conectores
   intercambiables.
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
6. **Publica los resultados en dos interfaces web**: un tablero de análisis para
   escritorio (Streamlit) y un visor de solo lectura (FastAPI) para consulta por
   terceros.

> Documentación completa en [`docs/`](docs/): [Arquitectura](docs/ARQUITECTURA.md) ·
> [Instalación en Windows](docs/INSTALACION_WINDOWS.md) ·
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
| `ptnt analizar` | Pipeline: promedio → potencia → reconciliación → hurto |
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
│   ├── sources/  # conectores multi-origen (csv, sql, duckdb)
│   └── commercial_parser.py   # parseo §6.4 (separadores por columna)
├── load/
│   ├── averaging.py           # promedio multi-mes (mecanismo clave)
│   └── demand.py              # P, Q, S, I §6 (corrige el √3 y el último-mes)
├── quality/reconciliation.py  # informe SIG vs corregido (§6.1)
├── ntl/
│   ├── signals.py             # señales S1–S10 de hurto
│   └── scoring.py             # consenso + ranking
├── store/        # DuckDB (schema.sql) + persistencia
├── security/     # auth (hash), secretos por entorno
├── synth/        # generador de datos con hurtos inyectados
├── dashboard/    # tablero Streamlit (escritorio)
├── webviewer/    # visor FastAPI (solo lectura)
├── pipeline.py   # orquestador
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

Esta versión implementa el **flujo de valor inmediato** de la especificación
PTNT-BAL v1.0: ingesta comercial multi-origen, promedio multi-mes, recálculo de
potencia con informe de reconciliación, y detección de hurto con las interfaces
web. Las etapas de red eléctrica de la especificación (topología en grafo, flujo
de potencia, pérdidas técnicas por componente, balance jerárquico con cabecera)
están descritas en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) como hoja de
ruta y el esquema de base de datos (`src/ptnt/store/schema.sql`) ya las soporta.
