"""Clasificación y segmentación de clientes para el análisis de PNT.

**Por qué segmentar.** El comportamiento de consumo de un taller metalmecánico, un
supermercado y un departamento no tienen nada que ver entre sí: distinto nivel,
distinta estacionalidad, distinta variabilidad y distinta forma de hurtar. Un
único modelo o un único grupo par para todos produce dos errores caros a la vez:

* **Falsos positivos en residenciales pequeños** — un cliente de 40 kWh/mes
  comparado contra una mediana global inflada por los grandes parece "muy por
  debajo de sus pares" cuando es perfectamente normal.
* **Falsos negativos en comerciales e industriales** — un cliente de 20 000
  kWh/mes que hurta el 20 % sigue estando muy por encima de la mediana global, así
  que ninguna señal de "consumo bajo" se activa. Y es justamente donde está la
  energía: un solo industrial recuperado vale lo que cientos de residenciales.

La literatura de detección de PNT converge en lo mismo: construir modelos y
comparaciones **por segmento** (clase tarifaria, nivel de tensión, potencia
contratada) en vez de un modelo único mejora tanto la precisión como la
explicabilidad del resultado ante el área comercial.

**Los ejes de segmentación implementados** (§11.3):

1. **Clase tarifaria** — residencial, comercial, industrial, oficial, asistencia
   social, bombeo de agua, alumbrado público. Es el primer corte porque define el
   patrón de uso. Se deduce de la descripción de tarifa (``DESTARI`` /
   ``TIPOTARIFA`` de ``ATRIBUTOSCONSUMIDOR``).
2. **Nivel de tensión** — BT / MT / AT. Un cliente de media tensión nunca debe
   compararse con uno de baja: son pocos, enormes y medidos de otra forma.
3. **Modalidad de medición** — con o sin demanda facturada. Los clientes "con
   demanda" tienen medición de potencia y admiten controles que los otros no.
4. **Estrato de consumo** — bloques de kWh/mes dentro de cada clase.

**El estrato de consumo NO forma parte del grupo par.** Es la lección más
importante de este módulo, y se aprendió midiendo. Estratificar por consumo para
comparar consumos es **circular**: un cliente que hurta durante toda la ventana
tiene su nivel base deprimido, cae en un estrato bajo y termina comparado contra
clientes genuinamente pequeños — precisamente donde deja de destacar. Medido
sobre el escenario de prueba, incluir el estrato en la clave del grupo par baja el
lift de la señal S5 de **10,0× a 2,4×**: la vuelve casi inútil.

Por eso el grupo par se arma solo con claves **exógenas al consumo** (clase,
nivel de tensión, número de fases, ruta comercial), y el estrato se conserva
para dos usos donde sí es correcto y valioso:

* **Reportar** dónde está la energía y qué rinde inspeccionar en cada bloque.
* **Estimar la energía recuperable**, comparando contra el nivel base propio.

El nivel base se calcula como un percentil alto de la **historia propia** del
cliente y no como la media, porque responde "de qué tamaño es este cliente" y no
"cuánto consumió": un cliente que hurta parte de la ventana conserva meses en su
nivel real, que es lo que el percentil alto recoge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class ClaseConsumo(str, Enum):
    """Clase tarifaria del cliente (primer eje de segmentación)."""

    RESIDENCIAL = "RESIDENCIAL"
    COMERCIAL = "COMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    OFICIAL = "OFICIAL"                    # entidades oficiales
    ASISTENCIA_SOCIAL = "ASISTENCIA_SOCIAL"  # y beneficio público
    BOMBEO_AGUA = "BOMBEO_AGUA"
    ALUMBRADO_PUBLICO = "ALUMBRADO_PUBLICO"
    NO_CLASIFICADO = "NO_CLASIFICADO"

    @property
    def es_residencial(self) -> bool:
        return self is ClaseConsumo.RESIDENCIAL

    @property
    def admite_grupo_par(self) -> bool:
        """Si la comparación contra pares es estadísticamente sensata.

        En residencial y comercial hay miles de clientes homogéneos y el grupo par
        es la señal más potente. En industrial y oficial hay pocos y muy
        heterogéneos: ahí la comparación válida es **contra la propia historia**
        del cliente, no contra otros.
        """

        return self in (ClaseConsumo.RESIDENCIAL, ClaseConsumo.COMERCIAL)


class NivelTension(str, Enum):
    BAJA = "BT"
    MEDIA = "MT"
    ALTA = "AT"
    DESCONOCIDO = "DESCONOCIDO"


# --------------------------------------------------------------------------- #
# Reconocimiento de la clase desde la descripción de tarifa
# --------------------------------------------------------------------------- #
# El orden importa: se evalúa de la regla más específica a la más general, porque
# descripciones como "INDUSTRIAL ARTESANAL" contienen palabras de varias clases.
_REGLAS_CLASE: list[tuple[ClaseConsumo, re.Pattern]] = [
    (ClaseConsumo.ALUMBRADO_PUBLICO, re.compile(
        r"ALUMBRADO|A\.?\s*P\.?\b|LUMINARIA|SEMAFORO|SEM[ÁA]FORO", re.I)),
    (ClaseConsumo.BOMBEO_AGUA, re.compile(r"BOMBEO|AGUA\s+POTABLE", re.I)),
    (ClaseConsumo.ASISTENCIA_SOCIAL, re.compile(
        r"ASISTENCIA\s+SOCIAL|BENEFICIO\s+P[ÚU]BLICO|CULTO|IGLESIA|"
        r"ESCENARIO[S]?\s+DEPORTIVO", re.I)),
    (ClaseConsumo.OFICIAL, re.compile(
        r"OFICIAL|ENTIDAD(ES)?\s+P[ÚU]BLICA|GUBERNAMENTAL|MUNICIP", re.I)),
    (ClaseConsumo.INDUSTRIAL, re.compile(r"INDUSTRIAL|ARTESANAL|MANUFACTUR", re.I)),
    (ClaseConsumo.COMERCIAL, re.compile(r"COMERCIAL|GENERAL|NEGOCIO", re.I)),
    (ClaseConsumo.RESIDENCIAL, re.compile(
        r"RESIDENCIAL|DOMICILIAR|DIGNIDAD|VIVIENDA|DOM[ÉE]STIC", re.I)),
]

_RE_MEDIA = re.compile(r"MEDIA\s+TENSI[ÓO]N|\bMT\b|MEDIO\s+VOLTAJE", re.I)
_RE_ALTA = re.compile(r"ALTA\s+TENSI[ÓO]N|\bAT\b|ALTO\s+VOLTAJE", re.I)
_RE_BAJA = re.compile(r"BAJA\s+TENSI[ÓO]N|\bBT\b|BAJO\s+VOLTAJE", re.I)
_RE_CON_DEMANDA = re.compile(r"CON\s+DEMANDA", re.I)
_RE_SIN_DEMANDA = re.compile(r"SIN\s+DEMANDA", re.I)


def clasificar_tarifa(descripcion: object) -> ClaseConsumo:
    """Deduce la clase tarifaria desde la descripción textual de la tarifa.

    Devuelve ``NO_CLASIFICADO`` cuando el texto está vacío o no coincide con
    ninguna regla — nunca adivina "residencial" por defecto, porque clasificar mal
    a un industrial como residencial es precisamente el error que hay que evitar.
    """

    if descripcion is None or (isinstance(descripcion, float) and np.isnan(descripcion)):
        return ClaseConsumo.NO_CLASIFICADO
    texto = str(descripcion).strip()
    if not texto:
        return ClaseConsumo.NO_CLASIFICADO
    for clase, patron in _REGLAS_CLASE:
        if patron.search(texto):
            return clase
    return ClaseConsumo.NO_CLASIFICADO


def clasificar_tension(descripcion: object) -> NivelTension:
    """Deduce el nivel de tensión desde la descripción de tarifa."""

    if descripcion is None:
        return NivelTension.DESCONOCIDO
    texto = str(descripcion)
    if _RE_ALTA.search(texto):
        return NivelTension.ALTA
    if _RE_MEDIA.search(texto):
        return NivelTension.MEDIA
    if _RE_BAJA.search(texto):
        return NivelTension.BAJA
    return NivelTension.DESCONOCIDO


def tiene_demanda_facturada(descripcion: object) -> bool | None:
    """``True``/``False`` si la tarifa declara con/sin demanda; ``None`` si no dice."""

    if descripcion is None:
        return None
    texto = str(descripcion)
    if _RE_SIN_DEMANDA.search(texto):
        return False
    if _RE_CON_DEMANDA.search(texto):
        return True
    return None


def resolver_clave_config(
    descripcion: object, claves_config: tuple[str, ...]
) -> str | None:
    """Empareja una descripción de tarifa real con una clave del catálogo de clases.

    El catálogo de configuración usa nombres cortos (``"BT Residencial"``,
    ``"MT Industrial"``), pero ``DESTARI`` en la base real trae textos como
    ``"INDUSTRIAL CON DEMANDA MEDIA TENSION"``. Una búsqueda por igualdad exacta
    falla con todos ellos y **cae silenciosamente a la clase por defecto**, lo que
    asignaría coeficientes de Velander residenciales a un industrial de media
    tensión — un error grande y difícil de notar, porque el cálculo no falla:
    simplemente da mal.

    La resolución es semántica: se clasifican tanto la descripción como las
    claves del catálogo, y se emparejan por (clase, nivel de tensión), degradando
    a solo clase si no hay coincidencia de tensión. Devuelve ``None`` si no hay
    forma de emparejar, para que el llamador decida qué hacer.
    """

    objetivo = (clasificar_tarifa(descripcion), clasificar_tension(descripcion))
    if objetivo[0] is ClaseConsumo.NO_CLASIFICADO:
        return None

    indice: dict[tuple[ClaseConsumo, NivelTension], str] = {}
    por_clase: dict[ClaseConsumo, str] = {}
    for clave in claves_config:
        c, t = clasificar_tarifa(clave), clasificar_tension(clave)
        indice.setdefault((c, t), clave)
        por_clase.setdefault(c, clave)

    if objetivo in indice:
        return indice[objetivo]
    return por_clase.get(objetivo[0])


# --------------------------------------------------------------------------- #
# Estratos de consumo
# --------------------------------------------------------------------------- #
def etiquetar_estrato(valor: float, cortes: list[float]) -> str:
    """Etiqueta legible del estrato al que pertenece ``valor``.

    ``cortes`` son los límites superiores de cada bloque; el último estrato es
    abierto. Las etiquetas se emiten con ancho fijo para que ordenen bien de forma
    alfabética en los reportes.
    """

    if valor is None or not np.isfinite(valor):
        return "E00_SIN_DATO"
    previo = 0.0
    for i, corte in enumerate(cortes, start=1):
        if valor <= corte:
            return f"E{i:02d}_{previo:.0f}-{corte:.0f}"
        previo = corte
    return f"E{len(cortes) + 1:02d}_{previo:.0f}+"


def consumo_base(serie: np.ndarray, percentil: float = 75.0) -> float:
    """Nivel habitual del cliente: percentil alto de su **propia** historia.

    Se usa un percentil alto (por defecto P75) y no la media porque el objetivo es
    responder "¿de qué tamaño es este cliente?" y no "¿cuánto consumió?". Un
    cliente que hurta durante parte de la ventana tiene la media deprimida pero
    conserva meses en su nivel real, que es lo que el P75 recoge.
    """

    valores = np.asarray(serie, dtype=float)
    valores = valores[np.isfinite(valores)]
    if valores.size == 0:
        return float("nan")
    return float(np.percentile(valores, percentil))


# --------------------------------------------------------------------------- #
# Segmentación completa
# --------------------------------------------------------------------------- #
@dataclass
class ResumenSegmentacion:
    """Resultado de segmentar el padrón, con lo necesario para auditarlo."""

    clientes: pd.DataFrame          # padrón con las columnas de segmento añadidas
    por_clase: pd.DataFrame         # conteo y energía por clase
    por_segmento: pd.DataFrame      # conteo y energía por segmento
    n_no_clasificados: int = 0
    pct_energia_no_residencial: float = 0.0
    advertencias: list[str] = field(default_factory=list)

    @property
    def cobertura_pct(self) -> float:
        n = len(self.clientes)
        return 100.0 * (n - self.n_no_clasificados) / n if n else 0.0


def segmentar_clientes(
    clientes: pd.DataFrame,
    consumo_largo: pd.DataFrame | None = None,
    *,
    col_tarifa: str = "tariff_description",
    col_cuenta: str = "contract_account",
    percentil_base: float = 75.0,
    cortes_residencial: list[float] | None = None,
    cortes_no_residencial: list[float] | None = None,
    umbral_gran_cliente_kwh: float = 5000.0,
) -> ResumenSegmentacion:
    """Añade al padrón las columnas de segmentación y devuelve el resumen.

    Columnas añadidas:

    * ``clase_consumo``      — ``ClaseConsumo`` (residencial, comercial, …)
    * ``nivel_tension``      — BT / MT / AT / DESCONOCIDO
    * ``con_demanda``        — ``True``/``False``/``None``
    * ``consumo_base_kwh``   — nivel habitual (P75 de la historia propia)
    * ``estrato_consumo``    — bloque de kWh dentro de la clase
    * ``segmento``           — clave compuesta legible
    * ``es_gran_cliente``    — merece revisión individual, no por ranking relativo
    * ``admite_grupo_par``   — si la comparación contra pares es válida para su clase

    ``consumo_largo`` aporta la serie por cliente para calcular el consumo base. Sin
    ella, el estrato queda ``E00_SIN_DATO`` y la segmentación se limita a la clase.
    """

    cortes_res = cortes_residencial or [50, 100, 130, 200, 300, 500, 1000]
    cortes_nores = cortes_no_residencial or [200, 1000, 5000, 20000, 100000]

    df = clientes.copy()
    advertencias: list[str] = []

    if col_tarifa not in df.columns:
        advertencias.append(
            f"No existe la columna de tarifa '{col_tarifa}': todos los clientes "
            "quedan NO_CLASIFICADO y la segmentación no aporta separación."
        )
        df["clase_consumo"] = ClaseConsumo.NO_CLASIFICADO.value
        df["nivel_tension"] = NivelTension.DESCONOCIDO.value
        df["con_demanda"] = None
    else:
        df["clase_consumo"] = [clasificar_tarifa(v).value for v in df[col_tarifa]]
        df["nivel_tension"] = [clasificar_tension(v).value for v in df[col_tarifa]]
        df["con_demanda"] = [tiene_demanda_facturada(v) for v in df[col_tarifa]]

    # --- consumo base desde la historia propia -------------------------------
    if consumo_largo is not None and not consumo_largo.empty:
        base = (
            consumo_largo.groupby(col_cuenta)["kwh"]
            .apply(lambda s: consumo_base(s.to_numpy(), percentil_base))
            .rename("consumo_base_kwh")
        )
        df = df.merge(base, left_on=col_cuenta, right_index=True, how="left")
    else:
        df["consumo_base_kwh"] = np.nan
        advertencias.append(
            "Sin serie de consumo: el estrato no se puede calcular y el grupo par "
            "queda solo por clase y nivel de tensión."
        )

    es_res = df["clase_consumo"] == ClaseConsumo.RESIDENCIAL.value
    df["estrato_consumo"] = [
        etiquetar_estrato(v, cortes_res if r else cortes_nores)
        for v, r in zip(df["consumo_base_kwh"], es_res)
    ]

    df["segmento"] = (
        df["clase_consumo"].astype(str) + "|"
        + df["nivel_tension"].astype(str) + "|"
        + df["estrato_consumo"].astype(str)
    )

    # Un gran cliente merece revisión individual: un error del 5 % en él vale más
    # que el 100 % de un residencial pequeño, y su ranking relativo lo esconde.
    df["es_gran_cliente"] = (
        (df["consumo_base_kwh"].fillna(0) >= umbral_gran_cliente_kwh)
        | (df["nivel_tension"].isin([NivelTension.MEDIA.value, NivelTension.ALTA.value]))
    )
    df["admite_grupo_par"] = [
        ClaseConsumo(c).admite_grupo_par for c in df["clase_consumo"]
    ]

    # --- resúmenes -----------------------------------------------------------
    energia = df["consumo_base_kwh"].fillna(0.0)
    por_clase = (
        df.assign(_kwh=energia)
        .groupby("clase_consumo")
        .agg(clientes=(col_cuenta, "count"), kwh_base_mes=("_kwh", "sum"))
        .reset_index()
        .sort_values("kwh_base_mes", ascending=False)
    )
    total_kwh = float(por_clase["kwh_base_mes"].sum())
    por_clase["pct_clientes"] = (
        por_clase["clientes"] / max(len(df), 1) * 100).round(2)
    por_clase["pct_energia"] = (
        por_clase["kwh_base_mes"] / total_kwh * 100 if total_kwh else 0.0)
    por_clase["pct_energia"] = por_clase["pct_energia"].round(2)

    por_segmento = (
        df.assign(_kwh=energia)
        .groupby("segmento")
        .agg(clientes=(col_cuenta, "count"), kwh_base_mes=("_kwh", "sum"))
        .reset_index()
        .sort_values("kwh_base_mes", ascending=False)
    )

    n_nc = int((df["clase_consumo"] == ClaseConsumo.NO_CLASIFICADO.value).sum())
    if n_nc:
        advertencias.append(
            f"{n_nc} cliente(s) sin clase reconocible en '{col_tarifa}'. Se agrupan "
            "aparte: revise las descripciones de tarifa antes de usar sus señales "
            "de grupo par."
        )

    no_res_kwh = float(
        por_clase.loc[
            por_clase["clase_consumo"] != ClaseConsumo.RESIDENCIAL.value, "kwh_base_mes"
        ].sum()
    )
    pct_no_res = (no_res_kwh / total_kwh * 100) if total_kwh else 0.0

    return ResumenSegmentacion(
        clientes=df,
        por_clase=por_clase,
        por_segmento=por_segmento,
        n_no_clasificados=n_nc,
        pct_energia_no_residencial=round(pct_no_res, 2),
        advertencias=advertencias,
    )
