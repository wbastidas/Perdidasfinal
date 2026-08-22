"""Tablero de análisis PTNT-BAL para escritorio (Streamlit).

Interfaz interactiva para el analista. Lee exclusivamente de las salidas ya
calculadas (DuckDB / Parquet); no ejecuta el pipeline pesado. Requiere el extra
``dashboard`` (``pip install 'ptnt-bal[dashboard]'``).

Ejecutar con:  ``ptnt dashboard -c config/base.yaml``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Streamlit no está instalado. Instale: pip install 'ptnt-bal[dashboard]'"
    ) from exc

from ptnt.config.loader import load_config


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/base.yaml")
    known, _ = parser.parse_known_args()
    return known


@st.cache_data(show_spinner=False)
def _load_outputs(salidas: str) -> dict:
    base = Path(salidas)
    data: dict = {}
    for nombre in ("ranking_clientes", "reconciliacion", "potencias"):
        pq = base / f"{nombre}.parquet"
        csv = base / f"{nombre}.csv"
        if pq.exists():
            data[nombre] = pd.read_parquet(pq)
        elif csv.exists():
            data[nombre] = pd.read_csv(csv)
    mj = base / "metricas.json"
    if mj.exists():
        data["metricas"] = json.loads(mj.read_text(encoding="utf-8"))
    return data


def _inject_theme() -> None:
    """Estilo visual: tipografía, tarjetas de métricas y encabezado con degradado."""

    st.markdown(
        """
        <style>
          .stApp { background: #0b1220; }
          .block-container { padding-top: 1.2rem; max-width: 1250px; }
          h1, h2, h3 { color: #e2e8f0; letter-spacing: -0.01em; }
          /* Métricas como tarjetas */
          div[data-testid="stMetric"] {
            background: linear-gradient(160deg,#1e293b 0%,#172033 100%);
            border: 1px solid #334155; border-radius: 14px;
            padding: 14px 18px; box-shadow: 0 2px 10px rgba(0,0,0,.25);
          }
          div[data-testid="stMetricValue"] { color: #38bdf8; font-weight: 700; }
          div[data-testid="stMetricLabel"] { color: #94a3b8; }
          .ptnt-hero {
            background: linear-gradient(110deg,#0ea5e9 0%,#2563eb 55%,#7c3aed 100%);
            border-radius: 16px; padding: 20px 26px; margin-bottom: 18px;
            color: white; box-shadow: 0 6px 24px rgba(37,99,235,.35);
          }
          .ptnt-hero h1 { color:white; margin:0; font-size:1.5rem; }
          .ptnt-hero p { color: #e0f2fe; margin:.3rem 0 0; font-size:.9rem; }
          .stTabs [data-baseweb="tab-list"] { gap: 4px; }
          .stTabs [data-baseweb="tab"] {
            background:#1e293b; border-radius:10px 10px 0 0; padding:8px 16px;
          }
          .stTabs [aria-selected="true"] { background:#2563eb; color:white; }
          .stDataFrame { border:1px solid #334155; border-radius:10px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _check_auth(cfg) -> bool:
    """Autenticación simple usuario/contraseña contra el almacén de usuarios."""

    if not cfg.seguridad.autenticacion_habilitada:
        # Sin autenticación no hay a quién atribuir un alcance. Se entra como
        # matriz porque es una instalación de prueba o de un solo usuario; en
        # producción la autenticación está activa y esto no ocurre.
        st.session_state.setdefault("usuario", "sin-autenticacion")
        st.session_state.setdefault("matriz", True)
        st.session_state.setdefault("unidades", [])
        st.session_state.setdefault("role", "admin")
        return True
    from ptnt.security.auth import UserStore

    if st.session_state.get("auth_ok"):
        return True
    st.title("PTNT-BAL — Acceso")
    with st.form("login"):
        usuario = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        ok = st.form_submit_button("Ingresar")
    if ok:
        store = UserStore(cfg.seguridad.ruta_usuarios)
        user = store.authenticate(usuario, password)
        if user and user.role in {"analyst", "admin"}:
            st.session_state["auth_ok"] = True
            st.session_state["role"] = user.role
            # El alcance se fija al entrar y no se vuelve a preguntar: que cada
            # pantalla lo consultara por su cuenta es justo como se acaba
            # olvidando en una de ellas.
            st.session_state["usuario"] = user.username
            st.session_state["unidades"] = list(user.unidades)
            st.session_state["matriz"] = bool(user.matriz)
            st.rerun()
        else:
            st.error("Credenciales inválidas o rol sin permiso de análisis.")
    return False


def _alcance():
    """El alcance de quien está dentro, tal como quedó al autenticarse."""

    from ptnt.security.scope import Alcance

    return Alcance(usuario=st.session_state.get("usuario", ""),
                   unidades=frozenset(st.session_state.get("unidades", [])),
                   matriz=bool(st.session_state.get("matriz", False)))


def _barra_lateral(cfg, alcance):
    """Quién está dentro, qué alcanza y —si es matriz— sobre qué quiere mirar."""

    with st.sidebar:
        st.markdown(f"### 👤 {alcance.usuario or 'invitado'}")
        st.caption(alcance.descripcion())

        elegida = None
        if alcance.matriz:
            unidades = []
            ruta = cfg.organizacion.catalogo or ""
            if ruta and Path(ruta).exists():
                from ptnt.org.hierarchy import load_jerarquia
                unidades = load_jerarquia(ruta).unidades
            if unidades:
                # La matriz **elige**, no acumula: mirar todas a la vez y mirar
                # una concreta son dos preguntas distintas.
                elegida = st.selectbox("Ver los datos de",
                                       ["Todas las unidades", *unidades])
                elegida = None if elegida == "Todas las unidades" else elegida
        elif alcance.sin_alcance:
            st.error("No tiene ninguna unidad de negocio asignada, así que no "
                     "verá datos. Pídale a un administrador que se la asigne.")

        st.divider()
        st.caption("¿Perdido? Empiece por **Actualizar los números** para "
                   "calcular, y luego **Dónde inspeccionar** para ver a dónde ir.")
        if st.button("Salir"):
            for k in ("auth_ok", "usuario", "unidades", "matriz", "role"):
                st.session_state.pop(k, None)
            st.rerun()
    return elegida


_COLS_ALIMENTADOR = ("alimentador", "feeder_code", "feeder", "codigo_alimentador")


@st.cache_data(show_spinner=False)
def _mapa_unidades(ruta_catalogo: str) -> dict:
    """Alimentador → unidad de negocio, del catálogo organizacional."""

    if not ruta_catalogo or not Path(ruta_catalogo).exists():
        return {}
    from ptnt.org.hierarchy import load_jerarquia

    jer = load_jerarquia(ruta_catalogo)
    return {c: a.unidad_negocio for c, a in jer.alimentadores.items()}


def _con_unidad(df, mapa: dict):
    """Añade ``unidad_negocio`` deduciéndola del alimentador de cada fila.

    Los resultados se guardan por alimentador, no por unidad de negocio. Sin
    este paso el filtro no tendría por dónde agarrar y —al fallar cerrado—
    dejaría todas las tablas vacías: la protección existiría sobre el papel y el
    tablero no serviría para nada.
    """

    if df is None or not hasattr(df, "columns") or df.empty or not mapa:
        return df
    if "unidad_negocio" in df.columns:
        return df
    col = next((c for c in _COLS_ALIMENTADOR if c in df.columns), None)
    if col is None:
        return df
    return df.assign(
        unidad_negocio=df[col].astype(str).map(mapa).fillna(""))


def _filtrar(df, alcance, mapa: dict, unidad_elegida=None):
    """Deja solo lo que este usuario puede ver.

    Se aplica **a cada tabla que se pinta**, no una sola vez al cargar: los
    resultados son archivos ya calculados con todas las unidades dentro, y una
    pestaña nueva que se olvide de filtrar mostraría el padrón de otra unidad.
    """

    if df is None or not hasattr(df, "columns") or df.empty:
        return df
    out = alcance.filtrar(_con_unidad(df, mapa))
    if unidad_elegida is not None and "unidad_negocio" in getattr(out, "columns", []):
        out = out[out["unidad_negocio"].astype(str) == str(unidad_elegida)]
    return out


def main() -> None:
    args = _args()
    cfg = load_config(args.config)
    st.set_page_config(page_title="PTNT-BAL", layout="wide", page_icon="⚡")

    _inject_theme()
    if not _check_auth(cfg):
        return

    from ptnt.dashboard.paneles import (panel_ejecucion, panel_escenarios,
                                        panel_tareas)

    alcance = _alcance()
    unidad_elegida = _barra_lateral(cfg, alcance)
    mapa = _mapa_unidades(cfg.organizacion.catalogo or "")
    usuario = st.session_state.get("usuario", "")
    es_admin = st.session_state.get("role") == "admin"

    data = _load_outputs(cfg.rutas.salidas)
    if "ranking_clientes" not in data:
        # Antes esto era un callejón sin salida: decía «ejecute este comando» a
        # quien había entrado por el navegador justamente para no escribir
        # comandos. Ahora se le ofrece el botón.
        st.markdown(
            """
            <div class="ptnt-hero">
              <h1>⚡ PTNT-BAL</h1>
              <p>Todavía no hay resultados calculados. Empiece por aquí.</p>
            </div>
            """, unsafe_allow_html=True)
        st.info("**Es la primera vez que se usa.** Pulse *Empezar* abajo para "
                "traer los datos y calcular. Tarda unos minutos y puede cerrar "
                "la ventana mientras tanto.")
        panel_ejecucion(cfg, args.config, usuario)
        return

    ranking = _filtrar(data["ranking_clientes"], alcance, mapa, unidad_elegida)
    met = data.get("metricas", {})

    if ranking is not None and ranking.empty and not alcance.matriz:
        st.warning(
            "No hay resultados de su unidad de negocio. O todavía no se ha "
            "calculado, o los datos cargados son de otra unidad.")

    ambito = (unidad_elegida or
              ("todas las unidades" if alcance.matriz
               else ", ".join(sorted(alcance.unidades)) or "sin unidad asignada"))
    st.markdown(
        f"""
        <div class="ptnt-hero">
          <h1>⚡ PTNT-BAL — Pérdidas No Técnicas y Balance Energético</h1>
          <p>Viendo: {ambito} ·
             Análisis de consumo multi-mes, recálculo de potencia y detección de hurto</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -- Portada / línea base ------------------------------------------------
    # Las cifras salen de lo que este usuario alcanza, no del archivo global:
    # enseñarle a un analista de una unidad el total de la empresa lo llevaría a
    # informar un número que no es el suyo.
    propio = alcance.matriz and unidad_elegida is None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cuentas analizadas",
              f"{met.get('n_cuentas', len(ranking)) if propio else len(ranking):,}")
    c2.metric("Meses de consumo", met.get("n_meses", "-"))
    if propio:
        n_sosp = met.get("n_sospechosos", 0)
    else:
        col = next((c for c in ("es_sospechoso", "sospechoso")
                    if c in getattr(ranking, "columns", [])), None)
        n_sosp = int(ranking[col].astype(bool).sum()) if col else "-"
    c3.metric("Clientes sospechosos",
              f"{n_sosp:,}" if isinstance(n_sosp, int) else n_sosp)
    dp = met.get("delta_p_total_pct")
    c4.metric("Δ Potencia (SIG→corregido)", f"{dp:.1f}%" if dp is not None else "-")

    st.caption(
        f"Método de promedio: **{met.get('metodo_promedio','-')}** "
        f"(ventana {met.get('ventana_meses','-')} meses) · "
        f"Método de demanda: **{met.get('metodo_demanda','-')}**"
    )

    (tab_run, tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8,
     tab_esc, tab_prog) = st.tabs(
        ["▶️ Actualizar los números", "📍 Dónde inspeccionar",
         "🎯 Sospecha de hurto",
         "🔌 Reconciliación de potencia", "📈 Cliente", "⚖️ Balance de red",
         "🏢 Unidad y subestación", "📅 Histórico", "📥 Carga de datos",
         "📱 Trabajo de campo", "🧪 Probar cambios", "⏰ Automático"]
    )

    with tab_run:
        panel_ejecucion(cfg, args.config, usuario)

    with tab_esc:
        panel_escenarios(cfg, args.config, usuario, alcance)

    with tab_prog:
        panel_tareas(cfg, args.config, usuario, es_admin)

    # -- V7: focalización de levantamientos (§11.5) --------------------------
    with tab0:
        st.subheader("Plan de levantamientos: dónde ir a inspeccionar")
        plan_path = Path(cfg.rutas.salidas) / "plan_levantamientos.json"
        ot_path = Path(cfg.rutas.salidas) / "ordenes_levantamiento.csv"
        if not plan_path.exists():
            st.info("Sin plan de focalización. Ejecute: `ptnt focalizar`.")
        else:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            res = plan.get("resumen", {})
            objetivos = pd.DataFrame(plan.get("objetivos", []))

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Objetivos priorizados", f"{res.get('n_objetivos', 0):,}")
            c2.metric("kWh/mes recuperables", f"{res.get('recuperable_total_kwh_mes', 0):,.0f}")
            c3.metric("Problema de datos", f"{res.get('objetivos_con_problema_datos', 0):,}",
                      help="Score alto en zona de baja confiabilidad: corregir datos antes de inspeccionar")
            if ot_path.exists():
                ot = _filtrar(pd.read_csv(ot_path), alcance, mapa, unidad_elegida)
                c4.metric("Órdenes de trabajo", f"{len(ot):,}")
            else:
                ot = pd.DataFrame()

            if not ot.empty:
                st.markdown("#### 📋 Órdenes de levantamiento (por rendimiento por visita)")
                st.caption(
                    f"{int(ot['clientes_a_revisar'].sum()):,} clientes cubiertos en "
                    f"{len(ot)} visitas · {ot['kwh_por_visita'].sum():,.0f} kWh/mes en juego"
                )
                st.dataframe(
                    ot[[c for c in ["orden_trabajo", "nivel", "entidad", "accion",
                                    "clientes_a_revisar", "kwh_por_visita",
                                    "motivo_principal"] if c in ot.columns]],
                    use_container_width=True, hide_index=True,
                )
                st.download_button("Descargar órdenes (CSV)",
                                   ot.to_csv(index=False).encode("utf-8"),
                                   "ordenes_levantamiento.csv", "text/csv")

            if not objetivos.empty:
                st.markdown("#### Objetivos por nivel")
                niveles = objetivos["nivel"].unique().tolist()
                sel = st.selectbox("Nivel de focalización", niveles)
                sub = objetivos[objetivos["nivel"] == sel].head(100)
                st.dataframe(
                    sub[[c for c in ["orden", "entidad", "alimentador", "prioridad",
                                     "recuperable_kwh_mes", "clientes", "red_km",
                                     "accion", "razon_1"] if c in sub.columns]],
                    use_container_width=True, hide_index=True,
                )
                # Mapa de los objetivos con coordenadas
                geo = sub.dropna(subset=["x", "y"]) if {"x", "y"}.issubset(sub.columns) else pd.DataFrame()
                if not geo.empty:
                    st.markdown("#### Ubicación de los objetivos")
                    st.map(pd.DataFrame({"lat": geo["y"] / 1e5, "lon": geo["x"] / 1e5}),
                           size=20)
                    st.caption("Coordenadas UTM 17S normalizadas para vista rápida; "
                               "el export lleva las coordenadas reales.")

    # -- V7: sectores de sospecha -------------------------------------------
    with tab1:
        st.subheader("Ranking de clientes por sospecha de PNT")
        n = st.slider("Mostrar top-N", 10, min(500, len(ranking)), 50)
        vista = ranking.head(n).copy()
        vista["razon_principal"] = vista["razones"].apply(
            lambda r: r[0] if isinstance(r, (list, tuple)) and r else "-"
        )
        cols = ["rank", "contract_account", "score", "n_senales_activas",
                "recuperable_kwh_mes", "razon_principal"]
        cols = [c for c in cols if c in vista.columns]
        st.dataframe(vista[cols], use_container_width=True, hide_index=True)
        st.download_button(
            "Descargar ranking (CSV)",
            ranking.to_csv(index=False).encode("utf-8"),
            "ranking_clientes.csv",
            "text/csv",
        )

    # -- V4/E6: reconciliación ----------------------------------------------
    with tab2:
        st.subheader("Informe de reconciliación de potencia (SIG vs corregido)")
        if "reconciliacion" in data:
            rec = data["reconciliacion"]
            st.caption(
                "El SIG calcula la potencia con el consumo del **último mes**; "
                "el sistema la recalcula con el **promedio multi-mes** y la fórmula "
                "correcta por fase. La diferencia es material sobre todo en clientes "
                "no residenciales."
            )
            c1, c2 = st.columns(2)
            c1.metric("Σ P previa (SIG)", f"{rec['p_kw_previous'].sum():,.0f} kW")
            c2.metric("Σ P corregida", f"{rec['p_kw_corrected'].sum():,.0f} kW")
            peores = rec.reindex(
                rec["delta_p_kw"].abs().sort_values(ascending=False).index
            ).head(30)
            st.dataframe(peores, use_container_width=True, hide_index=True)
        else:
            st.info("Sin datos de reconciliación.")

    # -- V7 detalle de cliente ----------------------------------------------
    with tab3:
        st.subheader("Detalle de cliente")
        st.info(
            "Seleccione una cuenta del ranking para ver su serie de consumo. "
            "(La serie completa se sirve desde DuckDB en la instalación operativa.)"
        )
        if ranking.empty:
            # Puede pasar sin que nada esté roto: un analista cuya unidad de
            # negocio todavía no se ha cargado. Antes esto tumbaba el tablero
            # entero con un IndexError.
            st.warning("No hay clientes que usted pueda consultar.")
        else:
            cuenta = st.selectbox("Cuenta contrato",
                                  ranking["contract_account"].head(200))
            coincide = ranking[ranking["contract_account"] == cuenta]
            fila = coincide.iloc[0]
            st.write("**Score de sospecha:**", round(float(fila["score"]), 3))
            st.write("**Señales activas:**", int(fila["n_senales_activas"]))
            st.write("**Razones:**")
            razones = fila["razones"]
            if isinstance(razones, str):
                razones = [r.strip() for r in razones.split(",") if r.strip()]
            for r in (razones if isinstance(razones, (list, tuple)) else []):
                st.write(f"- {r}")

    # -- V4/V5: balance energético y pérdidas técnicas (pipeline de red) -----
    with tab4:
        st.subheader("Balance energético y pérdidas técnicas (pipeline de red)")
        bpath = Path(cfg.rutas.salidas) / "balance_red.json"
        if not bpath.exists():
            st.info("Sin balance de red. Ejecute: `ptnt analizar-red`.")
        else:
            bal = json.loads(bpath.read_text(encoding="utf-8"))
            tipo = bal.get("balance_type", "-")
            if tipo == "INDICATIVO":
                st.warning("Balance **INDICATIVO** (sin medición de cabecera): "
                           "la PNT no es verificable.")
            else:
                st.success("Balance **MEDIDO** (con medición de cabecera).")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entrada (cabecera)", f"{bal.get('e_input_kwh',0):,.0f} kWh")
            c2.metric("Facturado", f"{bal.get('e_billed_kwh',0):,.0f} kWh")
            c3.metric("Pérdidas técnicas", f"{bal.get('loss_technical_kwh',0):,.0f} kWh",
                      help=f"P10–P90: {bal.get('loss_technical_p10',0):,.0f}–{bal.get('loss_technical_p90',0):,.0f}")
            c4.metric("PNT", f"{bal.get('ntl_kwh',0):,.0f} kWh ({bal.get('ntl_pct',0):.1f}%)",
                      help=f"P10–P90: {bal.get('ntl_p10',0):,.0f}–{bal.get('ntl_p90',0):,.0f} kWh")

            c5, c6, c7 = st.columns(3)
            c5.metric("Alumbrado público no medido", f"{bal.get('ap_unmetered_kwh',0):,.0f} kWh",
                      help="Incluye luminarias, semáforos y cámaras (por regulación)")
            c6.metric("Motor de flujo", bal.get("engine", "-"))
            c7.metric("Zonas BT (por transformador)", bal.get("n_lv_zones", "-"))

            comp = bal.get("loss_components", {})
            if comp:
                st.write("**Pérdidas técnicas por componente (kWh):**")
                st.bar_chart(pd.Series(comp))
            trig = bal.get("controls_triggered", [])
            if trig:
                st.error(f"Controles de coherencia disparados: {', '.join(trig)}")
            else:
                st.caption("Controles de coherencia C01–C06: todos OK")

            # Señales N3 (balance de totalizador) — la señal de hurto más limpia
            tot = bal.get("totalizer_signals", [])
            if tot:
                st.write("**Señal N3 — balance de totalizador (diferencia totalizador − individuales):**")
                st.dataframe(pd.DataFrame(tot), use_container_width=True, hide_index=True)


    # -- V10: jerarquía organizacional (UN → subestación → alimentador) -------
    with tab5:
        st.subheader("Consolidado por unidad de negocio y subestación")
        st.caption(
            "El presupuesto se decide por unidad de negocio y la operación por "
            "subestación. La energía se suma hacia arriba; la **credibilidad no**: "
            "basta un alimentador sin medición confiable para que el consolidado "
            "no pueda presentarse como MEDIDO."
        )
        bal_path = Path(cfg.rutas.salidas) / "balance_alimentadores.csv"
        if not bal_path.exists():
            st.info(
                "Sin balance por alimentador. Ejecute `ptnt analizar-red` para "
                "cada alimentador, o la prueba a escala "
                "`python scripts/prueba_costa_20k.py`."
            )
        else:
            from ptnt.org import (
                agregar_balance, jerarquia_desde_alimentadores, load_jerarquia,
            )

            df_bal = _filtrar(pd.read_csv(bal_path), alcance, mapa,
                              unidad_elegida)
            if cfg.organizacion.catalogo and Path(cfg.organizacion.catalogo).exists():
                jer = load_jerarquia(cfg.organizacion.catalogo)
            else:
                jer = jerarquia_desde_alimentadores(
                    df_bal["alimentador"].astype(str).tolist(),
                    separador=cfg.organizacion.separador_codigo)
                for adv in jer.advertencias:
                    st.warning(adv)

            agregados = agregar_balance(df_bal, jer)

            nivel = st.radio(
                "Nivel de consolidación", ["UNIDAD_NEGOCIO", "SUBESTACION",
                                           "ALIMENTADOR"],
                horizontal=True, key="nivel_org")
            d = agregados[nivel]

            if "tipo_balance" in d.columns:
                no_medidos = int((d["tipo_balance"] != "MEDIDO").sum())
                if no_medidos:
                    st.error(
                        f"{no_medidos} de {len(d)} consolidados NO son MEDIDO. "
                        "Su PNT es una estimación y no debe presentarse como "
                        "verificada."
                    )
                else:
                    st.success("Todos los consolidados de este nivel son MEDIDO.")

            if {"pnt_kwh", "entrada_kwh"} <= set(d.columns):
                c1, c2, c3 = st.columns(3)
                c1.metric("Entrada total", f"{d['entrada_kwh'].sum():,.0f} kWh")
                c2.metric("PNT total", f"{d['pnt_kwh'].sum():,.0f} kWh")
                pct = (d["pnt_kwh"].sum() / max(d["entrada_kwh"].sum(), 1e-9) * 100)
                c3.metric("PNT global", f"{pct:.2f} %")

            st.dataframe(d, use_container_width=True, hide_index=True)
            if "pnt_pct" in d.columns:
                etq = ("unidad_negocio" if nivel == "UNIDAD_NEGOCIO"
                       else "subestacion" if nivel == "SUBESTACION"
                       else "alimentador")
                if etq in d.columns:
                    st.bar_chart(d.set_index(etq)["pnt_pct"], height=280)
            st.download_button(
                f"Descargar consolidado {nivel} (CSV)",
                d.to_csv(index=False).encode("utf-8"),
                file_name=f"consolidado_{nivel.lower()}.csv", mime="text/csv")

            with st.expander("Catálogo organizacional en uso"):
                st.dataframe(jer.to_dataframe(), use_container_width=True,
                             hide_index=True)

    # -- V11: histórico de balance -------------------------------------------
    with tab6:
        st.subheader("Evolución histórica de la PNT")
        st.caption(
            "Una foto no sirve para gestionar: lo que dice si un plan funciona es "
            "la serie. Y un salto que coincide con una recarga de datos es un "
            "problema de datos, no un hurto masivo."
        )
        from ptnt.store.history import HistoricoBalance

        hist = HistoricoBalance.load(cfg.historico.ruta)
        if hist.df.empty:
            st.info(
                "Histórico vacío. Cada corrida de `ptnt analizar-red` con "
                "`historico.habilitado: true` agrega una instantánea."
            )
        else:
            for adv in hist.advertencias():
                st.warning(adv)

            c1, c2, c3 = st.columns(3)
            c1.metric("Períodos registrados", f"{len(hist.periodos):,}")
            c2.metric("Instantáneas", f"{len(hist.df):,}")
            c3.metric("Entidades", f"{hist.df['entidad'].nunique():,}")

            nivel_h = st.selectbox(
                "Nivel", sorted(hist.df["nivel"].dropna().unique()), key="nivel_hist")
            ents = sorted(hist.df.loc[hist.df["nivel"] == nivel_h,
                                      "entidad"].dropna().unique())
            sel = st.multiselect("Entidades a graficar", ents, default=ents[:5])
            metrica = st.radio("Métrica", ["pnt_pct", "pnt_kwh",
                                           "perdidas_totales_kwh"],
                               horizontal=True, key="met_hist")
            if sel:
                series = {}
                for e in sel:
                    s_e = hist.serie(e, nivel=nivel_h, metrica=metrica)
                    if not s_e.empty:
                        series[e] = s_e.set_index("periodo")[metrica]
                if series:
                    st.line_chart(pd.DataFrame(series), height=320)

            if len(hist.periodos) >= 2:
                st.markdown("#### Comparación entre períodos")
                cc1, cc2 = st.columns(2)
                pa = cc1.selectbox("Período base", hist.periodos,
                                   index=0, key="per_a")
                pb = cc2.selectbox("Período a comparar", hist.periodos,
                                   index=len(hist.periodos) - 1, key="per_b")
                comp = hist.comparar_periodos(pa, pb, nivel=nivel_h)
                if comp.empty:
                    st.info("Sin datos comunes entre esos períodos.")
                else:
                    incomparables = int((~comp["comparable"]).sum())
                    if incomparables:
                        st.warning(
                            f"{incomparables} entidad(es) se calcularon con "
                            "configuraciones distintas: esa variación NO es una "
                            "tendencia de la red."
                        )
                    st.dataframe(comp, use_container_width=True, hide_index=True)

    # -- V12: carga parcial de información -----------------------------------
    with tab7:
        st.subheader("Carga de información y cobertura")
        st.caption(
            "La información no llega toda junta. Aquí se declara el alcance de "
            "cada carga para que un consolidado incompleto se marque **PARCIAL** "
            "en vez de leerse como el total de la entidad."
        )
        from ptnt.ingest import AlcanceCarga, Insumo

        alcance = AlcanceCarga.load(cfg.carga_parcial.ruta_alcance)
        if cfg.carga_parcial.universo_alimentadores:
            alcance.universo_alimentadores = list(
                cfg.carga_parcial.universo_alimentadores)

        resumen_cob = alcance.resumen()
        completos = int((resumen_cob["estado"] == "COMPLETA").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Insumos completos", f"{completos}/{len(resumen_cob)}")
        c2.metric("Universo declarado",
                  f"{len(alcance.universo_alimentadores):,} alimentadores")
        c3.metric("Listos para balance MEDIDO",
                  f"{len(alcance.listos_para_balance_medido()):,}",
                  help="Requiere padrón + red + cabecera del mismo alimentador")

        st.dataframe(resumen_cob, use_container_width=True, hide_index=True)

        pend = alcance.pendientes()
        if not pend.empty:
            st.markdown("#### Pendientes por cargar")
            st.caption("Es la lista de trabajo: qué insumo falta de qué alimentador.")
            st.dataframe(pend.head(200), use_container_width=True, hide_index=True)
            st.download_button("Descargar pendientes (CSV)",
                               pend.to_csv(index=False).encode("utf-8"),
                               file_name="pendientes_carga.csv", mime="text/csv")

        st.markdown("#### Registrar una carga")
        with st.form("registrar_carga"):
            f1, f2 = st.columns(2)
            insumo_sel = f1.selectbox("Insumo cargado", [i.value for i in Insumo])
            origen = f2.text_input("Origen (archivo, base, responsable)", "")
            alis = st.text_area(
                "Alimentadores incluidos (uno por línea o separados por coma)", "")
            g1, g2 = st.columns(2)
            desde = g1.text_input("Período desde (YYYY-MM)", "")
            hasta = g2.text_input("Período hasta (YYYY-MM)", "")
            if st.form_submit_button("Registrar carga"):
                lista = [a.strip() for a in alis.replace(",", "\n").split("\n")
                         if a.strip()]
                if not lista:
                    st.error("Indique al menos un alimentador.")
                else:
                    carga = alcance.registrar(
                        Insumo(insumo_sel), lista, origen=origen,
                        periodo_desde=desde or None, periodo_hasta=hasta or None)
                    ruta = alcance.save(cfg.carga_parcial.ruta_alcance)
                    st.success(
                        f"Registrada: {len(lista)} alimentador(es) de "
                        f"{insumo_sel}. Alcance guardado en {ruta}.")
                    for adv in carga.advertencias:
                        st.warning(adv)

        with st.expander("Historial de cargas"):
            if alcance.cargas:
                st.dataframe(
                    pd.DataFrame([c.to_dict() for c in alcance.cargas])
                    [["insumo", "n_alimentadores", "registros", "periodo_desde",
                      "periodo_hasta", "origen", "cargado_en"]],
                    use_container_width=True, hide_index=True)
            else:
                st.caption("Sin cargas registradas todavía.")


    # -- V13: trabajo de campo (asignación, paquetes, revisión) ---------------
    with tab8:
        st.subheader("Trabajo de campo: asignar, entregar y revisar")
        st.caption(
            "El ciclo completo: el análisis dice dónde ir → se asignan órdenes a "
            "un técnico → se arma un GeoPackage con la red del área y la "
            "cartografía offline → el técnico edita sin señal → sube los cambios "
            "→ **el supervisor los revisa** → los aceptados actualizan el modelo "
            "y disparan el recálculo."
        )
        from ptnt.field import EstadoOrden, RegistroCampo, RolCampo
        from ptnt.field.sync import HistoricoCambios

        dir_campo = Path(cfg.rutas.salidas) / "campo"
        dir_campo.mkdir(parents=True, exist_ok=True)
        reg = RegistroCampo(dir_campo / "registro.json")

        sub_a, sub_b, sub_c, sub_d = st.tabs(
            ["👷 Técnicos", "📋 Asignar trabajo", "📦 Paquetes",
             "🔍 Revisar cambios"])

        # ---- técnicos ----
        with sub_a:
            st.markdown("#### Usuarios de la aplicación móvil")
            st.caption(
                "Se crean **aquí**, nunca en el dispositivo: quién puede editar "
                "la red es una decisión administrativa. Cada teléfono recibe un "
                "token revocable; si se pierde el equipo, se revoca y deja de "
                "sincronizar sin tocar la cuenta del técnico."
            )
            if reg.usuarios:
                st.dataframe(reg.resumen_por_usuario(),
                             use_container_width=True, hide_index=True)
            else:
                st.info("Aún no hay técnicos registrados.")

            with st.form("nuevo_tecnico"):
                c1, c2 = st.columns(2)
                nu = c1.text_input("Usuario")
                nn = c2.text_input("Nombre completo")
                c3, c4 = st.columns(2)
                nr = c3.selectbox("Rol", [r.value for r in RolCampo])
                nun = c4.text_input("Unidad de negocio", value=cfg.proyecto.unidad_negocio)
                np_ = st.text_input("Contraseña inicial", type="password",
                                    help="Mínimo 8 caracteres. Se guarda solo el hash.")
                if st.form_submit_button("Crear técnico"):
                    try:
                        reg.crear_usuario(nu, nn, np_, rol=RolCampo(nr),
                                          unidad_negocio=nun)
                        st.success(f"Técnico '{nu}' creado.")
                        st.rerun()
                    except (ValueError, KeyError) as exc:
                        st.error(str(exc))

            if reg.usuarios:
                cr1, cr2 = st.columns([3, 1])
                rev = cr1.selectbox("Revocar dispositivo de",
                                    sorted(reg.usuarios), key="rev_disp")
                if cr2.button("Revocar", type="secondary"):
                    reg.revocar_dispositivo(rev)
                    st.warning(f"Dispositivo de '{rev}' revocado.")

        # ---- asignación ----
        with sub_b:
            ot_path = Path(cfg.rutas.salidas) / "ordenes_levantamiento.csv"
            if not ot_path.exists():
                st.info("Sin órdenes de trabajo. Ejecute `ptnt focalizar` primero.")
            elif not reg.usuarios:
                st.info("Cree al menos un técnico en la pestaña anterior.")
            else:
                ot = _filtrar(pd.read_csv(ot_path), alcance, mapa,
                              unidad_elegida)
                asignadas = set(reg.asignaciones)
                ot["asignada_a"] = ot["orden_trabajo"].map(
                    lambda o: reg.asignaciones[o].asignado_a
                    if o in asignadas else "")
                st.markdown("#### Órdenes disponibles")
                st.caption(
                    "Ordenadas por energía recuperable por visita. Seleccione "
                    "varias y asígnelas de una vez: una cuadrilla sale con la "
                    "jornada completa, no con una orden.")
                cols = [c for c in ["orden_trabajo", "nivel", "entidad",
                                    "clientes_a_revisar", "kwh_por_visita",
                                    "accion", "asignada_a"] if c in ot.columns]
                st.dataframe(ot[cols], use_container_width=True, hide_index=True)

                libres = ot[ot["asignada_a"] == ""]["orden_trabajo"].tolist()

                modo_a, modo_b = st.tabs(
                    ["👥 Repartir entre varias cuadrillas", "👤 Asignar a una"])

                # -- reparto multiusuario --
                with modo_a:
                    st.caption(
                        "Reparte la jornada entre varios técnicos equilibrando la "
                        "carga **y** manteniendo cada grupo junto en el territorio. "
                        "Un reparto por sorteo manda a la misma cuadrilla a dos "
                        "extremos de la ciudad y el traslado se come las visitas."
                    )
                    tecnicos = st.multiselect(
                        "Cuadrillas", sorted(reg.usuarios),
                        default=sorted(reg.usuarios)[:3], key="rep_users")
                    r1, r2, r3, r4 = st.columns(4)
                    n_ot = r1.number_input("Órdenes del ranking", 1,
                                           max(1, len(libres)),
                                           min(30, max(1, len(libres))), 1)
                    crit = r2.selectbox("Equilibrar por",
                                        ["kwh", "clientes", "visitas"])
                    tope = r3.number_input("Tope por técnico (0 = sin tope)",
                                           0, 200, 0, 1)
                    beta = r4.slider("Cercanía ↔ carga pareja", 0.0, 4.0, 1.0, 0.5,
                                     help="0 agrupa por cercanía; alto iguala "
                                          "cargas aunque el recorrido se alargue.")

                    if tecnicos:
                        from ptnt.field import asignar_reparto, repartir_ordenes

                        base = ot[ot["orden_trabajo"].isin(libres)].head(int(n_ot))
                        rep = repartir_ordenes(
                            base, tecnicos, criterio=crit,
                            max_por_usuario=int(tope) or None,
                            peso_balance=float(beta))
                        st.dataframe(rep.resumen(), use_container_width=True,
                                     hide_index=True)
                        st.caption(
                            f"Desbalance entre la cuadrilla más y la menos "
                            f"cargada: **{rep.desbalance_pct:.1f} %** · "
                            f"dispersión media del recorrido: "
                            f"**{rep.resumen()['dispersion_km'].mean():.2f} km**")
                        for adv in rep.advertencias():
                            st.warning(adv)
                        radio_r = st.number_input("Radio del área (m)", 50, 2000,
                                                  150, 50, key="rep_radio")
                        if st.button("Aplicar reparto", type="primary"):
                            try:
                                hecho = asignar_reparto(
                                    reg, rep, asignado_por="dashboard",
                                    radio_m=float(radio_r))
                                st.success(
                                    f"{sum(len(v) for v in hecho.values())} "
                                    f"orden(es) repartidas entre "
                                    f"{len(tecnicos)} cuadrilla(s). Cada técnico "
                                    "las verá al conectarse desde la app.")
                                st.rerun()
                            except (ValueError, KeyError) as exc:
                                st.error(str(exc))
                    else:
                        st.info("Seleccione al menos una cuadrilla.")

                # -- asignación puntual --
                with modo_b:
                    sel = st.multiselect("Órdenes a asignar", libres,
                                         default=libres[:5])
                    ca1, ca2 = st.columns([3, 1])
                    dest = ca1.selectbox("Asignar a", sorted(reg.usuarios))
                    radio = ca2.number_input("Radio (m)", 50, 2000, 150, 50)
                    if st.button("Asignar seleccionadas", type="primary",
                                 disabled=not sel):
                        try:
                            nuevas = reg.asignar(
                                ot[ot["orden_trabajo"].isin(sel)], dest,
                                asignado_por="dashboard", radio_m=float(radio))
                            st.success(
                                f"{len(nuevas)} orden(es) asignadas a '{dest}' · "
                                f"{sum(a.clientes_a_revisar for a in nuevas):,} "
                                f"clientes · "
                                f"{sum(a.recuperable_kwh_mes for a in nuevas):,.0f} "
                                "kWh/mes en juego.")
                            st.rerun()
                        except (ValueError, KeyError) as exc:
                            st.error(str(exc))

        # ---- paquetes ----
        with sub_c:
            st.markdown("#### Generar el paquete descargable")
            st.caption(
                "Contiene **solo** la red del área de las órdenes asignadas, más "
                "el contexto topológico necesario. Bajar la red completa a un "
                "teléfono de gama baja lo vuelve inusable."
            )
            if not reg.asignaciones:
                st.info("Sin órdenes asignadas.")
            else:
                usuarios_con = sorted({a.asignado_a for a in reg.asignaciones.values()})
                up = st.selectbox("Técnico", usuarios_con, key="usr_paq")
                asigs = reg.de_usuario(up)
                st.write(f"**{len(asigs)} orden(es)** · "
                         f"{sum(a.clientes_a_revisar for a in asigs):,} clientes")
                st.dataframe(pd.DataFrame([{
                    "orden": a.orden_trabajo, "nivel": a.nivel,
                    "entidad": a.entidad, "estado": a.estado.value,
                    "clientes": a.clientes_a_revisar,
                } for a in asigs]), use_container_width=True, hide_index=True)

                st.caption(
                    "La generación del paquete requiere la red migrada. Use "
                    "`ptnt campo-paquete --usuario " + up + "` desde la línea de "
                    "comandos, o el script de demostración "
                    "`scripts/demo_campo.py`.")

                paq = dir_campo / "paquetes" / f"{up}.gpkg"
                if paq.exists():
                    st.success(f"Paquete disponible: {paq.name} "
                               f"({paq.stat().st_size/1e6:.1f} MB)")
                    with open(paq, "rb") as fh:
                        st.download_button("Descargar .gpkg", fh.read(),
                                           file_name=paq.name,
                                           mime="application/geopackage+sqlite3")

        # ---- revisión ----
        with sub_d:
            st.markdown("#### Cambios recibidos del campo")
            st.caption(
                "**Nada entra al modelo sin revisión.** Un técnico puede "
                "equivocarse de elemento o capturar con el GPS derivando: "
                "aceptar a ciegas degradaría el SIG en vez de mejorarlo."
            )
            lotes_dir = dir_campo / "lotes"
            lotes = sorted(lotes_dir.glob("*.json")) if lotes_dir.exists() else []
            if not lotes:
                st.info("Sin lotes pendientes de revisión.")
            else:
                nombres = [l.stem for l in lotes]
                sel_l = st.selectbox("Lote", nombres)
                datos = json.loads(
                    (lotes_dir / f"{sel_l}.json").read_text(encoding="utf-8"))

                c1, c2, c3 = st.columns(3)
                c1.metric("Cambios", len(datos.get("cambios", [])))
                c2.metric("Fotos", len(datos.get("fotos", [])))
                c3.metric("Técnico", datos.get("usuario", "-"))

                for h in datos.get("hallazgos", []):
                    if h["severidad"] == "BLOQUEANTE":
                        st.error(f"[{h['codigo']}] {h['detalle']}")
                    else:
                        st.warning(f"[{h['codigo']}] {h['detalle']}")

                camb = pd.DataFrame(datos.get("cambios", []))
                if not camb.empty:
                    cols = [c for c in ["secuencia", "capa", "elemento_guid",
                                        "operacion", "campo", "valor_antes",
                                        "valor_despues", "propagado_de",
                                        "precision_m", "motivo"]
                            if c in camb.columns]
                    st.dataframe(camb[cols], use_container_width=True,
                                 hide_index=True)
                    st.caption(
                        "Los cambios con `propagado_de` no los hizo el técnico "
                        "directamente: se movieron porque se movió otro elemento. "
                        "Aceptar el origen y rechazar el propagado dejaría la red "
                        "desconectada en ese punto.")

                st.caption(
                    "La aceptación se realiza con `ptnt campo-revisar "
                    f"--lote {sel_l}`, que aplica los cambios y dispara el "
                    "recálculo de las etapas afectadas.")

            hist_c = HistoricoCambios(dir_campo / "historico_cambios.parquet")
            if not hist_c.df.empty:
                st.markdown("#### Histórico de modificaciones de la red")
                st.caption(
                    "Acumula tanto las ediciones de campo como las cargas desde "
                    "archivo: la pregunta de auditoría es siempre la misma —"
                    "*¿quién cambió esto, cuándo y por qué?*— y la respuesta no "
                    "puede depender de por qué puerta entró el cambio.")
                st.dataframe(hist_c.resumen_por_origen(),
                             use_container_width=True, hide_index=True)
                mas = hist_c.elementos_mas_editados(10)
                if not mas.empty:
                    st.markdown("**Elementos que más cambian**")
                    st.caption(
                        "Casi siempre son un problema de datos de origen, no una "
                        "red que se modifica tanto.")
                    st.dataframe(mas, use_container_width=True, hide_index=True)


if __name__ == "__main__":  # pragma: no cover
    main()
else:
    # Streamlit ejecuta el módulo como script
    main()
