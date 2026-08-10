"""Focalización por **ruta comercial** (`CLIRLSCOD`).

La ruta de lectura es la unidad organizativa con la que la distribuidora ya
gestiona el trabajo de campo: un lector la recorre completa, con un orden
establecido. Por eso es el mecanismo natural para **seleccionar qué levantar**, y
tiene una ventaja sobre el sector geométrico: no hay que inventar un recorrido,
ya existe.

Dos criterios de selección, ambos accionables:

* **Sospecha** — concentración de clientes con señales de PNT en la ruta.
* **Incoherencia** — anomalías de la propia ruta que apuntan a un problema de
  lectura/gestión antes que a hurto: rachas de ceros, exceso de lecturas
  estimadas, consumo colapsado frente a rutas comparables, o clientes que
  facturan sin estar en el SIG concentrados en la misma ruta. Una ruta
  incoherente suele señalar un **problema del lector o del proceso**, y ese
  hallazgo vale tanto como el hurto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class RouteStats:
    """Diagnóstico de una ruta comercial."""

    route_id: str
    n_clientes: int
    n_sospechosos: int
    densidad_sospecha: float
    energia_kwh: float
    recuperable_kwh: float
    # Indicadores de incoherencia de la ruta
    pct_ceros: float = 0.0
    pct_estimadas: float = 0.0
    cv_consumo: float = 0.0
    ratio_vs_pares: float = 1.0        # consumo medio de la ruta / mediana global
    n_sin_sig: int = 0                 # clientes que facturan sin estar en el SIG
    incoherencia: float = 0.0          # 0–1
    motivos: list[str] = field(default_factory=list)
    feeder_code: str | None = None


def analyze_commercial_routes(
    clientes: pd.DataFrame,
    consumo_largo: pd.DataFrame | None = None,
    *,
    ruta_col: str = "grupo_lectura",
    cuenta_col: str = "contract_account",
    energia_col: str = "kwh_representativo",
    suspect_customers: set[str] | None = None,
    recoverable_by_customer: dict[str, float] | None = None,
    cuentas_sin_sig: set[str] | None = None,
    min_clientes: int = 5,
    umbral_ceros_pct: float = 20.0,
    umbral_estimadas_pct: float = 30.0,
) -> list[RouteStats]:
    """Agrupa por ruta comercial y calcula sospecha e incoherencia.

    ``clientes`` debe traer la cuenta, la ruta (``CLIRLSCOD``) y la energía
    representativa. ``consumo_largo`` (opcional) aporta las rachas de ceros y las
    lecturas estimadas.
    """

    if clientes is None or clientes.empty or ruta_col not in clientes.columns:
        return []

    suspect_customers = suspect_customers or set()
    recoverable_by_customer = recoverable_by_customer or {}
    cuentas_sin_sig = cuentas_sin_sig or set()

    df = clientes.copy()
    df[cuenta_col] = df[cuenta_col].astype(str)
    df = df[df[ruta_col].notna()]
    if df.empty:
        return []
    if energia_col not in df.columns:
        df[energia_col] = 0.0
    df["_energia"] = pd.to_numeric(df[energia_col], errors="coerce").fillna(0.0)

    # Estadísticas de consumo por cuenta (ceros y estimadas)
    ceros_por_cuenta: dict[str, float] = {}
    estimadas_por_cuenta: dict[str, float] = {}
    if consumo_largo is not None and not consumo_largo.empty:
        cl = consumo_largo.copy()
        cl[cuenta_col] = cl[cuenta_col].astype(str)
        if "is_zero" in cl.columns:
            ceros_por_cuenta = cl.groupby(cuenta_col)["is_zero"].mean().to_dict()
        if "is_estimated" in cl.columns:
            estimadas_por_cuenta = cl.groupby(cuenta_col)["is_estimated"].mean().to_dict()

    mediana_global = float(df["_energia"].median()) or 1.0
    salida: list[RouteStats] = []

    for ruta, sub in df.groupby(ruta_col):
        if len(sub) < min_clientes:
            continue
        cuentas = sub[cuenta_col].tolist()
        sospechosos = [c for c in cuentas if c in suspect_customers]
        energia = float(sub["_energia"].sum())
        media = float(sub["_energia"].mean())
        recuperable = sum(recoverable_by_customer.get(c, 0.0) for c in cuentas)

        pct_ceros = float(np.mean([ceros_por_cuenta.get(c, 0.0) for c in cuentas]) * 100)
        pct_est = float(np.mean([estimadas_por_cuenta.get(c, 0.0) for c in cuentas]) * 100)
        cv = float(sub["_energia"].std() / media) if media > 0 else 0.0
        ratio = media / mediana_global if mediana_global > 0 else 1.0
        sin_sig = sum(1 for c in cuentas if c in cuentas_sin_sig)

        # --- incoherencia de la ruta ---
        motivos: list[str] = []
        comp = []
        if pct_ceros > umbral_ceros_pct:
            comp.append(min(pct_ceros / 100.0, 1.0))
            motivos.append(
                f"{pct_ceros:.0f}% de lecturas en cero en la ruta: revisar el "
                "proceso de lectura antes que presumir hurto")
        if pct_est > umbral_estimadas_pct:
            comp.append(min(pct_est / 100.0, 1.0))
            motivos.append(
                f"{pct_est:.0f}% de lecturas estimadas: la ruta no se está leyendo")
        if ratio < 0.6:
            comp.append(min((0.6 - ratio) / 0.6, 1.0))
            motivos.append(
                f"Consumo medio {ratio:.2f}× la mediana de las demás rutas: "
                "nivel colapsado frente a sus pares")
        if sin_sig > 0:
            comp.append(min(sin_sig / max(len(cuentas), 1), 1.0))
            motivos.append(
                f"{sin_sig} cliente(s) facturan sin estar en el SIG: su energía no "
                "se asigna y se confunde con PNT")
        incoherencia = float(np.clip(np.mean(comp), 0, 1)) if comp else 0.0

        densidad = len(sospechosos) / len(cuentas)
        if sospechosos:
            motivos.insert(0, (
                f"{len(sospechosos)} de {len(cuentas)} clientes de la ruta con señal "
                f"de PNT ({densidad*100:.0f}% de densidad)"))

        feeder = None
        if "feeder_code" in sub.columns:
            vals = sub["feeder_code"].dropna()
            feeder = str(vals.iloc[0]) if not vals.empty else None

        salida.append(RouteStats(
            route_id=str(ruta), n_clientes=len(cuentas), n_sospechosos=len(sospechosos),
            densidad_sospecha=densidad, energia_kwh=energia, recuperable_kwh=recuperable,
            pct_ceros=pct_ceros, pct_estimadas=pct_est, cv_consumo=cv,
            ratio_vs_pares=ratio, n_sin_sig=sin_sig, incoherencia=incoherencia,
            motivos=motivos[:3], feeder_code=feeder,
        ))

    salida.sort(key=lambda r: (r.densidad_sospecha, r.incoherencia), reverse=True)
    return salida


def routes_to_target_input(rutas: list[RouteStats]) -> list[dict]:
    """Convierte las rutas al formato que consume ``build_survey_plan``."""

    return [
        {
            "route_id": r.route_id, "feeder_code": r.feeder_code,
            "customers": r.n_clientes, "suspect_customers": r.n_sospechosos,
            "energy_kwh": r.energia_kwh, "recoverable_kwh": r.recuperable_kwh,
            "incoherencia": r.incoherencia, "motivos": r.motivos,
            "pct_ceros": r.pct_ceros, "pct_estimadas": r.pct_estimadas,
            "ratio_vs_pares": r.ratio_vs_pares, "n_sin_sig": r.n_sin_sig,
        }
        for r in rutas
    ]


def routes_to_dataframe(rutas: list[RouteStats]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ruta_comercial": r.route_id, "alimentador": r.feeder_code,
            "clientes": r.n_clientes, "sospechosos": r.n_sospechosos,
            "densidad_sospecha_pct": round(r.densidad_sospecha * 100, 1),
            "incoherencia": round(r.incoherencia, 3),
            "pct_ceros": round(r.pct_ceros, 1),
            "pct_estimadas": round(r.pct_estimadas, 1),
            "ratio_vs_pares": round(r.ratio_vs_pares, 2),
            "clientes_sin_sig": r.n_sin_sig,
            "energia_kwh": round(r.energia_kwh, 1),
            "recuperable_kwh_mes": round(r.recuperable_kwh, 1),
            "motivo_principal": r.motivos[0] if r.motivos else "",
        }
        for r in rutas
    ])
