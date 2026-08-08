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
