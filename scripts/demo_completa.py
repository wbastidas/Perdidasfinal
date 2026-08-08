"""Demostración completa del proceso PTNT-BAL con datos ficticios.

Ejecuta el flujo entero de punta a punta y muestra los resultados de cada etapa,
incluyendo el ciclo de vida real de la operación:

  1. Generación del escenario ficticio (comercial, cabecera, multados, red, SIG)
  2. Versionado inicial de la red (ALTA)
  3. Análisis comercial (promedio multi-mes, potencia, reconciliación, hurto)
  4. Análisis de red (topología, flujo, pérdidas, balance, PNT)
  5. Diagnóstico de credibilidad (transferencias, faltantes, incoherencias)
  6. Validación contra la base de multados (lift real)
  7. Focalización: dónde ir a hacer el levantamiento
  8. Carga de un mes nuevo: qué cambia y qué se conserva
  9. Modificación de topología: qué se recalcula y qué ubicaciones persisten

Uso:  python scripts/demo_completa.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from ptnt.config.loader import load_config  # noqa: E402


def titulo(n: int, texto: str) -> None:
    print(f"\n{'='*78}\n  PASO {n}. {texto}\n{'='*78}")


def sub(texto: str) -> None:
    print(f"\n── {texto} " + "─" * max(0, 74 - len(texto)))


def main() -> int:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    cfg = load_config(str(RAIZ / "config" / "base.yaml"))
    salidas = RAIZ / "outputs" / "demo"
    # Demo reproducible: se parte de cero para que el versionado y el registro de
    # ubicaciones no acumulen corridas anteriores.
    if salidas.exists():
        for f in salidas.glob("*"):
            if f.is_file():
                f.unlink()
    salidas.mkdir(parents=True, exist_ok=True)
    cfg.rutas.salidas = str(salidas)

    # ------------------------------------------------------------------ 1
    titulo(1, "GENERACIÓN DEL ESCENARIO FICTICIO")
    from ptnt.synth.scenario import build_scenario

    esc = build_scenario(RAIZ / "data" / "demo", n_clientes=1200,
                         n_transformadores=8, clientes_por_trafo=20)
    for k, v in esc.resumen.items():
        print(f"  {k:42s}: {v}")
    print(f"\n  Archivos generados en {esc.directorio}:")
    for f in (esc.csv_consumos, esc.csv_cabecera, esc.csv_multados, esc.csv_sig):
        print(f"    - {f.name:24s} ({f.stat().st_size/1024:,.0f} KB)")

    # ------------------------------------------------------------------ 2
    titulo(2, "VERSIONADO DE LA RED (primera carga = ALTA)")
    from ptnt.topology.versioning import VersionStore

    store = VersionStore(salidas / "versiones_red.json")
    v1 = store.register(esc.red)
    store.save()
    print(f"  Acción: {v1.action.value}   ·   versión {v1.version_id}")
    print(f"  {v1.detail}")
    print(f"  Inventario: {v1.change_summary.get('altas', {})}")

    # ------------------------------------------------------------------ 3
    titulo(3, "ANÁLISIS COMERCIAL (promedio multi-mes → potencia → hurto)")
    from ptnt.pipeline import run_analysis

    com = run_analysis(cfg, str(esc.csv_consumos), persistir=False)
    m = com.metricas
    print(f"  Cuentas analizadas      : {m['n_cuentas']:,}  ×  {m['n_meses']} meses")
    print(f"  Método de promedio      : {m['metodo_promedio']} "
          f"(ventana {m['ventana_meses']} meses)")
    print(f"  Método de demanda       : {m['metodo_demanda']}")
    print(f"  Δ Potencia SIG→corregido: {m['delta_p_total_kw']:,.0f} kW "
          f"({m['delta_p_total_pct']:.1f} %)")
    print(f"  Clientes sospechosos    : {m['n_sospechosos']:,}")

    sub("Segmentación del padrón: dónde está la energía")
    print(f"  Clasificado por tarifa  : {m.get('segmentacion_cobertura_pct')}%  "
          f"(sin clase: {m.get('segmentacion_no_clasificados')})")
    print(com.segmentos_por_clase.to_string(index=False))
    print(f"\n  Los no residenciales concentran el "
          f"{m.get('pct_energia_no_residencial')}% de la energía.")

    sub("Grupo par: contra quién se compara cada cliente")
    print(com.grupos_par_por_nivel.to_string(index=False))
    print("  El grupo par se arma con claves EXÓGENAS al consumo (clase, tensión,")
    print("  fases, ruta). Estratificar por consumo sería circular: un cliente que")
    print("  hurta todo el período caería en un estrato bajo y quedaría comparado")
    print("  contra clientes genuinamente pequeños, donde ya no destaca.")

    from ptnt.segment.report import (
        grandes_clientes_a_revisar,
        rendimiento_por_segmento,
    )

    rend = rendimiento_por_segmento(com.ranking)
    sub("Rendimiento esperado por visita (top 5 % de cada clase)")
    print(rend.tabla.to_string(index=False))
    for r in rend.recomendaciones:
        print(f"  → {r}")

    gc = grandes_clientes_a_revisar(com.ranking, top=5)
    if not gc.empty:
        sub("Grandes clientes con indicios (revisión individual)")
        print(gc[["contract_account", "clase_consumo", "score",
                  "recuperable_kwh_mes"]].to_string(index=False))
        print("  Su posición relativa en el ranking los esconde: un desvío del 10 %")
        print("  en uno de ellos equivale a cientos de residenciales completos.")

    sub("Top 5 del ranking de sospecha")
    cols = ["rank", "contract_account", "clase_consumo", "score",
            "n_senales_activas", "recuperable_kwh_mes"]
    print(com.ranking.head(5)[[c for c in cols if c in com.ranking.columns]]
          .to_string(index=False))

    # ------------------------------------------------------------------ 4
    titulo(4, "ANÁLISIS DE RED (topología → flujo → pérdidas → balance → PNT)")
    from ptnt.grid_pipeline import run_grid_analysis

    grid = run_grid_analysis(esc.red, cfg, head_energy_kwh=esc.head_energy_kwh,
                             trifasico=True)
    b = grid.balance
    print(f"  Motor de flujo          : {grid.engine} "
          f"(converge={grid.powerflow_converged}, Vmin={grid.v_min_pu:.4f} pu)")
    print(f"  Desbalance máximo       : {grid.imbalance_pct_max:.0f} % "
          "(en tramos trifásicos; las acometidas monofásicas no cuentan)")
    print(f"\n  BALANCE ({b.balance_type.value})")
    print(f"    Entrada (cabecera)    : {b.e_input_kwh:12,.0f} kWh")
    print(f"    − Facturado           : {b.e_billed_kwh:12,.0f} kWh")
    print(f"    − Alumbrado no medido : {b.e_streetlight_unmetered_kwh:12,.0f} kWh "
          f"(incluye semáforos y cámaras)")
    print(f"    − Consumos propios    : {b.e_own_use_kwh:12,.0f} kWh")
    print(f"    = Pérdidas totales    : {b.loss_total_kwh:12,.0f} kWh")
    print(f"    − Pérdidas técnicas   : {b.loss_technical_kwh:12,.0f} kWh "
          f"(P10–P90: {grid.loss_technical_p10:,.0f}–{grid.loss_technical_p90:,.0f})")
    print(f"    = PNT                 : {b.ntl_kwh:12,.0f} kWh ({b.ntl_pct:.1f} %) "
          f"[P10–P90: {grid.ntl_p10:,.0f}–{grid.ntl_p90:,.0f}]")
    sub("Pérdidas técnicas por componente")
    for k, v in grid.loss_components_kwh.items():
        print(f"    {k:22s}: {v:10,.1f} kWh")
    disparados = [c.code for c in b.controls if c.triggered]
    print(f"\n  Controles C01–C06 disparados: {disparados or 'ninguno'}")
    if grid.totalizer_signals:
        sub("Señal N3 — balance de totalizador (la evidencia más limpia)")
        print(pd.DataFrame(grid.totalizer_signals).to_string(index=False))

    # ------------------------------------------------------------------ 5
    titulo(5, "DIAGNÓSTICO DE CREDIBILIDAD")
    from ptnt.anomalies import (
        analyze_feeder_coherence, analyze_unmatched_customers, detect_transfers,
    )

    sub("5a. Transferencias entre alimentadores no reportadas")
    tr = detect_transfers(pd.read_csv(esc.csv_cabecera))
    print(f"  Estado: {tr.status.value}")
    if tr.candidates:
        print(tr.to_dataframe()[["alimentador_a", "alimentador_b", "periodo",
                                 "magnitud_kwh", "simetria", "confianza"]]
              .to_string(index=False))
        print(f"\n  Inyectado en el escenario: {esc.transferencia['origen']} → "
              f"{esc.transferencia['destino']} en {esc.transferencia['periodo']} "
              f"({esc.transferencia['magnitud_kwh']:,.0f} kWh)")
        c = tr.candidates[0]
        acierto = ({c.feeder_a, c.feeder_b} ==
                   {esc.transferencia["origen"], esc.transferencia["destino"]})
        print(f"  ¿Detectó la transferencia real?  {'SÍ' if acierto else 'NO'}")

    sub("5b. Clientes faltantes (vinculación comercial ↔ SIG)")
    base_cli = com.clientes.merge(com.promedios, on="contract_account", how="left")
    unm = analyze_unmatched_customers(base_cli, pd.read_csv(esc.csv_sig, dtype=str))
    r = unm.resumen()
    print(f"  Cuentas vinculadas    : {r['pct_cuentas_vinculadas']} %")
    print(f"  ENERGÍA vinculada     : {r['pct_energia_vinculada']} %   "
          f"← techo de calidad del balance")
    print(f"  CSV sin SIG           : {r['n_csv_sin_sig']:,} cuentas "
          f"({r['energia_sin_vincular_kwh']:,.0f} kWh sin ubicar)")
    print(f"  SIG sin facturación   : {r['n_sig_sin_csv']:,} clientes")
    print(f"  Apto para balance MEDIDO: {r['apto_balance_medido']}")
    print(f"\n  Inyectado: {len(esc.clientes_sin_sig)} clientes sin SIG · "
          f"detectados: {r['n_csv_sin_sig']}")

    sub("5c. Alimentadores con valores incoherentes")
    coh = analyze_feeder_coherence([b], transfer_report=tr, unmatched_report=unm)
    df_coh = coh.to_dataframe()
    if df_coh.empty:
        print("  Sin incoherencias: el resultado es publicable.")
    else:
        print(df_coh[["alimentador", "codigo", "severidad", "descripcion"]]
              .to_string(index=False))

    # ------------------------------------------------------------------ 6
    titulo(6, "VALIDACIÓN CONTRA LA BASE DE MULTADOS (precisión real)")
    from ptnt.ntl.confirmed import (
        calibrate_signal_thresholds, load_confirmed_theft, validate_against_confirmed,
    )

    conf = load_confirmed_theft(pd.read_csv(esc.csv_multados))
    print(f"  Multados cargados: {conf.n:,}  ·  rango de fechas: {conf.rango_fechas}")
    met = validate_against_confirmed(com.ranking, conf)
    v = met.resumen()
    print(f"\n  Prevalencia (tasa base)      : {v['prevalencia_pct']} %")
    print(f"  Precisión en el top 1 %      : {v['precision_top_1pct']} %")
    print(f"  Precisión en el top 5 %      : {v['precision_top_5pct']} %")
    print(f"  Recall en el top 10 %        : {v['recall_top_10pct']} %")
    print(f"  LIFT en el top 5 %           : {v['lift_top_5pct']}×  "
          f"← veces más hurtos que inspeccionar al azar")
    print(f"  AUC                          : {v['auc']}")
    print(f"  Posición mediana de los hurtos: {v['posicion_mediana_pct']} % del ranking")
    sub("Calibración de señales con casos reales")
    calib = calibrate_signal_thresholds(com.señales, conf)
    if not calib.empty:
        print(calib[["senal", "activacion_en_confirmados_pct",
                     "activacion_en_resto_pct", "lift", "recomendacion"]]
              .to_string(index=False))

    # ------------------------------------------------------------------ 7
    titulo(7, "FOCALIZACIÓN: DÓNDE IR A HACER EL LEVANTAMIENTO")
    from ptnt.survey.collect import (
        bind_commercial_customers, collect_branch_stats, collect_transformer_stats,
        collect_zone_stats,
    )
    from ptnt.survey.locations import LocationRegistry, register_plan
    from ptnt.survey.report import write_survey_report
    from ptnt.survey.routes import analyze_commercial_routes, routes_to_target_input
    from ptnt.survey.targeting import TargetLevel, build_survey_plan
    from ptnt.topology.graph import build_radial_graph

    coords = com.clientes[["contract_account", "x", "y"]].copy()
    coords["contract_account"] = coords["contract_account"].astype(str)
    bind_commercial_customers(
        esc.red, sorted(com.ranking["contract_account"].astype(str)), coords)
    graph = build_radial_graph(esc.red)

    umbral = com.ranking["score"].quantile(0.90)
    suspect = set(com.ranking[com.ranking["score"] >= umbral]["contract_account"].astype(str))
    recuperable = dict(zip(com.ranking["contract_account"].astype(str),
                           com.ranking["recuperable_kwh_mes"]))
    rutas = analyze_commercial_routes(base_cli, com.consumo,
                                      suspect_customers=suspect,
                                      recoverable_by_customer=recuperable,
                                      cuentas_sin_sig=set(esc.clientes_sin_sig))
    plan = build_survey_plan(
        feeder_balances=[{"feeder_code": grid.feeder_code, "ntl_kwh": b.ntl_kwh,
                          "ntl_pct": b.ntl_pct,
                          "customers": sum(len(x) for x in esc.red.customer_nodes.values()),
                          "network_km": graph.total_length_km(), "confidence": 100.0,
                          "balance_type": b.balance_type.value}],
        zone_signals=collect_zone_stats(graph),
        branch_stats=collect_branch_stats(graph, suspect_customers=suspect,
                                          recoverable_by_customer=recuperable),
        transformer_stats=collect_transformer_stats(
            graph, grid.lv_zones, grid.transformer_loading,
            suspect_customers=suspect, recoverable_by_customer=recuperable),
        route_stats=routes_to_target_input(rutas),
        customer_ranking=com.ranking, customer_coords=coords,
    )
    print(f"  Objetivos priorizados: {plan.resumen['n_objetivos']:,}")
    print(f"  Por nivel: {plan.resumen['por_nivel']}")

    for lvl in (TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.RUTA_COMERCIAL,
                TargetLevel.SECTOR):
        objetivos = plan.by_level(lvl)
        if not objetivos:
            continue
        sub(f"{lvl.value} — top 3")
        for t in objetivos[:3]:
            print(f"    {t.entity_id:24s} prio={t.priority_score:.3f} "
                  f"{t.recoverable_kwh_month:8,.0f} kWh/mes  {t.customers_count:3d} cli")
            print(f"      → {t.action}")
            print(f"      → {t.reasons[0][:88] if t.reasons else ''}")

    sub("ÓRDENES DE LEVANTAMIENTO (ordenadas por rendimiento por visita)")
    ot = plan.work_orders(top_n=10)
    print(ot[["orden_trabajo", "nivel", "entidad", "clientes_a_revisar",
              "kwh_por_visita"]].to_string(index=False))
    print(f"\n  {int(ot['clientes_a_revisar'].sum()):,} clientes cubiertos en "
          f"{len(ot)} visitas · {ot['kwh_por_visita'].sum():,.0f} kWh/mes en juego")

    # Registro persistente de ubicaciones
    registro = LocationRegistry(salidas / "ubicaciones.json")
    n_reg = register_plan(plan, registro)
    registro.save()
    print(f"\n  Ubicaciones registradas con identidad geográfica estable: {n_reg:,}")

    write_survey_report(plan, str(salidas / "reporte_focalizacion.html"))
    plan.to_dataframe().to_csv(salidas / "plan_levantamientos.csv", index=False)
    ot.to_csv(salidas / "ordenes_levantamiento.csv", index=False)

    # ------------------------------------------------------------------ 8
    titulo(8, "CARGA DE UN MES NUEVO: ¿qué cambia y qué se conserva?")
    sectores_antes = {t.entity_id for t in plan.by_level(TargetLevel.SECTOR)}
    print(f"  Sectores en la corrida 1: {len(sectores_antes)}")
    print(f"    ejemplos: {sorted(sectores_antes)[:3]}")

    # Simula el mes siguiente: aparecen clientes nuevos en una zona nueva
    coords2 = pd.concat([coords, pd.DataFrame({
        "contract_account": [f"NUEVO{i}" for i in range(8)],
        "x": [612000 + i * 30 for i in range(8)],
        "y": [9751000 + i * 30 for i in range(8)],
    })], ignore_index=True)
    ranking2 = pd.concat([com.ranking, pd.DataFrame({
        "contract_account": [f"NUEVO{i}" for i in range(8)],
        "score": [0.97] * 8, "recuperable_kwh_mes": [800.0] * 8,
        "razones": [["Consumo en cero con servicio activo"]] * 8,
        "rank": range(9000, 9008), "n_senales_activas": [2] * 8,
    })], ignore_index=True)
    plan2 = build_survey_plan(customer_ranking=ranking2, customer_coords=coords2)
    sectores_despues = {t.entity_id for t in plan2.by_level(TargetLevel.SECTOR)}
    persisten = sectores_antes & sectores_despues
    nuevos = sectores_despues - sectores_antes
    print(f"\n  Sectores en la corrida 2: {len(sectores_despues)}")
    print(f"    ✅ Conservan su identificador : {len(persisten)}")
    print(f"    🆕 Sectores nuevos detectados : {len(nuevos)} {sorted(nuevos)[:2]}")
    print("\n  Las órdenes ya emitidas siguen apuntando al mismo sitio físico,")
    print("  porque el identificador se deriva de la COORDENADA, no del cálculo.")

    n2 = register_plan(plan2, registro)
    registro.save()
    reinc = registro.reincidentes()
    print(f"\n  Registro acumulado: {len(registro):,} ubicaciones · "
          f"{len(reinc)} reincidentes (priorizadas ≥3 veces sin inspeccionar)")

    # ------------------------------------------------------------------ 9
    titulo(9, "MODIFICACIÓN DE TOPOLOGÍA: ¿qué se recalcula?")
    from ptnt.synth.scenario import modify_topology

    # Cada modificación se compara contra la MISMA línea base (la versión 1),
    # para aislar el efecto de cada tipo de cambio.
    sub("SIN CAMBIOS — se vuelve a cargar la misma red")
    store_base = VersionStore(salidas / "_tmp_sin_cambio.json")
    store_base.register(esc.red)
    res0 = store_base.register(esc.red)
    print(f"    Acción            : {res0.action.value}")
    print(f"    {res0.detail}")

    for cambio in ("conductor", "nuevo_ramal", "maniobra"):
        red_mod, desc = modify_topology(esc.red, cambio=cambio)
        st = VersionStore(salidas / f"_tmp_{cambio}.json")
        st.register(esc.red)                 # línea base común
        res = st.register(red_mod)           # el cambio contra esa base
        sub(f"{cambio.upper()} — {desc}")
        print(f"    Acción            : {res.action.value} (versión {res.version_id})")
        print(f"    Hashes cambiados  : {res.hashes_cambiados or '—'}")
        print(f"    Se recalcula      : {', '.join(res.etapas_a_recalcular) or '—'}")
        if res.change_summary.get("delta"):
            print(f"    Cambio de inventario: {res.change_summary['delta']}")
        print(f"    Ubicaciones conservadas: {res.ubicaciones_conservadas}")
    for f in salidas.glob("_tmp_*.json"):
        f.unlink()

    # Se adopta la ampliación en el registro oficial, para mostrar el histórico real.
    red_ampliada, desc_amp = modify_topology(esc.red, cambio="nuevo_ramal")
    store.register(red_ampliada)
    store.save()

    sub(f"Histórico oficial del alimentador {esc.red.feeder_code}")
    print(f"    Se adopta en producción: {desc_amp}")
    for v in store.history(esc.red.feeder_code):
        marca = "◀ vigente" if v.is_current else "  histórica"
        print(f"    v{v.version_id}  {v.loaded_at[:19]}  "
              f"{v.element_counts.get('tramos', 0):4d} tramos · "
              f"{v.element_counts.get('clientes', 0):4d} clientes   {marca}")
    print("  Las versiones anteriores se conservan (is_current=False) para auditar "
          "qué cambió y cuándo.")

    # ------------------------------------------------------------------ fin
    print(f"\n{'='*78}\n  RESULTADOS GENERADOS EN {salidas}\n{'='*78}")
    for f in sorted(salidas.glob("*")):
        print(f"  {f.name:34s} {f.stat().st_size/1024:8,.1f} KB")
    print("\n  Abra reporte_focalizacion.html para ver el plan de campo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
