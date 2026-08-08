"""Jerarquía organizacional: Unidad de Negocio → Subestación → Alimentador.

Hasta ahora el sistema modelaba la red **desde el alimentador hacia abajo**
(alimentador → zona → ramal → puesto → cliente). Eso basta para el análisis
técnico, pero no para la gestión: quien decide el presupuesto de un plan de
reducción de pérdidas razona por **unidad de negocio**, y quien opera la red lo
hace por **subestación**. Sin esos dos niveles, el sistema produce números
correctos que nadie puede firmar.

La jerarquía completa queda así::

    Unidad de Negocio        (CNEL EP: Guayaquil, Milagro, Santa Elena, …)
      └── Subestación        (barra de MT de la que salen los alimentadores)
            └── Alimentador
                  └── Zona de protección
                        └── Ramal
                              └── Puesto de transformación
                                    └── Cliente

**El balance se agrega hacia arriba, pero su credibilidad NO.** Esta es la regla
que gobierna todo el módulo: si un solo alimentador de una subestación tiene
balance ``INDICATIVO`` (sin medición de cabecera confiable), el balance de la
subestación entera es ``INDICATIVO``. La energía se puede sumar; la garantía de
que ese número es verificable, no. Presentar el consolidado de una unidad de
negocio como "medido" cuando el 30 % de sus alimentadores es estimado es el
fallo de credibilidad más caro de este tipo de proyecto, y aquí es imposible por
construcción.

El mapa alimentador → (subestación, unidad de negocio) se carga de un catálogo
—CSV o la propia base de origen— porque es información organizacional que cambia
por reorganizaciones administrativas, no por cambios en la red.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pandas as pd


class NivelOrganizacional(str, Enum):
    UNIDAD_NEGOCIO = "UNIDAD_NEGOCIO"
    SUBESTACION = "SUBESTACION"
    ALIMENTADOR = "ALIMENTADOR"


# Orden de precedencia: el peor tipo de balance de los hijos manda en el padre.
_PRECEDENCIA_BALANCE = {"MEDIDO": 0, "INDICATIVO": 1, "SIN_DATOS": 2}


@dataclass
class Alimentador:
    """Ubicación de un alimentador en la jerarquía organizacional."""

    feeder_code: str
    subestacion: str
    unidad_negocio: str
    nombre: str = ""
    tension_kv: float | None = None
    activo: bool = True


@dataclass
class Jerarquia:
    """Catálogo alimentador → subestación → unidad de negocio."""

    alimentadores: dict[str, Alimentador] = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.alimentadores)

    def get(self, feeder_code: str) -> Alimentador | None:
        return self.alimentadores.get(str(feeder_code))

    def subestacion_de(self, feeder_code: str) -> str | None:
        a = self.get(feeder_code)
        return a.subestacion if a else None

    def unidad_de(self, feeder_code: str) -> str | None:
        a = self.get(feeder_code)
        return a.unidad_negocio if a else None

    @property
    def unidades(self) -> list[str]:
        return sorted({a.unidad_negocio for a in self.alimentadores.values()})

    @property
    def subestaciones(self) -> list[str]:
        return sorted({a.subestacion for a in self.alimentadores.values()})

    def subestaciones_de(self, unidad: str) -> list[str]:
        return sorted({a.subestacion for a in self.alimentadores.values()
                       if a.unidad_negocio == unidad})

    def alimentadores_de(self, *, subestacion: str | None = None,
                         unidad: str | None = None) -> list[str]:
        return sorted(
            c for c, a in self.alimentadores.items()
            if (subestacion is None or a.subestacion == subestacion)
            and (unidad is None or a.unidad_negocio == unidad)
        )

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"feeder_code": a.feeder_code, "subestacion": a.subestacion,
             "unidad_negocio": a.unidad_negocio, "nombre": a.nombre,
             "tension_kv": a.tension_kv, "activo": a.activo}
            for a in sorted(self.alimentadores.values(),
                            key=lambda x: (x.unidad_negocio, x.subestacion,
                                           x.feeder_code))
        ])


def load_jerarquia(
    origen: str | Path | pd.DataFrame,
    *,
    col_alimentador: str = "feeder_code",
    col_subestacion: str = "subestacion",
    col_unidad: str = "unidad_negocio",
    col_nombre: str = "nombre",
    col_tension: str = "tension_kv",
) -> Jerarquia:
    """Carga el catálogo organizacional desde un CSV o un DataFrame."""

    df = (origen if isinstance(origen, pd.DataFrame)
          else pd.read_csv(origen, dtype=str))
    faltantes = [c for c in (col_alimentador, col_subestacion, col_unidad)
                 if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"El catálogo de jerarquía no tiene la(s) columna(s): "
            f"{', '.join(faltantes)}. Se requieren "
            f"'{col_alimentador}', '{col_subestacion}' y '{col_unidad}'."
        )

    alimentadores: dict[str, Alimentador] = {}
    advertencias: list[str] = []
    for _, r in df.iterrows():
        code = str(r[col_alimentador]).strip()
        if not code or code.lower() == "nan":
            continue
        if code in alimentadores:
            advertencias.append(
                f"Alimentador '{code}' repetido en el catálogo: se conserva la "
                "primera aparición. Un alimentador no puede pertenecer a dos "
                "subestaciones a la vez.")
            continue
        tension = None
        if col_tension in df.columns and pd.notna(r.get(col_tension)):
            try:
                tension = float(r[col_tension])
            except (TypeError, ValueError):
                tension = None
        alimentadores[code] = Alimentador(
            feeder_code=code,
            subestacion=str(r[col_subestacion]).strip(),
            unidad_negocio=str(r[col_unidad]).strip(),
            nombre=str(r.get(col_nombre, "") or "").strip(),
            tension_kv=tension,
        )
    return Jerarquia(alimentadores=alimentadores, advertencias=advertencias)


def jerarquia_desde_alimentadores(
    feeder_codes: list[str], *, unidad_por_defecto: str = "SIN_UN",
    separador: str = "-",
) -> Jerarquia:
    """Jerarquía inferida cuando no hay catálogo disponible.

    Deduce la unidad de negocio del prefijo del código (``GYE-01`` → ``GYE``) y
    deja la subestación como **desconocida**. Es un respaldo para no bloquear el
    análisis, pero se emite una advertencia: agrupar por una subestación inventada
    daría consolidados que parecen correctos y no lo son.
    """

    alimentadores = {}
    for code in feeder_codes:
        c = str(code)
        un = c.split(separador)[0] if separador in c else unidad_por_defecto
        alimentadores[c] = Alimentador(
            feeder_code=c, subestacion="SIN_SUBESTACION", unidad_negocio=un)
    return Jerarquia(
        alimentadores=alimentadores,
        advertencias=[
            "Jerarquía inferida del código de alimentador: la unidad de negocio "
            "sale del prefijo y la subestación queda SIN_SUBESTACION. Los "
            "consolidados por subestación no son utilizables hasta cargar el "
            "catálogo real (`organizacion.catalogo`)."
        ],
    )


# --------------------------------------------------------------------------- #
# Agregación jerárquica del balance
# --------------------------------------------------------------------------- #
_SUMABLES = (
    "entrada_kwh", "facturado_kwh", "ap_no_medido_kwh", "consumos_propios_kwh",
    "perdidas_totales_kwh", "perdidas_tecnicas_kwh", "pnt_kwh",
    "pnt_p10_kwh", "pnt_p90_kwh", "clientes",
)


def agregar_balance(
    balances: pd.DataFrame,
    jerarquia: Jerarquia,
    *,
    col_alimentador: str = "alimentador",
    col_tipo: str = "tipo_balance",
) -> dict[str, pd.DataFrame]:
    """Consolida el balance por alimentador hacia subestación y unidad de negocio.

    Devuelve ``{"ALIMENTADOR": df, "SUBESTACION": df, "UNIDAD_NEGOCIO": df}``.

    Reglas:

    * Las **energías se suman**; los porcentajes se **recalculan** sobre los
      totales, nunca se promedian. Promediar porcentajes de alimentadores de
      tamaños distintos da un número que no corresponde a ninguna realidad
      física: un alimentador de 700 MWh al 4 % y uno de 4 400 MWh al 6 % no dan
      5 % en conjunto.
    * El **tipo de balance del padre es el peor de sus hijos**: basta un
      ``INDICATIVO`` para que el consolidado no pueda presentarse como medido.
    * Se reporta ``alimentadores_indicativos`` para que el consolidado diga *por
      qué* quedó degradado.
    """

    df = balances.copy()
    if col_alimentador not in df.columns:
        raise ValueError(f"Falta la columna '{col_alimentador}' en el balance.")

    df["subestacion"] = df[col_alimentador].map(jerarquia.subestacion_de)
    df["unidad_negocio"] = df[col_alimentador].map(jerarquia.unidad_de)
    sin_mapa = int(df["subestacion"].isna().sum())
    df["subestacion"] = df["subestacion"].fillna("SIN_SUBESTACION")
    df["unidad_negocio"] = df["unidad_negocio"].fillna("SIN_UN")

    sumables = [c for c in _SUMABLES if c in df.columns]
    tiene_tipo = col_tipo in df.columns

    def _consolidar(claves: list[str]) -> pd.DataFrame:
        agg = df.groupby(claves)[sumables].sum().reset_index()
        agg["alimentadores"] = (
            df.groupby(claves)[col_alimentador].nunique().to_numpy())
        if tiene_tipo:
            peor = df.groupby(claves)[col_tipo].apply(
                lambda s: max(s, key=lambda v: _PRECEDENCIA_BALANCE.get(str(v), 9)))
            agg[col_tipo] = peor.to_numpy()
            agg["alimentadores_indicativos"] = (
                df[df[col_tipo] != "MEDIDO"].groupby(claves)[col_alimentador]
                .nunique().reindex(peor.index).fillna(0).astype(int).to_numpy())
        # Porcentajes recalculados sobre los totales, nunca promediados
        if {"pnt_kwh", "entrada_kwh"} <= set(agg.columns):
            agg["pnt_pct"] = (
                agg["pnt_kwh"] / agg["entrada_kwh"].where(agg["entrada_kwh"] > 0)
                * 100).round(2)
        if {"perdidas_totales_kwh", "entrada_kwh"} <= set(agg.columns):
            agg["perdidas_pct"] = (
                agg["perdidas_totales_kwh"]
                / agg["entrada_kwh"].where(agg["entrada_kwh"] > 0) * 100).round(2)
        return agg.sort_values(
            "pnt_kwh" if "pnt_kwh" in agg.columns else claves[0], ascending=False
        ).reset_index(drop=True)

    salida = {
        NivelOrganizacional.ALIMENTADOR.value: df,
        NivelOrganizacional.SUBESTACION.value: _consolidar(
            ["unidad_negocio", "subestacion"]),
        NivelOrganizacional.UNIDAD_NEGOCIO.value: _consolidar(["unidad_negocio"]),
    }
    if sin_mapa:
        for nivel in (NivelOrganizacional.SUBESTACION.value,
                      NivelOrganizacional.UNIDAD_NEGOCIO.value):
            salida[nivel].attrs["advertencia"] = (
                f"{sin_mapa} alimentador(es) sin catálogo organizacional quedaron "
                "agrupados como SIN_SUBESTACION/SIN_UN.")
    return salida
