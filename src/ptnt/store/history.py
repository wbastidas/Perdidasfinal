"""Histórico de corridas: evolución de la PNT por unidad, subestación y alimentador.

Una foto de la PNT de este mes no sirve para gestionar. Lo que decide si un plan
de reducción de pérdidas está funcionando es la **serie**: si el alimentador que
se intervino en marzo bajó del 9 % al 6 % y se mantuvo, o si rebotó a los dos
meses. Y lo que decide si un número es creíble es poder ver **cuándo cambió y
por qué** — un salto de PNT que coincide con una recarga de datos es un problema
de datos, no un hurto masivo.

El histórico se guarda como un archivo de instantáneas (Parquet o JSON) y no
depende de DuckDB, para que funcione también en la instalación mínima. Cada
instantánea registra, por entidad y período:

* la energía y la PNT,
* el **tipo de balance** (MEDIDO / INDICATIVO / PARCIAL) y la cobertura,
* el hash de configuración con que se calculó.

Ese último punto es el que hace el histórico auditable: dos valores calculados
con configuraciones distintas **no son comparables**, y el sistema lo advierte en
vez de dibujar una línea continua entre ellos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNAS = [
    "snapshot_id", "registrado_en", "periodo", "nivel", "entidad",
    "unidad_negocio", "subestacion", "entrada_kwh", "facturado_kwh",
    "perdidas_totales_kwh", "perdidas_tecnicas_kwh", "pnt_kwh", "pnt_pct",
    "clientes", "tipo_balance", "cobertura_pct", "config_hash",
]


@dataclass
class HistoricoBalance:
    """Serie de instantáneas del balance, consultable por entidad y período."""

    ruta: Path
    df: pd.DataFrame

    # -- carga / guardado --------------------------------------------------
    @classmethod
    def load(cls, ruta: str | Path) -> "HistoricoBalance":
        p = Path(ruta)
        if not p.exists():
            return cls(ruta=p, df=pd.DataFrame(columns=COLUMNAS))
        try:
            df = (pd.read_parquet(p) if p.suffix == ".parquet"
                  else pd.read_csv(p))
        except Exception:      # pragma: no cover - archivo corrupto o sin pyarrow
            df = pd.DataFrame(columns=COLUMNAS)
        for c in COLUMNAS:
            if c not in df.columns:
                df[c] = np.nan
        return cls(ruta=p, df=df[COLUMNAS])

    def save(self) -> Path:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.ruta.suffix == ".parquet":
                self.df.to_parquet(self.ruta, index=False)
            else:
                self.df.to_csv(self.ruta, index=False)
        except Exception:      # pragma: no cover - sin pyarrow
            self.ruta = self.ruta.with_suffix(".csv")
            self.df.to_csv(self.ruta, index=False)
        return self.ruta

    # -- registro ----------------------------------------------------------
    def registrar(
        self,
        balances: dict[str, pd.DataFrame],
        *,
        periodo: str,
        config_hash: str = "",
        cobertura_pct: float = 100.0,
    ) -> int:
        """Guarda una instantánea de los tres niveles organizacionales.

        Re-registrar el mismo período **reemplaza** la instantánea anterior en vez
        de duplicarla: volver a correr el análisis de un mes es una corrección,
        no un dato nuevo, y acumular las dos versiones haría que el histórico
        mostrara dos valores para el mismo mes.
        """

        snapshot = datetime.now().isoformat(timespec="seconds")
        filas = []
        for nivel, df in balances.items():
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                entidad = (r.get("alimentador") or r.get("subestacion")
                           or r.get("unidad_negocio") or "")
                filas.append({
                    "snapshot_id": f"{periodo}|{nivel}|{entidad}",
                    "registrado_en": snapshot,
                    "periodo": periodo,
                    "nivel": nivel,
                    "entidad": str(entidad),
                    "unidad_negocio": r.get("unidad_negocio"),
                    "subestacion": r.get("subestacion"),
                    "entrada_kwh": r.get("entrada_kwh"),
                    "facturado_kwh": r.get("facturado_kwh"),
                    "perdidas_totales_kwh": r.get("perdidas_totales_kwh"),
                    "perdidas_tecnicas_kwh": r.get("perdidas_tecnicas_kwh"),
                    "pnt_kwh": r.get("pnt_kwh"),
                    "pnt_pct": r.get("pnt_pct"),
                    "clientes": r.get("clientes"),
                    "tipo_balance": r.get("tipo_balance"),
                    "cobertura_pct": r.get("cobertura_pct", cobertura_pct),
                    "config_hash": config_hash,
                })
        if not filas:
            return 0
        nuevo = pd.DataFrame(filas)[COLUMNAS]
        if not self.df.empty:
            self.df = self.df[~self.df["snapshot_id"].isin(nuevo["snapshot_id"])]
        self.df = pd.concat([self.df, nuevo], ignore_index=True)
        return len(nuevo)

    # -- consulta ----------------------------------------------------------
    @property
    def periodos(self) -> list[str]:
        return sorted(self.df["periodo"].dropna().unique().tolist())

    def serie(
        self, entidad: str, *, nivel: str | None = None, metrica: str = "pnt_pct"
    ) -> pd.DataFrame:
        """Serie temporal de una entidad, ordenada por período."""

        d = self.df[self.df["entidad"] == entidad]
        if nivel:
            d = d[d["nivel"] == nivel]
        cols = ["periodo", metrica, "tipo_balance", "cobertura_pct", "config_hash"]
        return d[[c for c in cols if c in d.columns]].sort_values("periodo")

    def comparar_periodos(
        self, periodo_a: str, periodo_b: str, *, nivel: str = "ALIMENTADOR"
    ) -> pd.DataFrame:
        """Variación entre dos períodos: dónde mejoró y dónde empeoró.

        Es la vista que responde "¿sirvió la intervención?". Marca además si la
        comparación es **válida**: dos períodos calculados con configuraciones
        distintas no son comparables, y presentarlos como una tendencia sería
        atribuir a la red un cambio que fue de parámetros.
        """

        a = self.df[(self.df["periodo"] == periodo_a) & (self.df["nivel"] == nivel)]
        b = self.df[(self.df["periodo"] == periodo_b) & (self.df["nivel"] == nivel)]
        if a.empty or b.empty:
            return pd.DataFrame()

        m = a.merge(b, on="entidad", suffixes=("_a", "_b"))
        m["delta_pnt_pct"] = (m["pnt_pct_b"] - m["pnt_pct_a"]).round(2)
        m["delta_pnt_kwh"] = (m["pnt_kwh_b"] - m["pnt_kwh_a"]).round(0)
        m["comparable"] = m["config_hash_a"] == m["config_hash_b"]
        m["tendencia"] = np.where(
            m["delta_pnt_pct"] < -0.5, "MEJORA",
            np.where(m["delta_pnt_pct"] > 0.5, "EMPEORA", "ESTABLE"))
        cols = ["entidad", "pnt_pct_a", "pnt_pct_b", "delta_pnt_pct",
                "delta_pnt_kwh", "tendencia", "comparable",
                "tipo_balance_a", "tipo_balance_b"]
        return m[cols].sort_values("delta_pnt_pct")

    def advertencias(self) -> list[str]:
        """Problemas que invalidan una lectura ingenua del histórico."""

        avisos: list[str] = []
        if self.df.empty:
            return ["Histórico vacío: aún no hay instantáneas registradas."]

        hashes = self.df["config_hash"].dropna().nunique()
        if hashes > 1:
            avisos.append(
                f"El histórico mezcla {hashes} configuraciones distintas. Las "
                "series que cruzan un cambio de configuración NO son una "
                "tendencia: parte de la variación es de parámetros, no de la red.")

        no_medidos = self.df[self.df["tipo_balance"].isin(["INDICATIVO", "PARCIAL"])]
        if not no_medidos.empty:
            avisos.append(
                f"{len(no_medidos):,} instantánea(s) con balance no MEDIDO "
                "(INDICATIVO o PARCIAL): esos puntos son estimaciones y no deben "
                "graficarse junto a los medidos sin distinguirlos.")

        baja = self.df[pd.to_numeric(self.df["cobertura_pct"],
                                     errors="coerce").fillna(100) < 90]
        if not baja.empty:
            avisos.append(
                f"{len(baja):,} instantánea(s) con cobertura menor al 90 % del "
                "universo declarado.")
        return avisos
