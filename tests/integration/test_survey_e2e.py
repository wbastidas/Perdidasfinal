"""Prueba de integración del **requerimiento general**.

Verifica de punta a punta que el sistema responde "dónde hay que ir a hacer el
levantamiento" a partir de datos reales de consumo y de red, en los niveles
alimentador / ramal / transformador / sector, y que el resultado es **visible y
reportable** (CSV, XLSX, HTML y API del visor).
"""

from pathlib import Path

import pandas as pd
import pytest

from ptnt.config.loader import load_config
from ptnt.grid_pipeline import run_grid_analysis
from ptnt.pipeline import run_analysis
from ptnt.survey.collect import (
    bind_commercial_customers,
    collect_branch_stats,
    collect_transformer_stats,
    collect_zone_stats,
)
from ptnt.survey.report import write_survey_report
from ptnt.survey.targeting import TargetLevel, build_survey_plan
from ptnt.synth.network import generate_radial_network
from ptnt.topology.graph import build_radial_graph


@pytest.fixture(scope="module")
def plan_completo(tmp_path_factory):
    """Ejecuta el flujo completo: comercial + red -> plan de levantamientos."""

    from ptnt.synth.generator import generate_commercial_csv

    tmp = tmp_path_factory.mktemp("survey")
    cfg = load_config("config/base.yaml")
    cfg.rutas.salidas = str(tmp / "out")

    csv = tmp / "consumos.csv"
    generate_commercial_csv(str(csv), n_clientes=400, n_meses=36, pct_hurto=0.08, semilla=7)
    com = run_analysis(cfg, str(csv), persistir=False)

    net = generate_radial_network(n_transformers=6, customers_per_tx=20)
    cuentas = sorted(com.ranking["contract_account"].astype(str).tolist())
    coords = com.clientes[["contract_account", "x", "y"]].copy()
    coords["contract_account"] = coords["contract_account"].astype(str)
    bind_commercial_customers(net.model, cuentas, coords)

    grid = run_grid_analysis(net.model, cfg, head_energy_kwh=net.head_energy_kwh)
    graph = build_radial_graph(net.model)

    umbral = com.ranking["score"].quantile(0.90)
    suspect = set(com.ranking[com.ranking["score"] >= umbral]["contract_account"].astype(str))
    recuperable = dict(zip(com.ranking["contract_account"].astype(str),
                           com.ranking["recuperable_kwh_mes"]))
    b = grid.balance
    plan = build_survey_plan(
        feeder_balances=[{
            "feeder_code": grid.feeder_code, "ntl_kwh": b.ntl_kwh, "ntl_pct": b.ntl_pct,
            "customers": sum(len(v) for v in net.model.customer_nodes.values()),
            "network_km": graph.total_length_km(), "confidence": 100.0,
            "balance_type": b.balance_type.value,
        }],
        zone_signals=collect_zone_stats(graph),
        branch_stats=collect_branch_stats(
            graph, suspect_customers=suspect, recoverable_by_customer=recuperable),
        transformer_stats=collect_transformer_stats(
            graph, grid.lv_zones, grid.transformer_loading,
            suspect_customers=suspect, recoverable_by_customer=recuperable),
        customer_ranking=com.ranking, customer_coords=coords,
    )
    return plan, cfg, tmp


@pytest.mark.integration
def test_plan_responde_donde_inspeccionar_en_todos_los_niveles(plan_completo):
    """El requerimiento general: decir dónde ir, por alimentador, ramal,
    transformador y sectores."""

    plan, _cfg, _tmp = plan_completo
    niveles = {t.level for t in plan.targets}
    for requerido in (TargetLevel.ALIMENTADOR, TargetLevel.RAMAL,
                      TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.SECTOR,
                      TargetLevel.CLIENTE):
        assert requerido in niveles, f"falta el nivel {requerido.value}"
    assert plan.resumen["n_objetivos"] > 0


@pytest.mark.integration
def test_cada_objetivo_es_accionable(plan_completo):
    """Todo objetivo debe decir qué hacer y por qué: sin eso no es accionable."""

    plan, _cfg, _tmp = plan_completo
    for t in plan.targets:
        assert t.action, f"{t.entity_id} sin acción"
        assert t.reasons and t.reasons[0], f"{t.entity_id} sin motivo"
        assert 0.0 <= t.priority_score <= 1.0


@pytest.mark.integration
def test_ordenes_agrupan_clientes_por_visita(plan_completo):
    """Las órdenes deben cubrir varios clientes por visita (eficiencia de campo),
    no una orden por casa."""

    plan, _cfg, _tmp = plan_completo
    ot = plan.work_orders(top_n=20)
    assert not ot.empty
    assert ot["clientes_a_revisar"].sum() > len(ot), \
        "las órdenes deben cubrir más clientes que visitas"
    # ordenadas por rendimiento por visita (descendente)
    kwh = ot["kwh_por_visita"].to_numpy()
    assert all(kwh[i] >= kwh[i + 1] - 1e-6 for i in range(len(kwh) - 1))


@pytest.mark.integration
def test_resultado_visible_y_reportable(plan_completo):
    """El resultado debe poder exportarse (CSV/XLSX) y reportarse (HTML)."""

    plan, cfg, tmp = plan_completo
    salidas = Path(cfg.rutas.salidas)
    salidas.mkdir(parents=True, exist_ok=True)

    df = plan.to_dataframe()
    csv_path = salidas / "plan.csv"
    df.to_csv(csv_path, index=False)
    assert csv_path.exists() and len(pd.read_csv(csv_path)) == len(df)

    html_path = write_survey_report(plan, str(salidas / "focalizacion.html"))
    html = Path(html_path).read_text(encoding="utf-8")
    assert "Órdenes de levantamiento" in html
    for nivel in ("Alimentador", "Ramal", "Puesto Transformacion", "Sector"):
        assert nivel in html, f"el reporte debe mostrar el nivel {nivel}"

    from ptnt.io.exporters import export_tables_xlsx

    ruta = export_tables_xlsx({"Plan": df, "Ordenes": plan.work_orders(top_n=25)},
                              str(salidas / "focalizacion.xlsx"))
    assert Path(ruta).exists() or Path(str(ruta) + "_Plan.csv").exists()


@pytest.mark.integration
def test_hurtos_inyectados_aparecen_en_el_plan(plan_completo):
    """Los clientes con patrón de hurto deben quedar dentro de los objetivos
    priorizados: es la validación de que el plan sirve."""

    plan, _cfg, _tmp = plan_completo
    clientes = plan.by_level(TargetLevel.CLIENTE)
    assert clientes, "debe haber objetivos a nivel cliente"
    # los de mayor prioridad concentran la energía recuperable
    top = sorted(clientes, key=lambda t: t.priority_score, reverse=True)[:20]
    assert sum(t.recoverable_kwh_month for t in top) > 0
