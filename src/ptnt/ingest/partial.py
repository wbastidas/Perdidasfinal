"""Carga parcial de información: alcance declarado y balance honesto (D06).

En producción la información **no llega toda junta**. Llega el padrón comercial
de una unidad de negocio, después la red de tres subestaciones, después la
cabecera de solo algunos alimentadores. Un sistema que exige el universo completo
para arrancar no se usa nunca; uno que acepta cargas parciales **sin decirlo**
produce consolidados que parecen completos y no lo son — y alguien firma un
informe de pérdidas de una unidad de negocio calculado sobre la mitad de sus
alimentadores.

Este módulo resuelve las dos mitades del problema:

1. **Declarar el alcance** de cada carga: qué unidades, subestaciones y
   alimentadores entraron, con qué insumos (padrón, red, cabecera, multados) y
   en qué fecha.
2. **Degradar el resultado en consecuencia**: la cobertura se calcula contra el
   universo esperado y el balance de un consolidado incompleto se marca
   ``PARCIAL``, con el porcentaje de cobertura visible en el propio resultado.

La regla es la misma que gobierna el resto del sistema: **el número puede ser
parcial; lo que no puede es no decirlo**.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import pandas as pd


class Insumo(str, Enum):
    """Los insumos que el análisis puede consumir, cada uno cargable por separado."""

    PADRON_COMERCIAL = "PADRON_COMERCIAL"     # consumos 36 meses
    RED = "RED"                               # topología (FGDB / SQL)
    CABECERA = "CABECERA"                     # energía medida de cabecera
    MULTADOS = "MULTADOS"                     # base de clientes con multa
    SIG_CLIENTES = "SIG_CLIENTES"             # vinculación comercial ↔ SIG
    JERARQUIA = "JERARQUIA"                   # catálogo UN / subestación

    @property
    def habilita_balance_medido(self) -> bool:
        """Sin cabecera no hay balance MEDIDO, solo estimación."""

        return self is Insumo.CABECERA


class EstadoCobertura(str, Enum):
    COMPLETA = "COMPLETA"
    PARCIAL = "PARCIAL"
    VACIA = "VACIA"


@dataclass
class CargaParcial:
    """Una carga concreta: qué insumo, qué alcance y cuándo."""

    insumo: Insumo
    alimentadores: list[str] = field(default_factory=list)
    registros: int = 0
    periodo_desde: str | None = None
    periodo_hasta: str | None = None
    origen: str = ""
    cargado_en: str = ""
    advertencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "insumo": self.insumo.value,
            "alimentadores": sorted(self.alimentadores),
            "n_alimentadores": len(self.alimentadores),
            "registros": self.registros,
            "periodo_desde": self.periodo_desde,
            "periodo_hasta": self.periodo_hasta,
            "origen": self.origen,
            "cargado_en": self.cargado_en,
            "advertencias": self.advertencias,
        }


@dataclass
class Cobertura:
    """Cobertura de un insumo contra el universo esperado."""

    insumo: Insumo
    esperados: int
    cargados: int
    faltantes: list[str] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return 100.0 * self.cargados / self.esperados if self.esperados else 0.0

    @property
    def estado(self) -> EstadoCobertura:
        if self.cargados == 0:
            return EstadoCobertura.VACIA
        if self.cargados >= self.esperados:
            return EstadoCobertura.COMPLETA
        return EstadoCobertura.PARCIAL


@dataclass
class AlcanceCarga:
    """Registro acumulado de todo lo cargado, con su cobertura."""

    universo_alimentadores: list[str] = field(default_factory=list)
    cargas: list[CargaParcial] = field(default_factory=list)
    ruta: Path | None = None

    # -- registro ----------------------------------------------------------
    def registrar(
        self,
        insumo: Insumo,
        alimentadores: list[str],
        *,
        registros: int = 0,
        periodo_desde: str | None = None,
        periodo_hasta: str | None = None,
        origen: str = "",
    ) -> CargaParcial:
        """Anota una carga. Las cargas del mismo insumo se **acumulan**: cargar
        tres subestaciones en tres pasos equivale a haberlas cargado juntas."""

        carga = CargaParcial(
            insumo=insumo,
            alimentadores=sorted({str(a) for a in alimentadores}),
            registros=registros, periodo_desde=periodo_desde,
            periodo_hasta=periodo_hasta, origen=origen,
            cargado_en=datetime.now().isoformat(timespec="seconds"),
        )
        desconocidos = (set(carga.alimentadores) - set(self.universo_alimentadores)
                        if self.universo_alimentadores else set())
        if desconocidos:
            carga.advertencias.append(
                f"{len(desconocidos)} alimentador(es) fuera del universo declarado: "
                f"{', '.join(sorted(desconocidos)[:5])}"
                + ("…" if len(desconocidos) > 5 else "")
            )
        self.cargas.append(carga)
        return carga

    # -- consulta ----------------------------------------------------------
    def alimentadores_con(self, insumo: Insumo) -> set[str]:
        return {a for c in self.cargas if c.insumo is insumo for a in c.alimentadores}

    def cobertura(self, insumo: Insumo) -> Cobertura:
        cargados = self.alimentadores_con(insumo)
        universo = set(self.universo_alimentadores) or cargados
        return Cobertura(
            insumo=insumo, esperados=len(universo), cargados=len(cargados & universo),
            faltantes=sorted(universo - cargados),
        )

    def listos_para_balance_medido(self) -> set[str]:
        """Alimentadores con padrón, red y cabecera: los únicos que admiten un
        balance MEDIDO. El resto solo puede producir estimación."""

        return (self.alimentadores_con(Insumo.PADRON_COMERCIAL)
                & self.alimentadores_con(Insumo.RED)
                & self.alimentadores_con(Insumo.CABECERA))

    def resumen(self) -> pd.DataFrame:
        filas = []
        for insumo in Insumo:
            cob = self.cobertura(insumo)
            filas.append({
                "insumo": insumo.value,
                "alimentadores_cargados": cob.cargados,
                "esperados": cob.esperados,
                "cobertura_pct": round(cob.pct, 1),
                "estado": cob.estado.value,
                "faltan": len(cob.faltantes),
            })
        return pd.DataFrame(filas)

    def pendientes(self) -> pd.DataFrame:
        """Qué falta por cargar, por insumo. Es la lista de trabajo del operador."""

        filas = []
        for insumo in Insumo:
            for a in self.cobertura(insumo).faltantes:
                filas.append({"alimentador": a, "insumo_faltante": insumo.value})
        return pd.DataFrame(filas)

    # -- persistencia ------------------------------------------------------
    def save(self, ruta: str | Path | None = None) -> Path:
        p = Path(ruta or self.ruta or "alcance_carga.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "universo_alimentadores": sorted(self.universo_alimentadores),
            "cargas": [c.to_dict() for c in self.cargas],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.ruta = p
        return p

    @classmethod
    def load(cls, ruta: str | Path) -> "AlcanceCarga":
        p = Path(ruta)
        if not p.exists():
            return cls(ruta=p)
        d = json.loads(p.read_text(encoding="utf-8"))
        cargas = [
            CargaParcial(
                insumo=Insumo(c["insumo"]), alimentadores=c.get("alimentadores", []),
                registros=c.get("registros", 0),
                periodo_desde=c.get("periodo_desde"),
                periodo_hasta=c.get("periodo_hasta"),
                origen=c.get("origen", ""), cargado_en=c.get("cargado_en", ""),
                advertencias=c.get("advertencias", []),
            )
            for c in d.get("cargas", [])
        ]
        return cls(universo_alimentadores=d.get("universo_alimentadores", []),
                   cargas=cargas, ruta=p)


def marcar_balance_parcial(
    balance_consolidado: pd.DataFrame,
    alcance: AlcanceCarga,
    *,
    col_alimentadores: str = "alimentadores",
    col_tipo: str = "tipo_balance",
) -> pd.DataFrame:
    """Añade la cobertura al balance consolidado y degrada el tipo si es parcial.

    Un consolidado de subestación calculado sobre 3 de sus 8 alimentadores no es
    el balance de esa subestación: es el de tres de sus alimentadores. Marcarlo
    ``PARCIAL`` y mostrar el porcentaje evita que se lea como el total.
    """

    df = balance_consolidado.copy()
    if col_alimentadores not in df.columns:
        return df

    listos = alcance.listos_para_balance_medido()
    universo = set(alcance.universo_alimentadores)

    # Cobertura por fila: sin el detalle de qué alimentadores componen cada fila,
    # se usa la cobertura global — conservador y explícito.
    cobertura_global = (100.0 * len(listos & universo) / len(universo)
                        if universo else 100.0)
    df["cobertura_pct"] = round(cobertura_global, 1)
    if cobertura_global < 99.9 and col_tipo in df.columns:
        df[col_tipo] = "PARCIAL"
        df["nota_cobertura"] = (
            f"Calculado sobre el {cobertura_global:.1f}% del universo declarado: "
            "no es el total de la entidad.")
    return df
