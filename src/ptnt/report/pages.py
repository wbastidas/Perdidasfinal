"""Generación de informes HTML autocontenidos y su conversión a PDF.

Un informe por **etapa** del proceso, no uno solo gigante: cada etapa la firma un
responsable distinto (calidad de datos, balance, planificación de campo) y
necesita circular por separado.

Las páginas son autocontenidas —CSS embebido, gráficos SVG en línea, sin
recursos externos— para que se puedan enviar por correo, archivar como evidencia
o imprimir sin depender de la red.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import pandas as pd

_CSS = """
@page { size: A4; margin: 14mm 12mm 16mm 12mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  color: #1c2530; margin: 0; padding: 0 22px 30px;
  font-size: 13px; line-height: 1.5; background: #fff;
}
header.doc {
  border-bottom: 3px solid #1f4e79; padding: 18px 0 12px; margin-bottom: 20px;
}
header.doc .etapa {
  display: inline-block; background: #1f4e79; color: #fff;
  padding: 3px 11px; border-radius: 3px; font-size: 11px;
  font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
}
header.doc h1 { font-size: 22px; margin: 10px 0 4px; color: #1f4e79; }
header.doc .sub { color: #5b6770; font-size: 12px; }
h2 {
  font-size: 15px; color: #1f4e79; margin: 26px 0 10px;
  padding-bottom: 5px; border-bottom: 1px solid #d5dde5;
}
h3 { font-size: 13px; margin: 18px 0 8px; color: #2c3e50; }
p { margin: 8px 0; }
.lead { font-size: 13.5px; color: #35414d; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 16px; font-size: 12px; }
th {
  background: #eef3f8; text-align: left; padding: 7px 9px;
  border-bottom: 2px solid #c3d0dd; font-weight: 600; color: #1f4e79;
}
td { padding: 6px 9px; border-bottom: 1px solid #e8edf2; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fafcfe; }
.kpis { display: flex; flex-wrap: wrap; gap: 11px; margin: 14px 0 6px; }
.kpi {
  flex: 1 1 150px; border: 1px solid #dbe3ec; border-left: 4px solid #1f4e79;
  border-radius: 3px; padding: 10px 13px; background: #fbfcfe;
}
.kpi .k { font-size: 10.5px; color: #5b6770; text-transform: uppercase; letter-spacing: .04em; }
.kpi .v { font-size: 19px; font-weight: 700; color: #1f4e79; margin-top: 3px;
          font-variant-numeric: tabular-nums; }
.kpi .n { font-size: 10.5px; color: #6b7885; margin-top: 2px; }
.kpi.alerta { border-left-color: #a8352e; } .kpi.alerta .v { color: #a8352e; }
.kpi.ok { border-left-color: #4a7c3f; } .kpi.ok .v { color: #4a7c3f; }
.nota, .alerta-box, .ok-box {
  border-radius: 3px; padding: 10px 13px; margin: 12px 0; font-size: 12.5px;
}
.nota { background: #f4f7fa; border-left: 4px solid #5b6770; }
.alerta-box { background: #fdf3f2; border-left: 4px solid #a8352e; }
.ok-box { background: #f2f8ef; border-left: 4px solid #4a7c3f; }
.nota b, .alerta-box b, .ok-box b { color: #1c2530; }
.chart { margin: 14px 0 18px; }
.vacio { color: #8a96a3; font-style: italic; }
footer.doc {
  margin-top: 30px; padding-top: 10px; border-top: 1px solid #d5dde5;
  font-size: 10.5px; color: #7d8894; display: flex; justify-content: space-between;
}
.svg-title { font: 600 13px "Segoe UI", Arial, sans-serif; fill: #1f4e79; }
.svg-lab { font: 12px "Segoe UI", Arial, sans-serif; fill: #35414d; }
.svg-val { font: 600 11.5px "Segoe UI", Arial, sans-serif; fill: #35414d; }
.svg-ax  { font: 10.5px "Segoe UI", Arial, sans-serif; fill: #6b7885; }
.svg-ax-dim { font: 10px "Segoe UI", Arial, sans-serif; fill: #97a2ae; }
.svg-seg { font: 600 11px "Segoe UI", Arial, sans-serif; fill: #fff; }
.svg-serie { font: 600 11px "Segoe UI", Arial, sans-serif; }
.svg-grid { stroke: #e4eaf0; stroke-width: 1; }
.svg-evento { stroke: #a8352e; stroke-width: 1.6; stroke-dasharray: 5 3; }
.svg-evento-lab { font: 600 10px "Segoe UI", Arial, sans-serif; fill: #a8352e; }
.svg-mapa-bg { fill: #f7fafc; }
.svg-escala { stroke: #5b6770; stroke-width: 2; }
.pill { display:inline-block; padding:1px 7px; border-radius:9px; font-size:10.5px;
        font-weight:600; }
.pill.alta { background:#fbe4e2; color:#8f2d26; }
.pill.media { background:#fdf1de; color:#8a5a15; }
.pill.baja { background:#eaf1e7; color:#3d6634; }
"""


@dataclass
class Informe:
    """Informe de una etapa, construido por acumulación de bloques."""

    etapa: str
    titulo: str
    subtitulo: str = ""
    bloques: list[str] = field(default_factory=list)

    # -- bloques -----------------------------------------------------------
    def texto(self, html: str) -> "Informe":
        self.bloques.append(f"<p class='lead'>{html}</p>")
        return self

    def seccion(self, titulo: str) -> "Informe":
        self.bloques.append(f"<h2>{escape(titulo)}</h2>")
        return self

    def subseccion(self, titulo: str) -> "Informe":
        self.bloques.append(f"<h3>{escape(titulo)}</h3>")
        return self

    def nota(self, html: str, tipo: str = "nota") -> "Informe":
        clase = {"nota": "nota", "alerta": "alerta-box", "ok": "ok-box"}[tipo]
        self.bloques.append(f"<div class='{clase}'>{html}</div>")
        return self

    def kpis(self, items: list[tuple[str, str, str]], estados: list[str] | None = None
             ) -> "Informe":
        """``items`` = [(rótulo, valor, nota)]; ``estados`` ∈ {'', 'ok', 'alerta'}."""

        cajas = []
        for i, (k, v, n) in enumerate(items):
            est = estados[i] if estados and i < len(estados) else ""
            cajas.append(
                f"<div class='kpi {est}'><div class='k'>{escape(k)}</div>"
                f"<div class='v'>{escape(str(v))}</div>"
                f"<div class='n'>{escape(n)}</div></div>")
        self.bloques.append(f"<div class='kpis'>{''.join(cajas)}</div>")
        return self

    def tabla(self, df: pd.DataFrame, *, max_filas: int = 25,
              numericas: list[str] | None = None, formatos: dict | None = None
              ) -> "Informe":
        if df is None or df.empty:
            self.bloques.append("<p class='vacio'>Sin registros.</p>")
            return self
        d = df.head(max_filas).copy()
        num = set(numericas or [c for c in d.columns
                                if pd.api.types.is_numeric_dtype(d[c])])
        fmt = formatos or {}

        # Un conteo es un entero: mostrar "44,00 clientes" delata que el número
        # salió de una división y resta credibilidad al informe.
        enteras = {
            c for c in num
            if pd.api.types.is_numeric_dtype(d[c])
            and d[c].dropna().apply(float.is_integer
                                    if d[c].dtype == float else lambda v: True).all()
        }

        th = "".join(
            f"<th class='num'>{escape(str(c))}</th>" if c in num
            else f"<th>{escape(str(c))}</th>" for c in d.columns)
        filas = []
        for _, r in d.iterrows():
            tds = []
            for c in d.columns:
                v = r[c]
                if c in fmt:
                    txt = fmt[c](v)
                elif c in num and pd.notna(v):
                    if c in enteras:
                        txt = f"{float(v):,.0f}"
                    else:
                        txt = f"{v:,.2f}" if abs(float(v)) < 100 else f"{v:,.0f}"
                else:
                    txt = "" if pd.isna(v) else str(v)
                cls = " class='num'" if c in num else ""
                tds.append(f"<td{cls}>{escape(txt)}</td>")
            filas.append(f"<tr>{''.join(tds)}</tr>")
        omitidas = len(df) - len(d)
        extra = (f"<p class='vacio'>… y {omitidas:,} fila(s) más en el CSV/XLSX "
                 f"exportado.</p>" if omitidas > 0 else "")
        self.bloques.append(
            f"<table><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(filas)}</tbody></table>{extra}")
        return self

    def grafico(self, svg: str) -> "Informe":
        self.bloques.append(f"<div class='chart'>{svg}</div>")
        return self

    # -- salida ------------------------------------------------------------
    def html(self, pie: str = "") -> str:
        return (
            "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<title>{escape(self.titulo)}</title><style>{_CSS}</style></head><body>"
            f"<header class='doc'><span class='etapa'>{escape(self.etapa)}</span>"
            f"<h1>{escape(self.titulo)}</h1>"
            f"<div class='sub'>{escape(self.subtitulo)}</div></header>"
            + "".join(self.bloques)
            + f"<footer class='doc'><span>PTNT-BAL — {escape(self.etapa)}</span>"
              f"<span>{escape(pie)}</span></footer></body></html>"
        )

    def escribir(self, ruta: str | Path, pie: str = "") -> Path:
        p = Path(ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.html(pie), encoding="utf-8")
        return p


# --------------------------------------------------------------------------- #
# Conversión a PDF
# --------------------------------------------------------------------------- #
def _buscar_chromium() -> str | None:
    for nombre in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    for patron in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                   "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/*"):
        for c in sorted(Path("/").glob(patron.lstrip("/"))):
            if c.is_file():
                return str(c)
    return None


def html_a_pdf(html_path: str | Path, pdf_path: str | Path,
               *, timeout: int = 120) -> Path | None:
    """Convierte un HTML a PDF con Chromium headless.

    Devuelve ``None`` —sin lanzar excepción— si no hay navegador disponible: el
    HTML sigue siendo un entregable válido y perder el PDF no debe abortar un
    pipeline de análisis que ya corrió.
    """

    navegador = _buscar_chromium()
    if navegador is None:
        return None
    pdf = Path(pdf_path)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        navegador, "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=6000",
        f"--print-to-pdf={pdf}", Path(html_path).resolve().as_uri(),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return pdf if pdf.exists() and pdf.stat().st_size > 0 else None


def compendio(informes: list[Informe], salida_html: str | Path, *,
              titulo: str = "Informe completo", subtitulo: str = "",
              pie: str = "") -> Path:
    """Une varios informes en un solo documento con saltos de página.

    Se arma a nivel de HTML y no uniendo los PDF ya generados, porque unir PDF
    exige una herramienta externa (``pdfunite``/``pdftk``) que no siempre está
    instalada en el servidor de la distribuidora. Concatenar el contenido y
    convertir una sola vez da el mismo resultado sin dependencias nuevas.
    """

    partes = []
    for i, inf in enumerate(informes):
        salto = "page-break-before:always;" if i else ""
        partes.append(
            f"<section style='{salto}'>"
            f"<header class='doc'><span class='etapa'>{escape(inf.etapa)}</span>"
            f"<h1>{escape(inf.titulo)}</h1>"
            f"<div class='sub'>{escape(inf.subtitulo)}</div></header>"
            + "".join(inf.bloques) + "</section>")

    indice = "".join(
        f"<li><b>{escape(i.etapa)}</b> — {escape(i.titulo)}</li>"
        for i in informes)
    portada = (
        f"<header class='doc'><span class='etapa'>Compendio</span>"
        f"<h1>{escape(titulo)}</h1><div class='sub'>{escape(subtitulo)}</div>"
        f"</header><h2>Contenido</h2><ol>{indice}</ol>")

    html = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        f"<title>{escape(titulo)}</title><style>{_CSS}</style></head><body>"
        + portada + "".join(partes)
        + f"<footer class='doc'><span>PTNT-BAL</span><span>{escape(pie)}</span>"
          "</footer></body></html>")

    p = Path(salida_html)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p


def unir_pdfs(pdfs: list[Path], salida: str | Path) -> Path | None:
    """Une varios PDF en uno solo, si hay una herramienta disponible."""

    pdfs = [p for p in pdfs if p and Path(p).exists()]
    if not pdfs:
        return None
    out = Path(salida)
    for herramienta in ("pdfunite", "pdftk"):
        exe = shutil.which(herramienta)
        if not exe:
            continue
        cmd = ([exe, *map(str, pdfs), str(out)] if herramienta == "pdfunite"
               else [exe, *map(str, pdfs), "cat", "output", str(out)])
        try:
            subprocess.run(cmd, capture_output=True, timeout=180, check=True)
            if out.exists():
                return out
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
    return None
