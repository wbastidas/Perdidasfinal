# Focalización: dónde ir a hacer el levantamiento

Este es el **entregable operativo** del proyecto (§11.5): convertir el análisis de
pérdidas en una respuesta accionable — *a qué alimentador, ramal, transformador o
sector hay que ir, en qué orden y por qué*.

```bash
ptnt focalizar --trafos 8 --clientes-trafo 20 --ordenes 25
```

## Los seis niveles

| Nivel | Qué responde | Acción de campo |
|---|---|---|
| 🔌 **Alimentador** | Dónde está la PNT a gran escala | Campaña y verificación de cabecera |
| 🛡️ **Zona de protección** | Qué zona aislable concentra el residuo | Instalar medición de frontera y seccionalizar |
| 🌿 **Ramal** | Qué tramo de calle recorrer | Recorrido del ramal con medición de frontera |
| ⚡ **Puesto de transformación** | Qué transformador auditar | Censo de carga y revisión de acometidas |
| 📍 **Sector** | Qué zona geográfica barrer | Recorrido casa por casa |
| 🏠 **Cliente** | Qué predio inspeccionar | Inspección de acometida y medidor |

## Cómo se prioriza

```
prioridad = 0,55 · sospecha + 0,45 · energía_normalizada
            × factor_confiabilidad(datos)
```

Decisiones de diseño que evitan errores caros en campo:

1. **La energía que cuenta es la recuperable, no la facturada.** Un ramal con mucho
   consumo pero sin indicios no es objetivo de inspección; mandar cuadrilla ahí es
   gastar presupuesto. Los objetivos sin ninguna señal quedan con prioridad
   residual (×0,15), no se eliminan.
2. **Densidad + volumen.** Un ramal de 1 cliente con 1 señal daría 100 % de
   densidad y superaría a uno de 20 clientes con 10 señales. La sospecha combina
   ambas: `0,5·densidad + 0,5·volumen_normalizado`.
3. **Una acometida no bifurca la red.** La descomposición en ramales solo considera
   bifurcaciones de tendido; si no, cada poste con un cliente partiría el ramal.
   Las derivaciones de un solo cliente son acometidas y se atienden en el nivel
   CLIENTE.
4. **Baja confiabilidad = problema de datos, no hurto** (§10.7). Un score alto en
   una zona cuyo balance cierra mal por calidad de datos se marca
   `data_problem_flag` y su acción pasa a *"CORREGIR DATOS antes de inspeccionar"*.

## Órdenes de levantamiento

El orden de las órdenes de trabajo **no** es el `priority_score` (que se normaliza
dentro de cada nivel y no es comparable entre niveles), sino el **rendimiento por
visita**: la energía recuperable que cubre una salida de cuadrilla. Así un sector
con 19 clientes agrupados se antepone a 19 visitas individuales — que es la
decisión logística correcta. Con `evitar_solape` no se emiten dos órdenes para el
mismo predio.

Ejemplo real de salida:

```
OT-0001 SECTOR  SEC-0003  25 clientes  12 589 kWh/mes  25 clientes sospechosos en 1109 m
OT-0002 SECTOR  SEC-0010  19 clientes  12 488 kWh/mes  19 clientes sospechosos en 1023 m
OT-0007 PUESTO  TS4       20 clientes   5 392 kWh/mes  Cargabilidad incoherente (2%)
→ 12 visitas cubren 168 clientes y 87 415 kWh/mes
```

## Sectores geográficos

`survey/sectors.py` agrupa los clientes priorizados con **HDBSCAN** (si hay
scikit-learn) y, si no —o si HDBSCAN etiqueta todo como ruido por densidad
uniforme—, cae a una **rejilla espacial** determinista. Perder los sectores sería
perder el nivel más accionable del plan, así que el respaldo siempre agrupa.

Cada sector reporta centroide, radio, nº de clientes y energía bajo sospecha.

## Salidas (visible y reportable)

| Archivo | Contenido |
|---|---|
| `reporte_focalizacion.html` | Reporte navegable con KPIs, órdenes y tabla por nivel |
| `focalizacion.xlsx` | Una hoja por nivel + plan + órdenes |
| `plan_levantamientos.csv` | Plan completo, todos los niveles |
| `ordenes_levantamiento.csv` | Órdenes de trabajo para campo |
| `plan_levantamientos.json` | Consumido por el tablero y el visor web |

**Interfaces:** el tablero Streamlit tiene la pestaña *"📍 Dónde inspeccionar"*
(KPIs, órdenes descargables, filtro por nivel y mapa); el visor web de solo lectura
muestra las órdenes y expone `GET /api/focalizacion?nivel=SECTOR` y `GET /api/ordenes`
(ambos autenticados: los objetivos identifican predios concretos).

## Advertencia metodológica

La **energía total** de PNT es medida (donde hay cabecera); su **ubicación es una
inferencia** construida por convergencia de evidencias. El plan ordena la
inspección por probabilidad y rendimiento, no afirma hurto. Los objetivos marcados
como problema de datos deben corregirse antes de enviar cuadrilla.
