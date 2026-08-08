"""Visor web de solo lectura (FastAPI).

Propósito: que otras personas (jefaturas, cuadrillas, auditoría) puedan **ver**
los resultados sin poder recalcular ni modificar nada. Complementa al tablero de
escritorio (Streamlit), que es para el analista.

Controles de seguridad:
  * Autenticación básica (usuario/contraseña) contra el almacén de usuarios (solo
    hashes). Rol mínimo ``viewer``.
  * Restricción por red de origen (CIDR) si ``seguridad.redes_permitidas`` está
    poblado.
  * Solo lectura: no expone ningún endpoint de escritura.

Requiere el extra ``webviewer`` (``pip install 'ptnt-bal[webviewer]'``).
"""

from __future__ import annotations

import ipaddress
import json
import secrets
from pathlib import Path

import pandas as pd

try:
    from fastapi import Depends, FastAPI, HTTPException, Request, status
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.security import HTTPBasic, HTTPBasicCredentials
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "FastAPI no está instalado. Instale: pip install 'ptnt-bal[webviewer]'"
    ) from exc

from ptnt.config.loader import load_config
from ptnt.config.models import AppConfig
from ptnt.security.auth import UserStore


def _load_ranking(cfg: AppConfig) -> pd.DataFrame:
    base = Path(cfg.rutas.salidas)
    pq, csv = base / "ranking_clientes.parquet", base / "ranking_clientes.csv"
    if pq.exists():
        return pd.read_parquet(pq)
    if csv.exists():
        return pd.read_csv(csv)
    return pd.DataFrame()


def _load_metricas(cfg: AppConfig) -> dict:
    mj = Path(cfg.rutas.salidas) / "metricas.json"
    return json.loads(mj.read_text(encoding="utf-8")) if mj.exists() else {}


def _load_balance(cfg: AppConfig) -> dict:
    bj = Path(cfg.rutas.salidas) / "balance_red.json"
    return json.loads(bj.read_text(encoding="utf-8")) if bj.exists() else {}


def _load_plan(cfg: AppConfig) -> dict:
    pj = Path(cfg.rutas.salidas) / "plan_levantamientos.json"
    return json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}


def _load_work_orders(cfg: AppConfig) -> pd.DataFrame:
    p = Path(cfg.rutas.salidas) / "ordenes_levantamiento.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def create_app(config_path: str = "config/base.yaml") -> FastAPI:
    cfg = load_config(config_path)
    app = FastAPI(title=cfg.visor.titulo, docs_url=None, redoc_url=None)
    security = HTTPBasic()
    users = UserStore(cfg.seguridad.ruta_usuarios)

    def _check_network(request: Request) -> None:
        redes = cfg.seguridad.redes_permitidas
        if not redes:
            return
        client_ip = request.client.host if request.client else "0.0.0.0"
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "IP inválida")
        if not any(ip in ipaddress.ip_network(r, strict=False) for r in redes):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Red no autorizada")

    def _auth(request: Request, creds: HTTPBasicCredentials = Depends(security)):
        _check_network(request)
        if not cfg.seguridad.autenticacion_habilitada:
            return "anon"
        user = users.authenticate(creds.username, creds.password)
        if user is None:
            # respuesta en tiempo ~constante ya gestionada en UserStore
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Credenciales inválidas",
                headers={"WWW-Authenticate": "Basic"},
            )
        return user.username

    @app.get("/salud")
    def salud() -> dict:
        return {"estado": "ok", "titulo": cfg.visor.titulo}

    @app.get("/api/metricas")
    def api_metricas(_user: str = Depends(_auth)) -> JSONResponse:
        return JSONResponse(_load_metricas(cfg))

    @app.get("/api/balance")
    def api_balance(_user: str = Depends(_auth)) -> JSONResponse:
        bal = _load_balance(cfg)
        if not bal:
            return JSONResponse({"error": "sin balance de red calculado"}, status_code=404)
        return JSONResponse(bal)

    @app.get("/api/focalizacion")
    def api_focalizacion(_user: str = Depends(_auth), nivel: str | None = None,
                         top: int = 100) -> JSONResponse:
        """Objetivos de levantamiento, opcionalmente filtrados por nivel."""

        plan = _load_plan(cfg)
        if not plan:
            return JSONResponse({"error": "sin plan de focalización"}, status_code=404)
        objetivos = plan.get("objetivos", [])
        if nivel:
            objetivos = [o for o in objetivos if str(o.get("nivel", "")).upper() == nivel.upper()]
        return JSONResponse({
            "resumen": plan.get("resumen", {}),
            "objetivos": objetivos[: max(1, min(top, 5000))],
        })

    @app.get("/api/ordenes")
    def api_ordenes(_user: str = Depends(_auth)) -> JSONResponse:
        """Órdenes de levantamiento para gestión de campo."""

        ot = _load_work_orders(cfg)
        if ot.empty:
            return JSONResponse({"error": "sin órdenes generadas"}, status_code=404)
        return JSONResponse(json.loads(ot.to_json(orient="records")))

    @app.get("/api/ranking")
    def api_ranking(_user: str = Depends(_auth), top: int = 100) -> JSONResponse:
        df = _load_ranking(cfg)
        if df.empty:
            return JSONResponse({"error": "sin resultados calculados"}, status_code=404)
        top = max(1, min(top, 5000))
        cols = [c for c in ["rank", "contract_account", "score",
                            "n_senales_activas", "recuperable_kwh_mes", "razones"]
                if c in df.columns]
        return JSONResponse(json.loads(df[cols].head(top).to_json(orient="records")))

    @app.get("/", response_class=HTMLResponse)
    def index(user: str = Depends(_auth)) -> str:
        df = _load_ranking(cfg)
        met = _load_metricas(cfg)
        bal = _load_balance(cfg)
        return _render_html(cfg, df, met, user, bal, _load_work_orders(cfg), _load_plan(cfg))

    return app


def _balance_html(bal: dict) -> str:
    if not bal:
        return ""
    tipo = bal.get("balance_type", "-")
    aviso = (
        '<p class="note" style="color:#fbbf24">Balance INDICATIVO: sin medición de '
        'cabecera, la PNT no es verificable.</p>'
        if tipo == "INDICATIVO" else ""
    )
    return f"""
 <h2 style="font-size:1rem;margin-top:28px">Balance energético de red ({_esc(tipo)})</h2>
 {aviso}
 <div class="cards">
  <div class="card"><div class="v">{bal.get('e_input_kwh',0):,.0f}</div><div class="l">Entrada kWh</div></div>
  <div class="card"><div class="v">{bal.get('e_billed_kwh',0):,.0f}</div><div class="l">Facturado kWh</div></div>
  <div class="card"><div class="v">{bal.get('loss_technical_kwh',0):,.0f}</div><div class="l">Pérdidas técnicas kWh</div></div>
  <div class="card"><div class="v">{bal.get('ntl_kwh',0):,.0f}</div><div class="l">PNT kWh ({bal.get('ntl_pct',0):.1f}%)</div></div>
 </div>"""


def _ordenes_html(ot: pd.DataFrame, plan: dict) -> str:
    """Bloque de focalización: dónde ir a hacer el levantamiento."""

    if ot is None or ot.empty:
        return ""
    res = plan.get("resumen", {}) if plan else {}
    filas = []
    for _, r in ot.head(25).iterrows():
        filas.append(
            f"<tr><td>{_esc(r.get('orden_trabajo',''))}</td>"
            f"<td>{_esc(r.get('nivel',''))}</td>"
            f"<td>{_esc(r.get('entidad',''))}</td>"
            f"<td>{_esc(r.get('accion',''))}</td>"
            f"<td class='num'>{int(r.get('clientes_a_revisar',0) or 0)}</td>"
            f"<td class='num'>{float(r.get('kwh_por_visita',0) or 0):,.0f}</td>"
            f"<td>{_esc(str(r.get('motivo_principal',''))[:70])}</td></tr>"
        )
    cobertura = int(ot["clientes_a_revisar"].sum()) if "clientes_a_revisar" in ot else 0
    energia = float(ot["kwh_por_visita"].sum()) if "kwh_por_visita" in ot else 0.0
    return f"""
 <h2 style="font-size:1rem;margin-top:28px">📍 Dónde inspeccionar — órdenes de levantamiento</h2>
 <div class="cards">
  <div class="card"><div class="v">{res.get('n_objetivos',0):,}</div><div class="l">Objetivos priorizados</div></div>
  <div class="card"><div class="v">{len(ot):,}</div><div class="l">Órdenes de trabajo</div></div>
  <div class="card"><div class="v">{cobertura:,}</div><div class="l">Clientes cubiertos</div></div>
  <div class="card"><div class="v">{energia:,.0f}</div><div class="l">kWh/mes en juego</div></div>
 </div>
 <table><thead><tr><th>OT</th><th>Nivel</th><th>Entidad</th><th>Acción</th>
   <th>Clientes</th><th>kWh/visita</th><th>Motivo</th></tr></thead>
 <tbody>{''.join(filas)}</tbody></table>"""


def _render_html(cfg: AppConfig, df: pd.DataFrame, met: dict, user: str,
                 bal: dict | None = None, ot: pd.DataFrame | None = None,
                 plan: dict | None = None) -> str:
    if df.empty:
        cuerpo = "<p>No hay resultados calculados todavía.</p>"
    else:
        filas = []
        for _, r in df.head(100).iterrows():
            razones = r.get("razones")
            razon = razones[0] if isinstance(razones, (list, tuple)) and razones else "-"
            filas.append(
                f"<tr><td>{int(r['rank'])}</td>"
                f"<td>{_esc(str(r['contract_account']))}</td>"
                f"<td>{r['score']:.3f}</td>"
                f"<td>{int(r.get('n_senales_activas', 0))}</td>"
                f"<td>{_esc(str(razon))}</td></tr>"
            )
        cuerpo = (
            "<table><thead><tr><th>#</th><th>Cuenta</th><th>Score</th>"
            "<th>Señales</th><th>Razón principal</th></tr></thead><tbody>"
            + "".join(filas)
            + "</tbody></table>"
        )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(cfg.visor.titulo)}</title>
<style>
 body{{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}
 header{{background:#1e293b;padding:16px 24px;border-bottom:2px solid #334155}}
 h1{{font-size:1.2rem;margin:0}} main{{padding:24px;max-width:1100px;margin:0 auto}}
 .cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
 .card{{background:#1e293b;border-radius:10px;padding:16px 20px;min-width:160px}}
 .card .v{{font-size:1.6rem;font-weight:700;color:#38bdf8}}
 .card .l{{font-size:.8rem;color:#94a3b8}}
 table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden}}
 th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #334155;font-size:.9rem}}
 th{{background:#334155}} tr:hover td{{background:#243049}}
 .note{{color:#94a3b8;font-size:.8rem;margin-top:16px}}
</style></head>
<body>
<header><h1>⚡ {_esc(cfg.visor.titulo)}</h1></header>
<main>
 <div class="cards">
  <div class="card"><div class="v">{met.get('n_cuentas','-'):,}</div><div class="l">Cuentas analizadas</div></div>
  <div class="card"><div class="v">{met.get('n_sospechosos','-'):,}</div><div class="l">Clientes sospechosos</div></div>
  <div class="card"><div class="v">{met.get('n_meses','-')}</div><div class="l">Meses de consumo</div></div>
  <div class="card"><div class="v">{_fmt_pct(met.get('delta_p_total_pct'))}</div><div class="l">Δ Potencia SIG→corregido</div></div>
 </div>
 {_ordenes_html(ot if ot is not None else pd.DataFrame(), plan or {})}
 <h2 style="font-size:1rem;margin-top:28px">Top 100 clientes por sospecha de hurto</h2>
 {cuerpo}
 {_balance_html(bal or {})}
 <p class="note">Vista de solo lectura · usuario: {_esc(user)} · método de promedio:
   {_esc(str(met.get('metodo_promedio','-')))} · Datos ya calculados; esta interfaz no ejecuta análisis.</p>
</main></body></html>"""


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_pct(v) -> str:
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "-"
