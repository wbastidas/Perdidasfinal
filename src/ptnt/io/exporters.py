"""Exportadores de resultados (§E12): XLSX/CSV y reporte ejecutivo HTML.

Los exportadores XLSX usan pandas (motor ``openpyxl`` si está disponible; si no,
caen a CSV). El reporte ejecutivo HTML es autocontenido (sin dependencias de red)
para poder abrirse en cualquier navegador o adjuntarse a un correo.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def export_tables_xlsx(tablas: dict[str, pd.DataFrame], ruta: str) -> str:
    """Exporta varias tablas a un XLSX (una hoja por tabla).

    Si no hay motor de Excel instalado, exporta cada tabla a un CSV junto al XLSX
    y devuelve la ruta base.
    """

    p = Path(ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(p, engine="openpyxl") as xw:
            for nombre, df in tablas.items():
                df.to_excel(xw, sheet_name=nombre[:31], index=False)
        return str(p)
    except Exception:
        # Fallback a CSV
        base = p.with_suffix("")
        for nombre, df in tablas.items():
            df.to_csv(f"{base}_{nombre}.csv", index=False)
        return str(base)


def executive_report_html(
    feeder_code: str,
    balance: dict,
    ranking_top: pd.DataFrame | None = None,
    *,
    titulo: str = "PTNT-BAL — Reporte ejecutivo",
) -> str:
    """Genera un reporte ejecutivo HTML autocontenido para un alimentador."""

    tipo = balance.get("balance_type", "-")
    aviso = (
        '<p class="warn">Balance INDICATIVO: sin medición de cabecera, la PNT no es '
        'verificable.</p>' if tipo == "INDICATIVO" else ""
    )
    comp = balance.get("loss_components", {})
    comp_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{v:,.1f}</td></tr>"
        for k, v in comp.items()
    )
    rank_rows = ""
    if ranking_top is not None and not ranking_top.empty:
        for _, r in ranking_top.head(20).iterrows():
            razones = r.get("razones")
            razon = razones[0] if isinstance(razones, (list, tuple)) and razones else "-"
            rank_rows += (
                f"<tr><td>{int(r.get('rank', 0))}</td>"
                f"<td>{_esc(str(r.get('contract_account','')))}</td>"
                f"<td class='num'>{r.get('score', 0):.3f}</td>"
                f"<td>{_esc(str(razon))}</td></tr>"
            )

    ntl_band = ""
    if balance.get("metrics", {}).get("ntl_p10") is not None:
        m = balance["metrics"]
        ntl_band = (
            f"<p>Banda de incertidumbre PNT (P10–P90): "
            f"{m.get('ntl_p10',0):,.0f} – {m.get('ntl_p90',0):,.0f} kWh</p>"
        )

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>{_esc(titulo)} — {_esc(feeder_code)}</title>
<style>
 body{{font-family:system-ui,Segoe UI,sans-serif;margin:2rem;color:#1e293b;max-width:900px}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:1.5rem;border-bottom:2px solid #e2e8f0;padding-bottom:4px}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0}}
 th,td{{border:1px solid #cbd5e1;padding:6px 10px;text-align:left;font-size:.9rem}}
 th{{background:#f1f5f9}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .kpi{{display:inline-block;margin:0 1.5rem .5rem 0}}
 .kpi b{{display:block;font-size:1.5rem;color:#0369a1}}
 .warn{{background:#fef3c7;border-left:4px solid #f59e0b;padding:8px 12px}}
 .foot{{color:#64748b;font-size:.8rem;margin-top:2rem}}
</style></head><body>
<h1>{_esc(titulo)}</h1>
<p><b>Alimentador:</b> {_esc(feeder_code)} · <b>Balance:</b> {_esc(tipo)} ·
   <b>Fecha:</b> {datetime.now():%Y-%m-%d %H:%M}</p>
{aviso}
<h2>Balance energético</h2>
<div>
 <span class="kpi"><b>{balance.get('e_input_kwh',0):,.0f}</b>Entrada kWh</span>
 <span class="kpi"><b>{balance.get('e_billed_kwh',0):,.0f}</b>Facturado kWh</span>
 <span class="kpi"><b>{balance.get('loss_technical_kwh',0):,.0f}</b>Pérdidas técnicas kWh</span>
 <span class="kpi"><b>{balance.get('ntl_kwh',0):,.0f}</b>PNT kWh ({balance.get('ntl_pct',0):.1f}%)</span>
</div>
{ntl_band}
<h2>Pérdidas técnicas por componente</h2>
<table><thead><tr><th>Componente</th><th class="num">kWh</th></tr></thead>
<tbody>{comp_rows}</tbody></table>
{"<h2>Top clientes por sospecha de hurto</h2><table><thead><tr><th>#</th><th>Cuenta</th><th class='num'>Score</th><th>Razón</th></tr></thead><tbody>" + rank_rows + "</tbody></table>" if rank_rows else ""}
<p class="foot">Generado por PTNT-BAL. Cifras MEDIDO/INDICATIVO y bandas de
incertidumbre según §2.2 de la especificación.</p>
</body></html>"""


def write_executive_report(
    feeder_code: str, balance: dict, ruta: str,
    ranking_top: pd.DataFrame | None = None,
) -> str:
    html = executive_report_html(feeder_code, balance, ranking_top)
    p = Path(ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return str(p)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
