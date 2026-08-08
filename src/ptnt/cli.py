"""Interfaz de línea de comandos de PTNT-BAL (``ptnt ...``).

Todos los subcomandos aceptan ``--config`` con la ruta al YAML. El arranque valida
la configuración: si falta un parámetro obligatorio, el comando falla con un
mensaje que nombra el parámetro.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ptnt.config.loader import ConfigError, load_config

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

    console.print(f"\n[bold]Top {top} clientes por sospecha de hurto[/] "
                  f"(sospechosos: {result.metricas['n_sospechosos']})")
    tabla = Table()
    tabla.add_column("#")
    tabla.add_column("Cuenta")
    tabla.add_column("Score", justify="right")
    tabla.add_column("Señales", justify="right")
    tabla.add_column("Razón principal")
    for _, row in result.ranking.head(top).iterrows():
        razon = row["razones"][0] if row["razones"] else "-"
        tabla.add_row(
            str(int(row["rank"])),
            str(row["contract_account"]),
            f"{row['score']:.3f}",
            str(int(row["n_senales_activas"])),
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
        customer_ranking=ranking, customer_coords=coords,
    )

    # 5) Mostrar
    console.print(f"\n[bold]📍 Plan de levantamientos[/] — "
                  f"{plan.resumen['n_objetivos']} objetivos priorizados")
    conteo = ", ".join(f"{k.replace('_',' ').title()}: {v}"
                       for k, v in plan.resumen["por_nivel"].items() if v)
    console.print(f"  Por nivel: {conteo}")

    for lvl in (TargetLevel.ALIMENTADOR, TargetLevel.ZONA_PROTECCION, TargetLevel.RAMAL,
                TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.SECTOR):
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
    config: str = _CONFIG_OPT,
):
    """Crea un usuario para las interfaces web (contraseña por prompt seguro)."""

    from ptnt.security.auth import AuthError, UserStore

    cfg = _cargar(config)
    password = typer.prompt("Contraseña", hide_input=True, confirmation_prompt=True)
    store = UserStore(cfg.seguridad.ruta_usuarios)
    try:
        store.add_user(usuario, password, rol)
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)
    console.print(f"[green]✓[/] Usuario '{usuario}' ({rol}) creado en {cfg.seguridad.ruta_usuarios}")


if __name__ == "__main__":  # pragma: no cover
    app()
