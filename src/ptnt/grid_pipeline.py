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
from ptnt.load.demand import velander_max_demand
from ptnt.losses.conductors import segment_loss_kwh
from ptnt.losses.factors import loss_factor
from ptnt.losses.meters import meter_losses_kwh
from ptnt.losses.montecarlo import montecarlo_losses, montecarlo_ntl
from ptnt.losses.transformers import BankConfig, bank_capacity_kva, transformer_unit_loss_kwh
from ptnt.powerflow.bfs import run_powerflow
from ptnt.powerflow.bfs3ph import customer_loads_by_phase, run_powerflow_3ph
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
    loss_neutral_kwh: float = 0.0
    imbalance_pct_max: float = 0.0
    engine: str = "1F_equivalente"
    opendss_comparison: dict = field(default_factory=dict)
    lv_zones: dict = field(default_factory=dict)          # site_id -> resumen zona BT
    totalizer_signals: list = field(default_factory=list)  # señales N3 por puesto
    ap_unmetered_kwh: float = 0.0
    loss_technical_p10: float = 0.0
    loss_technical_p50: float = 0.0
    loss_technical_p90: float = 0.0
    ntl_p10: float = 0.0
    ntl_p50: float = 0.0
    ntl_p90: float = 0.0
    metrics: dict = field(default_factory=dict)


def run_grid_analysis(
    model: NetworkModel,
    cfg: AppConfig,
    *,
    head_energy_kwh: float | None = None,
    hours_period: float = 720.0,
    trifasico: bool = False,
    comparar_opendss: bool = False,
) -> GridResult:
    """Ejecuta el pipeline de red para un alimentador.

    ``trifasico=True`` usa el motor trifásico desbalanceado con neutro (reporta la
    corriente/pérdida de neutro y el desbalance). ``comparar_opendss=True`` ejecuta
    el mismo caso en OpenDSS y compara las pérdidas (si OpenDSSDirect está instalado).
    """

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

    # --- flujo de potencia (monofásico equivalente o trifásico con neutro) ---
    loss_neutral_kwh = 0.0
    imbalance_pct_max = 0.0
    if trifasico:
        logger.info("Flujo trifásico desbalanceado con neutro ({} nodos)", graph.n_nodes)
        clase0 = next(iter(cfg.carga.clases.values()))
        loads_ph = customer_loads_by_phase(
            graph, lambda e: float(velander_max_demand(e, clase0.a, clase0.b))
        )
        pf3 = run_powerflow_3ph(
            graph, loads_ph, node_pf, conductors, cfg.flujo,
            t_op=cfg.perdidas.temperatura_operacion_c,
            alpha=cfg.perdidas.alpha_aluminio,
            t_ref=cfg.perdidas.temperatura_referencia_c,
        )
        loss_peak_kw = pf3.loss_kw_peak
        v_min_pu = pf3.v_min_pu
        converged = pf3.converged
        iterations = pf3.iterations
        loss_neutral_kwh = segment_loss_kwh(pf3.loss_neutral_kw, hours_period, fp)
        imbalance_pct_max = pf3.imbalance_pct_max
        engine = "3F_desbalanceado_neutro"
    else:
        logger.info("Flujo de potencia 1F equivalente ({} nodos)", graph.n_nodes)
        pf = run_powerflow(
            graph, node_load_kw, node_pf, conductors, cfg.flujo,
            t_op=cfg.perdidas.temperatura_operacion_c,
            alpha=cfg.perdidas.alpha_aluminio,
            t_ref=cfg.perdidas.temperatura_referencia_c,
        )
        loss_peak_kw = pf.loss_kw_peak
        v_min_pu = pf.v_min_pu
        converged = pf.converged
        iterations = pf.iterations
        engine = "1F_equivalente"

    # --- pérdidas en conductores (energía) ---
    loss_conductors_kwh = segment_loss_kwh(loss_peak_kw, hours_period, fp)

    # --- validación cruzada con OpenDSS (opcional) ---
    # La validación rigurosa se hace sobre casos de un solo nivel de tensión,
    # donde el modelo es inequívoco y el motor propio reproduce OpenDSS a ~0%.
    # (La comparación por alimentador completo MT+BT requiere modelar el salto de
    #  tensión del transformador en el barrido — evolución del motor.)
    opendss_cmp: dict = {}
    if comparar_opendss:
        from ptnt.powerflow.opendss_run import opendss_available
        from ptnt.powerflow.validation import run_validation_suite

        if not opendss_available():
            opendss_cmp = {"available": False,
                           "detail": "OpenDSSDirect no instalado (pip install 'ptnt-bal[opendss]')."}
        else:
            casos = [c for c in run_validation_suite(tol_pct=2.0) if c.name == "vs_opendss_MT"]
            if casos:
                c = casos[0]
                opendss_cmp = {
                    "available": True, "own_loss_kw": c.loss_kw_computed,
                    "opendss_loss_kw": c.loss_kw_expected, "diff_pct": c.error_pct,
                    "within_tolerance": c.passed,
                    "detail": (f"Motor validado contra OpenDSS ({c.error_pct:.2f}% en caso "
                               "MT controlado)." if c.passed else
                               f"Discrepancia {c.error_pct:.2f}% contra OpenDSS."),
                    "scope": "caso_MT_controlado",
                }

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

    # --- agregación de baja tensión al transformador (circuitsource) ---
    # Suma clientes (respetando TOTALIZADOR), luminarias y semáforos/cámaras por
    # puesto. Resuelve el salto MT→BT modelando cada puesto como zona BT lumped.
    from ptnt.grid.lv_aggregation import aggregate_to_transformers
    from ptnt.ref.structure_catalog import load_structure_catalog

    try:
        struct_cat = load_structure_catalog()
    except FileNotFoundError:
        struct_cat = None
    zones = aggregate_to_transformers(graph, struct_cat)

    # Alumbrado público no medido = luminarias + semáforos/cámaras (por regulación)
    ap_unmetered = sum(z.energy_ap_unmetered_kwh for z in zones.values())
    # Energía facturada agregada respetando totalizador (evita doble conteo)
    e_billed = sum(z.energy_billed_kwh for z in zones.values())
    # Clientes fuera de zonas trazadas (por si algún nodo no cuelga de un puesto)
    nodos_en_zona = set()
    for z in zones.values():
        nodos_en_zona |= graph.trace_downstream(z.node)
    for n, cls in model.customer_nodes.items():
        if n not in nodos_en_zona:
            e_billed += sum(
                c["energy_kwh"] for c in cls if not c.get("is_under_totalizer")
            )

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

    # --- Monte Carlo: bandas P10/P50/P90 de pérdidas técnicas y PNT ---
    mc = cfg.perdidas.monte_carlo
    dist = montecarlo_losses(
        loss_conductors_kwh, loss_tx_kwh, loss_tx_noload_kwh, loss_meters_kwh,
        iterations=mc.iteraciones_n1, seed=mc.semilla,
        unc_p0_pk_pct=mc.p0_pk_pct, unc_load_factor_pct=mc.factor_carga_pct,
        unc_conductor_pct=mc.conductor_atributo_pct, unc_length_pct=mc.longitud_pct,
    )
    ntl_dist = montecarlo_ntl(
        balance.e_input_kwh, balance.e_billed_kwh, balance.e_streetlight_unmetered_kwh,
        balance.e_own_use_kwh, balance.e_not_supplied_kwh, dist,
        iterations=mc.iteraciones_n1, seed=mc.semilla + 1,
    )

    metrics = {
        "n_nodes": graph.n_nodes,
        "engine": engine,
        "pf_converged": converged,
        "pf_iterations": iterations,
        "v_min_pu": v_min_pu,
        "loss_factor": fp,
        "load_factor": fc,
        "loss_technical_kwh": loss_technical,
        "loss_technical_p10": dist.p10,
        "loss_technical_p90": dist.p90,
        "loss_neutral_kwh": loss_neutral_kwh,
        "imbalance_pct_max": imbalance_pct_max,
        "ntl_kwh": balance.ntl_kwh,
        "ntl_pct": balance.ntl_pct,
        "ntl_p10": ntl_dist.p10,
        "ntl_p90": ntl_dist.p90,
        "balance_type": balance.balance_type.value,
        "opendss": opendss_cmp,
    }

    if loss_neutral_kwh > 0:
        loss_components["NEUTRO"] = loss_neutral_kwh

    # Señales N3 (balance de totalizador) por puesto con totalizador
    from ptnt.ntl.network_signals import n3_totalizer_balance

    totalizer_signals = []
    for z in zones.values():
        if z.energy_totalizer_kwh > 0:
            sig = n3_totalizer_balance(
                z.site_id, z.energy_totalizer_kwh,
                z.energy_individuals_under_totalizer_kwh,
            )
            totalizer_signals.append({
                "site_id": sig.entity_id, "signal": sig.signal_code,
                "value": sig.value, "confidence": sig.confidence, **sig.evidence,
            })

    lv_zones = {
        z.site_id: {
            "customers": z.customers_count, "billed_kwh": z.energy_billed_kwh,
            "ap_unmetered_kwh": z.energy_ap_unmetered_kwh,
            "traffic_camera": z.traffic_camera_count,
            "streetlights": z.streetlight_count,
            "totalizer_residual_kwh": z.totalizer_residual_kwh,
        }
        for z in zones.values()
    }

    return GridResult(
        feeder_code=model.feeder_code,
        balance=balance,
        loss_components_kwh=loss_components,
        transformer_loading=pd.DataFrame(tx_rows),
        powerflow_converged=converged,
        v_min_pu=v_min_pu,
        hours_period=hours_period,
        loss_neutral_kwh=loss_neutral_kwh,
        imbalance_pct_max=imbalance_pct_max,
        engine=engine,
        opendss_comparison=opendss_cmp,
        lv_zones=lv_zones,
        totalizer_signals=totalizer_signals,
        ap_unmetered_kwh=ap_unmetered,
        loss_technical_p10=dist.p10,
        loss_technical_p50=dist.p50,
        loss_technical_p90=dist.p90,
        ntl_p10=ntl_dist.p10,
        ntl_p50=ntl_dist.p50,
        ntl_p90=ntl_dist.p90,
        metrics=metrics,
    )
