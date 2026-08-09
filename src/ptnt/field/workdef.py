"""Definir el trabajo de campo: no solo lo peor del ranking.

El sistema nació apuntando al hurto, y la focalización responde a «¿dónde se
pierde más energía?». Pero una cuadrilla en la calle sirve para más de eso, y
hay trabajo que **nunca** va a salir de un ranking de sospecha:

* Un **censo** de una zona nueva: no hay clientes registrados, así que no hay
  consumo anómalo que detectar. Justamente por eso hay que ir.
* Una **actualización cartográfica** de un barrio donde el SIG está viejo: el
  balance de esa zona no es malo, es *incalculable*.
* Un **reclamo** puntual, o la **verificación** de un medidor tras una denuncia.
* El **mantenimiento** de luminarias y seccionadores.
* El alta de una **obra** recién construida.

Todos producen las mismas órdenes de trabajo y viajan por el mismo circuito
—asignar, empaquetar, editar sin señal, revisar, recalcular—, así que se generan
con la misma estructura de columnas que la focalización. El resto del sistema no
distingue el origen: lo que cambia es el `tipo_trabajo`, que sirve para medir
cada campaña por su cuenta. Un censo no se evalúa por kWh recuperados.

La selección se hace por **alimentador, sector, área dibujada o lista de
cuentas**. La lista es la que más se usa en la práctica: el supervisor recibe un
Excel del área comercial y quiere convertirlo en trabajo sin transcribir nada.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd


class TipoTrabajo(str, Enum):
    """Para qué se va a campo. Determina cómo se mide el resultado."""

    INSPECCION_PNT = "INSPECCION_PNT"
    CENSO = "CENSO"
    ACTUALIZACION_CARTOGRAFICA = "ACTUALIZACION_CARTOGRAFICA"
    VERIFICACION_MEDIDOR = "VERIFICACION_MEDIDOR"
    MANTENIMIENTO = "MANTENIMIENTO"
    RECLAMO = "RECLAMO"
    OBRA = "OBRA"

    @property
    def accion(self) -> str:
        return _ACCIONES[self]

    @property
    def mide_energia(self) -> bool:
        """¿Tiene sentido evaluar esta campaña por energía recuperada?

        Un censo o una actualización cartográfica no recuperan kWh directamente:
        corrigen el denominador del balance. Evaluarlos por energía los haría
        parecer inútiles y dejarían de hacerse.
        """

        return self in (TipoTrabajo.INSPECCION_PNT,
                        TipoTrabajo.VERIFICACION_MEDIDOR)


_ACCIONES: dict[TipoTrabajo, str] = {
    TipoTrabajo.INSPECCION_PNT:
        "Recorrido de acometidas y contraste de medidores",
    TipoTrabajo.CENSO:
        "Censo de consumidores: registrar los no vinculados",
    TipoTrabajo.ACTUALIZACION_CARTOGRAFICA:
        "Verificar la red dibujada contra la red real y corregirla",
    TipoTrabajo.VERIFICACION_MEDIDOR:
        "Verificación de medidor: lectura, sello y estado",
    TipoTrabajo.MANTENIMIENTO:
        "Inspección de estructuras, luminarias y protecciones",
    TipoTrabajo.RECLAMO:
        "Atención de reclamo en sitio",
    TipoTrabajo.OBRA:
        "Levantamiento de red nueva construida",
}

# Columnas que espera el resto del circuito (las mismas que produce focalizar).
COLUMNAS_ORDEN = [
    "orden_trabajo", "nivel", "entidad", "alimentador", "accion",
    "motivo_principal", "clientes_a_revisar", "recuperable_kwh_mes",
    "x", "y", "tipo_trabajo",
]


@dataclass
class DefinicionTrabajo:
    """El resultado: órdenes listas para asignar, más lo que hay que saber."""

    ordenes: pd.DataFrame
    tipo: TipoTrabajo
    advertencias: list[str]

    @property
    def clientes(self) -> int:
        if self.ordenes.empty:
            return 0
        return int(self.ordenes["clientes_a_revisar"].sum())

    def resumen(self) -> dict:
        return {
            "tipo_trabajo": self.tipo.value,
            "ordenes": len(self.ordenes),
            "clientes": self.clientes,
            "recuperable_kwh_mes": (
                round(float(self.ordenes["recuperable_kwh_mes"].sum()), 1)
                if not self.ordenes.empty else 0.0),
            "mide_energia": self.tipo.mide_energia,
        }


# --------------------------------------------------------------------------- #
# Constructores
# --------------------------------------------------------------------------- #
def por_alimentador(
    clientes: pd.DataFrame,
    alimentadores: list[str],
    *,
    tipo: TipoTrabajo = TipoTrabajo.CENSO,
    clientes_por_orden: int = 40,
    prefijo: str = "OTM",
) -> DefinicionTrabajo:
    """Todo un alimentador, partido en órdenes de tamaño manejable.

    Se parte por proximidad geográfica y no por orden de lista: una orden con 40
    clientes repartidos por todo el alimentador es una orden que no se hace.
    """

    df = _filtrar(clientes, "alimentador", alimentadores)
    if df.empty:
        return DefinicionTrabajo(
            _vacio(), tipo,
            [f"Ningún cliente en {', '.join(alimentadores)}. "
             "Revise el código del alimentador."])

    ordenes = []
    advertencias: list[str] = []
    n = 0
    for feeder, grupo in df.groupby("alimentador", sort=True):
        bloques = _agrupar_por_cercania(grupo, clientes_por_orden)
        for bloque in bloques:
            n += 1
            ordenes.append(_orden(
                f"{prefijo}-{n:04d}", "SECTOR", f"{feeder}/B{n:03d}",
                str(feeder), tipo, bloque))
    return DefinicionTrabajo(_tabla(ordenes), tipo, advertencias)


def por_sector(
    clientes: pd.DataFrame,
    sectores: list[str],
    *,
    tipo: TipoTrabajo = TipoTrabajo.INSPECCION_PNT,
    columna_sector: str = "sector_id",
    prefijo: str = "OTM",
) -> DefinicionTrabajo:
    """Sectores ya identificados por el análisis o por el operador."""

    if columna_sector not in clientes.columns:
        return DefinicionTrabajo(
            _vacio(), tipo,
            [f"Los clientes no traen la columna '{columna_sector}'. "
             "Ejecute `ptnt focalizar` o indique otra columna."])
    df = _filtrar(clientes, columna_sector, sectores)
    if df.empty:
        return DefinicionTrabajo(_vacio(), tipo,
                                 ["Ningún cliente en los sectores indicados."])

    ordenes = []
    for i, (sector, grupo) in enumerate(df.groupby(columna_sector, sort=True), 1):
        ordenes.append(_orden(
            f"{prefijo}-{i:04d}", "SECTOR", str(sector),
            _feeder_dominante(grupo), tipo, grupo))
    return DefinicionTrabajo(_tabla(ordenes), tipo, [])


def por_area(
    clientes: pd.DataFrame,
    *,
    x: float, y: float, radio_m: float,
    tipo: TipoTrabajo = TipoTrabajo.ACTUALIZACION_CARTOGRAFICA,
    clientes_por_orden: int = 40,
    prefijo: str = "OTM",
) -> DefinicionTrabajo:
    """Un círculo dibujado sobre el mapa: «revisen todo esto».

    Es la forma en que un jefe de zona describe el trabajo cuando el criterio no
    está en ningún dato —una urbanización nueva, una zona tras un temporal—.
    """

    if not {"x", "y"}.issubset(clientes.columns):
        return DefinicionTrabajo(
            _vacio(), tipo,
            ["Los clientes no traen coordenadas: no se puede seleccionar por área."])
    xy = clientes[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    dentro = np.hypot(xy["x"] - x, xy["y"] - y) <= radio_m
    df = clientes[dentro.fillna(False)]
    if df.empty:
        return DefinicionTrabajo(
            _vacio(), tipo,
            [f"Ningún cliente dentro de {radio_m:,.0f} m de ({x:,.0f}, {y:,.0f})."])

    ordenes = []
    for i, bloque in enumerate(_agrupar_por_cercania(df, clientes_por_orden), 1):
        ordenes.append(_orden(
            f"{prefijo}-{i:04d}", "AREA", f"AREA-{i:03d}",
            _feeder_dominante(bloque), tipo, bloque))
    return DefinicionTrabajo(_tabla(ordenes), tipo, [])


def por_lista(
    clientes: pd.DataFrame,
    cuentas: list[str] | pd.Series | Path | str,
    *,
    tipo: TipoTrabajo = TipoTrabajo.VERIFICACION_MEDIDOR,
    columna_cuenta: str = "cuenta_contrato",
    clientes_por_orden: int = 25,
    prefijo: str = "OTM",
) -> DefinicionTrabajo:
    """Una lista de cuentas concreta.

    Es el caso más frecuente en la operación real: el área comercial manda un
    listado y el supervisor lo quiere convertir en trabajo sin transcribir nada.
    Acepta una lista, una Serie o la ruta de un CSV con la columna de cuenta.

    Las cuentas que no aparecen en el padrón **se reportan**, no se descartan en
    silencio: normalmente significan que el listado viene de otro sistema o de
    otra unidad de negocio, y eso hay que resolverlo antes de salir.
    """

    pedidas = _leer_cuentas(cuentas, columna_cuenta)
    if not pedidas:
        return DefinicionTrabajo(_vacio(), tipo, ["La lista de cuentas está vacía."])
    if columna_cuenta not in clientes.columns:
        return DefinicionTrabajo(
            _vacio(), tipo,
            [f"El padrón no tiene la columna '{columna_cuenta}'."])

    serie = clientes[columna_cuenta].astype(str).str.strip()
    df = clientes[serie.isin(pedidas)]
    encontradas = set(serie[serie.isin(pedidas)])
    faltan = sorted(pedidas - encontradas)

    advertencias: list[str] = []
    if faltan:
        advertencias.append(
            f"{len(faltan)} de {len(pedidas)} cuenta(s) no están en el padrón "
            f"cargado ({', '.join(faltan[:5])}"
            + ("…" if len(faltan) > 5 else "")
            + "). Suelen venir de otro sistema o de otra unidad de negocio: "
              "resuélvalo antes de salir, o la cuadrilla irá a una dirección "
              "que no existe.")
    if df.empty:
        return DefinicionTrabajo(_vacio(), tipo, advertencias)

    ordenes = []
    for i, bloque in enumerate(_agrupar_por_cercania(df, clientes_por_orden), 1):
        ordenes.append(_orden(
            f"{prefijo}-{i:04d}", "LISTA", f"LISTA-{i:03d}",
            _feeder_dominante(bloque), tipo, bloque))
    return DefinicionTrabajo(_tabla(ordenes), tipo, advertencias)


def desde_plan(
    ordenes_focalizacion: pd.DataFrame,
    *,
    tipo: TipoTrabajo = TipoTrabajo.INSPECCION_PNT,
) -> DefinicionTrabajo:
    """Las órdenes que ya produjo la focalización, etiquetadas con su tipo.

    Existe para que **todo** el trabajo pase por la misma estructura: el ranking
    de sospecha es un origen más, no un camino aparte.
    """

    df = ordenes_focalizacion.copy()
    if "tipo_trabajo" not in df.columns:
        df["tipo_trabajo"] = tipo.value
    for c in COLUMNAS_ORDEN:
        if c not in df.columns:
            df[c] = "" if c not in ("clientes_a_revisar", "recuperable_kwh_mes",
                                    "x", "y") else np.nan
    return DefinicionTrabajo(df, tipo, [])


def unir(*definiciones: DefinicionTrabajo) -> pd.DataFrame:
    """Junta varias campañas en una sola lista asignable.

    Los identificadores se renumeran para que no choquen: dos campañas generadas
    por separado empiezan las dos en 0001, y dos órdenes con el mismo código son
    imposibles de seguir después.
    """

    partes = [d.ordenes for d in definiciones if not d.ordenes.empty]
    if not partes:
        return _vacio()
    df = pd.concat(partes, ignore_index=True)
    df["orden_trabajo"] = [
        f"{str(o).split('-')[0]}-{i:04d}"
        for i, o in enumerate(df["orden_trabajo"], 1)
    ]
    return df


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #
def _vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS_ORDEN)


def _tabla(ordenes: list[dict]) -> pd.DataFrame:
    if not ordenes:
        return _vacio()
    return pd.DataFrame(ordenes)[COLUMNAS_ORDEN]


def _filtrar(df: pd.DataFrame, columna: str, valores: list[str]) -> pd.DataFrame:
    if columna not in df.columns:
        return df.iloc[0:0]
    objetivo = {str(v).strip() for v in valores}
    return df[df[columna].astype(str).str.strip().isin(objetivo)]


def _leer_cuentas(fuente, columna: str) -> set[str]:
    if isinstance(fuente, (str, Path)):
        ruta = Path(fuente)
        if ruta.exists():
            leido = pd.read_csv(ruta, dtype=str)
            col = columna if columna in leido.columns else leido.columns[0]
            return {str(v).strip() for v in leido[col].dropna()}
        return {str(fuente).strip()}
    if isinstance(fuente, pd.Series):
        return {str(v).strip() for v in fuente.dropna()}
    return {str(v).strip() for v in fuente if str(v).strip()}


def _feeder_dominante(grupo: pd.DataFrame) -> str:
    if "alimentador" not in grupo.columns or grupo.empty:
        return ""
    modo = grupo["alimentador"].astype(str).mode()
    return str(modo.iloc[0]) if len(modo) else ""


def _agrupar_por_cercania(df: pd.DataFrame, tamano: int) -> list[pd.DataFrame]:
    """Parte un conjunto de clientes en bloques compactos del tamaño pedido.

    Sin coordenadas se parte por orden de lista, que es lo único disponible. Con
    coordenadas se ordena por celda de una grilla del tamaño adecuado: es una
    curva de recorrido burda, pero mantiene juntos a los vecinos, que es lo que
    importa —recorrer una manzana entera antes de pasar a la siguiente.
    """

    if tamano <= 0 or len(df) <= tamano:
        return [df]
    if not {"x", "y"}.issubset(df.columns):
        return [df.iloc[i:i + tamano] for i in range(0, len(df), tamano)]

    xy = df[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    if xy.isna().any(axis=1).mean() > 0.10:
        return [df.iloc[i:i + tamano] for i in range(0, len(df), tamano)]
    xy = xy.fillna(xy.mean())

    # Lado de celda tal que caiga aproximadamente un bloque en cada una.
    n_bloques = max(1, math.ceil(len(df) / tamano))
    ancho = max(float(xy["x"].max() - xy["x"].min()), 1.0)
    alto = max(float(xy["y"].max() - xy["y"].min()), 1.0)
    lado = max(math.sqrt(ancho * alto / n_bloques), 1.0)

    cx = ((xy["x"] - xy["x"].min()) // lado).astype(int)
    cy = ((xy["y"] - xy["y"].min()) // lado).astype(int)
    # Serpentina: filas alternas se recorren al revés, para que el final de una
    # quede junto al principio de la siguiente y no al otro extremo.
    orden = cy * 100000 + np.where(cy % 2 == 0, cx, 99999 - cx)

    ordenado = df.assign(_orden=orden.to_numpy()).sort_values(
        "_orden", kind="stable").drop(columns="_orden")
    return [ordenado.iloc[i:i + tamano] for i in range(0, len(ordenado), tamano)]


def _orden(codigo: str, nivel: str, entidad: str, feeder: str,
           tipo: TipoTrabajo, grupo: pd.DataFrame) -> dict:
    x = y = np.nan
    if {"x", "y"}.issubset(grupo.columns):
        xy = grupo[["x", "y"]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(xy):
            x, y = float(xy["x"].mean()), float(xy["y"].mean())

    # Solo las campañas que persiguen energía llevan energía estimada. Poner un
    # número en un censo invitaría a evaluarlo por algo que no hace.
    recuperable = 0.0
    if tipo.mide_energia and "recuperable_kwh_mes" in grupo.columns:
        recuperable = float(pd.to_numeric(
            grupo["recuperable_kwh_mes"], errors="coerce").fillna(0).sum())

    return {
        "orden_trabajo": codigo, "nivel": nivel, "entidad": entidad,
        "alimentador": feeder, "accion": tipo.accion,
        "motivo_principal": f"Campaña {tipo.value.replace('_', ' ').lower()}",
        "clientes_a_revisar": int(len(grupo)),
        "recuperable_kwh_mes": round(recuperable, 1),
        "x": x, "y": y, "tipo_trabajo": tipo.value,
    }
