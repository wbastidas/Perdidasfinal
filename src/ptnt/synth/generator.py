"""Generador de datos sintéticos comerciales.

Produce un CSV en el **mismo formato** que el archivo comercial real (separador
``;``, ``.`` como miles en KWH, ``.`` decimal en coordenadas, columnas
``KWH_1..KWH_36``, ``CLIRLSCOD`` como grupo de ruta de lectura), con hurtos
inyectados de patrones conocidos para validar el pipeline de detección de punta a
punta:

  * ``caida_recuperacion`` (S1)
  * ``cero_activo``        (S4)
  * ``ruptura_nivel``      (S3)
  * ``planitud``           (S7)
  * ``bajo_grupo``         (S5)

Cada cliente sintético sabe si es hurto y de qué tipo (columna ``_hurto_tipo``),
de modo que las pruebas puedan medir la posición mediana de los hurtos en el
ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_CLASES = ["BT Residencial", "BT Comercial", "BT Industrial", "MT Industrial"]
_CLASE_BASE_KWH = {
    "BT Residencial": (120, 60),
    "BT Comercial": (900, 400),
    "BT Industrial": (8000, 3000),
    "MT Industrial": (120000, 50000),
}
# Zonas: las rutas de lectura no son muestras aleatorias del padrón, son
# territorios con vocación. Un corredor comercial tiene otra mezcla de clases que
# un barrio, y esa correlación clase↔geografía es la que justifica separar por
# clase antes de comparar: sin ella, comparar dentro de la ruta ya bastaría.
# La mezcla global resultante es ~85 % residencial, que es lo típico de un padrón
# de distribución: el residencial domina en número de clientes pero no en energía.
# Orden de las columnas: [residencial, comercial, industrial BT, industrial MT].
_ZONAS = ["residencial", "comercial", "industrial"]
_ZONA_PROPORCION = np.array([0.70, 0.22, 0.08])
_ZONA_MEZCLA = {
    "residencial": np.array([0.96, 0.035, 0.005, 0.000]),
    "comercial":   np.array([0.45, 0.500, 0.045, 0.005]),
    "industrial":  np.array([0.30, 0.250, 0.380, 0.070]),
}
# Descripciones de tarifa como aparecen en DESTARI/TIPOTARIFA, para ejercitar el
# clasificador con texto parecido al real y no con etiquetas ya normalizadas.
_CLASE_DESTARI = {
    "BT Residencial": [
        "RESIDENCIAL BAJA TENSION", "RESIDENCIAL TEMPORAL", "TARIFA DIGNIDAD",
    ],
    "BT Comercial": [
        "COMERCIAL SIN DEMANDA BAJA TENSION", "COMERCIAL CON DEMANDA BAJA TENSION",
    ],
    "BT Industrial": [
        "INDUSTRIAL CON DEMANDA BAJA TENSION", "INDUSTRIAL ARTESANAL BAJA TENSION",
    ],
    "MT Industrial": [
        "INDUSTRIAL CON DEMANDA MEDIA TENSION",
        "INDUSTRIAL CON DEMANDA HORARIA MEDIA TENSION",
    ],
}
_HURTO_TIPOS = ["caida_recuperacion", "cero_activo", "ruptura_nivel", "planitud", "bajo_grupo"]


@dataclass
class SyntheticDataset:
    df: pd.DataFrame  # formato ancho, como el CSV real + metadatos con prefijo _
    ruta_csv: str | None = None
    hurtos: dict[str, int] = field(default_factory=dict)


def _fmt_kwh(valor: float) -> str:
    """Formatea un kWh como en el archivo real: miles con '.', sin decimales.

    Ej.: 1302 -> '1.302'; 963 -> '963'.
    """

    v = int(round(max(valor, 0)))
    return f"{v:,}".replace(",", ".")


def _serie_normal(base: float, ruido: float, n: int, rng: np.random.Generator) -> np.ndarray:
    estacional = 1 + 0.15 * np.sin(np.linspace(0, 2 * np.pi * (n / 12), n))
    serie = base * estacional + rng.normal(0, ruido, n)
    return np.clip(serie, 0, None)


def _inyectar_hurto(serie: np.ndarray, tipo: str, rng: np.random.Generator) -> np.ndarray:
    n = serie.size
    s = serie.copy()
    if tipo == "caida_recuperacion":
        ini = rng.integers(n // 3, n // 2)
        fin = min(ini + rng.integers(4, 8), n - 2)
        s[ini:fin] *= 0.25
    elif tipo == "cero_activo":
        ini = rng.integers(n // 2, n - 6)
        s[ini:] = 0.0
    elif tipo == "ruptura_nivel":
        pt = rng.integers(n // 3, 2 * n // 3)
        s[pt:] *= 0.45
    elif tipo == "planitud":
        s[:] = np.mean(s) * (1 + rng.normal(0, 0.01, n))
    elif tipo == "bajo_grupo":
        s[:] *= 0.15
    return np.clip(s, 0, None)


def generate_commercial_csv(
    ruta_salida: str,
    *,
    n_clientes: int = 2000,
    n_meses: int = 36,
    n_alimentadores: int = 5,
    pct_hurto: float = 0.05,
    semilla: int = 20260807,
    separador: str = ";",
    encoding: str = "latin-1",
) -> SyntheticDataset:
    """Genera y escribe un CSV comercial sintético. Devuelve el dataset con
    metadatos de hurto para validación."""

    rng = np.random.default_rng(semilla)
    filas = []
    hurtos_por_tipo: dict[str, int] = {t: 0 for t in _HURTO_TIPOS}

    # Cada ruta de lectura (CLIRLSCOD) pertenece a una zona con su propia mezcla de
    # clases: barrios residenciales, corredores comerciales y polígonos
    # industriales. Es como es la realidad, y es lo que hace que separar por clase
    # tenga valor: sin esta correlación, una ruta sería una muestra aleatoria del
    # padrón y comparar dentro de la ruta ya bastaría.
    rutas = [
        f"F{a:03d}-R{r:02d}"
        for a in range(1, n_alimentadores + 1) for r in range(1, 12)
    ]
    zona_de_ruta = {
        ruta: str(rng.choice(_ZONAS, p=_ZONA_PROPORCION)) for ruta in rutas
    }

    for i in range(n_clientes):
        grupo_lectura = str(rng.choice(rutas))          # CLIRLSCOD
        alimentador = grupo_lectura.split("-")[0]
        clase = str(rng.choice(_CLASES, p=_ZONA_MEZCLA[zona_de_ruta[grupo_lectura]]))
        base, ruido = _CLASE_BASE_KWH[clase]
        base = base * rng.uniform(0.6, 1.6)
        serie = _serie_normal(base, ruido, n_meses, rng)

        es_hurto = rng.random() < pct_hurto
        tipo = ""
        if es_hurto:
            tipo = _HURTO_TIPOS[rng.integers(0, len(_HURTO_TIPOS))]
            serie = _inyectar_hurto(serie, tipo, rng)
            hurtos_por_tipo[tipo] += 1

        cuenta = f"2000{i:08d}"
        division = f"10{rng.integers(1, 6):02d}"
        x = 620000 + rng.uniform(0, 5000)
        y = 9755000 + rng.uniform(0, 5000)
        fases = {"BT Residencial": 1, "BT Comercial": 2, "BT Industrial": 3, "MT Industrial": 3}[clase]
        # servicio activo salvo algunos suspendidos legítimos
        suspendido = (not es_hurto) and rng.random() < 0.03
        if suspendido:
            serie[-rng.integers(3, 8):] = 0.0

        fila = {
            "DIVISION": division,
            "CUENTACONTRATO": cuenta,
            "NOMBRE": f"CLIENTE SINTETICO {i}",
            "DESTARI": str(rng.choice(_CLASE_DESTARI[clase])),
            "ZZUTM_X": f"{x:.6f}",
            "ZZUTM_Y": f"{y:.6f}",
            "CLIRLSCOD": grupo_lectura,
            "CDAFAS": str(fases),
            "EDCCOD": "SUSP" if suspendido else "ACT",
            "CLIULTCONM": _fmt_kwh(serie[-1]),
            "POTENCIAACTIVA": f"{serie[-1] / (30 * 24) * rng.uniform(2, 6):.3f}",
            "POTENCIAREACTIVA": f"{serie[-1] / (30 * 24) * rng.uniform(1, 3):.3f}",
            "_hurto": int(es_hurto),
            "_hurto_tipo": tipo,
        }
        for m in range(n_meses):
            fila[f"KWH_{m + 1}"] = _fmt_kwh(serie[m])
        filas.append(fila)

    df = pd.DataFrame(filas)

    # Escribir solo las columnas reales (sin las de metadatos con prefijo _)
    cols_reales = [c for c in df.columns if not c.startswith("_")]
    import os

    os.makedirs(os.path.dirname(ruta_salida) or ".", exist_ok=True)
    df[cols_reales].to_csv(
        ruta_salida, sep=separador, encoding=encoding, index=False
    )

    return SyntheticDataset(df=df, ruta_csv=ruta_salida, hurtos=hurtos_por_tipo)
