"""Clientes faltantes: análisis de vinculación comercial ↔ SIG (§E3.3).

Dos huecos distintos, ambos materiales para el balance:

* **CSV_SIN_SIG** — el cliente factura pero no está en el SIG: su energía no se
  puede asignar a ningún transformador ni alimentador. El balance del alimentador
  la **omite**, lo que **infla artificialmente la PNT** de la zona donde debería
  estar.
* **SIG_SIN_CSV** — el cliente existe en la red pero **no factura**: puede ser un
  cliente nuevo sin activar, un retiro no depurado… o un **consumo no facturado**
  (que sí es señal de PNT y por eso se reporta aparte).

Métrica rectora (§E3.3): el **% de energía vinculada** es el techo de calidad del
balance. Si es 85 %, la PNT resultante es indistinguible de ese 15 % sin ubicar; por
eso el sistema se niega a emitir balance MEDIDO por debajo del umbral configurado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class UnmatchedReport:
    """Resultado del análisis de clientes faltantes."""

    csv_sin_sig: pd.DataFrame        # facturan pero no están en la red
    sig_sin_csv: pd.DataFrame        # están en la red pero no facturan
    n_csv: int = 0
    n_sig: int = 0
    n_vinculados: int = 0
    pct_cuentas_vinculadas: float = 0.0
    pct_energia_vinculada: float = 0.0
    energia_sin_vincular_kwh: float = 0.0
    apto_balance_medido: bool = True
    detail: str = ""
    por_ruta: pd.DataFrame = field(default_factory=pd.DataFrame)

    def resumen(self) -> dict:
        return {
            "n_cuentas_csv": self.n_csv,
            "n_clientes_sig": self.n_sig,
            "n_vinculados": self.n_vinculados,
            "pct_cuentas_vinculadas": round(self.pct_cuentas_vinculadas, 2),
            "pct_energia_vinculada": round(self.pct_energia_vinculada, 2),
            "energia_sin_vincular_kwh": round(self.energia_sin_vincular_kwh, 1),
            "n_csv_sin_sig": int(len(self.csv_sin_sig)),
            "n_sig_sin_csv": int(len(self.sig_sin_csv)),
            "apto_balance_medido": self.apto_balance_medido,
        }


def analyze_unmatched_customers(
    comercial: pd.DataFrame,
    sig: pd.DataFrame,
    *,
    cuenta_col: str = "contract_account",
    energia_col: str = "kwh_representativo",
    ruta_col: str | None = "grupo_lectura",
    umbral_energia_vinculada_pct: float = 95.0,
) -> UnmatchedReport:
    """Cruza el padrón comercial contra el SIG e informa los faltantes.

    ``comercial``: cuentas que facturan (con su energía). ``sig``: clientes
    presentes en la red (``contract_account`` y, si existe, su puesto/alimentador).
    """

    com = comercial.copy()
    com[cuenta_col] = com[cuenta_col].astype(str)
    red = sig.copy()
    if cuenta_col not in red.columns:
        red[cuenta_col] = pd.Series(dtype=str)
    red[cuenta_col] = red[cuenta_col].astype(str)

    cuentas_com = set(com[cuenta_col])
    cuentas_sig = set(red[cuenta_col]) - {"", "nan", "None"}
    vinculados = cuentas_com & cuentas_sig

    energia = (
        pd.to_numeric(com[energia_col], errors="coerce").fillna(0.0)
        if energia_col in com.columns else pd.Series(np.zeros(len(com)))
    )
    com["_energia"] = energia
    energia_total = float(com["_energia"].sum())
    energia_vinc = float(com.loc[com[cuenta_col].isin(vinculados), "_energia"].sum())
    energia_sin = energia_total - energia_vinc

    # --- CSV sin SIG: facturan pero no están en la red -----------------------
    faltan_sig = com[~com[cuenta_col].isin(cuentas_sig)].copy()
    faltan_sig["direccion"] = "CSV_SIN_SIG"
    faltan_sig["motivo"] = (
        "Factura pero no está en el SIG: su energía no se asigna a ningún "
        "transformador y infla la PNT de la zona donde debería estar"
    )
    cols_csv = [c for c in [cuenta_col, "_energia", "direccion", "motivo",
                            ruta_col, "tariff_description", "x", "y"]
                if c and c in faltan_sig.columns]
    csv_sin_sig = faltan_sig[cols_csv].rename(columns={"_energia": "energia_kwh"})
    csv_sin_sig = csv_sin_sig.sort_values("energia_kwh", ascending=False)

    # --- SIG sin CSV: están en la red pero no facturan -----------------------
    faltan_com = red[~red[cuenta_col].isin(cuentas_com)].copy()
    faltan_com["direccion"] = "SIG_SIN_CSV"
    faltan_com["motivo"] = (
        "Está en el SIG pero no factura: cliente nuevo sin activar, retiro no "
        "depurado o consumo no facturado (posible PNT)"
    )
    cols_sig = [c for c in [cuenta_col, "customer_id", "site_id", "feeder_code",
                            "direccion", "motivo", "x", "y"]
                if c in faltan_com.columns]
    sig_sin_csv = faltan_com[cols_sig]

    pct_cuentas = (len(vinculados) / len(cuentas_com) * 100.0) if cuentas_com else 0.0
    pct_energia = (energia_vinc / energia_total * 100.0) if energia_total > 0 else 0.0
    apto = pct_energia >= umbral_energia_vinculada_pct

    # --- Concentración por ruta comercial (CLIRLSCOD) ------------------------
    por_ruta = pd.DataFrame()
    if ruta_col and ruta_col in faltan_sig.columns and not faltan_sig.empty:
        por_ruta = (
            faltan_sig.groupby(ruta_col)
            .agg(cuentas_sin_sig=(cuenta_col, "count"),
                 energia_sin_sig_kwh=("_energia", "sum"))
            .reset_index()
            .sort_values("energia_sin_sig_kwh", ascending=False)
        )

    detalle = (
        f"{pct_energia:.1f}% de la energía facturada está vinculada al SIG "
        f"({pct_cuentas:.1f}% de las cuentas). "
        + (
            f"Por encima del umbral de {umbral_energia_vinculada_pct}%: apto para "
            "balance MEDIDO."
            if apto else
            f"Por DEBAJO del umbral de {umbral_energia_vinculada_pct}%: el balance se "
            f"degrada a INDICATIVO — la PNT sería indistinguible de los "
            f"{energia_sin:,.0f} kWh sin ubicar."
        )
    )

    return UnmatchedReport(
        csv_sin_sig=csv_sin_sig, sig_sin_csv=sig_sin_csv,
        n_csv=len(cuentas_com), n_sig=len(cuentas_sig), n_vinculados=len(vinculados),
        pct_cuentas_vinculadas=pct_cuentas, pct_energia_vinculada=pct_energia,
        energia_sin_vincular_kwh=energia_sin, apto_balance_medido=apto,
        detail=detalle, por_ruta=por_ruta,
    )
