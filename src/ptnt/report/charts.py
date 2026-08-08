"""Gráficos SVG sin dependencias externas.

El servidor de la distribuidora no siempre puede instalar matplotlib ni una
cadena de renderizado; y para un informe que se imprime a PDF, el SVG generado a
mano es más liviano, escala sin pixelarse y no arrastra fuentes del sistema.

Paleta pensada para leerse igual en pantalla y en impresión a escala de grises:
las series se distinguen por luminosidad además de por tono.
"""

from __future__ import annotations

from html import escape

# Secuencia categórica: tonos distintos y luminosidades escalonadas
PALETA = [
    "#1f4e79", "#2e8b8b", "#c9702a", "#7a4b93",
    "#4a7c3f", "#a8352e", "#5b6770", "#b08d2e",
]
COLOR_ALERTA = "#a8352e"
COLOR_OK = "#4a7c3f"
COLOR_NEUTRO = "#5b6770"


def _t(v: float) -> str:
    """Número corto para etiquetas dentro del gráfico."""

    a = abs(v)
    if a >= 1e6:
        return f"{v/1e6:,.1f}M"
    if a >= 1e3:
        return f"{v/1e3:,.1f}k"
    if a >= 10:
        return f"{v:,.0f}"
    return f"{v:,.1f}"


def barras_horizontales(
    etiquetas: list[str],
    valores: list[float],
    *,
    titulo: str = "",
    unidad: str = "",
    ancho: int = 720,
    alto_barra: int = 26,
    colores: list[str] | None = None,
    destacar: int | None = None,
) -> str:
    """Barras horizontales ordenadas, con el valor al final de cada barra.

    Se eligen horizontales porque las etiquetas de este dominio (códigos de
    alimentador, ramal, sector) son largas y en barras verticales quedarían
    rotadas e ilegibles en el PDF.
    """

    if not etiquetas:
        return "<p class='vacio'>Sin datos para graficar.</p>"

    n = len(etiquetas)
    m_izq, m_der, m_sup = 190, 96, 30 if titulo else 8
    alto = m_sup + n * alto_barra + 12
    max_v = max(max(valores), 1e-9)
    ancho_util = ancho - m_izq - m_der

    partes = [
        f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
        f'preserveAspectRatio="xMinYMin meet" role="img" '
        f'aria-label="{escape(titulo or "gráfico de barras")}">'
    ]
    if titulo:
        partes.append(
            f'<text x="0" y="16" class="svg-title">{escape(titulo)}</text>')

    for i, (et, val) in enumerate(zip(etiquetas, valores)):
        y = m_sup + i * alto_barra
        w = max(ancho_util * (val / max_v), 1.5)
        color = (colores[i] if colores else
                 (COLOR_ALERTA if destacar is not None and i < destacar
                  else PALETA[i % len(PALETA)]))
        partes.append(
            f'<text x="{m_izq - 8}" y="{y + alto_barra/2 + 4}" '
            f'text-anchor="end" class="svg-lab">{escape(str(et))}</text>'
            f'<rect x="{m_izq}" y="{y + 3}" width="{w:.1f}" '
            f'height="{alto_barra - 8}" rx="2" fill="{color}"/>'
            f'<text x="{m_izq + w + 7}" y="{y + alto_barra/2 + 4}" '
            f'class="svg-val">{_t(val)}{escape(unidad)}</text>'
        )
    partes.append("</svg>")
    return "".join(partes)


def lineas_temporales(
    periodos: list[str],
    series: dict[str, list[float]],
    *,
    titulo: str = "",
    unidad: str = "",
    ancho: int = 760,
    alto: int = 300,
    marcar_periodo: str | None = None,
) -> str:
    """Series temporales. ``marcar_periodo`` dibuja una línea vertical de evento
    (por ejemplo, el mes en que se detectó una transferencia de carga)."""

    if not periodos or not series:
        return "<p class='vacio'>Sin datos para graficar.</p>"

    m_izq, m_der, m_sup, m_inf = 78, 130, 34 if titulo else 12, 44
    w = ancho - m_izq - m_der
    h = alto - m_sup - m_inf
    todos = [v for s in series.values() for v in s if v == v]
    lo, hi = min(todos), max(todos)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def px(i: int) -> float:
        return m_izq + w * (i / max(len(periodos) - 1, 1))

    def py(v: float) -> float:
        return m_sup + h * (1 - (v - lo) / (hi - lo))

    partes = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
              f'preserveAspectRatio="xMinYMin meet">']
    if titulo:
        partes.append(f'<text x="0" y="18" class="svg-title">{escape(titulo)}</text>')

    # rejilla y eje Y
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = py(v)
        partes.append(
            f'<line x1="{m_izq}" y1="{y:.1f}" x2="{m_izq + w}" y2="{y:.1f}" '
            f'class="svg-grid"/>'
            f'<text x="{m_izq - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="svg-ax">{_t(v)}</text>')

    # eje X (máximo 8 etiquetas para que no se solapen)
    paso = max(1, len(periodos) // 8)
    for i, p in enumerate(periodos):
        if i % paso and i != len(periodos) - 1:
            continue
        partes.append(
            f'<text x="{px(i):.1f}" y="{m_sup + h + 20}" text-anchor="middle" '
            f'class="svg-ax">{escape(str(p)[:7])}</text>')

    if marcar_periodo and marcar_periodo in periodos:
        i = periodos.index(marcar_periodo)
        partes.append(
            f'<line x1="{px(i):.1f}" y1="{m_sup}" x2="{px(i):.1f}" '
            f'y2="{m_sup + h}" class="svg-evento"/>'
            f'<text x="{px(i):.1f}" y="{m_sup - 4}" text-anchor="middle" '
            f'class="svg-evento-lab">evento</text>')

    for j, (nombre, vals) in enumerate(series.items()):
        color = PALETA[j % len(PALETA)]
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}"
                       for i, v in enumerate(vals) if v == v)
        partes.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                      f'stroke-width="2.2" stroke-linejoin="round"/>')
        if vals:
            partes.append(
                f'<text x="{m_izq + w + 8}" y="{py(vals[-1]) + 4:.1f}" '
                f'class="svg-serie" fill="{color}">{escape(nombre)}</text>')

    partes.append(f'<text x="{m_izq - 8}" y="{m_sup - 6}" text-anchor="end" '
                  f'class="svg-ax">{escape(unidad)}</text></svg>')
    return "".join(partes)


def mapa_puntos(
    xs: list[float],
    ys: list[float],
    *,
    valores: list[float] | None = None,
    grupos: list[str] | None = None,
    titulo: str = "",
    ancho: int = 760,
    alto: int = 460,
    leyenda: str = "",
) -> str:
    """Dispersión geográfica en coordenadas proyectadas (UTM).

    Mantiene la **relación de aspecto real** del terreno: en UTM un metro en X y
    un metro en Y valen lo mismo, y deformar la escala daría una idea falsa de
    las distancias que va a recorrer la cuadrilla.
    """

    if not xs:
        return "<p class='vacio'>Sin coordenadas para graficar.</p>"

    m = 52
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    esc = min((ancho - 2 * m) / dx, (alto - 2 * m) / dy)   # isométrica
    ox = m + ((ancho - 2 * m) - dx * esc) / 2
    oy = m + ((alto - 2 * m) - dy * esc) / 2

    cat = sorted(set(grupos)) if grupos else []
    color_de = {c: PALETA[i % len(PALETA)] for i, c in enumerate(cat)}
    v_max = max(valores) if valores else 1.0

    partes = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
              f'preserveAspectRatio="xMinYMin meet">',
              f'<rect x="0" y="0" width="{ancho}" height="{alto}" class="svg-mapa-bg"/>']
    if titulo:
        partes.append(f'<text x="0" y="18" class="svg-title">{escape(titulo)}</text>')

    for i, (x, y) in enumerate(zip(xs, ys)):
        cx = ox + (x - x0) * esc
        cy = alto - (oy + (y - y0) * esc)     # SVG crece hacia abajo; Norte arriba
        r = 3.0 if not valores else 2.5 + 9.0 * (valores[i] / max(v_max, 1e-9)) ** 0.5
        color = color_de.get(grupos[i], PALETA[0]) if grupos else PALETA[0]
        partes.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
                      f'fill="{color}" fill-opacity="0.62"/>')

    # escala gráfica: 5 km medidos sobre el propio factor de escala
    largo = 5000 * esc
    if largo < ancho - 2 * m:
        yb = alto - 22
        partes.append(
            f'<line x1="{m}" y1="{yb}" x2="{m + largo:.1f}" y2="{yb}" '
            f'class="svg-escala"/>'
            f'<text x="{m + largo/2:.1f}" y="{yb - 6}" text-anchor="middle" '
            f'class="svg-ax">5 km</text>')

    if cat:
        for i, c in enumerate(cat[:8]):
            yy = 34 + i * 17
            partes.append(
                f'<circle cx="{ancho - 150}" cy="{yy - 4}" r="5" '
                f'fill="{color_de[c]}"/>'
                f'<text x="{ancho - 138}" y="{yy}" class="svg-ax">'
                f'{escape(str(c))}</text>')
    if leyenda:
        partes.append(f'<text x="0" y="{alto - 6}" class="svg-ax">'
                      f'{escape(leyenda)}</text>')
    partes.append("</svg>")
    return "".join(partes)


def barra_apilada_balance(
    componentes: list[tuple[str, float]],
    total: float,
    *,
    titulo: str = "",
    ancho: int = 760,
) -> str:
    """Descomposición del balance de energía en una sola barra apilada.

    Es la forma más directa de mostrar que el balance **cierra**: la suma de los
    segmentos es exactamente la energía de entrada.
    """

    if total <= 0 or not componentes:
        return "<p class='vacio'>Sin balance para graficar.</p>"

    alto, m_sup = 130, 30 if titulo else 6
    y = m_sup + 8
    h = 46
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%" '
              f'preserveAspectRatio="xMinYMin meet">']
    if titulo:
        partes.append(f'<text x="0" y="18" class="svg-title">{escape(titulo)}</text>')

    x = 0.0
    for i, (nombre, val) in enumerate(componentes):
        w = ancho * (max(val, 0) / total)
        color = PALETA[i % len(PALETA)] if "PNT" not in nombre else COLOR_ALERTA
        partes.append(
            f'<rect x="{x:.1f}" y="{y}" width="{max(w,0.5):.1f}" height="{h}" '
            f'fill="{color}"/>')
        if w > 62:
            partes.append(
                f'<text x="{x + w/2:.1f}" y="{y + h/2 + 4}" text-anchor="middle" '
                f'class="svg-seg">{val/total*100:.1f}%</text>')
        x += w

    x = 0.0
    for i, (nombre, val) in enumerate(componentes):
        w = ancho * (max(val, 0) / total)
        if w > 62:
            partes.append(
                f'<text x="{x + w/2:.1f}" y="{y + h + 20}" text-anchor="middle" '
                f'class="svg-ax">{escape(nombre)}</text>'
                f'<text x="{x + w/2:.1f}" y="{y + h + 34}" text-anchor="middle" '
                f'class="svg-ax-dim">{_t(val)} kWh</text>')
        x += w
    partes.append("</svg>")
    return "".join(partes)
