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

    st.title("⚡ PTNT-BAL — Análisis de consumo y detección de hurto")

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

    tab1, tab2, tab3 = st.tabs(
        ["🎯 Sospecha de hurto", "🔌 Reconciliación de potencia", "📈 Cliente"]
    )

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


if __name__ == "__main__":  # pragma: no cover
    main()
else:
    # Streamlit ejecuta el módulo como script
    main()
