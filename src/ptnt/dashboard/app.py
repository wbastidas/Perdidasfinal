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
            st.rerun()
        else:
            st.error("Credenciales inválidas o rol sin permiso de análisis.")
    return False


def main() -> None:
    args = _args()
    cfg = load_config(args.config)
    st.set_page_config(page_title="PTNT-BAL", layout="wide", page_icon="⚡")

    _inject_theme()
    if not _check_auth(cfg):
        return

    data = _load_outputs(cfg.rutas.salidas)
    if "ranking_clientes" not in data:
        st.warning(
            "No hay resultados calculados. Ejecute primero: "
            "`ptnt analizar --csv <archivo.csv>`"
        )
        return

    ranking = data["ranking_clientes"]
    met = data.get("metricas", {})

    st.markdown(
        f"""
        <div class="ptnt-hero">
          <h1>⚡ PTNT-BAL — Pérdidas No Técnicas y Balance Energético</h1>
          <p>Unidad de negocio {met.get('unidad_negocio', cfg.proyecto.unidad_negocio)} ·
             Análisis de consumo multi-mes, recálculo de potencia y detección de hurto</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -- Portada / línea base ------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cuentas analizadas", f"{met.get('n_cuentas', len(ranking)):,}")
    c2.metric("Meses de consumo", met.get("n_meses", "-"))
    c3.metric("Clientes sospechosos", f"{met.get('n_sospechosos', 0):,}")
    dp = met.get("delta_p_total_pct")
    c4.metric("Δ Potencia (SIG→corregido)", f"{dp:.1f}%" if dp is not None else "-")

    st.caption(
        f"Método de promedio: **{met.get('metodo_promedio','-')}** "
        f"(ventana {met.get('ventana_meses','-')} meses) · "
        f"Método de demanda: **{met.get('metodo_demanda','-')}**"
    )

    tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        ["📍 Dónde inspeccionar", "🎯 Sospecha de hurto",
         "🔌 Reconciliación de potencia", "📈 Cliente", "⚖️ Balance de red",
         "🏢 Unidad y subestación", "📅 Histórico", "📥 Carga de datos"]
    )

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
                ot = pd.read_csv(ot_path)
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
        cuenta = st.selectbox("Cuenta contrato", ranking["contract_account"].head(200))
        fila = ranking[ranking["contract_account"] == cuenta].iloc[0]
        st.write("**Score de sospecha:**", round(float(fila["score"]), 3))
        st.write("**Señales activas:**", int(fila["n_senales_activas"]))
        st.write("**Razones:**")
        for r in (fila["razones"] if isinstance(fila["razones"], (list, tuple)) else []):
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

            df_bal = pd.read_csv(bal_path)
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


if __name__ == "__main__":  # pragma: no cover
    main()
else:
    # Streamlit ejecuta el módulo como script
    main()
