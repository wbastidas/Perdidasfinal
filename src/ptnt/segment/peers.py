"""Grupo par jerárquico con degradación controlada (§11.3).

El grupo par ideal es lo más específico posible —misma clase, mismo nivel de
tensión, mismo estrato de consumo y misma ruta de lectura— porque cuanto más
homogéneo el grupo, más significativo es quedar por debajo de él. Pero la
especificidad tiene un costo: los grupos se vacían. Comparar a un cliente contra
dos pares no es evidencia de nada, y un umbral percentil sobre tres datos es ruido.

La solución estándar es **degradar en cascada**: se intenta el grupo más fino y,
si no reúne el mínimo de miembros, se retrocede a uno más general, hasta llegar a
la clase completa. Lo importante para la credibilidad del resultado es **reportar
en qué nivel quedó cada cliente**: una señal S5 calculada contra "toda la clase
residencial" no vale lo mismo que una calculada contra "residenciales del mismo
estrato en la misma ruta", y el analista tiene que poder distinguirlas.

Por eso cada cliente sale con tres columnas: el identificador del grupo par, el
**nivel** de la jerarquía que se usó y un **factor de confianza** que después pondera
la intensidad de la señal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Jerarquía de grupo par, de lo más específico a lo más general. Cada nivel es la
# lista de columnas que definen el grupo, y la confianza que merece la comparación.
#
# CRÍTICO: todas las claves son **exógenas al consumo**. El estrato de consumo NO
# aparece aquí, y es deliberado. Estratificar el grupo par por consumo es circular
# y destruye la señal: un cliente que hurta durante toda la ventana tiene su nivel
# base deprimido, cae en un estrato bajo y termina comparado contra clientes
# genuinamente pequeños — que es exactamente donde deja de destacar. Medido sobre
# el escenario de prueba, incluir el estrato baja el lift de S5 de 10,0× a 2,4×.
#
# El estrato sí se calcula y se conserva (`estrato_consumo`), pero se usa para
# reportar y para estimar la energía recuperable, nunca para armar el grupo par.
NIVELES_GRUPO_PAR: list[tuple[str, list[str], float]] = [
    ("CLASE_TENSION_FASES_RUTA",
     ["clase_consumo", "nivel_tension", "phases_count", "grupo_lectura"], 1.00),
    ("CLASE_TENSION_RUTA",
     ["clase_consumo", "nivel_tension", "grupo_lectura"], 0.90),
    ("CLASE_TENSION_FASES",
     ["clase_consumo", "nivel_tension", "phases_count"], 0.70),
    ("CLASE_TENSION",
     ["clase_consumo", "nivel_tension"], 0.60),
    ("CLASE",
     ["clase_consumo"], 0.40),
]


@dataclass
class ResultadoGrupoPar:
    clientes: pd.DataFrame          # con grupo_par_id / nivel / confianza
    resumen_niveles: pd.DataFrame   # cuántos clientes quedaron en cada nivel
    n_sin_grupo: int = 0
    advertencias: list[str] = field(default_factory=list)


def asignar_grupo_par(
    clientes: pd.DataFrame,
    *,
    min_pares: int = 8,
    col_cuenta: str = "contract_account",
    niveles: list[tuple[str, list[str], float]] | None = None,
) -> ResultadoGrupoPar:
    """Asigna a cada cliente el grupo par más fino que reúna ``min_pares`` miembros.

    Requiere que ``clientes`` traiga las columnas de segmentación
    (``clase_consumo``, ``nivel_tension``, ``estrato_consumo``) y ``grupo_lectura``.
    Las que falten se ignoran: un nivel que referencia una columna ausente
    simplemente no se intenta.

    Devuelve el padrón con:

    * ``grupo_par_id``        — identificador del grupo asignado
    * ``grupo_par_nivel``     — nivel de la jerarquía que se pudo usar
    * ``grupo_par_confianza`` — 0–1, cuánto vale la comparación en ese nivel
    * ``grupo_par_n``         — tamaño del grupo
    """

    niveles = niveles or NIVELES_GRUPO_PAR
    df = clientes.copy()
    n = len(df)

    asignado = pd.Series(False, index=df.index)
    df["grupo_par_id"] = pd.Series([None] * n, index=df.index, dtype=object)
    df["grupo_par_nivel"] = pd.Series([None] * n, index=df.index, dtype=object)
    df["grupo_par_confianza"] = 0.0
    df["grupo_par_n"] = 0

    advertencias: list[str] = []

    for nombre, columnas, confianza in niveles:
        faltantes = [c for c in columnas if c not in df.columns]
        if faltantes:
            advertencias.append(
                f"Nivel {nombre} omitido: falta(n) la(s) columna(s) "
                f"{', '.join(faltantes)}."
            )
            continue
        pendientes = ~asignado
        if not pendientes.any():
            break

        sub = df.loc[pendientes, columnas]
        # Una fila con cualquier clave nula no puede formar grupo fiable
        validas = sub.notna().all(axis=1)
        if not validas.any():
            continue

        claves = sub.loc[validas].astype(str).agg("|".join, axis=1)
        tam = claves.map(claves.value_counts())
        ok = tam >= min_pares
        if not ok.any():
            continue

        idx = claves.index[ok]
        df.loc[idx, "grupo_par_id"] = (nombre + "::" + claves.loc[ok]).to_numpy()
        df.loc[idx, "grupo_par_nivel"] = nombre
        df.loc[idx, "grupo_par_confianza"] = confianza
        df.loc[idx, "grupo_par_n"] = tam.loc[ok].to_numpy()
        asignado.loc[idx] = True

    n_sin = int((~asignado).sum())
    if n_sin:
        advertencias.append(
            f"{n_sin} cliente(s) sin grupo par de al menos {min_pares} miembros. "
            "Sus señales de comparación (S5) quedan en cero: para ellos la "
            "evidencia debe venir de su propia historia o de la red."
        )

    resumen = (
        df.groupby("grupo_par_nivel", dropna=False)
        .agg(clientes=(col_cuenta, "count"),
             confianza=("grupo_par_confianza", "first"),
             tam_medio=("grupo_par_n", "mean"))
        .reset_index()
        .sort_values("confianza", ascending=False)
    )
    resumen["tam_medio"] = resumen["tam_medio"].round(1)

    return ResultadoGrupoPar(
        clientes=df, resumen_niveles=resumen, n_sin_grupo=n_sin,
        advertencias=advertencias,
    )


def consumo_esperado_por_grupo(
    consumo_medio: pd.Series,
    grupos: pd.Series,
    *,
    estadistico: str = "mediana",
) -> pd.Series:
    """Consumo que "debería" tener cada cliente según su grupo par.

    Es la referencia contra la que se mide el déficit. Se usa la **mediana** y no
    la media porque el propio grupo puede contener otros clientes con hurto, y la
    mediana es robusta a esa contaminación.
    """

    df = pd.DataFrame({"kwh": consumo_medio, "grupo": grupos})
    fn = np.nanmedian if estadistico == "mediana" else np.nanmean
    esperado = pd.Series(np.nan, index=consumo_medio.index, dtype=float)
    for grupo, sub in df.groupby("grupo"):
        if grupo is None:
            continue
        valor = float(fn(sub["kwh"].to_numpy(dtype=float)))
        esperado.loc[sub.index] = valor
    return esperado


def energia_recuperable(
    consumo_actual: pd.Series,
    consumo_esperado_grupo: pd.Series,
    consumo_base_propio: pd.Series,
) -> pd.Series:
    """Energía mensual recuperable estimada, por segmento.

    Se toma el **máximo** de dos estimadores, porque cubren dos formas distintas
    de hurto que no se pueden detectar con el mismo criterio:

    * **Caída respecto de sí mismo** (``base_propio − actual``): el cliente
      consumía 800 kWh y ahora marca 300. La evidencia está en su historia.
    * **Déficit respecto de sus pares** (``esperado_grupo − actual``): el cliente
      siempre marcó bajo; no hay caída que detectar, pero está muy por debajo de
      clientes equivalentes.

    Esta es la corrección de fondo frente a comparar contra una **mediana global**:
    con un único valor de referencia para todo el padrón, un industrial que hurta
    el 30 % de 20 000 kWh sigue estando por encima de la mediana y su recuperable
    sale **cero**, mientras que a un residencial pequeño y honesto se le atribuye
    un recuperable inventado. Ambos errores desaparecen al comparar dentro del
    segmento.
    """

    actual = consumo_actual.astype(float)
    caida_propia = (consumo_base_propio.astype(float) - actual).fillna(0.0)
    deficit_grupo = (consumo_esperado_grupo.astype(float) - actual).fillna(0.0)
    rec = np.maximum(np.maximum(caida_propia, deficit_grupo), 0.0)
    return pd.Series(rec, index=consumo_actual.index)
