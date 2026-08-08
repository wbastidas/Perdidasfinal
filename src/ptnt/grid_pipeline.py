"""Pipeline de red eléctrica (etapas E4–E10).

Encadena: grafo radial → flujo de potencia → pérdidas técnicas por componente →
alumbrado público → balance jerárquico y PNT → cargabilidad. Opera sobre un
``NetworkModel`` (real o sintético).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from ptnt.balance.energy_balance import BalanceResult, compute_balance
from ptnt.config.models import AppConfig
from ptnt.grid.loadability import classify_loadability
from ptnt.lighting.streetlight import compute_streetlight_energy
from ptnt.load.demand import velander_max_demand
from ptnt.losses.conductors import segment_loss_kwh
from ptnt.losses.factors import loss_factor
from ptnt.losses.meters import meter_losses_kwh
from ptnt.losses.transformers import BankConfig, bank_capacity_kva, transformer_unit_loss_kwh
from ptnt.powerflow.bfs import run_powerflow
from ptnt.ref.catalogs import load_conductor_catalog, load_transformer_catalog
from ptnt.topology.graph import NetworkModel, build_radial_graph


@dataclass
class GridResult:
    feeder_code: str
    balance: BalanceResult
    loss_components_kwh: dict[str, float]
    transformer_loading: pd.DataFrame
    powerflow_converged: bool
    v_min_pu: float
    hours_period: float
    metrics: dict = field(default_factory=dict)


def run_grid_analysis(
    model: NetworkModel,
    cfg: AppConfig,
    *,
    head_energy_kwh: float | None = None,
    hours_period: float = 720.0,
) -> GridResult:
    """Ejecuta el pipeline de red para un alimentador."""

    conductors = load_conductor_catalog(cfg.catalogos.conductores)
    tx_catalog = load_transformer_catalog(cfg.catalogos.transformadores)
    graph = build_radial_graph(model)

    # --- factor de carga y factor de pérdidas ---
    clase0 = next(iter(cfg.carga.clases.values()))
    fc = clase0.factor_carga
    k = cfg.perdidas.k_por_tipo_alimentador.get(model.feeder_type, cfg.perdidas.k_por_defecto)
    fp = loss_factor(fc, k)

    # --- demanda por nodo (Velander individual, kW) ---
    node_load_kw: dict[str, float] = {}
    node_pf: dict[str, float] = {}
    for node, clientes in model.customer_nodes.items():
        p = 0.0
        for c in clientes:
            clase = cfg.carga.clases.get(c.get("tariff", ""), clase0)
            p += float(velander_max_demand(c["energy_kwh"], clase.a, clase.b))
        node_load_kw[node] = p
        node_pf[node] = clase0.cos_phi

    # --- flujo de potencia ---
    logger.info("Flujo de potencia ({} nodos)", graph.n_nodes)
    pf = run_powerflow(
        graph, node_load_kw, node_pf, conductors, cfg.flujo,
        t_op=cfg.perdidas.temperatura_operacion_c,
        alpha=cfg.perdidas.alpha_aluminio,
        t_ref=cfg.perdidas.temperatura_referencia_c,
    )

    # --- pérdidas en conductores (energía) ---
    loss_conductors_kwh = segment_loss_kwh(pf.loss_kw_peak, hours_period, fp)

    # --- pérdidas en transformadores ---
    asignacion = graph.assign_customers_to_transformers()
    energia_por_site: dict[str, float] = {}
    for node, clientes in model.customer_nodes.items():
        for c in clientes:
            site = asignacion.get(c["customer_id"])
            if site:
                energia_por_site[site] = energia_por_site.get(site, 0.0) + c["energy_kwh"]

    loss_tx_kwh = 0.0
    loss_tx_noload_kwh = 0.0
    tx_rows = []
    for node, ts in model.transformer_sites.items():
        site_id = ts["site_id"]
        kva = ts.get("kva", ts["kvas"][0])
        fases = ts.get("fases", 3)
        spec = tx_catalog.nearest(kva, fases)
        # demanda máxima del puesto (suma de clientes aguas abajo, con Velander agregado)
        e_site = energia_por_site.get(site_id, 0.0)
        s_max = float(velander_max_demand(e_site, clase0.a, clase0.b)) / max(clase0.cos_phi, 0.1)
        e_tx, e_vacio = transformer_unit_loss_kwh(
            spec.p0_kw, spec.pk_kw, s_max, kva, hours_period, fp,
            f_desbalance=cfg.perdidas.factor_desbalance_cobre,
            apply_loss_factor_to_no_load=cfg.perdidas.aplicar_factor_perdidas_a_vacio,
        )
        loss_tx_kwh += e_tx
        loss_tx_noload_kwh += e_vacio
        cap = bank_capacity_kva([kva], BankConfig(ts.get("config", "UNIDAD_SIMPLE")))
        ratio = s_max / cap if cap > 0 else 0.0
        tx_rows.append({
            "site_id": site_id, "kva_installed": kva, "kva_capacity": cap,
            "s_max_kva": s_max, "loading_ratio": ratio,
            "loading_class": classify_loadability(ratio, cfg.cargabilidad).value,
            "customers": sum(1 for c in graph.subtree_customers(node)),
            "loss_kwh": e_tx,
        })

    # --- pérdidas en medidores ---
    tipos = [c.get("meter_type", "_default")
             for cls in model.customer_nodes.values() for c in cls]
    loss_meters_kwh = meter_losses_kwh(tipos, hours_period, cfg.perdidas.watts_medidor)

    # --- alumbrado público ---
    lums = [l for cls in model.streetlight_nodes.values() for l in cls]
    ap_unmetered = 0.0
    if lums:
        ap = compute_streetlight_energy(pd.DataFrame(lums), cfg.alumbrado)
        ap_unmetered = ap.total_unmetered_kwh

    loss_technical = (
        loss_conductors_kwh + loss_tx_kwh + loss_meters_kwh
    )
    loss_components = {
        "RED_CONDUCTORES": loss_conductors_kwh,
        "TRANSF_DIST_TOTAL": loss_tx_kwh,
        "TRANSF_DIST_VACIO": loss_tx_noload_kwh,
        "MEDIDORES": loss_meters_kwh,
    }

    # --- balance ---
    e_billed = sum(c["energy_kwh"] for cls in model.customer_nodes.values() for c in cls)
    balance = compute_balance(
        model.feeder_code,
        cfg=cfg.balance,
        e_billed_kwh=e_billed,
        loss_technical_kwh=loss_technical,
        e_input_kwh=head_energy_kwh,
        e_streetlight_unmetered_kwh=ap_unmetered,
        customers_traced=sum(len(v) for v in model.customer_nodes.values()),
        customers_declared=sum(len(v) for v in model.customer_nodes.values()),
        energy_coverage_pct=100.0,
    )

    metrics = {
        "n_nodes": graph.n_nodes,
        "pf_converged": pf.converged,
        "pf_iterations": pf.iterations,
        "v_min_pu": pf.v_min_pu,
        "loss_factor": fp,
        "load_factor": fc,
        "loss_technical_kwh": loss_technical,
        "ntl_kwh": balance.ntl_kwh,
        "ntl_pct": balance.ntl_pct,
        "balance_type": balance.balance_type.value,
    }

    return GridResult(
        feeder_code=model.feeder_code,
        balance=balance,
        loss_components_kwh=loss_components,
        transformer_loading=pd.DataFrame(tx_rows),
        powerflow_converged=pf.converged,
        v_min_pu=pf.v_min_pu,
        hours_period=hours_period,
        metrics=metrics,
    )
