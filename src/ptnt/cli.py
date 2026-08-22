"""Interfaz de línea de comandos de PTNT-BAL (``ptnt ...``).

Todos los subcomandos aceptan ``--config`` con la ruta al YAML. El arranque valida
la configuración: si falta un parámetro obligatorio, el comando falla con un
mensaje que nombra el parámetro.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ptnt.config.loader import ConfigError, load_config
from ptnt.segment.report import (
    grandes_clientes_a_revisar,
    rendimiento_por_segmento,
)

app = typer.Typer(
    add_completion=False,
    help="PTNT-BAL — análisis de consumo, recálculo de potencia y detección de hurto.",
)
console = Console()

_CONFIG_OPT = typer.Option("config/base.yaml", "--config", "-c", help="Ruta al YAML de configuración")


def _cargar(config: str):
    try:
        return load_config(config)
    except ConfigError as exc:
        console.print(f"[bold red]Error de configuración:[/]\n{exc}")
        raise typer.Exit(code=2)


@app.command()
def verificar_config(config: str = _CONFIG_OPT):
    """Valida el YAML de configuración y resume la efectiva."""

    cfg = _cargar(config)
    console.print(f"[bold green]✓ Configuración válida[/]  ({config})")
    console.print(f"  Proyecto: {cfg.proyecto.nombre} / UN {cfg.proyecto.unidad_negocio}")
    console.print(f"  Fuentes: {', '.join(f.nombre for f in cfg.fuentes) or '(ninguna)'}")
    console.print(f"  Método de promedio: {cfg.promedio.metodo.value} "
                  f"(ventana {cfg.promedio.ventana_meses} meses)")
    console.print(f"  Método de demanda: {cfg.carga.metodo_demanda_maxima.value}")


@app.command()
def probar_fuentes(config: str = _CONFIG_OPT):
    """Prueba la conectividad de todas las bases de origen configuradas."""

    from ptnt.io.sources import SourceError, build_connector

    cfg = _cargar(config)
    tabla = Table(title="Prueba de conectividad de fuentes")
    tabla.add_column("Fuente")
    tabla.add_column("Tipo")
    tabla.add_column("Estado")
    for fuente in cfg.fuentes:
        try:
            conn = build_connector(fuente)
            conn.test_connection()
            estado = "[green]OK[/]"
        except SourceError as exc:
            estado = f"[red]FALLO[/] {exc}"
        except Exception as exc:  # noqa: BLE001
            estado = f"[red]ERROR[/] {exc}"
        tabla.add_row(fuente.nombre, fuente.tipo.value, estado)
    console.print(tabla)


@app.command()
def generar_sinteticos(
    salida: str = typer.Option("data/entrada/consumos_36m.csv", "--salida", "-o"),
    n_clientes: int = typer.Option(2000, "--clientes"),
    n_meses: int = typer.Option(36, "--meses"),
    pct_hurto: float = typer.Option(0.05, "--pct-hurto"),
    config: str = _CONFIG_OPT,
):
    """Genera un CSV comercial sintético con hurtos inyectados de magnitud conocida."""

    from ptnt.synth.generator import generate_commercial_csv

    cfg = _cargar(config)
    ds = generate_commercial_csv(
        salida,
        n_clientes=n_clientes,
        n_meses=n_meses,
        pct_hurto=pct_hurto,
        separador=cfg.comercial.separador,
        encoding=cfg.comercial.encoding,
    )
    console.print(f"[green]✓[/] Generados {n_clientes} clientes en {salida}")
    console.print(f"  Hurtos inyectados por tipo: {ds.hurtos}")


@app.command()
def analizar(
    csv: str = typer.Option(None, "--csv", help="CSV comercial (si se omite, usa la fuente 'comercial_csv')"),
    persistir: bool = typer.Option(True, "--persistir/--no-persistir"),
    top: int = typer.Option(15, "--top", help="Nº de clientes a mostrar en el ranking"),
    config: str = _CONFIG_OPT,
):
    """Ejecuta el pipeline completo: promedio → potencia → reconciliación → hurto."""

    from ptnt.pipeline import run_analysis

    cfg = _cargar(config)
    if csv is None:
        try:
            csv = cfg.fuente("comercial_csv").ruta
        except KeyError:
            console.print("[red]No se indicó --csv y no existe la fuente 'comercial_csv'.[/]")
            raise typer.Exit(code=2)
    if not Path(csv).exists():
        console.print(f"[red]No existe el CSV: {csv}[/]")
        console.print("Genere datos sintéticos con: [cyan]ptnt generar-sinteticos[/]")
        raise typer.Exit(code=2)

    result = run_analysis(cfg, csv, persistir=persistir)

    for adv in result.advertencias:
        console.print(f"[yellow]⚠ {adv}[/]")

    console.print("\n[bold]Reconciliación de potencia (SIG vs corregido)[/]")
    console.print(f"  ΔP total: {result.metricas.get('delta_p_total_kw'):.1f} kW "
                  f"({result.metricas.get('delta_p_total_pct'):.1f} %)")

    # --- Segmentación: dónde está la energía y qué rinde inspeccionar ---------
    if not result.segmentos_por_clase.empty:
        console.print("\n[bold]Segmentación del padrón[/] "
                      f"(clasificado: {result.metricas.get('segmentacion_cobertura_pct')}%)")
        t = Table()
        for c, j in (("Clase", "left"), ("Clientes", "right"), ("% clientes", "right"),
                     ("kWh base/mes", "right"), ("% energía", "right")):
            t.add_column(c, justify=j)
        for _, row in result.segmentos_por_clase.iterrows():
            t.add_row(str(row["clase_consumo"]), f"{int(row['clientes']):,}",
                      f"{row['pct_clientes']:.1f}", f"{row['kwh_base_mes']:,.0f}",
                      f"{row['pct_energia']:.1f}")
        console.print(t)

        rend = rendimiento_por_segmento(result.ranking)
        if not rend.tabla.empty:
            console.print("\n[bold]Rendimiento esperado por visita[/] "
                          "[dim](inspeccionando el top 5 % de cada clase)[/]")
            t2 = Table()
            for c in ("Clase", "Visitas", "kWh recuperables", "kWh por visita"):
                t2.add_column(c, justify="right" if c != "Clase" else "left")
            for _, row in rend.tabla.iterrows():
                t2.add_row(str(row["clase_consumo"]), f"{int(row['visitas']):,}",
                           f"{row['recuperable_top_kwh_mes']:,.0f}",
                           f"[bold]{row['kwh_por_visita']:,.0f}[/]")
            console.print(t2)
            for rec in rend.recomendaciones:
                console.print(f"  [cyan]→[/] {rec}")

        gc = grandes_clientes_a_revisar(result.ranking, top=5)
        if not gc.empty:
            console.print("\n[bold]Grandes clientes con indicios[/] "
                          "[dim](revisión individual: su ranking relativo los esconde)[/]")
            for _, row in gc.iterrows():
                console.print(
                    f"  {row['contract_account']}  {row.get('clase_consumo', '')}"
                    f"  score={row['score']:.3f}"
                    f"  recuperable={row['recuperable_kwh_mes']:,.0f} kWh/mes")

    console.print(f"\n[bold]Top {top} clientes por sospecha de hurto[/] "
                  f"(sospechosos: {result.metricas['n_sospechosos']})")
    tabla = Table()
    tabla.add_column("#")
    tabla.add_column("Cuenta")
    tabla.add_column("Clase")
    tabla.add_column("Score", justify="right")
    tabla.add_column("Recuperable", justify="right")
    tabla.add_column("Razón principal")
    for _, row in result.ranking.head(top).iterrows():
        razon = row["razones"][0] if row["razones"] else "-"
        tabla.add_row(
            str(int(row["rank"])),
            str(row["contract_account"]),
            str(row.get("clase_consumo", "-")),
            f"{row['score']:.3f}",
            f"{row.get('recuperable_kwh_mes', 0):,.0f}",
            razon,
        )
    console.print(tabla)
    if persistir:
        console.print(f"\n[green]✓[/] Resultados en {cfg.rutas.salidas}/ y {cfg.rutas.duckdb}")


@app.command()
def migrar(
    feeder: str = typer.Option(None, "--feeder", help="Código de alimentador a migrar"),
    analizar: bool = typer.Option(False, "--analizar", help="Ejecutar el pipeline tras migrar"),
    config: str = _CONFIG_OPT,
):
    """Migra la red desde la base de origen (config 'migracion') al modelo canónico."""

    from ptnt.io.migration import MigrationError, migrate_network

    cfg = _cargar(config)
    try:
        model = migrate_network(cfg, feeder_code=feeder)
    except MigrationError as exc:
        console.print(f"[red]Error de migración:[/] {exc}")
        raise typer.Exit(code=2)
    n_cli = sum(len(v) for v in model.customer_nodes.values())
    console.print(f"[green]✓[/] Migrado alimentador {model.feeder_code}: "
                  f"{len(model.edges)} tramos, {len(model.transformer_sites)} puestos, "
                  f"{n_cli} clientes (fuente '{cfg.migracion.fuente}')")
    if analizar:
        from ptnt.grid_pipeline import run_grid_analysis

        res = run_grid_analysis(model, cfg)
        console.print(f"  Balance {res.balance.balance_type.value} · "
                      f"PNT {res.balance.ntl_kwh:,.0f} kWh ({res.balance.ntl_pct:.1f}%)")


@app.command()
def analizar_red(
    n_trafos: int = typer.Option(8, "--trafos", help="Nº de transformadores (red sintética)"),
    clientes_por_trafo: int = typer.Option(20, "--clientes-trafo"),
    pnt: float = typer.Option(0.08, "--pnt", help="Fracción de PNT inyectada"),
    trifasico: bool = typer.Option(False, "--trifasico", help="Motor trifásico desbalanceado con neutro"),
    opendss: bool = typer.Option(False, "--opendss", help="Validar contra OpenDSS"),
    config: str = _CONFIG_OPT,
):
    """Ejecuta el pipeline de RED (E4–E10) sobre una red radial sintética:
    topología → flujo de potencia → pérdidas técnicas → balance y PNT."""

    from ptnt.grid_pipeline import run_grid_analysis
    from ptnt.synth.network import generate_radial_network

    cfg = _cargar(config)
    net = generate_radial_network(
        n_transformers=n_trafos, customers_per_tx=clientes_por_trafo, ntl_fraction=pnt
    )
    res = run_grid_analysis(
        net.model, cfg, head_energy_kwh=net.head_energy_kwh,
        trifasico=trifasico, comparar_opendss=opendss,
    )
    b = res.balance

    console.print(f"\n[bold]Alimentador {res.feeder_code}[/] — balance {b.balance_type.value}")
    console.print(f"  Motor de flujo: [cyan]{res.engine}[/] · converge={res.powerflow_converged} "
                  f"(iter {res.metrics['pf_iterations']}, Vmin={res.v_min_pu:.4f} pu)")
    if trifasico:
        console.print(f"  Pérdida de neutro: {res.loss_neutral_kwh:,.1f} kWh · "
                      f"desbalance máx: {res.imbalance_pct_max:.0f}%")
    if opendss and res.opendss_comparison:
        console.print(f"  OpenDSS: {res.opendss_comparison.get('detail')}")
    console.print(f"  Entrada (cabecera): {b.e_input_kwh:,.0f} kWh")
    console.print(f"  Facturado:          {b.e_billed_kwh:,.0f} kWh")
    console.print(f"  Alumbrado público:  {b.e_streetlight_unmetered_kwh:,.0f} kWh")
    console.print(f"  Pérdidas técnicas:  {b.loss_technical_kwh:,.0f} kWh "
                  f"(P10–P90: {res.loss_technical_p10:,.0f}–{res.loss_technical_p90:,.0f})")
    console.print(f"  [bold]PNT:               {b.ntl_kwh:,.0f} kWh ({b.ntl_pct:.1f}%)"
                  f"  P10–P90: {res.ntl_p10:,.0f}–{res.ntl_p90:,.0f} kWh[/]")

    tabla = Table(title="Pérdidas técnicas por componente")
    tabla.add_column("Componente")
    tabla.add_column("kWh", justify="right")
    for comp, val in res.loss_components_kwh.items():
        tabla.add_row(comp, f"{val:,.1f}")
    console.print(tabla)

    disparados = [c for c in b.controls if c.triggered]
    if disparados:
        console.print("[yellow]Controles de coherencia disparados:[/]")
        for c in disparados:
            console.print(f"  [yellow]{c.code}[/]: {c.detail}")
    else:
        console.print("[green]Controles de coherencia C01–C06: todos OK[/]")

    # Resumen de cargabilidad
    tl = res.transformer_loading
    if not tl.empty:
        conteo = tl["loading_class"].value_counts().to_dict()
        console.print(f"\nCargabilidad de {len(tl)} puestos: {conteo}")

    # Persistir para las interfaces web
    import json as _json
    from pathlib import Path as _Path

    salidas = _Path(cfg.rutas.salidas)
    salidas.mkdir(parents=True, exist_ok=True)
    balance_dict = {
        "feeder_code": b.feeder_code,
        "balance_type": b.balance_type.value,
        "e_input_kwh": b.e_input_kwh,
        "e_billed_kwh": b.e_billed_kwh,
        "e_streetlight_unmetered_kwh": b.e_streetlight_unmetered_kwh,
        "loss_total_kwh": b.loss_total_kwh,
        "loss_technical_kwh": b.loss_technical_kwh,
        "ntl_kwh": b.ntl_kwh,
        "ntl_pct": b.ntl_pct,
        "loss_components": res.loss_components_kwh,
        "controls_triggered": [c.code for c in b.controls if c.triggered],
        "ap_unmetered_kwh": res.ap_unmetered_kwh,
        "loss_neutral_kwh": res.loss_neutral_kwh,
        "imbalance_pct_max": res.imbalance_pct_max,
        "engine": res.engine,
        "loss_technical_p10": res.loss_technical_p10,
        "loss_technical_p90": res.loss_technical_p90,
        "ntl_p10": res.ntl_p10,
        "ntl_p90": res.ntl_p90,
        "totalizer_signals": res.totalizer_signals,
        "n_lv_zones": len(res.lv_zones),
        "metrics": res.metrics,
    }
    (salidas / "balance_red.json").write_text(
        _json.dumps(balance_dict, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    try:
        tl.to_parquet(salidas / "cargabilidad.parquet", index=False)
    except Exception:  # pragma: no cover - pyarrow opcional
        tl.to_csv(salidas / "cargabilidad.csv", index=False)

    # Motor de reglas de calidad sobre la red
    from ptnt.quality.rules import run_quality_rules
    from ptnt.ref.catalogs import load_conductor_catalog
    from ptnt.topology.graph import build_radial_graph

    cond = load_conductor_catalog(cfg.catalogos.conductores)
    graph = build_radial_graph(net.model)
    qr = run_quality_rules(graph, cond, cfg)
    if qr.findings:
        console.print(f"\n[bold]Calidad de datos:[/] {len(qr.findings)} hallazgos "
                      f"por regla {qr.by_rule()}")

    # Reporte ejecutivo HTML + exportador OpenDSS
    from ptnt.io.exporters import write_executive_report
    from ptnt.powerflow.opendss_export import write_dss

    write_executive_report(res.feeder_code, balance_dict, str(salidas / "reporte_ejecutivo.html"))
    write_dss(net.model, cond, str(salidas / f"{res.feeder_code}.dss"))
    console.print(f"[green]✓[/] Resultados de red en {salidas}/ "
                  f"(balance_red.json, reporte_ejecutivo.html, {res.feeder_code}.dss)")


@app.command()
def focalizar(
    csv: str = typer.Option(None, "--csv", help="CSV comercial (para el ranking de clientes)"),
    n_trafos: int = typer.Option(8, "--trafos", help="Nº de transformadores (red sintética)"),
    clientes_por_trafo: int = typer.Option(20, "--clientes-trafo"),
    top: int = typer.Option(15, "--top", help="Objetivos a mostrar por nivel"),
    ordenes: int = typer.Option(25, "--ordenes", help="Nº de órdenes de levantamiento"),
    config: str = _CONFIG_OPT,
):
    """**Dónde ir a hacer el levantamiento**: focalización por alimentador, zona,
    ramal, transformador, sector y cliente, con órdenes de trabajo priorizadas."""

    from pathlib import Path as _Path

    from ptnt.grid_pipeline import run_grid_analysis
    from ptnt.survey.collect import (
        collect_branch_stats,
        collect_transformer_stats,
        collect_zone_stats,
    )
    from ptnt.survey.report import write_survey_report
    from ptnt.survey.targeting import TargetLevel, build_survey_plan
    from ptnt.synth.network import generate_radial_network
    from ptnt.topology.graph import build_radial_graph

    cfg = _cargar(config)

    # 1) Red + balance (alimentador, zonas, ramales, transformadores)
    net = generate_radial_network(
        n_transformers=n_trafos, customers_per_tx=clientes_por_trafo
    )
    grid = run_grid_analysis(net.model, cfg, head_energy_kwh=net.head_energy_kwh)
    graph = build_radial_graph(net.model)

    # 2) Ranking de clientes (si hay CSV comercial)
    ranking = None
    coords = None
    suspect: set[str] = set()
    recuperable: dict[str, float] = {}
    if csv is None:
        try:
            csv = cfg.fuente("comercial_csv").ruta
        except KeyError:
            csv = None
    if csv and _Path(csv).exists():
        from ptnt.pipeline import run_analysis
        from ptnt.survey.collect import bind_commercial_customers

        com = run_analysis(cfg, csv, persistir=False)
        ranking = com.ranking
        # Vincula las cuentas contrato a los nodos de cliente de la red, para que
        # las señales de comportamiento agreguen a ramal, puesto y sector.
        coords_bind = None
        if {"x", "y"}.issubset(com.clientes.columns):
            coords_bind = com.clientes[["contract_account", "x", "y"]].copy()
            coords_bind["contract_account"] = coords_bind["contract_account"].astype(str)
        # Orden estable por cuenta (NO por score): vincular por score sesgaría la
        # red concentrando artificialmente los sospechosos en los primeros nodos.
        cuentas = sorted(ranking["contract_account"].astype(str).tolist())
        bind_commercial_customers(net.model, cuentas, coords_bind)
        graph = build_radial_graph(net.model)
        umbral = ranking["score"].quantile(0.95) if not ranking.empty else 1.0
        sos = ranking[ranking["score"] >= umbral]
        suspect = set(sos["contract_account"].astype(str))
        recuperable = dict(
            zip(ranking["contract_account"].astype(str), ranking["recuperable_kwh_mes"])
        )
        if {"x", "y"}.issubset(com.clientes.columns):
            coords = com.clientes[["contract_account", "x", "y"]].copy()
            coords["contract_account"] = coords["contract_account"].astype(str)
    else:
        console.print("[yellow]Sin CSV comercial: se focaliza solo con datos de red.[/]")

    # 3) Estadísticas por nivel
    b = grid.balance
    feeder_balances = [{
        "feeder_code": grid.feeder_code, "ntl_kwh": b.ntl_kwh, "ntl_pct": b.ntl_pct,
        "customers": sum(len(v) for v in net.model.customer_nodes.values()),
        "network_km": graph.total_length_km(), "confidence": 100.0,
        "balance_type": b.balance_type.value,
    }]
    # Rutas comerciales (CLIRLSCOD): sospecha + incoherencia de la ruta
    route_stats = []
    if ranking is not None:
        from ptnt.survey.routes import analyze_commercial_routes, routes_to_target_input

        base_rutas = com.clientes.merge(com.promedios, on="contract_account", how="left")
        rutas = analyze_commercial_routes(
            base_rutas, com.consumo, suspect_customers=suspect,
            recoverable_by_customer=recuperable,
        )
        route_stats = routes_to_target_input(rutas)

    branch_stats = collect_branch_stats(
        graph, suspect_customers=suspect, recoverable_by_customer=recuperable
    )
    transformer_stats = collect_transformer_stats(
        graph, grid.lv_zones, grid.transformer_loading,
        suspect_customers=suspect, recoverable_by_customer=recuperable,
    )
    zone_stats = collect_zone_stats(graph)

    # 4) Plan de levantamientos
    plan = build_survey_plan(
        feeder_balances=feeder_balances, zone_signals=zone_stats,
        branch_stats=branch_stats, transformer_stats=transformer_stats,
        route_stats=route_stats, customer_ranking=ranking, customer_coords=coords,
    )

    # 5) Mostrar
    console.print(f"\n[bold]📍 Plan de levantamientos[/] — "
                  f"{plan.resumen['n_objetivos']} objetivos priorizados")
    conteo = ", ".join(f"{k.replace('_',' ').title()}: {v}"
                       for k, v in plan.resumen["por_nivel"].items() if v)
    console.print(f"  Por nivel: {conteo}")

    for lvl in (TargetLevel.ALIMENTADOR, TargetLevel.ZONA_PROTECCION, TargetLevel.RAMAL,
                TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.RUTA_COMERCIAL,
                TargetLevel.SECTOR):
        objetivos = plan.by_level(lvl)
        if not objetivos:
            continue
        tabla = Table(title=f"{lvl.value.replace('_',' ').title()} — dónde inspeccionar")
        tabla.add_column("#", justify="right")
        tabla.add_column("Entidad")
        tabla.add_column("Prior.", justify="right")
        tabla.add_column("kWh/mes", justify="right")
        tabla.add_column("Clientes", justify="right")
        tabla.add_column("Motivo principal")
        for i, t in enumerate(objetivos[:top], start=1):
            tabla.add_row(str(i), t.entity_id, f"{t.priority_score:.3f}",
                          f"{t.recoverable_kwh_month:,.0f}", str(t.customers_count),
                          (t.reasons[0][:60] if t.reasons else "-"))
        console.print(tabla)

    # 6) Exportar (visible y reportable)
    salidas = _Path(cfg.rutas.salidas)
    salidas.mkdir(parents=True, exist_ok=True)
    df_plan = plan.to_dataframe()
    df_ot = plan.work_orders(top_n=ordenes)
    df_plan.to_csv(salidas / "plan_levantamientos.csv", index=False)
    df_ot.to_csv(salidas / "ordenes_levantamiento.csv", index=False)
    try:
        from ptnt.io.exporters import export_tables_xlsx

        export_tables_xlsx(
            {"Plan": df_plan, "Ordenes": df_ot,
             **{f"N_{l.value[:12]}": plan.to_dataframe(l)
                for l in TargetLevel if plan.by_level(l)}},
            str(salidas / "focalizacion.xlsx"),
        )
    except Exception as exc:  # pragma: no cover
        console.print(f"[yellow]XLSX no generado ({exc}); CSV disponible.[/]")
    write_survey_report(plan, str(salidas / "reporte_focalizacion.html"))
    import json as _json

    (salidas / "plan_levantamientos.json").write_text(
        _json.dumps({"resumen": plan.resumen,
                     "objetivos": _json.loads(df_plan.to_json(orient="records"))},
                    ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    console.print(f"\n[bold]Órdenes de levantamiento generadas:[/] {len(df_ot)}")
    console.print(f"[green]✓[/] {salidas}/reporte_focalizacion.html · "
                  f"focalizacion.xlsx · plan_levantamientos.csv · ordenes_levantamiento.csv")


@app.command()
def diagnostico(
    csv: str = typer.Option(None, "--csv", help="CSV comercial"),
    cabecera: str = typer.Option(None, "--cabecera",
                                 help="CSV de energía de cabecera (feeder_code,period,kwh_delivered)"),
    multados: str = typer.Option(None, "--multados",
                                 help="CSV de clientes multados por hurto (contract_account,fecha_multa)"),
    config: str = _CONFIG_OPT,
):
    """Diagnóstico de credibilidad: **transferencias no reportadas**, **clientes
    faltantes**, **alimentadores incoherentes** y **validación contra la base de
    multados** (precisión real del detector)."""

    from pathlib import Path as _Path

    from ptnt.anomalies import (
        analyze_feeder_coherence,
        analyze_unmatched_customers,
        detect_transfers,
    )
    from ptnt.grid_pipeline import run_grid_analysis
    from ptnt.synth.network import generate_radial_network

    cfg = _cargar(config)
    salidas = _Path(cfg.rutas.salidas)
    salidas.mkdir(parents=True, exist_ok=True)
    resumen_total = {}

    # --- 1. Transferencias entre alimentadores (§10.4) ----------------------
    console.print("\n[bold]1. Transferencias entre alimentadores no reportadas[/]")
    if cabecera and _Path(cabecera).exists():
        head = pd.read_csv(cabecera)
        tr = detect_transfers(head)
    else:
        tr = detect_transfers(pd.DataFrame())
    if tr.status.value != "OK":
        console.print(f"  [yellow]{tr.status.value}[/]: {tr.detail}")
    elif tr.candidates:
        tabla = Table(title="Transferencias probables")
        for col in ("Par", "Período", "Magnitud kWh", "Simetría", "Confianza"):
            tabla.add_column(col)
        for c in tr.candidates[:10]:
            tabla.add_row(f"{c.feeder_a} → {c.feeder_b}", c.period,
                          f"{c.magnitude_kwh:,.0f}", f"{c.similarity:.2f}",
                          f"{c.confidence:.2f}")
        console.print(tabla)
        console.print(f"  [yellow]{tr.detail}[/]")
        tr.to_dataframe().to_csv(salidas / "transferencias.csv", index=False)
    else:
        console.print(f"  [green]{tr.detail}[/]")
    resumen_total["transferencias"] = {
        "estado": tr.status.value, "n": len(tr.candidates),
        "afectados": sorted(tr.feeders_afectados), "detalle": tr.detail,
    }

    # --- 2. Clientes faltantes ----------------------------------------------
    console.print("\n[bold]2. Clientes faltantes (vinculación comercial ↔ SIG)[/]")
    unm = None
    if csv is None:
        try:
            csv = cfg.fuente("comercial_csv").ruta
        except KeyError:
            csv = None
    if csv and _Path(csv).exists():
        from ptnt.pipeline import run_analysis

        com = run_analysis(cfg, csv, persistir=False)
        base = com.clientes.merge(com.promedios, on="contract_account", how="left")
        net = generate_radial_network(n_transformers=8, customers_per_tx=20)
        # el SIG "tiene" a los clientes vinculados (los primeros N por cuenta)
        cuentas_ordenadas = sorted(base["contract_account"].astype(str))
        n_en_sig = int(len(cuentas_ordenadas) * 0.97)   # 3% sin vincular (demo)
        sig = pd.DataFrame({"contract_account": cuentas_ordenadas[:n_en_sig]})
        unm = analyze_unmatched_customers(
            base, sig,
            umbral_energia_vinculada_pct=cfg.comercial.__dict__.get(
                "umbral_min_energia_vinculada_pct", 95.0),
        )
        console.print(f"  Cuentas vinculadas: {unm.pct_cuentas_vinculadas:.1f}% · "
                      f"[bold]Energía vinculada: {unm.pct_energia_vinculada:.1f}%[/]")
        console.print(f"  CSV sin SIG: {len(unm.csv_sin_sig):,} cuentas "
                      f"({unm.energia_sin_vincular_kwh:,.0f} kWh sin ubicar)")
        console.print(f"  SIG sin CSV: {len(unm.sig_sin_csv):,} clientes")
        console.print(f"  {'[green]' if unm.apto_balance_medido else '[yellow]'}{unm.detail}[/]")
        unm.csv_sin_sig.to_csv(salidas / "clientes_csv_sin_sig.csv", index=False)
        unm.sig_sin_csv.to_csv(salidas / "clientes_sig_sin_csv.csv", index=False)
        if not unm.por_ruta.empty:
            console.print("\n  Rutas comerciales con más energía sin vincular:")
            for _, r in unm.por_ruta.head(5).iterrows():
                console.print(f"    {r.iloc[0]}: {int(r['cuentas_sin_sig'])} cuentas, "
                              f"{r['energia_sin_sig_kwh']:,.0f} kWh")
        resumen_total["clientes_faltantes"] = unm.resumen()
    else:
        console.print("  [yellow]Sin CSV comercial: no se puede analizar la vinculación.[/]")

    # --- 3. Alimentadores incoherentes --------------------------------------
    console.print("\n[bold]3. Alimentadores con valores incoherentes[/]")
    net = generate_radial_network(n_transformers=8, customers_per_tx=20)
    grid = run_grid_analysis(net.model, cfg, head_energy_kwh=net.head_energy_kwh)
    coh = analyze_feeder_coherence([grid.balance], transfer_report=tr,
                                   unmatched_report=unm)
    df_coh = coh.to_dataframe()
    if df_coh.empty:
        console.print("  [green]Sin incoherencias: todos los alimentadores son publicables.[/]")
    else:
        tabla = Table(title="Incoherencias detectadas")
        for col in ("Alimentador", "Código", "Severidad", "Descripción", "Causa probable"):
            tabla.add_column(col, overflow="fold")
        for _, r in df_coh.head(12).iterrows():
            tabla.add_row(str(r["alimentador"]), str(r["codigo"]), str(r["severidad"]),
                          str(r["descripcion"])[:52], str(r["causa_probable"])[:52])
        console.print(tabla)
        df_coh.to_csv(salidas / "alimentadores_incoherentes.csv", index=False)
    resumen_total["incoherencias"] = coh.resumen()

    # --- 4. Validación contra la base de multados ---------------------------
    console.print("\n[bold]4. Validación contra la base de clientes multados[/]")
    if multados and _Path(multados).exists() and csv and _Path(csv).exists():
        from ptnt.ntl.confirmed import (
            calibrate_signal_thresholds,
            load_confirmed_theft,
            validate_against_confirmed,
        )

        conf = load_confirmed_theft(pd.read_csv(multados))
        for a in conf.advertencias:
            console.print(f"  [yellow]⚠ {a}[/]")
        met = validate_against_confirmed(com.ranking, conf)
        for a in met.advertencias:
            console.print(f"  [yellow]⚠ {a}[/]")
        res = met.resumen()
        console.print(f"  Casos confirmados en el universo: {res['n_confirmados']:,} "
                      f"(prevalencia {res['prevalencia_pct']}%)")
        tabla = Table(title="Desempeño real del detector")
        for col in ("Métrica", "Valor"):
            tabla.add_column(col)
        tabla.add_row("Precisión en el top 1%", f"{res['precision_top_1pct']}%")
        tabla.add_row("Precisión en el top 5%", f"{res['precision_top_5pct']}%")
        tabla.add_row("Recall en el top 10%", f"{res['recall_top_10pct']}%")
        tabla.add_row("Lift en el top 5%", f"{res['lift_top_5pct']}×")
        tabla.add_row("Posición mediana de los hurtos", f"{res['posicion_mediana_pct']}%")
        tabla.add_row("AUC", f"{res['auc']}")
        console.print(tabla)
        console.print(f"  [bold]Interpretación:[/] siguiendo el ranking, la cuadrilla "
                      f"encuentra [bold]{res['lift_top_5pct']}× más hurtos[/] que "
                      "inspeccionando al azar.")
        if not met.por_decil.empty:
            met.por_decil.to_csv(salidas / "validacion_por_decil.csv", index=False)
        calib = calibrate_signal_thresholds(com.señales, conf)
        if not calib.empty:
            console.print("\n  Calibración de señales contra casos confirmados:")
            for _, r in calib.iterrows():
                console.print(f"    {r['senal']}: lift {r['lift']} → {r['recomendacion']}")
            calib.to_csv(salidas / "calibracion_senales.csv", index=False)
        resumen_total["validacion_multados"] = res
    else:
        console.print("  [yellow]Sin base de multados (--multados): el detector no puede "
                      "validarse contra casos reales.[/]")
        console.print("  Formato esperado: CSV con columnas "
                      "[cyan]contract_account[/], [cyan]fecha_multa[/] "
                      "(opcional: kwh_recuperado, tipo_hallazgo).")

    import json as _json

    (salidas / "diagnostico.json").write_text(
        _json.dumps(resumen_total, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    console.print(f"\n[green]✓[/] Diagnóstico en {salidas}/diagnostico.json")


@app.command()
def validar_flujo(
    tolerancia_pct: float = typer.Option(2.0, "--tol"),
    config: str = _CONFIG_OPT,
):
    """Valida el motor de flujo de potencia contra casos radiales de solución
    analítica cerrada (fallo si el error supera la tolerancia)."""

    from ptnt.powerflow.validation import run_validation_suite

    _cargar(config)
    casos = run_validation_suite(tol_pct=tolerancia_pct)
    tabla = Table(title="Validación del flujo de potencia")
    tabla.add_column("Caso")
    tabla.add_column("Esperado kW", justify="right")
    tabla.add_column("Calculado kW", justify="right")
    tabla.add_column("Error %", justify="right")
    tabla.add_column("Estado")
    todos_ok = True
    for c in casos:
        estado = "[green]PASA[/]" if c.passed else "[red]FALLA[/]"
        todos_ok &= c.passed
        tabla.add_row(c.name, f"{c.loss_kw_expected:.4f}", f"{c.loss_kw_computed:.4f}",
                      f"{c.error_pct:.3f}", estado)
    console.print(tabla)
    if not todos_ok:
        raise typer.Exit(code=1)


@app.command()
def dashboard(config: str = _CONFIG_OPT):
    """Lanza el tablero de análisis para escritorio (Streamlit)."""

    import subprocess

    cfg = _cargar(config)
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(cfg.dashboard.puerto),
        "--server.address", cfg.dashboard.host,
        "--", "--config", config,
    ]
    console.print(f"[cyan]Iniciando dashboard en http://{cfg.dashboard.host}:{cfg.dashboard.puerto}[/]")
    subprocess.run(cmd, check=False)


@app.command()
def servir_visor(config: str = _CONFIG_OPT):
    """Lanza el visor web de solo lectura (FastAPI) para consulta por terceros."""

    cfg = _cargar(config)
    try:
        import uvicorn
    except ImportError:
        console.print("[red]Instale el visor: pip install 'ptnt-bal[webviewer]'[/]")
        raise typer.Exit(code=2)
    import os

    os.environ["PTNT_CONFIG"] = config
    from ptnt.webviewer.app import create_app

    console.print(f"[cyan]Visor en http://{cfg.visor.host}:{cfg.visor.puerto}[/]")
    uvicorn.run(create_app(config), host=cfg.visor.host, port=cfg.visor.puerto)


@app.command()
def crear_usuario(
    usuario: str = typer.Argument(..., help="Nombre de usuario"),
    rol: str = typer.Option("viewer", "--rol", help="viewer | analyst | admin"),
    unidad: str = typer.Option("", "--unidad",
                               help="Unidad(es) de negocio, separadas por coma"),
    matriz: bool = typer.Option(False, "--matriz",
                                help="Oficina central: ve todas las unidades"),
    config: str = _CONFIG_OPT,
):
    """Crea un usuario para las interfaces web (contraseña por prompt seguro).

    El **alcance** determina qué datos puede ver: un analista de una unidad de
    negocio no ve el padrón de otra. La matriz las ve todas y elige cuál
    analizar.
    """

    from ptnt.security.auth import AuthError, UserStore

    cfg = _cargar(config)
    unidades = [u.strip() for u in unidad.split(",") if u.strip()]
    password = typer.prompt("Contraseña", hide_input=True, confirmation_prompt=True)
    store = UserStore(cfg.seguridad.ruta_usuarios)
    try:
        store.add_user(usuario, password, rol, unidades=unidades, matriz=matriz)
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)

    alcance = ("todas las unidades (matriz)" if matriz or rol == "admin"
               else ", ".join(unidades) if unidades else "SIN ALCANCE")
    console.print(f"[green]✓[/] Usuario '{usuario}' ({rol}) creado en "
                  f"{cfg.seguridad.ruta_usuarios}")
    console.print(f"  Alcance: {alcance}")
    if not matriz and rol != "admin" and not unidades:
        # No se rechaza el alta —crear y asignar después es legítimo— pero se
        # avisa aquí, que es donde se puede corregir en el momento.
        console.print("[yellow]⚠ Sin unidad asignada este usuario no verá ningún "
                      f"dato. Asígnela con:[/] ptnt usuario-unidad {usuario} "
                      "--unidad CNEL-GYE")


@app.command()
def usuario_unidad(
    usuario: str = typer.Argument(..., help="Nombre de usuario"),
    unidad: str = typer.Option("", "--unidad",
                               help="Unidad(es) de negocio, separadas por coma"),
    matriz: bool = typer.Option(None, "--matriz/--no-matriz",
                                help="Marcar o quitar el alcance de matriz"),
    config: str = _CONFIG_OPT,
):
    """Cambia a qué unidades de negocio alcanza un usuario."""

    from ptnt.security.auth import AuthError, UserStore

    cfg = _cargar(config)
    store = UserStore(cfg.seguridad.ruta_usuarios)
    unidades = [u.strip() for u in unidad.split(",") if u.strip()]
    try:
        store.set_unidades(usuario, unidades, matriz=matriz)
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)

    from ptnt.security.scope import Alcance
    u = next(x for x in store.usuarios() if x.username == usuario)
    console.print(f"[green]✓[/] {usuario}: {Alcance.desde_usuario(u).descripcion()}")


@app.command()
def usuarios(config: str = _CONFIG_OPT):
    """Lista los usuarios y qué alcanza cada uno."""

    from ptnt.security.auth import UserStore
    from ptnt.security.scope import Alcance

    cfg = _cargar(config)
    store = UserStore(cfg.seguridad.ruta_usuarios)
    if not len(store):
        console.print("[yellow]No hay usuarios creados todavía.[/] "
                      "Cree uno con [cyan]ptnt crear-usuario[/].")
        return

    tabla = Table(title="Usuarios y alcance")
    for c in ("Usuario", "Rol", "Alcance", "Estado"):
        tabla.add_column(c)
    for u in store.usuarios():
        a = Alcance.desde_usuario(u)
        estado = "[red]deshabilitado[/]" if u.disabled else "activo"
        alcance = a.descripcion()
        if a.sin_alcance:
            alcance = f"[yellow]{alcance}[/]"
        tabla.add_row(u.username, u.role, alcance, estado)
    console.print(tabla)



@app.command()
def registrar_carga(
    insumo: str = typer.Option(..., "--insumo",
                               help="PADRON_COMERCIAL|RED|CABECERA|MULTADOS|SIG_CLIENTES|JERARQUIA"),
    alimentadores: str = typer.Option(..., "--alimentadores",
                                      help="Códigos separados por coma, o @archivo.txt"),
    origen: str = typer.Option("", "--origen", help="Archivo, base o responsable"),
    desde: str = typer.Option(None, "--desde", help="Período desde (YYYY-MM)"),
    hasta: str = typer.Option(None, "--hasta", help="Período hasta (YYYY-MM)"),
    config: str = _CONFIG_OPT,
):
    """Declara el alcance de una carga parcial de información (D06).

    La información no llega toda junta. Declarar qué entró permite que un
    consolidado incompleto se marque PARCIAL en vez de leerse como el total.
    """

    from ptnt.ingest import AlcanceCarga, Insumo

    cfg = _cargar(config)
    try:
        ins = Insumo(insumo.upper())
    except ValueError:
        console.print(f"[red]Insumo desconocido: {insumo}[/]")
        console.print(f"Válidos: {', '.join(i.value for i in Insumo)}")
        raise typer.Exit(code=2)

    if alimentadores.startswith("@"):
        ruta = Path(alimentadores[1:])
        if not ruta.exists():
            console.print(f"[red]No existe el archivo: {ruta}[/]")
            raise typer.Exit(code=2)
        lista = [x.strip() for x in ruta.read_text().splitlines() if x.strip()]
    else:
        lista = [x.strip() for x in alimentadores.split(",") if x.strip()]
    if not lista:
        console.print("[red]No se indicó ningún alimentador.[/]")
        raise typer.Exit(code=2)

    alcance = AlcanceCarga.load(cfg.carga_parcial.ruta_alcance)
    if cfg.carga_parcial.universo_alimentadores:
        alcance.universo_alimentadores = list(cfg.carga_parcial.universo_alimentadores)
    carga = alcance.registrar(ins, lista, origen=origen,
                              periodo_desde=desde, periodo_hasta=hasta)
    ruta = alcance.save(cfg.carga_parcial.ruta_alcance)

    console.print(f"[green]✓[/] Registrados {len(lista)} alimentador(es) de "
                  f"[cyan]{ins.value}[/] · alcance en {ruta}")
    for adv in carga.advertencias:
        console.print(f"[yellow]⚠ {adv}[/]")

    tabla = Table(title="Cobertura por insumo")
    for c in ("Insumo", "Cargados", "Esperados", "Cobertura", "Estado"):
        tabla.add_column(c, justify="right" if c != "Insumo" else "left")
    for _, r in alcance.resumen().iterrows():
        color = {"COMPLETA": "green", "PARCIAL": "yellow", "VACIA": "red"}[r["estado"]]
        tabla.add_row(r["insumo"], f"{r['alimentadores_cargados']:,}",
                      f"{r['esperados']:,}", f"{r['cobertura_pct']:.1f}%",
                      f"[{color}]{r['estado']}[/]")
    console.print(tabla)

    listos = alcance.listos_para_balance_medido()
    console.print(f"\nListos para balance [bold]MEDIDO[/] (padrón + red + cabecera): "
                  f"[cyan]{len(listos)}[/] alimentador(es)")


@app.command()
def consolidar(
    balance: str = typer.Option("outputs/balance_alimentadores.csv", "--balance"),
    nivel: str = typer.Option("SUBESTACION", "--nivel",
                              help="UNIDAD_NEGOCIO|SUBESTACION|ALIMENTADOR"),
    registrar_historico: bool = typer.Option(
        False, "--historico/--no-historico",
        help="Guarda una instantánea en el histórico de balance"),
    periodo: str = typer.Option(None, "--periodo", help="Período (YYYY-MM)"),
    config: str = _CONFIG_OPT,
):
    """Consolida el balance por unidad de negocio y subestación.

    La energía se suma hacia arriba; la credibilidad no: basta un alimentador
    INDICATIVO para que el consolidado no pueda presentarse como MEDIDO.
    """

    from ptnt.org import (
        agregar_balance, jerarquia_desde_alimentadores, load_jerarquia,
    )

    cfg = _cargar(config)
    if not Path(balance).exists():
        console.print(f"[red]No existe el balance: {balance}[/]")
        console.print("Genérelo con [cyan]ptnt analizar-red[/].")
        raise typer.Exit(code=2)

    df = pd.read_csv(balance)
    if cfg.organizacion.catalogo and Path(cfg.organizacion.catalogo).exists():
        jer = load_jerarquia(cfg.organizacion.catalogo)
    elif cfg.organizacion.inferir_si_falta:
        jer = jerarquia_desde_alimentadores(
            df["alimentador"].astype(str).tolist(),
            separador=cfg.organizacion.separador_codigo)
    else:
        console.print("[red]Sin catálogo organizacional "
                      "(`organizacion.catalogo`).[/]")
        raise typer.Exit(code=2)
    for adv in jer.advertencias:
        console.print(f"[yellow]⚠ {adv}[/]")

    agregados = agregar_balance(df, jer)
    d = agregados[nivel.upper()]

    if "tipo_balance" in d.columns:
        no_medidos = int((d["tipo_balance"] != "MEDIDO").sum())
        if no_medidos:
            console.print(
                f"[bold yellow]⚠ {no_medidos} de {len(d)} consolidados NO son "
                "MEDIDO: su PNT es estimación, no debe presentarse como "
                "verificada.[/]")

    tabla = Table(title=f"Consolidado por {nivel.upper()}")
    cols = [c for c in ("unidad_negocio", "subestacion", "alimentador",
                        "entrada_kwh", "pnt_kwh", "pnt_pct", "alimentadores",
                        "tipo_balance", "alimentadores_indicativos")
            if c in d.columns]
    for c in cols:
        tabla.add_column(c, justify="right" if "kwh" in c or "pct" in c else "left")
    for _, r in d.head(30).iterrows():
        tabla.add_row(*[
            f"{r[c]:,.0f}" if isinstance(r[c], (int, float)) and "pct" not in c
            else (f"{r[c]:.2f}" if "pct" in c else str(r[c]))
            for c in cols])
    console.print(tabla)

    salida = Path(cfg.rutas.salidas) / f"consolidado_{nivel.lower()}.csv"
    salida.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(salida, index=False)
    console.print(f"[green]✓[/] {salida}")

    if registrar_historico and cfg.historico.habilitado:
        from ptnt.config.loader import config_hash
        from ptnt.store.history import HistoricoBalance

        per = periodo or cfg.comercial.mes_final[:7]
        hist = HistoricoBalance.load(cfg.historico.ruta)
        n = hist.registrar(agregados, periodo=per,
                           config_hash=config_hash(cfg)[:12])
        ruta = hist.save()
        console.print(f"[green]✓[/] {n} instantánea(s) del período {per} en {ruta}")
        for adv in hist.advertencias():
            console.print(f"[yellow]⚠ {adv}[/]")


@app.command()
def campo_usuario(
    usuario: str = typer.Argument(..., help="Nombre de usuario móvil"),
    nombre: str = typer.Option(..., "--nombre", help="Nombre completo"),
    rol: str = typer.Option("TECNICO", "--rol", help="TECNICO|SUPERVISOR|LECTOR"),
    unidad: str = typer.Option("", "--unidad", help="Unidad de negocio"),
    config: str = _CONFIG_OPT,
):
    """Crea un usuario de la aplicación móvil (contraseña por prompt seguro)."""

    from ptnt.field import RegistroCampo, RolCampo

    cfg = _cargar(config)
    ruta = Path(cfg.rutas.salidas) / "campo" / "registro.json"
    reg = RegistroCampo(ruta)
    password = typer.prompt("Contraseña", hide_input=True, confirmation_prompt=True)
    try:
        u = reg.crear_usuario(usuario, nombre, password, rol=RolCampo(rol.upper()),
                              unidad_negocio=unidad)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)
    reg.save()
    console.print(f"[green]✓[/] Usuario móvil [cyan]{u.usuario}[/] ({u.rol.value}) "
                  f"creado en {ruta}")
    console.print("  El técnico vincula su equipo desde la app; el token se emite "
                  "en ese momento y se puede revocar.")


@app.command()
def campo_asignar(
    usuario: str = typer.Option(..., "--usuario", help="Técnico destinatario"),
    ordenes: str = typer.Option("outputs/ordenes_levantamiento.csv", "--ordenes"),
    top: int = typer.Option(10, "--top", help="Cuántas órdenes asignar"),
    radio: float = typer.Option(150.0, "--radio", help="Radio del área (m)"),
    config: str = _CONFIG_OPT,
):
    """Asigna órdenes de levantamiento a un técnico de campo."""

    from ptnt.field import RegistroCampo

    cfg = _cargar(config)
    if not Path(ordenes).exists():
        console.print(f"[red]No existe {ordenes}[/]. Ejecute [cyan]ptnt focalizar[/].")
        raise typer.Exit(code=2)

    reg = RegistroCampo(Path(cfg.rutas.salidas) / "campo" / "registro.json")
    df = pd.read_csv(ordenes).head(top)
    try:
        nuevas = reg.asignar(df, usuario, asignado_por="cli", radio_m=radio)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)
    reg.save()

    tabla = Table(title=f"Órdenes asignadas a {usuario}")
    for c in ("Orden", "Nivel", "Entidad", "Clientes", "kWh/mes"):
        tabla.add_column(c, justify="right" if c in ("Clientes", "kWh/mes") else "left")
    for a in nuevas:
        tabla.add_row(a.orden_trabajo, a.nivel, a.entidad,
                      f"{a.clientes_a_revisar:,}", f"{a.recuperable_kwh_mes:,.0f}")
    console.print(tabla)
    console.print(f"[green]✓[/] {len(nuevas)} orden(es) · "
                  f"{sum(a.clientes_a_revisar for a in nuevas):,} clientes · "
                  f"{sum(a.recuperable_kwh_mes for a in nuevas):,.0f} kWh/mes")
    console.print("  Genere el paquete con: "
                  f"[cyan]ptnt campo-paquete --usuario {usuario}[/]")


@app.command()
def campo_revisar(
    lote: str = typer.Argument(..., help="Identificador del lote recibido"),
    aceptar_todo: bool = typer.Option(False, "--aceptar-todo"),
    rechazar: str = typer.Option("", "--rechazar",
                                 help="Secuencias a rechazar, separadas por coma"),
    revisor: str = typer.Option("supervisor", "--revisor"),
    config: str = _CONFIG_OPT,
):
    """Revisa un lote de cambios de campo y determina qué recalcular."""

    import json as _json

    from ptnt.field.sync import (
        CambioRecibido, EstadoRevision, HistoricoCambios, LoteSincronizacion,
        aplicar, revisar,
    )

    cfg = _cargar(config)
    dir_campo = Path(cfg.rutas.salidas) / "campo"
    archivo = dir_campo / "lotes" / f"{lote}.json"
    if not archivo.exists():
        console.print(f"[red]No existe el lote {lote}[/] en {archivo.parent}")
        raise typer.Exit(code=2)

    d = _json.loads(archivo.read_text(encoding="utf-8"))
    obj = LoteSincronizacion(
        lote_id=d["lote_id"], usuario=d.get("usuario", ""),
        paquete_id=d.get("paquete_id", ""), recibido_en=d.get("recibido_en", ""),
        fotos=d.get("fotos", []), ordenes=d.get("ordenes", []),
    )
    for c in d.get("cambios", []):
        c = dict(c)
        c["estado_revision"] = EstadoRevision(c.get("estado_revision", "PENDIENTE"))
        obj.cambios.append(CambioRecibido(**c))

    console.print(f"\n[bold]Lote {lote}[/] · técnico {obj.usuario}")
    console.print(obj.to_dataframe().to_string(index=False))

    rech = [int(x) for x in rechazar.split(",") if x.strip().isdigit()]
    acep = [c.secuencia for c in obj.cambios if c.secuencia not in rech] \
        if (aceptar_todo or rech) else []
    try:
        r = revisar(obj, aceptar=acep, rechazar=rech, revisor=revisor)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)

    console.print(f"\n[green]Aceptados: {r['aceptados']}[/] · "
                  f"[yellow]Rechazados: {r['rechazados']}[/]")
    if r["advertencia"]:
        console.print(f"[bold yellow]⚠ {r['advertencia']}[/]")

    ap = aplicar(obj)
    console.print(f"\n[bold]{ap.detalle}[/]")
    if ap.etapas_a_recalcular:
        console.print("\n[bold]Recálculo necesario:[/]")
        for e in ap.etapas_a_recalcular:
            console.print(f"  • {e}")
        console.print("\n  Ejecute: [cyan]ptnt analizar-red[/] y "
                      "[cyan]ptnt focalizar[/] para actualizar el balance y el "
                      "ranking con estos cambios.")

    hist = HistoricoCambios(dir_campo / "historico_cambios.parquet")
    n = hist.registrar_lote(obj)
    hist.save()
    console.print(f"[green]✓[/] {n} cambio(s) al histórico permanente")


@app.command()
def campo_servir(
    host: str = typer.Option("127.0.0.1", "--host"),
    puerto: int = typer.Option(8090, "--puerto"),
    config: str = _CONFIG_OPT,
):
    """Lanza la API de sincronización para la aplicación móvil."""

    import uvicorn

    from ptnt.field.api import crear_app

    cfg = _cargar(config)
    dir_campo = Path(cfg.rutas.salidas) / "campo"
    console.print(f"[green]API móvil[/] en http://{host}:{puerto}")
    console.print(f"  Registro:  {dir_campo / 'registro.json'}")
    console.print(f"  Paquetes:  {dir_campo / 'paquetes'}")
    console.print(f"  Capacidad: {cfg.recursos.descargas_simultaneas} descarga(s) "
                  f"y {cfg.recursos.subidas_simultaneas} subida(s) simultáneas · "
                  f"cola de {cfg.recursos.max_en_cola_api}")
    console.print("  Lo que no cabe espera; si la cola se llena se responde 503 "
                  "con Retry-After y el paquete se guarda igual.")
    uvicorn.run(
        crear_app(
            registro_ruta=dir_campo / "registro.json",
            paquetes_dir=dir_campo / "paquetes",
            entrantes_dir=dir_campo / "entrantes",
            lotes_dir=dir_campo / "lotes",
            recursos=cfg.recursos,
        ),
        host=host, port=puerto,
    )


@app.command()
def campo_paquete(
    usuario: str = typer.Option(..., "--usuario", help="Técnico destinatario"),
    red: str = typer.Option(None, "--red",
                            help="DuckDB/Parquet con la red migrada; omitir usa el escenario de demo"),
    teselas: str = typer.Option(None, "--teselas",
                                help="MBTiles de cartografía base (open source o caché de ArcGIS Server)"),
    margen: float = typer.Option(250.0, "--margen", help="Margen alrededor del área (m)"),
    config: str = _CONFIG_OPT,
):
    """Genera el GeoPackage descargable con la red del área de trabajo."""

    from ptnt.field import RegistroCampo, construir_paquete

    cfg = _cargar(config)
    dir_campo = Path(cfg.rutas.salidas) / "campo"
    reg = RegistroCampo(dir_campo / "registro.json")
    asigs = reg.de_usuario(usuario)
    if not asigs:
        console.print(f"[red]{usuario} no tiene órdenes asignadas.[/] "
                      f"Use [cyan]ptnt campo-asignar --usuario {usuario}[/].")
        raise typer.Exit(code=2)

    capas, conexiones = _red_de_campo(red, asigs)

    destino = dir_campo / "paquetes" / f"{usuario}.gpkg"
    res = construir_paquete(
        destino, usuario=usuario, asignaciones=asigs, red=capas,
        conexiones=conexiones, teselas=teselas, margen_m=margen,
        version_red=cfg.proyecto.version_config,
    )

    tabla = Table(title=f"Paquete de campo — {usuario}")
    tabla.add_column("Concepto"); tabla.add_column("Valor", justify="right")
    for k, v in res.resumen().items():
        tabla.add_row(k.replace("_", " ").capitalize(), f"{v:,}"
                      if isinstance(v, (int, float)) else str(v))
    console.print(tabla)
    for a in res.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")
    console.print(f"[green]✓[/] {destino}")
    console.print("  El técnico lo descarga desde la app "
                  "([cyan]ptnt campo-servir[/] debe estar corriendo).")


def _red_de_campo(red: str | None, asigs: list):
    """Carga la red para armar paquetes: desde DuckDB, o el escenario de demo."""

    if red and Path(red).exists():
        import duckdb
        con = duckdb.connect(red, read_only=True)
        capas = {}
        for t in ("ptnt_puesto_transformacion", "ptnt_cliente", "ptnt_tramo",
                  "ptnt_poste", "ptnt_luminaria", "ptnt_seccionador",
                  "ptnt_capacitor", "ptnt_unidad_transformacion"):
            try:
                capas[t] = con.execute(f"SELECT * FROM {t}").df()
            except Exception:
                capas[t] = pd.DataFrame()
        try:
            conexiones = con.execute("SELECT * FROM ptnt_conexion").df()
        except Exception:
            conexiones = pd.DataFrame()
        con.close()
        return capas, conexiones

    console.print("[yellow]Sin --red: se usa el escenario de demostración.[/]")
    from ptnt.field.demo_red import red_de_demostracion
    return red_de_demostracion(asigs)


@app.command()
def recursos(
    coste_mb: int = typer.Option(0, "--coste-mb",
                                 help="Memoria por tarea a simular; 0 usa la del YAML"),
    medir: bool = typer.Option(False, "--medir",
                               help="Mide el coste real de un alimentador sintético"),
    config: str = _CONFIG_OPT,
):
    """Muestra cuántas tareas caben en este equipo, y por qué ese número.

    Sirve para dos cosas: comprobar que el servidor está dimensionado antes de
    lanzar un recálculo de once unidades de negocio, y **medir** cuánta memoria
    consume de verdad un alimentador para ajustar `recursos.coste_mb_por_tarea`
    en vez de adivinarlo.
    """

    from ptnt.runtime.pool import _VENTANA_PRIORIDAD
    from ptnt.runtime.resources import Recursos, calcular_presupuesto

    cfg = _cargar(config)
    r = Recursos.detectar()

    tabla = Table(title="Recursos del equipo")
    tabla.add_column("Concepto"); tabla.add_column("Valor", justify="right")
    for k, v in r.resumen().items():
        tabla.add_row(k.replace("_", " ").capitalize(),
                      f"{v:,}" if isinstance(v, int) else str(v))
    console.print(tabla)

    if r.en_contenedor:
        # Sin decirlo, quien vea 2 GB en un servidor de 64 creerá que la
        # plataforma mide mal y subirá los límites a mano hasta que el núcleo
        # mate el contenedor.
        console.print(f"  [yellow]Se está dentro de un contenedor[/] "
                      f"({r.contenedor}): manda su límite, no lo que tenga el "
                      f"anfitrión.")

    if medir:
        pico = _medir_coste_alimentador()
        console.print(f"\n[bold]Medición sobre un alimentador sintético:[/] "
                      f"{pico:,} MB de pico")
        console.print("  Ajuste [cyan]recursos.coste_mb_por_tarea[/] a ese valor "
                      "con margen. Un alimentador urbano real consume más que el "
                      "sintético: mida con el suyo antes de producción.")
        coste_mb = coste_mb or pico

    rec = cfg.recursos
    efectivo = coste_mb or rec.coste_mb_por_tarea

    tabla2 = Table(title="Cuántas tareas caben")
    for c in ("Tipo de trabajo", "Trabajadores", "Limita", "Coste/tarea"):
        tabla2.add_column(c, justify="right" if c != "Tipo de trabajo" else "left")

    calculo = calcular_presupuesto(
        coste_mb_por_tarea=efectivo, cpus_maximos=rec.cpus,
        ram_reservada_mb=rec.ram_reservada_mb,
        fraccion_ram_utilizable=rec.fraccion_ram_utilizable,
        tope=rec.max_trabajadores or None, recursos=r)
    tabla2.add_row("Alimentadores (cálculo)", str(calculo.trabajadores),
                   calculo.limitado_por, f"{efectivo:,} MB")

    lectura = calcular_presupuesto(
        coste_mb_por_tarea=max(1, efectivo // 8),
        cpus_maximos=rec.lecturas_simultaneas,
        ram_reservada_mb=rec.ram_reservada_mb,
        fraccion_ram_utilizable=rec.fraccion_ram_utilizable,
        tope=rec.lecturas_simultaneas, ligado_a_cpu=False, recursos=r)
    tabla2.add_row("Lecturas de bases (E/S)", str(lectura.trabajadores),
                   lectura.limitado_por, f"{max(1, efectivo // 8):,} MB")
    console.print(tabla2)
    console.print(f"  {calculo.explicacion()}")

    tabla3 = Table(title="Concurrencia de la API móvil")
    for c in ("Operación", "Simultáneas", "Cola"):
        tabla3.add_column(c, justify="right" if c != "Operación" else "left")
    tabla3.add_row("Descargas de paquete", str(rec.descargas_simultaneas),
                   str(rec.max_en_cola_api))
    tabla3.add_row("Subidas de trabajo", str(rec.subidas_simultaneas),
                   str(rec.max_en_cola_api))
    tabla3.add_row("Consultas de órdenes",
                   str(max(8, rec.descargas_simultaneas * 4)),
                   str(rec.max_en_cola_api * 2))
    console.print(tabla3)
    console.print("  Lo que no cabe **espera en cola**; si la cola se llena se "
                  "responde 503 con [cyan]Retry-After[/], que la app reintenta "
                  "sola. El paquete de retorno se guarda igual: el trabajo del "
                  "día no se pierde por una cola llena.")

    console.print(
        f"\n[bold]Cola de trabajo:[/] las tareas con prioridad más alta se "
        f"atienden antes (ventana de "
        f"{rec.ventana_prioridad or _VENTANA_PRIORIDAD:,} tareas). "
        f"Una lectura que falle por algo pasajero —red, sesión caída— se "
        f"reintenta {rec.reintentos_lectura} vez/veces con espera creciente; "
        f"un fallo de datos no se reintenta, porque gastaría el lote dos veces "
        f"para fallar igual.")


def _medir_coste_alimentador() -> int:
    """Pico de memoria al procesar un alimentador sintético, en MB.

    Se mide con `tracemalloc` sobre el proceso actual: da el pico de asignación
    de Python, que es lo que multiplica el paralelismo. No incluye la memoria del
    intérprete ni de las librerías —esas se pagan una vez por proceso—, así que
    conviene sumar un margen.
    """

    import tracemalloc

    from ptnt.grid_pipeline import run_grid_analysis
    from ptnt.synth.network import generate_radial_network

    cfg = _cargar("config/base.yaml")
    sint = generate_radial_network(n_transformers=20, customers_per_tx=50)
    tracemalloc.start()
    try:
        run_grid_analysis(sint.model, cfg, trifasico=True)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Se suma margen: `tracemalloc` mide solo las asignaciones de Python, no los
    # búferes de numpy ni el propio intérprete, que se pagan por proceso.
    return max(64, int(pico / (1 << 20)) + 64)


@app.command()
def campo_definir(
    tipo: str = typer.Option(..., "--tipo",
                             help="INSPECCION_PNT | CENSO | ACTUALIZACION_CARTOGRAFICA | "
                                  "VERIFICACION_MEDIDOR | MANTENIMIENTO | RECLAMO | OBRA"),
    clientes: str = typer.Option("outputs/ranking_clientes.parquet", "--clientes",
                                 help="Padrón o ranking con los clientes"),
    alimentador: str = typer.Option("", "--alimentador",
                                    help="Códigos separados por coma"),
    sector: str = typer.Option("", "--sector", help="Sectores separados por coma"),
    lista: str = typer.Option("", "--lista",
                              help="CSV con cuentas, o cuentas separadas por coma"),
    centro: str = typer.Option("", "--centro", help="x,y del área a revisar"),
    radio: float = typer.Option(500.0, "--radio", help="Radio del área (m)"),
    por_orden: int = typer.Option(40, "--por-orden",
                                  help="Clientes por orden de trabajo"),
    salida: str = typer.Option("outputs/ordenes_campo.csv", "--salida"),
    config: str = _CONFIG_OPT,
):
    """Define trabajo de campo **sin pasar por el ranking de sospecha**.

    El sistema apunta al hurto, pero una cuadrilla sirve para más: un censo de
    una zona nueva, una actualización cartográfica, la verificación de un
    listado del área comercial. Nada de eso sale de un ranking de sospecha —un
    censo no tiene consumo anómalo que detectar, y justo por eso hay que ir.

    Las órdenes que produce entran al mismo circuito: asignar, repartir,
    empaquetar, editar sin señal, revisar y recalcular.
    """

    from ptnt.field import TipoTrabajo, por_alimentador, por_area, por_lista, por_sector

    _cargar(config)
    ruta = Path(clientes)
    if not ruta.exists():
        console.print(f"[red]No existe {clientes}[/].")
        raise typer.Exit(code=2)
    df = (pd.read_parquet(ruta) if ruta.suffix == ".parquet"
          else pd.read_csv(ruta))

    try:
        t = TipoTrabajo(tipo.upper())
    except ValueError:
        console.print(f"[red]Tipo '{tipo}' desconocido.[/] Válidos: "
                      + ", ".join(x.value for x in TipoTrabajo))
        raise typer.Exit(code=2)

    if alimentador:
        d = por_alimentador(df, [a.strip() for a in alimentador.split(",")],
                            tipo=t, clientes_por_orden=por_orden)
    elif sector:
        d = por_sector(df, [s.strip() for s in sector.split(",")], tipo=t)
    elif lista:
        fuente = lista if Path(lista).exists() else [c.strip() for c in lista.split(",")]
        d = por_lista(df, fuente, tipo=t, clientes_por_orden=por_orden)
    elif centro:
        try:
            cx, cy = (float(v) for v in centro.split(","))
        except ValueError:
            console.print("[red]--centro debe ser 'x,y' en coordenadas de la red.[/]")
            raise typer.Exit(code=2)
        d = por_area(df, x=cx, y=cy, radio_m=radio, tipo=t,
                     clientes_por_orden=por_orden)
    else:
        console.print("[red]Indique el alcance:[/] --alimentador, --sector, "
                      "--lista o --centro.")
        raise typer.Exit(code=2)

    for a in d.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")
    if d.ordenes.empty:
        console.print("[red]No se generó ninguna orden.[/]")
        raise typer.Exit(code=1)

    tabla = Table(title=f"Trabajo definido — {t.value}")
    for c in ("Orden", "Nivel", "Entidad", "Clientes", "kWh/mes"):
        tabla.add_column(c, justify="right" if c in ("Clientes", "kWh/mes") else "left")
    for _, r in d.ordenes.head(15).iterrows():
        tabla.add_row(str(r["orden_trabajo"]), str(r["nivel"]), str(r["entidad"]),
                      f"{int(r['clientes_a_revisar']):,}",
                      f"{float(r['recuperable_kwh_mes']):,.0f}")
    console.print(tabla)
    if len(d.ordenes) > 15:
        console.print(f"  … y {len(d.ordenes) - 15} orden(es) más")

    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    d.ordenes.to_csv(salida, index=False)
    console.print(f"[green]✓[/] {len(d.ordenes)} orden(es) · "
                  f"{d.clientes:,} clientes → [cyan]{salida}[/]")
    if not t.mide_energia:
        console.print("  [dim]Esta campaña no se evalúa por kWh recuperados: "
                      "corrige el denominador del balance, no lo recupera.[/]")
    console.print(f"  Repártalo con: [cyan]ptnt campo-repartir --ordenes {salida} "
                  "--usuarios ana,beto[/]")


@app.command()
def campo_repartir(
    usuarios: str = typer.Option(..., "--usuarios",
                                 help="Técnicos separados por coma: ana,beto,carla"),
    ordenes: str = typer.Option("outputs/ordenes_levantamiento.csv", "--ordenes"),
    top: int = typer.Option(0, "--top",
                            help="Cuántas órdenes tomar del ranking (0 = todas)"),
    criterio: str = typer.Option("kwh", "--criterio",
                                 help="Qué equilibrar: kwh | clientes | visitas"),
    max_por_usuario: int = typer.Option(0, "--max-por-usuario",
                                        help="Tope de órdenes por jornada (0 = sin tope)"),
    beta: float = typer.Option(1.0, "--balance",
                               help="0 = agrupar por cercanía; alto = igualar carga"),
    radio: float = typer.Option(150.0, "--radio", help="Radio del área (m)"),
    aplicar_: bool = typer.Option(False, "--aplicar",
                                  help="Escribe el reparto; sin esto solo lo simula"),
    config: str = _CONFIG_OPT,
):
    """Reparte las órdenes entre varias cuadrillas, parejo y por zonas.

    Sin ``--aplicar`` solo muestra el reparto propuesto: repartir una jornada es
    reversible en pantalla y caro en la calle.
    """

    from ptnt.field import RegistroCampo, asignar_reparto, repartir_ordenes

    cfg = _cargar(config)
    if not Path(ordenes).exists():
        console.print(f"[red]No existe {ordenes}[/]. Ejecute [cyan]ptnt focalizar[/].")
        raise typer.Exit(code=2)

    lista = [u.strip() for u in usuarios.split(",") if u.strip()]
    reg = RegistroCampo(Path(cfg.rutas.salidas) / "campo" / "registro.json")
    faltan = [u for u in lista if u not in reg.usuarios]
    if faltan:
        console.print(f"[red]No existen estos técnicos: {', '.join(faltan)}[/] "
                      "Créelos con [cyan]ptnt campo-usuario[/].")
        raise typer.Exit(code=2)

    df = pd.read_csv(ordenes)
    if top > 0:
        df = df.head(top)
    try:
        rep = repartir_ordenes(
            df, lista, criterio=criterio,
            max_por_usuario=max_por_usuario or None, peso_balance=beta)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)

    res = rep.resumen()
    tabla = Table(title=f"Reparto propuesto ({len(lista)} cuadrillas, "
                        f"criterio {criterio})")
    for c in res.columns:
        tabla.add_column(c.replace("_", " ").capitalize(),
                         justify="left" if c == "usuario" else "right")
    for _, r in res.iterrows():
        tabla.add_row(*[f"{v:,.1f}" if isinstance(v, float) else f"{v:,}"
                        if isinstance(v, int) else str(v) for v in r])
    console.print(tabla)
    console.print(f"  Desbalance entre la más y la menos cargada: "
                  f"[bold]{rep.desbalance_pct:.1f} %[/]")
    for a in rep.advertencias():
        console.print(f"[yellow]⚠ {a}[/]")

    if not aplicar_:
        console.print("\n[dim]Simulación. Añada [cyan]--aplicar[/] para "
                      "escribir la asignación.[/]")
        return

    try:
        hecho = asignar_reparto(reg, rep, asignado_por="cli", radio_m=radio)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)
    console.print(f"\n[green]✓[/] {sum(len(v) for v in hecho.values())} "
                  f"orden(es) asignadas a {len(lista)} técnico(s).")
    console.print("  Genere los paquetes con: [cyan]ptnt campo-paquetes[/]")


@app.command()
def campo_paquetes(
    usuarios: str = typer.Option("", "--usuarios",
                                 help="Técnicos separados por coma; vacío = todos "
                                      "los que tengan órdenes pendientes"),
    red: str = typer.Option(None, "--red",
                            help="DuckDB con la red migrada; omitir usa el escenario de demo"),
    teselas: str = typer.Option(None, "--teselas",
                                help="MBTiles de cartografía base"),
    margen: float = typer.Option(250.0, "--margen", help="Margen alrededor del área (m)"),
    config: str = _CONFIG_OPT,
):
    """Genera de una vez el paquete de cada técnico con trabajo pendiente.

    Es el despacho de la mañana: las cuadrillas salen juntas, así que los
    ``.gpkg`` tienen que estar todos listos antes, no uno por comando.
    """

    from ptnt.field import RegistroCampo, resumen_paquetes
    from ptnt.runtime.batch import construir_paquetes_en_paralelo

    cfg = _cargar(config)
    dir_campo = Path(cfg.rutas.salidas) / "campo"
    reg = RegistroCampo(dir_campo / "registro.json")
    lista = [u.strip() for u in usuarios.split(",") if u.strip()] or None

    todas = [a for a in reg.asignaciones.values()]
    if not todas:
        console.print("[red]No hay órdenes asignadas.[/] Use "
                      "[cyan]ptnt campo-repartir[/] o [cyan]ptnt campo-asignar[/].")
        raise typer.Exit(code=2)

    capas, conexiones = _red_de_campo(red, todas)
    # En paralelo, hasta donde el equipo aguante: el despacho de la mañana son
    # diez o quince paquetes y las cuadrillas están esperando para salir.
    resultados = construir_paquetes_en_paralelo(
        dir_campo / "paquetes", registro=reg, usuarios=lista, red=capas, cfg=cfg,
        conexiones=conexiones, teselas=teselas, margen_m=margen,
        version_red=cfg.proyecto.version_config)

    res = resumen_paquetes(resultados)
    tabla = Table(title="Paquetes generados")
    for c in ("usuario", "estado", "ordenes", "elementos", "area_km2",
              "tamano_mb"):
        if c in res.columns:
            tabla.add_column(c.replace("_", " ").capitalize(),
                             justify="left" if c in ("usuario", "estado") else "right")
    for _, r in res.iterrows():
        tabla.add_row(str(r["usuario"]), str(r["estado"]),
                      f"{int(r.get('ordenes', 0)):,}",
                      f"{int(r.get('elementos', 0)):,}",
                      f"{float(r.get('area_km2', 0)):,.2f}",
                      f"{float(r.get('tamano_mb', 0)):,.2f}")
    console.print(tabla)
    for u, r in resultados.items():
        if isinstance(r, str):
            console.print(f"[yellow]⚠ {u}: {r}[/]")
    ok = sum(1 for r in resultados.values() if not isinstance(r, str))
    console.print(f"[green]✓[/] {ok} paquete(s) en {dir_campo / 'paquetes'}")


def _almacen_escenarios(cfg):
    from ptnt.workspace import AlmacenEscenarios
    return AlmacenEscenarios(Path(cfg.rutas.salidas) / "escenarios" / "escenarios.db")


def _alcance_de(cfg, usuario: str):
    """Resuelve el alcance del usuario. Sin usuario declarado, no hay alcance."""

    from ptnt.security.auth import UserStore
    from ptnt.security.scope import Alcance

    store = UserStore(cfg.seguridad.ruta_usuarios)
    u = next((x for x in store.usuarios() if x.username == usuario), None)
    if u is None:
        console.print(f"[red]El usuario '{usuario}' no existe.[/] "
                      f"Créelo con [cyan]ptnt crear-usuario {usuario} "
                      "--unidad CNEL-GYE[/]")
        raise typer.Exit(code=2)
    return Alcance.desde_usuario(u)


def _jerarquia_de(cfg):
    """El catálogo organizacional configurado, o nada.

    A diferencia de ``consolidar``, aquí **no se infiere** la unidad de negocio
    del prefijo del código. La jerarquía decide quién puede ver y tocar qué: una
    unidad adivinada daría o negaría acceso por una coincidencia de texto.
    """

    from ptnt.org.hierarchy import load_jerarquia

    ruta = cfg.organizacion.catalogo or ""
    if ruta and Path(ruta).exists():
        return load_jerarquia(ruta)
    return None


@app.command()
def escenario_abrir(
    nombre: str = typer.Argument(..., help="Nombre del escenario"),
    usuario: str = typer.Option(..., "--usuario", help="Quién lo abre"),
    entidad: str = typer.Option(..., "--entidad",
                                help="Alimentador o subestación a trabajar"),
    nivel: str = typer.Option("ALIMENTADOR", "--nivel",
                              help="ALIMENTADOR | SUBESTACION"),
    comentario: str = typer.Option("", "--comentario"),
    config: str = _CONFIG_OPT,
):
    """Abre un espacio para probar cambios **sin publicarlos**.

    El usuario define el alcance —un alimentador o una subestación entera— y a
    partir de ahí acumula cambios y evalúa el balance cuando quiera. El modelo
    oficial no se toca hasta que se aplique explícitamente.
    """

    from ptnt.security.scope import AlcanceError, exigir_entidad

    cfg = _cargar(config)
    alcance = _alcance_de(cfg, usuario)
    jer = _jerarquia_de(cfg)
    if jer is None:
        console.print("[red]No hay catálogo organizacional cargado.[/] Sin él no "
                      "se puede saber a qué unidad pertenece cada alimentador, y "
                      "el alcance por unidad de negocio no se puede aplicar.")
        console.print("  Declare el CSV en [cyan]organizacion.catalogo[/] "
                      "(feeder_code, subestacion, unidad_negocio).")
        raise typer.Exit(code=2)

    try:
        res = exigir_entidad(alcance, jer, entidad, nivel)
    except AlcanceError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=3)

    with _almacen_escenarios(cfg) as alm:
        esc = alm.abrir(nombre=nombre, usuario=usuario,
                        unidad_negocio=res.unidad_negocio, nivel=nivel,
                        entidad=entidad, alimentadores=res.alimentadores,
                        comentario=comentario)

    console.print(f"[green]✓[/] Escenario '{nombre}' abierto "
                  f"([cyan]{esc.escenario_id[:8]}[/])")
    console.print(f"  Alcance: {nivel} {entidad} · "
                  f"{len(res.alimentadores)} alimentador(es) · "
                  f"unidad {res.unidad_negocio or '(sin catalogar)'}")
    console.print(f"\n  Acumule cambios y evalúe cuando quiera:")
    console.print(f"    [cyan]ptnt escenario-cambiar {esc.escenario_id[:8]} "
                  "--capa ptnt_puesto_transformacion --elemento <guid> "
                  "--campo potencia_nominal_kva --valor 75[/]")
    console.print(f"    [cyan]ptnt escenario-evaluar {esc.escenario_id[:8]}[/]")


@app.command()
def escenario_cambiar(
    escenario: str = typer.Argument(..., help="Identificador del escenario"),
    capa: str = typer.Option(..., "--capa"),
    elemento: str = typer.Option(..., "--elemento", help="guid del elemento"),
    campo: str = typer.Option(..., "--campo"),
    valor: str = typer.Option(..., "--valor"),
    autor: str = typer.Option("", "--autor"),
    motivo: str = typer.Option("", "--motivo"),
    desde_paquete: str = typer.Option("", "--desde-paquete",
                                      help="En vez de un cambio suelto, toma los "
                                           "del diario de un paquete de campo"),
    config: str = _CONFIG_OPT,
):
    """Acumula un cambio en el escenario. **No toca el modelo oficial.**"""

    from ptnt.workspace import CambioPropuesto, EscenarioError

    cfg = _cargar(config)
    cambios: list = []

    if desde_paquete:
        from ptnt.field.gpkg import GeoPackage
        ruta = Path(desde_paquete)
        if not ruta.exists():
            console.print(f"[red]No existe el paquete:[/] {ruta}")
            raise typer.Exit(code=2)
        with GeoPackage(ruta) as gp:
            try:
                diario = gp.leer("ptnt_cambio")
            except Exception:
                diario = []
        for c in diario:
            if str(c.get("operacion", "")) not in ("MODIFICAR", "RECONECTAR"):
                continue
            cambios.append(CambioPropuesto(
                capa=str(c.get("capa", "")),
                elemento_guid=str(c.get("elemento_guid", "")),
                campo=str(c.get("campo", "") or ""),
                valor_antes=c.get("valor_antes"),
                valor_despues=c.get("valor_despues"),
                operacion=str(c.get("operacion", "MODIFICAR")),
                origen="CAMPO", autor=str(c.get("autor", "") or ""),
                motivo=str(c.get("motivo", "") or "")))
        if not cambios:
            console.print("[yellow]El paquete no trae cambios evaluables.[/]")
            raise typer.Exit(code=1)
    else:
        cambios.append(CambioPropuesto(
            capa=capa, elemento_guid=elemento, campo=campo,
            valor_despues=_convertir(valor), autor=autor, motivo=motivo))

    with _almacen_escenarios(cfg) as alm:
        try:
            n = alm.acumular(escenario, cambios)
        except EscenarioError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=2)
        total = len(alm.cambios(escenario))

    console.print(f"[green]✓[/] {n} cambio(s) acumulados · "
                  f"{total} en total en el escenario")
    console.print("  El modelo oficial sigue intacto. Evalúe con "
                  f"[cyan]ptnt escenario-evaluar {escenario}[/]")


def _convertir(v: str):
    """Texto de la línea de comandos al tipo que corresponda."""

    for conv in (int, float):
        try:
            return conv(v)
        except ValueError:
            continue
    if v.lower() in ("true", "sí", "si"):
        return 1
    if v.lower() in ("false", "no"):
        return 0
    return v


@app.command()
def escenario_evaluar(
    escenario: str = typer.Argument(..., help="Identificador del escenario"),
    usuario: str = typer.Option("", "--usuario",
                                help="Para comprobar el alcance"),
    comentario: str = typer.Option("", "--comentario",
                                   help="Qué se estaba probando"),
    cabecera: str = typer.Option("", "--cabecera",
                                 help="CSV de energía de cabecera "
                                      "(feeder_code,period,kwh_delivered). Sin "
                                      "él el balance es INDICATIVO"),
    trifasico: bool = typer.Option(True, "--trifasico/--monofasico"),
    config: str = _CONFIG_OPT,
):
    """Calcula el balance **con los cambios acumulados**, sin publicarlos.

    Cada evaluación queda registrada como una iteración. Las iteraciones no se
    borran: son la evolución de ese alimentador en el tiempo.
    """

    from ptnt.workspace import energia_cabecera, evaluar_escenario

    cfg = _cargar(config)
    medicion = None
    if cabecera:
        try:
            medicion = energia_cabecera(cabecera)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=2)
    with _almacen_escenarios(cfg) as alm:
        esc = alm.obtener(escenario)
        if esc is None:
            console.print(f"[red]No existe el escenario '{escenario}'.[/]")
            raise typer.Exit(code=2)
        if usuario:
            from ptnt.security.scope import AlcanceError
            try:
                _alcance_de(cfg, usuario).exigir(
                    esc.unidad_negocio, f"el escenario '{esc.nombre}'")
            except AlcanceError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(code=3)

        cambios = alm.cambios(escenario)
        console.print(f"Evaluando '{esc.nombre}' · {esc.nivel} {esc.entidad} · "
                      f"{len(cambios)} cambio(s) acumulados…")

        res = evaluar_escenario(esc, cambios, cfg, trifasico=trifasico,
                                cabecera=medicion)
        if not res.metricas:
            for a in res.advertencias:
                console.print(f"[red]{a}[/]")
            raise typer.Exit(code=1)

        it = alm.registrar_iteracion(
            escenario, metricas=res.metricas, n_cambios=len(cambios),
            hash_topologia=res.hash_topologia,
            cambios_no_aplicados=[c.to_dict() for c in res.no_aplicados],
            comentario=comentario)

    tabla = Table(title=f"Iteración {it.n} — {esc.nombre}")
    tabla.add_column("Indicador"); tabla.add_column("Valor", justify="right")
    for k, v in res.metricas.items():
        if isinstance(v, float):
            texto = f"{v:,.3f}"
        elif isinstance(v, int):
            texto = f"{v:,}"
        else:
            texto = str(v)
        tabla.add_row(k.replace("_", " ").capitalize(), texto)
    console.print(tabla)

    if not res.por_alimentador.empty and len(res.por_alimentador) > 1:
        console.print(res.por_alimentador[
            ["alimentador", "pnt_pct", "tipo_balance", "converge"]
        ].to_string(index=False))

    console.print(f"\n  {res.lectura()}")
    for a in res.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")
    for c in res.controles:
        console.print(f"[red]✗ {c}[/]")
    for c in res.no_aplicados[:10]:
        console.print(f"[yellow]  · {c.capa}/{c.elemento_guid}: {c.motivo}[/]")

    console.print(f"\n  Vea la evolución con [cyan]ptnt escenario-evolucion "
                  f"{escenario}[/]")


@app.command()
def escenario_evolucion(
    escenario: str = typer.Argument("", help="Identificador del escenario"),
    entidad: str = typer.Option("", "--entidad",
                                help="En vez de un escenario, TODA la historia "
                                     "de un alimentador o subestación"),
    usuario: str = typer.Option("", "--usuario"),
    config: str = _CONFIG_OPT,
):
    """Cómo fue cambiando el balance, iteración a iteración.

    Con ``--entidad`` cruza todos los escenarios de ese alimentador: la historia
    no cabe en uno solo, porque se abre uno nuevo cada vez que se aplica el
    anterior.
    """

    cfg = _cargar(config)
    alcance = _alcance_de(cfg, usuario) if usuario else None

    with _almacen_escenarios(cfg) as alm:
        if entidad:
            evo = alm.evolucion_de_entidad(entidad, alcance=alcance)
            titulo = f"Evolución de {entidad}"
        elif escenario:
            evo = alm.evolucion(escenario)
            esc = alm.obtener(escenario)
            titulo = f"Evolución de '{esc.nombre if esc else escenario}'"
        else:
            console.print("[red]Indique un escenario o --entidad.[/]")
            raise typer.Exit(code=2)

    if evo.empty:
        console.print("[yellow]Sin iteraciones todavía.[/] "
                      "Evalúe con [cyan]ptnt escenario-evaluar[/].")
        return

    tabla = Table(title=titulo)
    for c in evo.columns:
        tabla.add_column(c.replace("_", " ").capitalize(),
                         justify="right" if evo[c].dtype.kind in "if" else "left")
    for _, r in evo.iterrows():
        tabla.add_row(*[f"{v:,.3f}" if isinstance(v, float) else str(v)
                        for v in r])
    console.print(tabla)

    if "pnt_pct" in evo.columns and len(evo) > 1:
        primero, ultimo = float(evo["pnt_pct"].iloc[0]), float(evo["pnt_pct"].iloc[-1])
        dif = ultimo - primero
        signo = "bajó" if dif < 0 else "subió"
        color = "green" if dif < 0 else "yellow"
        console.print(f"\n  De la primera iteración a la última, la PNT "
                      f"[{color}]{signo} {abs(dif):.2f} puntos[/] "
                      f"({primero:.2f} % → {ultimo:.2f} %).")


@app.command()
def escenario_listar(
    usuario: str = typer.Option(..., "--usuario", help="De quién es el alcance"),
    todos: bool = typer.Option(False, "--todos",
                               help="Incluir aplicados y descartados"),
    config: str = _CONFIG_OPT,
):
    """Escenarios que este usuario puede ver."""

    cfg = _cargar(config)
    alcance = _alcance_de(cfg, usuario)

    with _almacen_escenarios(cfg) as alm:
        escenarios = alm.listar(alcance=alcance, incluir_cerrados=todos)

    console.print(f"[bold]{usuario}[/] — {alcance.descripcion()}")
    if not escenarios:
        console.print("[yellow]Sin escenarios visibles.[/]")
        return

    tabla = Table(title="Escenarios")
    for c in ("Id", "Nombre", "Usuario", "Unidad", "Alcance", "Estado"):
        tabla.add_column(c)
    for e in escenarios:
        tabla.add_row(e.escenario_id[:8], e.nombre, e.usuario, e.unidad_negocio,
                      f"{e.nivel} {e.entidad}", e.estado)
    console.print(tabla)


@app.command()
def escenario_comparar(
    escenario: str = typer.Argument(...),
    desde: int = typer.Option(1, "--desde", help="Número de iteración"),
    hasta: int = typer.Option(0, "--hasta", help="0 = la última"),
    config: str = _CONFIG_OPT,
):
    """Qué cambió entre dos iteraciones del mismo escenario."""

    from ptnt.workspace import EscenarioError, comparar as _comparar

    cfg = _cargar(config)
    with _almacen_escenarios(cfg) as alm:
        its = alm.iteraciones(escenario)
        if not its:
            console.print("[yellow]El escenario no tiene iteraciones.[/]")
            raise typer.Exit(code=1)
        destino = hasta or its[-1].n
        try:
            comp = _comparar(alm, escenario, desde, destino)
        except EscenarioError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=2)

    df = comp.to_dataframe()
    if df.empty:
        console.print("[yellow]Nada numérico que comparar.[/]")
    else:
        tabla = Table(title=f"Iteración {comp.desde} → {comp.hasta}")
        for c in ("Métrica", "Desde", "Hasta", "Diferencia", "Variación %"):
            tabla.add_column(c, justify="right" if c != "Métrica" else "left")
        for _, r in df.iterrows():
            tabla.add_row(str(r["metrica"]), f"{r['desde']:,}", f"{r['hasta']:,}",
                          f"{r['diferencia']:+,}",
                          f"{r['variacion_pct']:+.2f}"
                          if r["variacion_pct"] is not None else "—")
        console.print(tabla)

    for a in comp.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")


@app.command()
def campo_aprender(
    paquete: str = typer.Option(..., "--paquete",
                                help="Paquete de retorno con las inspecciones"),
    multados: str = typer.Option("", "--multados",
                                 help="Base histórica de multados, para unirla"),
    ranking: str = typer.Option("", "--ranking",
                                help="CSV del ranking, para contrastar el acierto"),
    config: str = _CONFIG_OPT,
):
    """Incorpora lo que la cuadrilla encontró a la base de casos confirmados.

    Es el bucle que hace que el sistema mejore con el uso: cada campaña que
    vuelve del campo agranda la base propia de casos verificados, y el detector
    pasa a calibrarse contra la realidad de esta empresa en vez de contra una
    base heredada de la que nadie sabe cómo se eligió.

    Distingue tres cosas que **no** son lo mismo: hurto confirmado, cliente
    verificado y limpio —el dato más escaso del problema— y visita que no pudo
    concluir. Un predio cerrado no es un cliente inocente, y meterlo como tal
    enseñaría al modelo a dejar de mirar justo donde conviene insistir.
    """

    from ptnt.field.gpkg import GeoPackage
    from ptnt.ntl.feedback import (RegistroInspecciones, contrastar_con_ranking,
                                   extraer_resultados, guardar_resumen)

    ruta = Path(paquete)
    if not ruta.exists():
        console.print(f"[red]No existe el paquete:[/] {ruta}")
        raise typer.Exit(code=2)

    cfg = _cargar(config)
    salidas = Path(cfg.rutas.salidas) / "aprendizaje"
    with GeoPackage(ruta) as gp:
        try:
            clientes = pd.DataFrame(gp.leer("ptnt_cliente"))
        except Exception:
            clientes = pd.DataFrame()
    resultados = extraer_resultados(clientes)

    registro = RegistroInspecciones(salidas / "inspecciones.csv")
    resumen = registro.registrar(resultados)
    registro.guardar()

    tabla = Table(title="Lo que encontró la cuadrilla")
    tabla.add_column("Veredicto"); tabla.add_column("Visitas", justify="right")
    tabla.add_row("Hurto confirmado", f"{resumen.confirmados:,}")
    tabla.add_row("Verificado y limpio", f"{resumen.descartados:,}")
    tabla.add_row("[yellow]Sin concluir[/] (cerrado / acceso negado)",
                  f"{resumen.no_concluyentes:,}")
    tabla.add_row("Problema de datos", f"{resumen.problemas_datos:,}")
    console.print(tabla)
    console.print(f"  {resumen.lectura()}")
    for a in resumen.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")

    heredados = None
    if multados and Path(multados).exists():
        heredados = pd.read_csv(multados, dtype={"contract_account": str})
    base = registro.base_confirmados(incluir_heredados=heredados)
    destino = salidas / "confirmados.csv"
    base.to_csv(destino, index=False)

    acumulado = registro.resumen_acumulado()
    console.print(f"\n[bold]Base de casos confirmados:[/] {len(base):,} "
                  f"({acumulado.confirmados:,} verificados en campo por esta "
                  f"empresa) → {destino}")
    console.print(f"  Negativos verificados: "
                  f"{len(registro.negativos_verificados()):,} · son los únicos "
                  "clientes de los que se sabe con certeza que están limpios.")

    contraste = None
    if ranking and Path(ranking).exists():
        contraste = contrastar_con_ranking(
            registro, pd.read_csv(ranking, dtype={"contract_account": str}))
        t2 = Table(title="Lo que el ranking predijo vs. lo que el campo encontró")
        t2.add_column("Indicador"); t2.add_column("Valor", justify="right")
        for k, v in contraste.resumen().items():
            t2.add_row(k.replace("_", " ").capitalize(), str(v))
        console.print(t2)
        for a in contraste.advertencias:
            console.print(f"[yellow]⚠ {a}[/]")

    guardar_resumen(salidas / "resumen_campana.json", resumen, contraste)
    console.print(f"\n[green]✓[/] Recalibre con [cyan]ptnt diagnostico "
                  f"--multados {destino}[/] para que el detector use lo aprendido.")


@app.command()
def pares(
    entrada: str = typer.Option(..., "--entrada",
                                help="CSV con una fila por entidad y su perfil"),
    nivel: str = typer.Option("ALIMENTADOR", "--nivel",
                              help="ALIMENTADOR|RAMAL|PUESTO_TRANSFORMACION|"
                                   "RUTA_COMERCIAL|SECTOR"),
    metrica: str = typer.Option("pnt_pct", "--metrica"),
    parecidas_a: str = typer.Option("", "--parecidas-a",
                                    help="Muestra las entidades similares a esta"),
    z: float = typer.Option(3.0, "--z", help="Desviaciones para marcar atípica"),
    config: str = _CONFIG_OPT,
):
    """Compara cada entidad contra las que **se le parecen**, no contra el promedio.

    Un alimentador rural largo pierde más por física; uno urbano compacto con la
    misma cifra tiene un problema. Compararlos contra el promedio de la empresa
    mezcla los dos y produce lo peor: cuadrillas mandadas a alimentadores que
    están como deben estar, y alimentadores realmente malos que pasan por
    normales porque el promedio los tapa.
    """

    from ptnt.anomalies.peer_entities import (PERFIL_POR_NIVEL,
                                              comparar_contra_pares,
                                              entidades_similares)

    ruta = Path(entrada)
    if not ruta.exists():
        console.print(f"[red]No existe:[/] {ruta}")
        raise typer.Exit(code=2)
    d = pd.read_csv(ruta)
    cfg_nivel = PERFIL_POR_NIVEL.get(nivel.upper(), {})
    if not cfg_nivel:
        console.print(f"[red]Nivel desconocido:[/] {nivel}. "
                      f"Use uno de: {', '.join(PERFIL_POR_NIVEL)}")
        raise typer.Exit(code=2)

    if parecidas_a:
        sim = entidades_similares(d, parecidas_a, nivel=nivel.upper())
        if sim.empty:
            console.print(f"[yellow]Sin entidades comparables con {parecidas_a}[/]")
            raise typer.Exit(code=1)
        t = Table(title=f"Entidades parecidas a {parecidas_a}")
        cols = ["entidad"] + [c for c in cfg_nivel["perfil"] if c in sim.columns]
        if metrica in sim.columns:
            cols.append(metrica)
        for c in cols:
            t.add_column(c.replace("_", " ").capitalize(),
                         justify="right" if c != "entidad" else "left")
        for _, r in sim.iterrows():
            t.add_row(*[f"{r[c]:,.1f}" if isinstance(r[c], float) else str(r[c])
                        for c in cols])
        console.print(t)
        return

    rep = comparar_contra_pares(
        d, nivel=nivel.upper(), columna_id="entidad", columna_metrica=metrica,
        columnas_perfil=cfg_nivel["perfil"],
        columnas_categoricas=cfg_nivel.get("categoricas"), z_minimo=z)

    for a in rep.advertencias:
        console.print(f"[yellow]⚠ {a}[/]")

    if not rep.atipicas:
        console.print(f"[green]✓[/] Ninguna de las {rep.n_evaluadas:,} entidades "
                      f"de nivel {nivel} se aparta de sus pares. Que la métrica "
                      "sea alta en algunas no significa que estén mal: significa "
                      "que están como sus parecidas.")
        return

    t = Table(title=f"{nivel}: se apartan de lo que su estructura explicaría")
    for c in ("Entidad", "Observado", "Esperado", "Desviación", "z", "Severidad",
              "Se comparó con"):
        t.add_column(c, justify="left" if c in ("Entidad", "Severidad",
                                                "Se comparó con") else "right")
    for a in rep.atipicas[:25]:
        t.add_row(a.entidad, f"{a.observado:,.1f}", f"{a.esperado:,.1f}",
                  f"{a.desviacion:+,.1f}", f"{a.z_robusto:+.1f}", a.severidad,
                  ", ".join(a.pares[:3]))
    console.print(t)
    console.print(f"\n  {len(rep.altas)} entidad(es) pierden **más** de lo que su "
                  "estructura explica: ahí conviene mirar.")
    console.print("  [yellow]No es una acusación:[/] apartarse del grupo puede ser "
                  "hurto, un transformador mal asignado, un cliente no vinculado "
                  "o una maniobra no reportada. Es una pregunta bien hecha.")


@app.command()
def campo_simular(
    paquete: str = typer.Option(..., "--paquete",
                                help="Ruta del .gpkg descargado por el técnico"),
    usuario: str = typer.Option("simulador", "--usuario",
                                help="Quién firma las ediciones"),
    editar: int = typer.Option(2, "--editar",
                               help="Cuántos clientes inspeccionar"),
    subtipo: bool = typer.Option(True, "--subtipo/--sin-subtipo",
                                 help="Probar el cambio de subtipo y sus dominios"),
    fotos: bool = typer.Option(True, "--fotos/--sin-fotos"),
):
    """Hace en el paquete lo mismo que haría el técnico, **sin dispositivo**.

    Es la forma de probar el ciclo de campo completo desde un Windows de
    oficina: escribe sobre el GeoPackage real con las mismas reglas que la
    aplicación —diario de cambios, subtipos, dominios, snap topológico— y deja
    el archivo listo para subirlo con `ptnt campo-revisar`.

    No sustituye a probar en un teléfono: el render del mapa, los permisos y el
    GPS bajo cobertura real solo se comprueban en la calle. Sí sustituye a
    necesitar un teléfono para verificar que el **dato** que produce el ciclo es
    correcto, que es lo que se puede automatizar.
    """

    from ptnt.field.domains import aplicar_subtipo
    from ptnt.field.schema import capa_por_nombre
    from ptnt.field.simulator import SimuladorCampo

    ruta = Path(paquete)
    if not ruta.exists():
        console.print(f"[red]No existe el paquete:[/] {ruta}")
        raise typer.Exit(code=2)

    hechos: list[tuple[str, str]] = []
    with SimuladorCampo(ruta, usuario=usuario) as sim:
        ordenes = sim.ordenes()
        if ordenes:
            sim.abrir_orden(str(ordenes[0]["orden_trabajo"]))
            hechos.append(("Orden abierta", str(ordenes[0]["orden_trabajo"])))

        clientes = sim.elementos("ptnt_cliente", limite=max(editar, 1))
        for i, c in enumerate(clientes[:editar]):
            guid = str(c["guid"])
            sim.editar_atributos(
                "ptnt_cliente", guid,
                {"hallazgo": "MEDIDOR_MANIPULADO" if i == 0 else "SIN_NOVEDAD",
                 "inspeccionado": 1, "lectura_medidor": 10000.0 + i},
                motivo="Inspección simulada")
            hechos.append(("Cliente inspeccionado", guid[:8]))
            if fotos and i == 0:
                foto = sim.fotografiar(
                    guid, "ptnt_cliente",
                    descripcion="Medidor con sellos violentados")
                hechos.append(("Foto adjunta", f"{foto[:8]} · con ubicación y hora"))

        if subtipo:
            # La prueba que importa: cambiar el subtipo tiene que arrastrar los
            # dominios. Un banco de dos unidades no puede quedar sirviendo ABC.
            capa = capa_por_nombre("ptnt_puesto_transformacion")
            for p in sim.elementos("ptnt_puesto_transformacion", limite=1):
                actual = dict(p)
                cambio = aplicar_subtipo(capa, actual, "DELTA_ABIERTO")
                nuevos = {k: v for k, v in cambio.atributos.items()
                          if k in ("configuracion_banco", "fases", "n_unidades")}
                sim.editar_atributos("ptnt_puesto_transformacion",
                                     str(p["guid"]), nuevos,
                                     motivo="Reconfiguración verificada en sitio")
                hechos.append(("Subtipo cambiado",
                               f"{actual.get('configuracion_banco') or '(vacío)'}"
                               f" → DELTA_ABIERTO"))
                if cambio.resumen():
                    hechos.append(("Ajuste por subtipo", cambio.resumen()))

        pendientes = sim.pendientes()

    tabla = Table(title=f"Jornada simulada — {usuario}")
    tabla.add_column("Acción"); tabla.add_column("Detalle")
    for a, d in hechos:
        tabla.add_row(a, d)
    console.print(tabla)
    console.print(f"[green]✓[/] {pendientes} cambio(s) pendientes en {ruta}")
    console.print("  Súbalo con [cyan]ptnt campo-revisar --paquete "
                  f"{ruta}[/] o por la API con [cyan]ptnt campo-servir[/].")


if __name__ == "__main__":  # pragma: no cover
    app()
