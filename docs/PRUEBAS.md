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

**Propiedades verificadas (hypothesis):**
- `S ≥ P` para todo P ≥ 0 y cosφ ∈ [0.5, 1].
- `FC(1) = A + B = 1`; `FC` monótona decreciente; `FC ∈ [mínimo, 1]`.

## Pruebas de integración (`tests/integration/`)

`test_pipeline.py` ejecuta el pipeline completo sobre el CSV sintético y verifica:

- **Extremo a extremo:** 600 cuentas × 36 meses, ranking no vacío y ordenado.
- **Recuperación de hurtos** (criterio E10): la posición **mediana** de los hurtos
  inyectados cae en el tercio superior del ranking, y el **recall en el top-15 %**
  es ≥ 40 %. Es la métrica honesta de calidad del detector.
- **Persistencia DuckDB:** las tablas de resultado y el registro de corrida se
  crean correctamente.
- **Reconciliación:** produce una corrección de potencia medible (el SIG usaba el
  último mes; el sistema, el promedio multi-mes).

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

## Estado actual

```
47 passed
```

Cobertura del núcleo de dominio (objetivo de la especificación ≥ 85 %):
reconciliación 100 %, scoring 90 %, configuración 92 %, señales 85 %, promedio y
demanda ~75 % (las ramas no cubiertas son variantes de método y guardas de error).

## Integración continua

Añadir a la CI (GitHub Actions / Azure DevOps):

```yaml
- run: pip install -e ".[all]"
- run: pytest -m "unit or security" --cov=ptnt --cov-fail-under=70
- run: pytest -m integration
```
