"""Reporte de focalización de levantamientos (HTML autocontenido).

Responde visualmente "¿dónde voy a hacer el levantamiento?": tabla priorizada por
nivel, órdenes de trabajo y las razones operativas de cada objetivo.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ptnt.survey.targeting import SurveyPlan, TargetLevel

_NIVEL_ICONO = {
    "ALIMENTADOR": "🔌", "ZONA_PROTECCION": "🛡️", "RAMAL": "🌿",
    "PUESTO_TRANSFORMACION": "⚡", "SECTOR": "📍", "CLIENTE": "🏠",
}


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _tabla(df: pd.DataFrame, cols: list[tuple[str, str]]) -> str:
    if df.empty:
        return "<p class='vacio'>Sin objetivos en este nivel.</p>"
    head = "".join(f"<th>{_esc(t)}</th>" for _, t in cols)
    filas = []
    for _, r in df.iterrows():
        celdas = []
        for c, _ in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
            celdas.append(f"<td>{_esc(v)}</td>")
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(filas)}</tbody></table>"


def survey_report_html(plan: SurveyPlan, *, titulo: str = "Plan de levantamientos — PTNT-BAL",
                       top_por_nivel: int = 15) -> str:
    """Genera el reporte HTML de focalización."""

    res = plan.resumen
    bloques = []
    for lvl in TargetLevel:
        df = plan.to_dataframe(lvl).head(top_por_nivel)
        if df.empty:
            continue
        icono = _NIVEL_ICONO.get(lvl.value, "•")
        bloques.append(f"""
        <section>
          <h2>{icono} {_esc(lvl.value.replace('_',' ').title())}
              <span class="badge">{len(plan.by_level(lvl))} objetivos</span></h2>
          {_tabla(df, [
              ("orden","#"), ("entidad","Entidad"), ("alimentador","Alim."),
              ("prioridad","Prioridad"), ("recuperable_kwh_mes","Recuperable kWh/mes"),
              ("clientes","Clientes"), ("accion","Acción"), ("razon_1","Motivo principal"),
          ])}
        </section>""")

    ot = plan.work_orders(top_n=25)
    bloque_ot = f"""
        <section class="destacada">
          <h2>📋 Órdenes de levantamiento (top 25)</h2>
          <p class="nota">Objetivos accionables por una cuadrilla, excluidos los que
             son problema de datos. Ordenados por prioridad.</p>
          {_tabla(ot, [
              ("orden_trabajo","OT"), ("nivel","Nivel"), ("entidad","Entidad"),
              ("accion","Acción"), ("clientes_a_revisar","Clientes"),
              ("recuperable_kwh_mes","Recuperable kWh/mes"), ("motivo_principal","Motivo"),
          ])}
        </section>"""

    por_nivel = res.get("por_nivel", {})
    chips = "".join(
        f"<span class='chip'>{_NIVEL_ICONO.get(k,'•')} {_esc(k.replace('_',' ').title())}: <b>{v}</b></span>"
        for k, v in por_nivel.items() if v
    )

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(titulo)}</title>
<style>
 :root {{ color-scheme: light; }}
 body {{ font-family: system-ui,'Segoe UI',sans-serif; margin:0; background:#f1f5f9; color:#0f172a; }}
 .wrap {{ max-width:1180px; margin:0 auto; padding:24px; }}
 header.hero {{ background:linear-gradient(110deg,#0ea5e9,#2563eb 55%,#7c3aed);
   color:#fff; padding:26px 30px; border-radius:16px; box-shadow:0 8px 28px rgba(37,99,235,.28); }}
 header.hero h1 {{ margin:0; font-size:1.5rem; }}
 header.hero p {{ margin:.4rem 0 0; color:#e0f2fe; font-size:.9rem; }}
 .chips {{ margin:18px 0; display:flex; gap:10px; flex-wrap:wrap; }}
 .chip {{ background:#fff; border:1px solid #cbd5e1; border-radius:999px;
   padding:6px 14px; font-size:.85rem; }}
 .kpis {{ display:flex; gap:16px; flex-wrap:wrap; margin:18px 0 6px; }}
 .kpi {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px;
   padding:14px 20px; min-width:180px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
 .kpi b {{ display:block; font-size:1.6rem; color:#0369a1; }}
 .kpi span {{ color:#64748b; font-size:.8rem; }}
 section {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px;
   padding:18px 22px; margin:18px 0; box-shadow:0 1px 3px rgba(0,0,0,.05); }}
 section.destacada {{ border-color:#38bdf8; box-shadow:0 2px 12px rgba(14,165,233,.18); }}
 h2 {{ font-size:1.05rem; margin:0 0 12px; display:flex; align-items:center; gap:10px; }}
 .badge {{ background:#e0f2fe; color:#0369a1; border-radius:999px;
   padding:2px 10px; font-size:.75rem; font-weight:600; }}
 table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
 th,td {{ padding:7px 10px; text-align:left; border-bottom:1px solid #e2e8f0; }}
 th {{ background:#f8fafc; font-weight:600; color:#475569; }}
 tbody tr:hover {{ background:#f0f9ff; }}
 .nota {{ color:#64748b; font-size:.82rem; margin:.2rem 0 .8rem; }}
 .vacio {{ color:#94a3b8; font-style:italic; }}
 footer {{ color:#64748b; font-size:.78rem; margin:24px 0; }}
</style></head><body><div class="wrap">
<header class="hero">
  <h1>📍 {_esc(titulo)}</h1>
  <p>Dónde ir a hacer el levantamiento: prioridad por alimentador, zona, ramal,
     transformador, sector y cliente · Generado {_esc(plan.generated_at or datetime.now().isoformat(timespec='seconds'))}</p>
</header>
<div class="kpis">
  <div class="kpi"><b>{res.get('n_objetivos',0):,}</b><span>Objetivos priorizados</span></div>
  <div class="kpi"><b>{res.get('recuperable_total_kwh_mes',0):,.0f}</b><span>kWh/mes recuperables (clientes)</span></div>
  <div class="kpi"><b>{len(ot):,}</b><span>Órdenes de levantamiento</span></div>
  <div class="kpi"><b>{res.get('objetivos_con_problema_datos',0):,}</b><span>Marcados como problema de datos</span></div>
</div>
<div class="chips">{chips}</div>
{bloque_ot}
{''.join(bloques)}
<footer>PTNT-BAL · La ubicación de la PNT es una <b>inferencia</b> construida por
convergencia de evidencias; la energía total sí es medida. Los objetivos marcados
como problema de datos deben corregirse antes de enviar cuadrilla.</footer>
</div></body></html>"""


def write_survey_report(plan: SurveyPlan, ruta: str, **kw) -> str:
    p = Path(ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(survey_report_html(plan, **kw), encoding="utf-8")
    return str(p)
