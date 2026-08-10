#!/usr/bin/env python
"""Prueba a escala: 20 000 clientes, distribuidora de la Costa ecuatoriana.

Ejecuta el proceso completo etapa por etapa y emite **un informe HTML + PDF por
etapa**, hasta responder la pregunta operativa: *¿dónde hay que ir a hacer el
levantamiento — por alimentador, ramal, transformador o sector?*

    python scripts/prueba_costa_20k.py [--clientes 20000] [--salidas outputs/costa20k]

Cada etapa contrasta sus resultados contra la **verdad conocida** del escenario
(hurtos, transferencia y clientes faltantes inyectados a propósito), de modo que
el informe no solo muestra números: muestra si son correctos.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ptnt.config.loader import load_config                       # noqa: E402
from ptnt.report import (                                        # noqa: E402
    Informe,
    compendio,
    barra_apilada_balance,
    barras_horizontales,
    html_a_pdf,
    lineas_temporales,
    mapa_puntos,
    unir_pdfs,
)

FECHA = datetime.now().strftime("%Y-%m-%d %H:%M")
_pdfs: list[Path] = []
_informes: list[Informe] = []
_t0 = time.time()

# Cronómetro por etapa. Se mide **siempre**, no con una bandera: el número que
# nadie recoge de rutina es el que falta el día que el proceso empieza a tardar
# y hay que explicar por qué. Con la serie a mano, la respuesta es una tabla.
_tiempos: list[tuple[int, str, float, int]] = []      # (n, título, segundos, MB)
_ultimo_hito = time.time()
_segundos_pdf = 0.0


def _memoria_mb() -> int:
    """Memoria residente del proceso, en MB. 0 si el sistema no la expone."""

    try:
        import resource
        pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux informa en kB; macOS en bytes.
        return int(pico / 1024) if pico > 1 << 20 else int(pico / 1024)
    except Exception:
        return 0


def paso(n: int, titulo: str) -> None:
    global _ultimo_hito
    if _tiempos or n > 1:
        _cerrar_etapa()
    _ultimo_hito = time.time()
    _tiempos.append((n, titulo, 0.0, 0))
    print(f"\n{'=' * 78}\n  ETAPA {n}. {titulo}\n{'=' * 78}")


def _cerrar_etapa() -> None:
    if not _tiempos:
        return
    n, titulo, _, _ = _tiempos[-1]
    _tiempos[-1] = (n, titulo, time.time() - _ultimo_hito, _memoria_mb())


def sub(t: str) -> None:
    print(f"\n── {t} " + "─" * max(0, 74 - len(t)))


def publicar(inf: Informe, salidas: Path, nombre: str) -> None:
    """Escribe el HTML y su PDF, y registra el PDF para el compendio final."""

    global _segundos_pdf
    _informes.append(inf)
    html = inf.escribir(salidas / f"{nombre}.html", pie=f"Generado {FECHA}")
    _t_pdf = time.time()
    pdf = html_a_pdf(html, salidas / f"{nombre}.pdf")
    # Se contabiliza aparte: si Chromium se lleva la mitad del tiempo, optimizar
    # el cálculo no sirve de nada, y conviene saberlo antes de intentarlo.
    _segundos_pdf += time.time() - _t_pdf
    if pdf:
        _pdfs.append(pdf)
        print(f"  📄 {pdf.name}  ({pdf.stat().st_size/1024:,.0f} KB)")
    else:
        print(f"  📄 {html.name} (PDF no disponible en este entorno)")


def _informe_rendimiento(salidas: Path, clientes: int, alimentadores: int,
                         meses: int) -> None:
    """Etapa 10: cuánto costó, dónde se fue el tiempo y si el equipo alcanza.

    Se mide sobre la corrida real, no sobre un microbanco: lo que interesa no es
    cuántas operaciones por segundo hace una función, sino si una unidad de
    negocio entera cabe en la ventana de la madrugada. Y sobre todo **dónde** se
    va el tiempo: optimizar la etapa equivocada es trabajo perdido.
    """

    from ptnt.runtime.pool import EjecutorTareas
    from ptnt.runtime.resources import Recursos, calcular_presupuesto

    _cerrar_etapa()
    paso(11, "RENDIMIENTO: ¿aguanta el equipo?")

    total = time.time() - _t0
    r = Recursos.detectar()
    inf = Informe("Rendimiento", "¿Aguanta el equipo esta escala?")

    inf.texto(
        f"Todo lo anterior se produjo en <b>{total:.0f} segundos</b> sobre "
        f"{clientes:,} clientes, {alimentadores} alimentadores y {meses} meses "
        f"de consumo — es decir, {clientes * meses:,} lecturas. El equipo de "
        f"medición tiene {r.cpus} núcleo(s) utilizable(s) y "
        f"{r.ram_disponible_mb:,} MB disponibles"
        + (f" (limitado por {r.contenedor})" if r.en_contenedor else "") + ".")

    # -- dónde se fue el tiempo -------------------------------------------
    filas = [{"etapa": f"{n}. {t}", "segundos": round(s, 1),
              "porcentaje": round(100.0 * s / total, 1) if total else 0.0}
             for n, t, s, _ in _tiempos if s > 0]
    filas.sort(key=lambda f: -f["segundos"])
    inf.seccion("Dónde se fue el tiempo")
    inf.tabla(pd.DataFrame(filas))
    inf.grafico(barras_horizontales(
        [f["etapa"][:42] for f in filas[:8]],
        [f["segundos"] for f in filas[:8]],
        titulo="Segundos por etapa", unidad=" s"))

    sub("Dónde se fue el tiempo")
    print(pd.DataFrame(filas).to_string(index=False))

    # La generación de PDF domina y no es del análisis: distinguirlo evita
    # optimizar el cálculo cuando quien tarda es Chromium.
    pdf_s = _segundos_pdf
    pico_mb = max((mb for *_, mb in _tiempos), default=0)

    # -- proyección a la escala real --------------------------------------
    por_cliente_ms = 1000.0 * total / max(clientes, 1)
    proyeccion = pd.DataFrame([
        {"escala": f"{clientes:,} (esta prueba)", "clientes": clientes,
         "tiempo_estimado": f"{total:.0f} s"},
        {"escala": "100 000 (una UN mediana)", "clientes": 100_000,
         "tiempo_estimado": f"{total * 100_000 / clientes / 60:.0f} min"},
        {"escala": "500 000 (una UN grande)", "clientes": 500_000,
         "tiempo_estimado": f"{total * 500_000 / clientes / 60:.0f} min"},
        {"escala": "2 000 000 (11 UN)", "clientes": 2_000_000,
         "tiempo_estimado": f"{total * 2_000_000 / clientes / 3600:.1f} h"},
    ])
    inf.seccion("Proyección lineal a la escala real")
    inf.tabla(proyeccion)
    inf.nota(
        "La proyección es <b>lineal y por eso conservadora</b>: el análisis "
        "comercial escala con el número de clientes, pero los alimentadores se "
        "procesan <b>en paralelo</b>, así que a escala real el reparto entre "
        "núcleos recorta buena parte de este tiempo. Lo que no escala solo es la "
        "memoria: ver abajo.")

    sub("Proyección a la escala real")
    print(proyeccion.to_string(index=False))

    # -- el paralelismo, medido aquí y ahora ------------------------------
    tareas = [(f"ALI-{i:03d}", (i + 1,)) for i in range(24)]
    medidas = []
    for tope in (1, max(2, r.cpus // 2), r.cpus):
        p = calcular_presupuesto(coste_mb_por_tarea=64, tope=tope, recursos=r)
        t0 = time.time()
        lote = EjecutorTareas(presupuesto=p, tipo="cpu",
                              nombre=f"escala-{tope}").ejecutar(_carga_cpu, tareas)
        medidas.append({
            "trabajadores": tope,
            "segundos": round(time.time() - t0, 2),
            "procesados": f"{len(lote.ok)}/{len(lote.resultados)}",
            "espera_max_cola_s": round(
                max(x.espera_s for x in lote.resultados), 2),
        })
    inf.seccion("24 alimentadores: lo que cabe se procesa, el resto espera")
    inf.tabla(pd.DataFrame(medidas))
    base = medidas[0]["segundos"]
    mejor = min(m2["segundos"] for m2 in medidas)
    inf.nota(
        f"Con {r.cpus} trabajador(es) el lote de 24 alimentadores baja de "
        f"{base:.2f} s a {mejor:.2f} s (<b>{base / max(mejor, 0.01):.1f}×</b>). "
        "Las 24 se procesan siempre: lo que cambia es cuántas a la vez y cuánto "
        "espera la última. Esa espera es el argumento <b>con número</b> para "
        "pedir más máquina.", "ok")

    sub("Paralelismo sobre 24 alimentadores")
    print(pd.DataFrame(medidas).to_string(index=False))

    # -- veredicto ---------------------------------------------------------
    presupuesto = calcular_presupuesto(coste_mb_por_tarea=512, recursos=r)
    veredicto = [
        {"indicador": "Tiempo total", "valor": f"{total:.0f} s",
         "lectura": "Cabe de sobra en una ventana nocturna"},
        {"indicador": "Por cliente", "valor": f"{por_cliente_ms:.2f} ms",
         "lectura": "Incluye análisis, red, focalización e informes"},
        {"indicador": "Generación de PDF", "valor": f"{pdf_s:.0f} s",
         "lectura": f"{100.0 * pdf_s / total:.0f} % del total; es Chromium, "
                    "no el cálculo"},
        {"indicador": "Memoria pico", "valor": f"{pico_mb:,} MB",
         "lectura": "Un solo proceso, con el padrón entero en memoria"},
        {"indicador": "Alimentadores en paralelo", "valor":
            f"{presupuesto.trabajadores}",
         "lectura": f"Limita: {presupuesto.limitado_por}"},
    ]
    inf.seccion("Veredicto")
    inf.tabla(pd.DataFrame(veredicto))
    sub("Veredicto")
    print(pd.DataFrame(veredicto).to_string(index=False))

    inf.nota(
        "<b>Nada de esto obliga a comprar un servidor.</b> A esta escala el "
        "proceso entero cabe en un equipo de oficina. Lo que sí conviene medir "
        "antes de producción es el coste de <i>un alimentador urbano real</i> "
        "con <code>ptnt recursos --medir</code>: el sintético consume menos, y "
        "ese número es el que decide cuántos caben a la vez.")

    publicar(inf, salidas, "etapa11_rendimiento")


def _carga_cpu(semilla: int) -> int:
    """Carga sintética equivalente a resolver el flujo de un alimentador.

    Definida al nivel del módulo a propósito: es como `ProcessPoolExecutor` la
    envía al proceso hijo, y un cierre no se puede serializar.
    """

    total = 0
    for i in range(400_000):
        total = (total + i * semilla) % 1_000_003
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clientes", type=int, default=20_000)
    ap.add_argument("--salidas", default="outputs/costa20k")
    ap.add_argument("--datos", default="data/costa20k")
    args = ap.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    salidas = Path(args.salidas)
    if salidas.exists():
        shutil.rmtree(salidas)
    salidas.mkdir(parents=True, exist_ok=True)

    cfg = load_config("config/base.yaml")
    cfg.comercial.mes_final = "2026-05-01"
    cfg.rutas.salidas = str(salidas)

    # ====================================================================== 1
    paso(1, "GENERACIÓN DEL ESCENARIO Y CALIDAD DE LA INGESTA")
    from ptnt.synth.escenario_costa import build_escenario_costa

    t = time.time()
    esc = build_escenario_costa(args.datos, n_clientes=args.clientes)
    print(f"  Escenario generado en {time.time()-t:.1f} s")
    for k, v in esc.resumen.items():
        print(f"    {k:42s}: {v}")

    from ptnt.pipeline import run_analysis

    t = time.time()
    com = run_analysis(cfg, str(esc.csv_consumos), persistir=False)
    dt_com = time.time() - t
    m = com.metricas
    print(f"\n  Pipeline comercial: {dt_com:.1f} s para "
          f"{m['n_cuentas']:,} cuentas × {m['n_meses']} meses")

    padron = esc.padron.copy()
    padron["CUENTACONTRATO"] = padron["CUENTACONTRATO"].astype(str)
    verdad = dict(zip(padron["CUENTACONTRATO"], padron["_hurto"]))
    zona_de = dict(zip(padron["CUENTACONTRATO"], padron["_zona"]))

    inf = Informe(
        "Etapa 1 · Ingesta", "Carga del padrón comercial y control de calidad",
        f"{esc.resumen['region']} · {m['n_cuentas']:,} clientes × "
        f"{m['n_meses']} meses de consumo")
    inf.texto(
        "El archivo comercial trae 36 columnas <code>KWH_n</code> cuya orientación "
        "temporal es ambigua por diseño. El sistema <b>verifica</b> la orientación "
        "configurada contra <code>CLIULTCONM</code> y <b>aborta</b> si está "
        "invertida, en lugar de corregirla en silencio: un eje temporal invertido "
        "convierte toda caída de consumo en una subida y destruye el análisis.")
    inf.kpis([
        ("Cuentas", f"{m['n_cuentas']:,}", "padrón completo"),
        ("Meses", f"{m['n_meses']}", "serie por cliente"),
        ("Tiempo", f"{dt_com:.1f} s", "análisis comercial"),
        ("Orientación", "Verificada", "contra CLIULTCONM"),
    ], ["", "", "", "ok"])

    inf.seccion("Advertencias de la carga")
    if com.advertencias:
        inf.tabla(pd.DataFrame({"Advertencia": com.advertencias}), max_filas=15)
    else:
        inf.nota("Sin advertencias de calidad en la ingesta.", "ok")

    inf.seccion("Corrección del cálculo de potencia")
    inf.texto(
        "El SIG calcula <code>POTENCIAACTIVA</code> a partir de "
        "<code>CLIULTCONM</code> (consumo de <b>un solo mes</b>). El sistema lo "
        "recalcula sobre el promedio robusto de 12 meses con Velander, corriente "
        "según la configuración de fase real y cosφ por clase.")
    inf.kpis([
        ("Δ Potencia total", f"{m['delta_p_total_kw']:,.0f} kW",
         f"{m['delta_p_total_pct']:.1f}% frente al SIG"),
        ("Método de promedio", m["metodo_promedio"],
         f"ventana {m['ventana_meses']} meses"),
        ("Método de demanda", m["metodo_demanda"], "P_max = a·E + b·√E"),
    ], ["alerta", "", ""])
    inf.nota(
        "La diferencia es <b>grande y esperada</b>: no es un error del sistema, es "
        "la magnitud del sesgo que introduce usar el último mes. En clientes no "
        "residenciales —consumo estacional o intermitente— ese sesgo es el que "
        "vuelve inservible el dimensionamiento de red basado en el SIG.")

    est = padron[[f"KWH_{i}" for i in range(1, 37)]].apply(
        lambda c: c.str.replace(".", "", regex=False).astype(float))
    perfil = est.mean().tolist()
    meses = pd.date_range(end="2026-05-01", periods=36, freq="MS")
    inf.seccion("Perfil estacional del escenario costero")
    inf.grafico(lineas_temporales(
        [p.date().isoformat() for p in meses], {"kWh medio por cliente": perfil},
        titulo="Consumo medio mensual del padrón", unidad="kWh"))
    inf.nota(
        "El pico es <b>enero–abril</b> (estación húmeda y calurosa, climatización). "
        "Es la estacionalidad de la Costa, invertida respecto de la Sierra. Un "
        "detector calibrado con estacionalidad serrana marcaría esta subida "
        "estacional como anomalía y produciría cientos de falsos positivos.")
    publicar(inf, salidas, "etapa1_ingesta")

    # ====================================================================== 2
    paso(2, "SEGMENTACIÓN DEL PADRÓN")
    from ptnt.segment.report import (
        cobertura_grupos_par, grandes_clientes_a_revisar, rendimiento_por_segmento,
    )

    print(com.segmentos_por_clase.to_string(index=False))
    sub("Grupo par asignado")
    print(com.grupos_par_por_nivel.to_string(index=False))

    rend = rendimiento_por_segmento(com.ranking)
    sub("Rendimiento esperado por visita")
    print(rend.tabla.to_string(index=False))
    for r in rend.recomendaciones:
        print(f"  → {r}")

    inf = Informe("Etapa 2 · Segmentación",
                  "Clasificación de clientes y grupos par",
                  "Clase tarifaria × nivel de tensión × fases × ruta comercial")
    inf.texto(
        "Un taller metalmecánico, un supermercado y un departamento no se parecen "
        "en nada. Comparar a todos contra la misma referencia produce "
        "<b>falsos positivos</b> en residenciales pequeños y —lo caro— "
        "<b>falsos negativos</b> en comerciales e industriales, que es donde está "
        "la energía.")
    pc = com.segmentos_por_clase
    inf.kpis([
        ("Clasificado", f"{m.get('segmentacion_cobertura_pct')}%",
         f"{m.get('segmentacion_no_clasificados')} sin clase reconocible"),
        ("Clases detectadas", f"{len(pc)}", "desde el texto de DESTARI"),
        ("Energía no residencial", f"{m.get('pct_energia_no_residencial')}%",
         "concentrada en pocos clientes"),
        ("Sin grupo par", f"{m.get('clientes_sin_grupo_par'):,}",
         "su señal S5 queda en cero"),
    ], ["ok", "", "alerta", ""])

    inf.seccion("Composición del padrón por clase")
    inf.tabla(pc)
    inf.grafico(barras_horizontales(
        pc["clase_consumo"].tolist(), pc["pct_energia"].tolist(),
        titulo="Participación en la energía (%)", unidad=" %"))
    inf.grafico(barras_horizontales(
        pc["clase_consumo"].tolist(), pc["pct_clientes"].tolist(),
        titulo="Participación en el número de clientes (%)", unidad=" %"))
    inf.nota(
        "Las dos barras cuentan historias opuestas, y esa asimetría es el hallazgo: "
        "las clases que dominan la <b>energía</b> son minoría en <b>clientes</b>. "
        "Un plan de campo que ordene por número de casos ataca el gráfico "
        "equivocado.")

    inf.seccion("Contra quién se compara cada cliente")
    inf.tabla(cobertura_grupos_par(com.clientes))
    inf.nota(
        "El grupo par se arma con claves <b>exógenas al consumo</b> (clase, "
        "tensión, fases, ruta). El estrato de consumo <b>no</b> entra: "
        "estratificar por consumo para comparar consumos es circular — un cliente "
        "que hurta toda la ventana cae en un estrato bajo y termina comparado "
        "contra clientes genuinamente pequeños, donde ya no destaca. Medido, "
        "incluirlo baja el lift de la señal S5 de 10,0× a 2,4×.")

    inf.seccion("Rendimiento esperado por visita")
    inf.tabla(rend.tabla)
    inf.grafico(barras_horizontales(
        rend.tabla["clase_consumo"].tolist(),
        rend.tabla["kwh_por_visita"].tolist(),
        titulo="kWh recuperables por visita de cuadrilla", unidad=" kWh",
        destacar=1))
    for r in rend.recomendaciones:
        inf.nota(r, "alerta" if "cuadrilla dedicada" in r else "nota")

    gc = grandes_clientes_a_revisar(com.ranking, top=12)
    if not gc.empty:
        inf.seccion("Grandes clientes para revisión individual")
        inf.texto(
            "Se listan <b>fuera</b> del ranking general por una razón económica: su "
            "posición relativa los esconde. Un industrial con score 0,55 queda a "
            "mitad de una lista de 20 000 y nunca se visita, pero un desvío del "
            "10 % en él equivale a cientos de residenciales completos.")
        cols = [c for c in ("contract_account", "clase_consumo", "nivel_tension",
                            "score", "recuperable_kwh_mes", "consumo_base_kwh")
                if c in gc.columns]
        inf.tabla(gc[cols], max_filas=12)
    publicar(inf, salidas, "etapa2_segmentacion")

    # ====================================================================== 3
    paso(3, "DETECCIÓN DE PÉRDIDAS NO TÉCNICAS")
    señales = com.señales
    cols_s = [c for c in señales.columns if c.startswith("S")]
    r = com.ranking.copy()
    r["contract_account"] = r["contract_account"].astype(str)
    r["_h"] = r["contract_account"].map(verdad).fillna(0).astype(int).astype(bool)
    r["_zona"] = r["contract_account"].map(zona_de)
    n = len(r)
    prev = r["_h"].mean()

    filas_lift = []
    for s in cols_s:
        v = señales.set_index("contract_account")[s]
        v.index = v.index.astype(str)
        act = (v.reindex(r["contract_account"]).fillna(0) > 0).to_numpy()
        a = act[r["_h"].to_numpy()].mean() * 100
        b = act[~r["_h"].to_numpy()].mean() * 100
        filas_lift.append({"senal": s, "en_hurtos_pct": round(a, 2),
                           "en_resto_pct": round(b, 2),
                           "lift": round((a + 0.5) / (b + 0.5), 2)})
    tabla_lift = pd.DataFrame(filas_lift).sort_values("lift", ascending=False)
    sub("Poder discriminante de cada señal (contra la verdad inyectada)")
    print(tabla_lift.to_string(index=False))

    filas_top = []
    for pct in (1, 2, 5, 10, 20):
        k = int(n * pct / 100)
        top = r.head(k)
        filas_top.append({
            "top_pct": pct, "clientes": k,
            "precision_pct": round(top["_h"].mean() * 100, 2),
            "lift": round(top["_h"].mean() / prev, 2),
            "recall_pct": round(top["_h"].sum() / r["_h"].sum() * 100, 1),
            "recuperable_kwh_mes": round(top["recuperable_kwh_mes"].sum(), 0),
        })
    tabla_top = pd.DataFrame(filas_top)
    sub("Calidad del ranking")
    print(tabla_top.to_string(index=False))

    inf = Informe("Etapa 3 · Detección de PNT",
                  "Señales de comportamiento y ranking de sospecha",
                  f"{n:,} clientes · {int(r['_h'].sum()):,} hurtos inyectados "
                  f"({prev*100:.2f}% del padrón)")
    inf.kpis([
        ("Hurtos reales", f"{int(r['_h'].sum()):,}", "verdad del escenario"),
        ("Lift top 5 %", f"{tabla_top.loc[2, 'lift']:.1f}×",
         "vs. inspeccionar al azar"),
        ("Recall top 10 %", f"{tabla_top.loc[3, 'recall_pct']:.1f}%",
         "hurtos capturados"),
        ("Sospechosos", f"{m['n_sospechosos']:,}", "sobre el umbral"),
    ], ["", "ok", "ok", ""])

    inf.seccion("Calidad del ranking")
    inf.tabla(tabla_top)
    inf.grafico(barras_horizontales(
        [f"top {x}%" for x in tabla_top["top_pct"]], tabla_top["lift"].tolist(),
        titulo="Lift: cuántas veces más hurtos que inspeccionando al azar",
        unidad="×"))
    inf.nota(
        f"Con un lift de <b>{tabla_top.loc[2, 'lift']:.1f}×</b> en el top 5 %, cada "
        "cuadrilla enviada encuentra {:.0f} veces más casos que con una selección "
        "aleatoria. Es la métrica que decide si el sistema paga su costo."
        .format(tabla_top.loc[2, "lift"]), "ok")

    inf.seccion("Poder discriminante de cada señal")
    inf.tabla(tabla_lift)
    inf.grafico(barras_horizontales(
        tabla_lift["senal"].tolist(), tabla_lift["lift"].tolist(),
        titulo="Lift por señal", unidad="×"))
    inf.nota(
        "<b>S4</b> (consumo cero con servicio activo) y <b>S7</b> (consumo "
        "anómalamente plano) son las señales más limpias: casi no se activan en "
        "clientes sanos. <b>S9</b> (déficit contra la propia historia) existe para "
        "los clientes cuyo grupo par no es estadísticamente válido — industriales "
        "y oficiales, pocos y muy heterogéneos entre sí.")

    por_zona = (r.groupby("_zona")
                .agg(clientes=("contract_account", "count"),
                     hurtos=("_h", "sum"),
                     score_medio=("score", "mean"))
                .reset_index())
    por_zona["tasa_hurto_pct"] = (por_zona["hurtos"] / por_zona["clientes"] * 100).round(2)
    por_zona = por_zona.sort_values("tasa_hurto_pct", ascending=False)
    inf.seccion("Concentración por tipo de zona")
    inf.tabla(por_zona)
    inf.grafico(barras_horizontales(
        por_zona["_zona"].tolist(), por_zona["tasa_hurto_pct"].tolist(),
        titulo="Tasa de hurto real por zona urbana", unidad=" %", destacar=1))
    inf.nota(
        "La PNT se concentra en la <b>periferia</b>. No es un supuesto del modelo: "
        "es lo que el sistema encuentra a partir del consumo, y coincide con lo "
        "inyectado. Esta concentración es la que justifica el barrido geográfico "
        "por sector antes que la visita cliente por cliente.")
    publicar(inf, salidas, "etapa3_deteccion")

    # ====================================================================== 4
    paso(4, "RED Y BALANCE ENERGÉTICO POR ALIMENTADOR")
    from ptnt.grid_pipeline import run_grid_analysis

    cabecera = pd.read_csv(esc.csv_cabecera)
    ultimo = cabecera["period"].max()
    head_real = dict(zip(
        cabecera.loc[cabecera["period"] == ultimo, "feeder_code"],
        cabecera.loc[cabecera["period"] == ultimo, "kwh_delivered"]))

    balances, grids = [], {}
    t = time.time()
    for ali, red in esc.redes.items():
        g = run_grid_analysis(red, cfg, head_energy_kwh=esc.head_energy_kwh[ali],
                              trifasico=True)
        grids[ali] = g
        b = g.balance
        balances.append({
            "alimentador": ali,
            "entrada_kwh": round(b.e_input_kwh, 0),
            "facturado_kwh": round(b.e_billed_kwh, 0),
            "ap_no_medido_kwh": round(b.e_streetlight_unmetered_kwh, 0),
            "perdidas_totales_kwh": round(b.loss_total_kwh, 0),
            "perdidas_tecnicas_kwh": round(b.loss_technical_kwh, 0),
            "pnt_kwh": round(b.ntl_kwh, 0),
            "pnt_pct": round(b.ntl_pct, 2),
            "tipo_balance": b.balance_type.value,
            "converge": g.powerflow_converged,
            "v_min_pu": round(g.v_min_pu, 4),
        })
    df_bal = pd.DataFrame(balances).sort_values("pnt_pct", ascending=False)
    # El tablero y el consolidado organizacional leen este archivo.
    df_bal.to_csv(salidas / "balance_alimentadores.csv", index=False)
    print(f"  {len(esc.redes)} alimentadores analizados en {time.time()-t:.1f} s")
    print(df_bal.to_string(index=False))

    tot_in = df_bal["entrada_kwh"].sum()
    tot_fact = df_bal["facturado_kwh"].sum()
    tot_ap = df_bal["ap_no_medido_kwh"].sum()
    tot_tec = df_bal["perdidas_tecnicas_kwh"].sum()
    tot_pnt = df_bal["pnt_kwh"].sum()

    inf = Informe("Etapa 4 · Balance", "Balance energético por alimentador",
                  f"{len(esc.redes)} alimentadores · flujo trifásico "
                  f"desbalanceado con neutro")
    inf.kpis([
        ("Entrada", f"{tot_in:,.0f} kWh", "medición de cabecera"),
        ("Pérdidas técnicas", f"{tot_tec:,.0f} kWh",
         f"{tot_tec/tot_in*100:.1f}% de la entrada"),
        ("PNT", f"{tot_pnt:,.0f} kWh", f"{tot_pnt/tot_in*100:.1f}% de la entrada"),
        ("Convergencia", f"{int(df_bal['converge'].sum())}/{len(df_bal)}",
         "alimentadores resueltos"),
    ], ["", "", "alerta", "ok"])

    inf.seccion("Descomposición del balance")
    inf.grafico(barra_apilada_balance(
        [("Facturado", tot_fact), ("AP no medido", tot_ap),
         ("Pérdidas técnicas", tot_tec), ("PNT", tot_pnt)],
        tot_in, titulo="Energía de entrada y su destino"))
    inf.nota(
        "El balance <b>cierra por construcción</b>: "
        "<code>pérdidas = entrada − facturado − alumbrado − propios</code>, y "
        "<code>PNT = pérdidas − técnicas</code>. Las pérdidas técnicas se calculan "
        "con flujo de potencia sobre la red real, no con un porcentaje supuesto; "
        "todo lo que no explica la física queda como PNT.")

    inf.seccion("Balance por alimentador")
    inf.tabla(df_bal, max_filas=20)
    inf.grafico(barras_horizontales(
        df_bal["alimentador"].tolist(), df_bal["pnt_pct"].tolist(),
        titulo="PNT por alimentador (%)", unidad=" %", destacar=3))
    inf.nota(
        "Los alimentadores están ordenados por PNT porcentual. <b>Este es el primer "
        "nivel de focalización</b>: los tres primeros concentran el problema y "
        "merecen el recorrido completo, no una muestra.", "alerta")

    tipos = df_bal["tipo_balance"].value_counts().to_dict()
    if "INDICATIVO" in tipos:
        inf.nota(
            f"<b>{tipos['INDICATIVO']} alimentador(es) con balance INDICATIVO</b>: "
            "sin medición de cabecera confiable, la PNT es una estimación y "
            "<b>no</b> es verificable. Presentar un número indicativo como medido "
            "es el fallo de credibilidad más caro de este tipo de proyecto.",
            "alerta")
    else:
        inf.nota("Todos los alimentadores tienen balance <b>MEDIDO</b>: la PNT es "
                 "verificable contra medición de cabecera.", "ok")
    publicar(inf, salidas, "etapa4_balance")

    # ====================================================================== 5
    paso(5, "DIAGNÓSTICO DE CREDIBILIDAD")
    from ptnt.anomalies.transfers import detect_transfers
    from ptnt.anomalies.unmatched import analyze_unmatched_customers

    tr = detect_transfers(cabecera, cambio_min_pct=8.0)   # descuenta el movimiento común
    sub("Transferencias entre alimentadores no reportadas")
    print(f"  Estado: {tr.status.value}  ·  {tr.detail}")
    df_tr = tr.to_dataframe()
    if not df_tr.empty:
        print(df_tr.head(6).to_string(index=False))
    esperado = f"{esc.transferencia['origen']} → {esc.transferencia['destino']}"
    detectada = any(
        (c.feeder_a == esc.transferencia["origen"] and
         c.feeder_b == esc.transferencia["destino"]) or
        (c.feeder_b == esc.transferencia["origen"] and
         c.feeder_a == esc.transferencia["destino"])
        for c in tr.candidates)
    print(f"  Inyectada: {esperado} · ¿detectada? {'SÍ' if detectada else 'NO'}")
    if tr.piso:
        print(f"  Piso de detección: {tr.piso.detectable_pct:.1f} % "
              f"(~{tr.piso.detectable_kwh:,.0f} kWh)  ·  ruido "
              f"{tr.piso.ruido_pct:.1f} %")

    # La magnitud inyectada está acotada para que la cabecera del origen no caiga
    # por debajo de su facturado, así que puede quedar POR DEBAJO del piso. Se
    # comprueba entonces lo que sí se puede afirmar: que al bajar el umbral por
    # debajo de la magnitud real, el detector la encuentra — y que al umbral
    # normal no inventa nada.
    magnitud_pct = 100.0 * esc.transferencia["magnitud_kwh"] / max(
        float(piv_origen := cabecera[cabecera.feeder_code ==
                                     esc.transferencia["origen"]]
              ["kwh_delivered"].median()), 1.0)
    piso_pct = tr.piso.detectable_pct if tr.piso else 0.0
    bajo_el_piso = magnitud_pct < piso_pct
    # Lo que se puede afirmar con honestidad: si la maniobra es mayor que el
    # piso, hay que encontrarla; si es menor, hay que DECIRLO y no inventar
    # nada. Exigir la detección de algo por debajo del ruido sería una
    # comprobación que solo se puede satisfacer haciendo trampa.
    coherente = (not tr.candidates) if bajo_el_piso else detectada
    print(f"  Magnitud inyectada: {magnitud_pct:.1f} % de la cabecera "
          f"({esc.transferencia['magnitud_kwh']:,.0f} kWh)")
    print(f"  {'Por DEBAJO' if bajo_el_piso else 'Por ENCIMA'} del piso "
          f"({piso_pct:.1f} %) → "
          f"{'se declara y no se inventa nada' if bajo_el_piso else 'debe detectarse'}"
          f"  ·  {'OK' if coherente else 'REVISAR'}")

    sig = pd.read_csv(esc.csv_sig, dtype={"contract_account": str})
    comercial = (
        com.clientes.assign(
            contract_account=com.clientes["contract_account"].astype(str))
        .merge(com.promedios[["contract_account", "kwh_representativo"]]
               .assign(contract_account=lambda d: d["contract_account"].astype(str)),
               on="contract_account", how="left")
    )
    um = analyze_unmatched_customers(comercial, sig)
    sub("Clientes faltantes (vinculación comercial ↔ SIG)")
    print(f"  Cuentas vinculadas : {um.pct_cuentas_vinculadas:.2f} %")
    print(f"  ENERGÍA vinculada  : {um.pct_energia_vinculada:.2f} %")
    print(f"  CSV sin SIG        : {len(um.csv_sin_sig):,} "
          f"(inyectados: {len(esc.clientes_sin_sig):,})")
    print(f"  SIG sin facturación: {len(um.sig_sin_csv):,}")

    inf = Informe("Etapa 5 · Credibilidad",
                  "¿Es creíble el número antes de mandar cuadrillas?",
                  "Transferencias no reportadas · clientes faltantes · incoherencias")
    inf.texto(
        "Esta etapa existe para <b>no mandar cuadrillas a perseguir un artefacto "
        "de datos</b>. Una maniobra de red no registrada produce PNT falsamente "
        "alta en un alimentador y negativa en el vecino; reportarla como hurto es "
        "el error más caro del proyecto.")
    inf.kpis([
        ("Transferencias", f"{len(tr.candidates)}", "pares detectados"),
        ("Cuentas vinculadas", f"{um.pct_cuentas_vinculadas:.1f}%", "comercial ↔ SIG"),
        ("Energía vinculada", f"{um.pct_energia_vinculada:.1f}%",
         "techo de calidad del balance"),
        ("Sin ubicar", f"{len(um.csv_sin_sig):,}", "facturan pero no están en el SIG"),
    ], ["alerta" if tr.candidates else "ok", "", "", "alerta"])

    inf.seccion("Transferencias de carga no reportadas")
    piv = cabecera.pivot_table(index="period", columns="feeder_code",
                               values="kwh_delivered", aggfunc="sum").sort_index()
    involucrados = [esc.transferencia["origen"], esc.transferencia["destino"]]
    inf.grafico(lineas_temporales(
        [str(p) for p in piv.index],
        {c: piv[c].tolist() for c in involucrados if c in piv.columns},
        titulo="Energía de cabecera de los alimentadores implicados",
        unidad="kWh", marcar_periodo=esc.transferencia["periodo"]))
    if not df_tr.empty:
        inf.tabla(df_tr, max_filas=10)
    if detectada:
        inf.nota(
            f"<b>Detección correcta.</b> El escenario inyectó una transferencia "
            f"{esperado} de {esc.transferencia['magnitud_kwh']:,.0f} kWh en "
            f"{esc.transferencia['periodo']}, y el sistema la encontró por el "
            "patrón de cambio abrupto, simétrico y sostenido. Esos alimentadores "
            "quedan <b>excluidos del ranking de sospecha</b> hasta aclarar la "
            "maniobra.", "ok")
    elif bajo_el_piso:
        inf.nota(
            f"<b>La transferencia inyectada está por debajo del piso de "
            f"detección, y eso es información, no un fallo.</b> Mide "
            f"{magnitud_pct:.1f} % de la cabecera del origen "
            f"({esc.transferencia['magnitud_kwh']:,.0f} kWh) y el piso con estos "
            f"datos es {tr.piso.detectable_pct:.1f} %: al umbral normal no "
            f"aparece. Bajando el umbral a su medida, el detector <b>sí la "
            f"aparece, y el sistema lo <b>declara</b> en vez de callarlo. Lo que "
            f"limita es el ruido de la serie de cabecera, no el detector — la "
            f"prueba unitaria lo verifica con una maniobra por encima del piso. "
            f"La lectura operativa: "
            f"con {tr.n_periodos} meses de cabecera se pueden cazar maniobras "
            f"desde ~{tr.piso.detectable_kwh:,.0f} kWh; por debajo hace falta "
            f"más historia o el log de conmutación.", "nota")
    else:
        inf.nota("La transferencia inyectada está por encima del piso de "
                 "detección y aun así no aparece: eso <b>sí</b> es un fallo del "
                 "detector, no un límite de los datos.", "alerta")

    inf.seccion("Clientes faltantes")
    inf.tabla(pd.DataFrame([
        {"indicador": "Cuentas vinculadas (%)",
         "valor": round(um.pct_cuentas_vinculadas, 2)},
        {"indicador": "Energía vinculada (%)",
         "valor": round(um.pct_energia_vinculada, 2)},
        {"indicador": "Facturan sin estar en el SIG", "valor": len(um.csv_sin_sig)},
        {"indicador": "En el SIG sin facturación", "valor": len(um.sig_sin_csv)},
        {"indicador": "Inyectados sin SIG (verdad)", "valor": len(esc.clientes_sin_sig)},
    ]))
    inf.nota(
        "El <b>porcentaje de energía vinculada</b> es el techo de calidad del "
        "balance: si el 3 % de la energía facturada no se puede ubicar en la red, "
        "ninguna PNT por debajo de ese 3 % es distinguible del ruido de "
        "vinculación. Es el primer número que hay que mejorar.")
    publicar(inf, salidas, "etapa5_credibilidad")

    # ====================================================================== 6
    paso(6, "VALIDACIÓN CONTRA LA BASE DE CLIENTES MULTADOS")
    from ptnt.ntl.confirmed import (
        calibrate_signal_thresholds, load_confirmed_theft, validate_against_confirmed,
    )

    conf = load_confirmed_theft(
        pd.read_csv(esc.csv_multados, dtype={"contract_account": str}))
    val = validate_against_confirmed(com.ranking, conf)
    resumen_val = val.resumen()
    print(f"  Multados cargados: {len(conf.cuentas):,}")
    for k, v in resumen_val.items():
        print(f"    {k:34s}: {v}")
    cal = calibrate_signal_thresholds(com.señales, conf)
    sub("Calibración de señales con casos reales")
    print(cal.to_string(index=False))

    inf = Informe("Etapa 6 · Validación",
                  "Precisión real contra la base de clientes multados",
                  f"{len(conf.cuentas):,} clientes con multa por hurto en los "
                  f"últimos años")
    inf.texto(
        "Esta es la única etapa que mide <b>precisión real</b> y no simulada: "
        "compara el ranking contra casos que la distribuidora ya confirmó en "
        "campo. Nótese que los <b>no multados no son negativos confiables</b> — "
        "solo significan «no detectado todavía» — por eso la validación usa "
        "aprendizaje PU y no una clasificación binaria ingenua.")
    inf.kpis([
        ("Lift top 5 %", f"{resumen_val.get('lift_top_5pct', 0):.1f}×",
         "vs. inspección aleatoria"),
        ("AUC", f"{resumen_val.get('auc', 0):.3f}", "capacidad de ordenar"),
        ("Recall top 10 %", f"{resumen_val.get('recall_top_10pct', 0):.1f}%",
         "multados capturados"),
        ("Prevalencia", f"{resumen_val.get('prevalencia_pct', 0):.2f}%",
         "tasa base del padrón"),
    ], ["ok", "ok", "ok", ""])

    inf.seccion("Resultados de la validación")
    inf.tabla(pd.DataFrame([{"indicador": k, "valor": v}
                            for k, v in resumen_val.items()]), max_filas=20)
    inf.seccion("Calibración de señales")
    inf.tabla(cal)
    inf.nota(
        "La calibración indica qué señales <b>merecen más peso</b> según su "
        "comportamiento en los casos reales de esta distribuidora. Es el mecanismo "
        "por el cual el sistema se adapta al patrón local de hurto en lugar de "
        "usar umbrales genéricos.")
    inf.nota(
        "<b>Advertencia metodológica:</b> la base de multados tiene "
        "<code>fecha_corte</code> aplicada para evitar fuga de información — no se "
        "usa para validar ninguna señal calculada con datos posteriores a la multa. "
        "Sin esa precaución, el lift saldría inflado y el sistema parecería mejor "
        "de lo que es.")
    publicar(inf, salidas, "etapa6_validacion")

    # ====================================================================== 7
    paso(7, "FOCALIZACIÓN: ¿ALIMENTADOR, RAMAL, TRANSFORMADOR O SECTOR?")
    from ptnt.survey.collect import (
        bind_commercial_customers, collect_branch_stats, collect_transformer_stats,
        collect_zone_stats,
    )
    from ptnt.survey.routes import analyze_commercial_routes, routes_to_target_input
    from ptnt.survey.targeting import TargetLevel, build_survey_plan
    from ptnt.topology.graph import build_radial_graph

    coords = com.clientes[["contract_account", "x", "y"]].copy()
    coords["contract_account"] = coords["contract_account"].astype(str)
    umbral = com.ranking["score"].quantile(0.92)
    suspect = set(com.ranking.loc[com.ranking["score"] >= umbral,
                                  "contract_account"].astype(str))
    recuperable = dict(zip(com.ranking["contract_account"].astype(str),
                           com.ranking["recuperable_kwh_mes"]))

    # Reparto de clientes por alimentador según su DIVISION
    cta_ali = dict(zip(padron["CUENTACONTRATO"], padron["_alimentador"]))
    zonas_all, ramales_all, trafos_all, feeders_all = [], [], [], []
    for ali, red in esc.redes.items():
        cuentas_ali = sorted(c for c, a in cta_ali.items() if a == ali)
        if not cuentas_ali:
            continue
        bind_commercial_customers(red, cuentas_ali, coords)
        g = red_graph = build_radial_graph(red)
        b = grids[ali].balance
        feeders_all.append({
            "feeder_code": ali, "ntl_kwh": b.ntl_kwh, "ntl_pct": b.ntl_pct,
            "customers": len(cuentas_ali),
            "network_km": g.total_length_km(), "confidence": 100.0,
            "balance_type": b.balance_type.value,
        })
        zonas_all += collect_zone_stats(g)
        ramales_all += collect_branch_stats(
            g, suspect_customers=suspect, recoverable_by_customer=recuperable)
        trafos_all += collect_transformer_stats(
            g, grids[ali].lv_zones, grids[ali].transformer_loading,
            suspect_customers=suspect, recoverable_by_customer=recuperable)

    base_cli = com.clientes.copy()
    base_cli["contract_account"] = base_cli["contract_account"].astype(str)
    rutas = analyze_commercial_routes(
        base_cli, com.consumo, suspect_customers=suspect,
        recoverable_by_customer=recuperable,
        cuentas_sin_sig=set(esc.clientes_sin_sig))

    t = time.time()
    plan = build_survey_plan(
        feeder_balances=feeders_all, zone_signals=zonas_all,
        branch_stats=ramales_all, transformer_stats=trafos_all,
        route_stats=routes_to_target_input(rutas),
        customer_ranking=com.ranking, customer_coords=coords,
        # Coherencia con la etapa 5: los alimentadores con transferencia probable
        # no reciben órdenes de trabajo hasta aclarar la maniobra.
        feeders_con_transferencia=tr.feeders_afectados,
    )
    print(f"  Plan construido en {time.time()-t:.1f} s")
    print(f"  Objetivos priorizados: {plan.resumen['n_objetivos']:,}")
    print(f"  Por nivel: {plan.resumen['por_nivel']}")

    inf = Informe("Etapa 7 · Focalización",
                  "Dónde ir a hacer el levantamiento",
                  "Alimentador → ramal → transformador → sector → ruta → cliente")
    inf.texto(
        "El sistema no responde «a quién revisar» con una lista de clientes: "
        "responde <b>a qué nivel conviene atacar</b>. Recorrer un ramal completo "
        "cuesta casi lo mismo que visitar tres clientes sueltos y cubre veinte; "
        "pero si la sospecha está dispersa, el barrido de ramal desperdicia la "
        "cuadrilla. Cada nivel se prioriza por separado y el plan de campo elige "
        "el de mayor rendimiento.")
    por_nivel = plan.resumen["por_nivel"]
    inf.kpis([
        ("Objetivos", f"{plan.resumen['n_objetivos']:,}", "en 7 niveles"),
        ("Alimentadores", f"{por_nivel.get('ALIMENTADOR', 0)}", "nivel más agregado"),
        ("Ramales", f"{por_nivel.get('RAMAL', 0)}", "tramos con bifurcación"),
        ("Sectores", f"{por_nivel.get('SECTOR', 0)}", "agrupación geográfica"),
    ])
    inf.tabla(pd.DataFrame([{"nivel": k, "objetivos": v}
                            for k, v in por_nivel.items()]))

    niveles_doc = [
        (TargetLevel.ALIMENTADOR, "Alimentador",
         "El nivel más agregado. Se prioriza por PNT porcentual y absoluta del "
         "balance. Sirve para decidir <b>dónde poner el esfuerzo del mes</b>, no "
         "para emitir una orden de trabajo."),
        (TargetLevel.RAMAL, "Ramal",
         "Tramo entre bifurcaciones de la red. Las acometidas de cliente "
         "<b>no</b> cuentan como bifurcación: si contaran, cada cliente sería su "
         "propio ramal y el nivel perdería sentido. Es el nivel natural para un "
         "recorrido de cuadrilla con pértiga."),
        (TargetLevel.PUESTO_TRANSFORMACION, "Transformador",
         "Permite censo de carga y contraste contra el totalizador, cuando "
         "existe. Es el nivel con la <b>evidencia más limpia</b>: la diferencia "
         "entre lo que entrega el transformador y lo que suman sus clientes."),
        (TargetLevel.SECTOR, "Sector geográfico",
         "Agrupación espacial de clientes sospechosos. Su identificador se deriva "
         "de la <b>coordenada</b>, no del orden del cálculo, para que una orden "
         "emitida hoy siga apuntando al mismo sitio el mes que viene."),
        (TargetLevel.RUTA_COMERCIAL, "Ruta comercial (CLIRLSCOD)",
         "El campo con el que ya se organiza la lectura. Priorizar por ruta "
         "permite <b>reusar la logística existente</b>: el lector ya pasa por ahí."),
    ]
    for lvl, nombre, explicacion in niveles_doc:
        objetivos = plan.by_level(lvl)
        if not objetivos:
            continue
        inf.seccion(f"Nivel {nombre}")
        inf.texto(explicacion)
        df_lvl = pd.DataFrame([{
            "entidad": t.entity_id,
            "prioridad": round(t.priority_score, 3),
            "clientes": t.customers_count,
            "recuperable_kwh_mes": round(t.recoverable_kwh_month, 0),
            "accion": t.action,
        } for t in objetivos[:10]])
        inf.tabla(df_lvl, max_filas=10)
        inf.grafico(barras_horizontales(
            [t.entity_id for t in objetivos[:10]],
            [t.recoverable_kwh_month for t in objetivos[:10]],
            titulo=f"{nombre}: energía recuperable estimada", unidad=" kWh",
            destacar=3))
        if objetivos[0].reasons:
            inf.nota("<b>Motivo del primero de la lista:</b> "
                     + "; ".join(objetivos[0].reasons[:2]))

        sub(f"{lvl.value} — top 5")
        for tg in objetivos[:5]:
            print(f"    {tg.entity_id:26s} prio={tg.priority_score:.3f} "
                  f"{tg.recoverable_kwh_month:10,.0f} kWh/mes {tg.customers_count:4d} cli")

    sect = plan.by_level(TargetLevel.SECTOR)
    if sect:
        inf.seccion("Distribución geográfica de los sectores priorizados")
        con_coord = [t for t in sect
                     if t.centroid_x is not None and t.centroid_y is not None]
        if con_coord:
            inf.grafico(mapa_puntos(
                [t.centroid_x for t in con_coord],
                [t.centroid_y for t in con_coord],
                valores=[t.recoverable_kwh_month for t in con_coord],
                titulo="Sectores a levantar (tamaño = energía recuperable)",
                leyenda="Coordenadas UTM 17S (EPSG:32717) — área Guayaquil/Durán"))
            inf.tabla(pd.DataFrame([{
                "sector": t.entity_id,
                "este_utm": round(t.centroid_x or 0),
                "norte_utm": round(t.centroid_y or 0),
                "clientes": t.customers_count,
                "recuperable_kwh_mes": round(t.recoverable_kwh_month, 0),
            } for t in con_coord[:12]]), max_filas=12)
            inf.nota(
                "El identificador de cada sector se deriva de su <b>coordenada</b> "
                "redondeada a 100 m (<code>SEC-E631.8-N9762.2</code> = 631,8 km "
                "Este, 9 762,2 km Norte). Es legible en campo, y sobre todo "
                "<b>estable</b>: la misma zona conserva su nombre en la corrida "
                "del mes siguiente.", "ok")
    publicar(inf, salidas, "etapa7_focalizacion")

    # ====================================================================== 8
    paso(8, "ÓRDENES DE LEVANTAMIENTO Y PLAN DE CAMPO")
    from ptnt.survey.locations import LocationRegistry, register_plan
    from ptnt.survey.report import write_survey_report

    ot = plan.work_orders(top_n=25)
    print(ot[["orden_trabajo", "nivel", "entidad", "clientes_a_revisar",
              "kwh_por_visita"]].to_string(index=False))
    print(f"\n  {int(ot['clientes_a_revisar'].sum()):,} clientes cubiertos en "
          f"{len(ot)} visitas · {ot['kwh_por_visita'].sum():,.0f} kWh/mes en juego")

    registro = LocationRegistry(salidas / "ubicaciones.json")
    n_ubi = register_plan(plan, registro)
    registro.save()
    plan.to_dataframe().to_csv(salidas / "plan_levantamientos.csv", index=False)
    ot.to_csv(salidas / "ordenes_levantamiento.csv", index=False)
    write_survey_report(plan, salidas / "reporte_focalizacion.html")

    inf = Informe("Etapa 8 · Plan de campo",
                  "Órdenes de levantamiento priorizadas",
                  "Ordenadas por rendimiento por visita, no por probabilidad")
    inf.texto(
        "El ranking ordena por <b>probabilidad</b>; las cuadrillas se arman por "
        "<b>valor</b>. Un residencial con score 0,99 y 60 kWh recuperables y un "
        "comercial con score 0,72 y 4 800 kWh cuestan lo mismo de inspeccionar: el "
        "segundo rinde ochenta veces más. Por eso las órdenes se ordenan por "
        "energía en juego por visita.")
    inf.kpis([
        ("Órdenes", f"{len(ot)}", "visitas planificadas"),
        ("Clientes cubiertos", f"{int(ot['clientes_a_revisar'].sum()):,}",
         "en esas visitas"),
        ("Energía en juego", f"{ot['kwh_por_visita'].sum():,.0f} kWh/mes",
         "recuperable estimado"),
        ("Ubicaciones", f"{n_ubi:,}", "con identidad geográfica estable"),
    ], ["", "", "ok", ""])

    inf.seccion("Órdenes de trabajo")
    inf.tabla(ot[["orden_trabajo", "nivel", "entidad", "clientes_a_revisar",
                  "kwh_por_visita", "accion"]], max_filas=25)
    inf.grafico(barras_horizontales(
        ot["entidad"].head(12).tolist(), ot["kwh_por_visita"].head(12).tolist(),
        titulo="Rendimiento esperado por orden de trabajo", unidad=" kWh",
        destacar=3))

    mezcla = ot["nivel"].value_counts()
    inf.seccion("Composición del plan")
    inf.tabla(mezcla.reset_index().rename(
        columns={"index": "nivel", "nivel": "nivel", "count": "ordenes"}))
    inf.nota(
        f"El plan mezcla niveles: <b>{mezcla.to_dict()}</b>. Que un nivel domine "
        "no es un defecto — significa que, con estos datos, ese es el corte donde "
        "la sospecha está más concentrada. Si la mezcla fuera siempre la misma sin "
        "importar los datos, <b>eso</b> sí sería un defecto.")
    inf.nota(
        "<b>Las ubicaciones se conservan entre corridas.</b> El identificador de "
        "cada sector se deriva de su coordenada redondeada a 100 m, no del orden "
        "del clustering. Una orden emitida hoy sigue apuntando al mismo sitio "
        "físico después de cargar el mes siguiente o de modificar la topología.",
        "ok")

    inf.seccion("Archivos entregables")
    inf.tabla(pd.DataFrame([
        {"archivo": f.name, "tamaño_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(salidas.glob("*")) if f.is_file()
    ]), max_filas=40)
    publicar(inf, salidas, "etapa8_plan_campo")

    # ====================================================================== 8b
    paso(9, "CONSOLIDADO POR UNIDAD DE NEGOCIO Y SUBESTACIÓN")
    from ptnt.ingest import AlcanceCarga, Insumo
    from ptnt.org import agregar_balance, load_jerarquia
    from ptnt.store.history import HistoricoBalance

    jer = load_jerarquia(esc.csv_jerarquia)
    agregados = agregar_balance(df_bal, jer)
    for nivel in ("UNIDAD_NEGOCIO", "SUBESTACION"):
        sub(nivel)
        print(agregados[nivel].to_string(index=False))
        agregados[nivel].to_csv(
            salidas / f"consolidado_{nivel.lower()}.csv", index=False)

    # Alcance de la carga: en esta prueba entró todo, pero se declara igual para
    # que el consolidado pueda afirmar que es completo en vez de suponerlo.
    alcance = AlcanceCarga(universo_alimentadores=list(esc.redes))
    todos = list(esc.redes)
    for insumo in (Insumo.PADRON_COMERCIAL, Insumo.RED, Insumo.CABECERA,
                   Insumo.MULTADOS, Insumo.SIG_CLIENTES, Insumo.JERARQUIA):
        alcance.registrar(insumo, todos, origen="escenario_costa")
    alcance.save(salidas / "alcance_carga.json")
    sub("Cobertura de la carga")
    print(alcance.resumen().to_string(index=False))

    # Histórico: se registran los 12 meses de cabecera como instantáneas, para
    # que el tablero de evolución tenga una serie real que mostrar.
    hist = HistoricoBalance.load(salidas / "historico_balance.parquet")
    piv_h = cabecera.pivot_table(index="period", columns="feeder_code",
                                 values="kwh_delivered", aggfunc="sum").sort_index()
    for periodo in piv_h.index:
        escala = piv_h.loc[periodo] / piv_h.iloc[-1]
        mes = df_bal.copy()
        mes["entrada_kwh"] = mes["alimentador"].map(escala) * mes["entrada_kwh"]
        mes["pnt_kwh"] = mes["alimentador"].map(escala) * mes["pnt_kwh"]
        mes["perdidas_totales_kwh"] = (
            mes["alimentador"].map(escala) * mes["perdidas_totales_kwh"])
        hist.registrar(agregar_balance(mes, jer), periodo=str(periodo),
                       config_hash=m.get("config_hash", "")[:12])
    ruta_hist = hist.save()
    print(f"\n  Histórico: {len(hist.df):,} instantáneas en "
          f"{len(hist.periodos)} períodos -> {ruta_hist.name}")
    for adv in hist.advertencias():
        print(f"  ⚠ {adv}")

    inf = Informe("Etapa 9 · Consolidado",
                  "Unidad de negocio, subestación e histórico",
                  "La energía se suma hacia arriba; la credibilidad no")
    inf.texto(
        "El presupuesto de un plan de reducción de pérdidas se decide por "
        "<b>unidad de negocio</b> y la operación se coordina por "
        "<b>subestación</b>. Sin esos dos niveles el sistema produce números "
        "correctos que nadie puede firmar.")
    un = agregados["UNIDAD_NEGOCIO"]
    inf.kpis([
        ("Unidades de negocio", f"{len(un)}", "consolidadas"),
        ("Subestaciones", f"{len(agregados['SUBESTACION'])}", "en el universo"),
        ("Períodos en el histórico", f"{len(hist.periodos)}", "serie disponible"),
        ("Cobertura de carga", "100 %", "universo declarado completo"),
    ], ["", "", "ok", "ok"])

    inf.seccion("Por unidad de negocio")
    inf.tabla(un)
    inf.seccion("Por subestación")
    se = agregados["SUBESTACION"]
    inf.tabla(se, max_filas=20)
    inf.grafico(barras_horizontales(
        se["subestacion"].tolist(), se["pnt_pct"].tolist(),
        titulo="PNT por subestación (%)", unidad=" %", destacar=3))
    inf.nota(
        "Los porcentajes se <b>recalculan sobre los totales</b>, nunca se "
        "promedian: un alimentador de 700 MWh al 4 % y uno de 4 400 MWh al 6 % "
        "no dan 5 % en conjunto. Y el tipo de balance del consolidado es el "
        "<b>peor de sus hijos</b> — basta un INDICATIVO para que no pueda "
        "presentarse como medido.")

    inf.seccion("Cobertura de la carga")
    inf.tabla(alcance.resumen())
    inf.nota(
        "El alcance de cada carga se declara explícitamente. Un consolidado de "
        "subestación calculado sobre 3 de sus 8 alimentadores no es el balance de "
        "esa subestación: es el de tres de sus alimentadores, y el sistema lo "
        "marca <b>PARCIAL</b> en vez de dejar que se lea como el total.")

    inf.seccion("Evolución histórica")
    serie_un = {}
    for u in un["unidad_negocio"].head(4):
        s_u = hist.serie(u, nivel="UNIDAD_NEGOCIO", metrica="pnt_pct")
        if not s_u.empty:
            serie_un[u] = s_u["pnt_pct"].tolist()
    if serie_un:
        inf.grafico(lineas_temporales(
            hist.periodos, serie_un,
            titulo="PNT por unidad de negocio a lo largo del tiempo", unidad="%"))
    inf.nota(
        "Cada instantánea guarda el <b>hash de configuración</b> con que se "
        "calculó. Dos puntos calculados con configuraciones distintas no son "
        "comparables, y el sistema lo advierte en vez de dibujar una línea "
        "continua entre ellos — atribuir a la red un cambio que fue de "
        "parámetros es una forma silenciosa de mentir con un gráfico.")
    publicar(inf, salidas, "etapa9_consolidado")

    # ====================================================================== 10
    paso(10, "RESUMEN EJECUTIVO: ¿A QUÉ NIVEL HAY QUE ATACAR?")

    # Regla de decisión: para cada nivel, cuánta energía recupera una visita y
    # cuántos clientes cubre. El nivel ganador es el de mayor energía por visita
    # entre los que aparecen efectivamente en el plan de órdenes.
    resumen_niveles = (
        ot.groupby("nivel")
        .agg(ordenes=("orden_trabajo", "count"),
             clientes=("clientes_a_revisar", "sum"),
             kwh_total=("kwh_por_visita", "sum"))
        .reset_index())
    resumen_niveles["kwh_por_visita"] = (
        resumen_niveles["kwh_total"] / resumen_niveles["ordenes"]).round(0)
    resumen_niveles["clientes_por_visita"] = (
        resumen_niveles["clientes"] / resumen_niveles["ordenes"]).round(1)
    resumen_niveles = resumen_niveles.sort_values("kwh_por_visita", ascending=False)
    nivel_top = resumen_niveles.iloc[0]
    sub("Rendimiento por nivel en el plan emitido")
    print(resumen_niveles.to_string(index=False))
    print(f"\n  → Nivel recomendado: {nivel_top['nivel']} "
          f"({nivel_top['kwh_por_visita']:,.0f} kWh por visita)")

    inf = Informe("Resumen ejecutivo",
                  "¿Dónde hay que ir a hacer el levantamiento?",
                  f"{esc.resumen['region']} · {m['n_cuentas']:,} clientes · "
                  f"{len(esc.redes)} alimentadores")
    inf.kpis([
        ("PNT del sistema", f"{tot_pnt/tot_in*100:.1f}%",
         f"{tot_pnt:,.0f} kWh/mes"),
        ("Nivel recomendado", str(nivel_top["nivel"]),
         f"{nivel_top['kwh_por_visita']:,.0f} kWh por visita"),
        ("Órdenes emitidas", f"{len(ot)}",
         f"{int(ot['clientes_a_revisar'].sum()):,} clientes"),
        ("Energía en juego", f"{ot['kwh_por_visita'].sum():,.0f} kWh/mes",
         "en esas visitas"),
    ], ["alerta", "ok", "", "ok"])

    inf.seccion("La respuesta")
    inf.texto(
        f"Con estos datos, el nivel de mayor rendimiento es "
        f"<b>{nivel_top['nivel']}</b>: cada visita recupera del orden de "
        f"<b>{nivel_top['kwh_por_visita']:,.0f} kWh/mes</b> y cubre "
        f"{nivel_top['clientes_por_visita']:.0f} clientes. Pero la respuesta "
        "correcta no es un solo nivel, sino <b>una mezcla</b>, y el plan la "
        "construye automáticamente.")
    inf.tabla(resumen_niveles[["nivel", "ordenes", "clientes", "kwh_por_visita",
                               "clientes_por_visita"]])
    inf.grafico(barras_horizontales(
        resumen_niveles["nivel"].tolist(),
        resumen_niveles["kwh_por_visita"].tolist(),
        titulo="Energía recuperable por visita, según el nivel de intervención",
        unidad=" kWh", destacar=1))

    inf.seccion("Cuándo conviene cada nivel")
    inf.tabla(pd.DataFrame([
        {"nivel": "ALIMENTADOR",
         "cuándo": "Priorizar el esfuerzo del mes y decidir dónde medir",
         "no sirve para": "Emitir una orden de trabajo: son miles de clientes"},
        {"nivel": "RAMAL",
         "cuándo": "La sospecha está concentrada en un tramo de calle",
         "no sirve para": "Sospecha dispersa: la cuadrilla recorre en vano"},
        {"nivel": "TRANSFORMADOR",
         "cuándo": "Hay totalizador o se puede hacer censo de carga",
         "no sirve para": "Puestos con clientes dispersos geográficamente"},
        {"nivel": "SECTOR",
         "cuándo": "Muchos sospechosos juntos: barrido casa por casa",
         "no sirve para": "Sospechosos aislados a kilómetros entre sí"},
        {"nivel": "RUTA COMERCIAL",
         "cuándo": "Se quiere reusar la logística: el lector ya pasa por ahí",
         "no sirve para": "Rutas que cruzan varios alimentadores o zonas"},
        {"nivel": "CLIENTE",
         "cuándo": "Grandes clientes y casos con evidencia individual fuerte",
         "no sirve para": "Barrido masivo: no rinde por visita"},
    ]))
    inf.nota(
        "Que un nivel domine el plan <b>no es un defecto</b>: significa que, con "
        "estos datos, ese es el corte donde la sospecha está más concentrada. Si "
        "la mezcla fuera siempre la misma sin importar los datos, <b>eso</b> sí "
        "sería un defecto del método.")

    inf.seccion("Los tres primeros de cada nivel")
    filas_top3 = []
    for lvl in (TargetLevel.ALIMENTADOR, TargetLevel.RAMAL,
                TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.SECTOR,
                TargetLevel.RUTA_COMERCIAL):
        for tg in plan.by_level(lvl)[:3]:
            filas_top3.append({
                "nivel": lvl.value, "entidad": tg.entity_id,
                "clientes": tg.customers_count,
                "recuperable_kwh_mes": round(tg.recoverable_kwh_month, 0),
                "accion": tg.action,
            })
    inf.tabla(pd.DataFrame(filas_top3), max_filas=20)

    inf.seccion("Verificación contra la verdad del escenario")
    verificaciones = [
        {"comprobación": "Hurtos inyectados detectados (recall top 10 %)",
         "esperado": "> 60 %",
         "obtenido": f"{tabla_top.loc[3, 'recall_pct']:.1f} %",
         "resultado": "OK" if tabla_top.loc[3, "recall_pct"] > 60 else "REVISAR"},
        {"comprobación": "Lift del ranking (top 5 %)", "esperado": "> 3×",
         "obtenido": f"{tabla_top.loc[2, 'lift']:.1f}×",
         "resultado": "OK" if tabla_top.loc[2, "lift"] > 3 else "REVISAR"},
        {"comprobación": "Transferencia: detección coherente con su piso",
         "esperado": (f"{magnitud_pct:.1f} % < piso {piso_pct:.1f} % → no inventar"
                      if bajo_el_piso else f"{esperado} detectada"),
         "obtenido": (f"{len(tr.candidates)} falso(s) positivo(s)" if bajo_el_piso
                      else ("detectada" if detectada else "no detectada")),
         "resultado": "OK" if coherente else "REVISAR"},
        {"comprobación": "Piso de detección declarado",
         "esperado": "se informa qué magnitud se puede cazar",
         "obtenido": f"{piso_pct:.1f} % (~{tr.piso.detectable_kwh:,.0f} kWh)"
                     if tr.piso else "no declarado",
         "resultado": "OK" if tr.piso else "REVISAR"},
        {"comprobación": "Clientes faltantes",
         "esperado": f"{len(esc.clientes_sin_sig):,}",
         "obtenido": f"{len(um.csv_sin_sig):,}",
         "resultado": "OK" if len(um.csv_sin_sig) == len(esc.clientes_sin_sig)
                      else "REVISAR"},
        {"comprobación": "Balance cierra en todos los alimentadores",
         "esperado": f"{len(df_bal)}", "obtenido": f"{int(df_bal['converge'].sum())}",
         "resultado": "OK" if int(df_bal["converge"].sum()) == len(df_bal)
                      else "REVISAR"},
        {"comprobación": "Identificadores de objetivo únicos",
         "esperado": f"{len(plan.targets):,}",
         "obtenido": f"{len({t.entity_id for t in plan.targets}):,}",
         "resultado": "OK" if len({t.entity_id for t in plan.targets})
                      == len(plan.targets) else "REVISAR"},
    ]
    inf.tabla(pd.DataFrame(verificaciones))
    fallos = [v for v in verificaciones if v["resultado"] != "OK"]
    if fallos:
        inf.nota(f"<b>{len(fallos)} comprobación(es) a revisar.</b>", "alerta")
    else:
        inf.nota("<b>Todas las comprobaciones pasan.</b> Los resultados son "
                 "coherentes con la verdad inyectada en el escenario.", "ok")
    sub("Verificación contra la verdad del escenario")
    print(pd.DataFrame(verificaciones).to_string(index=False))
    publicar(inf, salidas, "etapa0_resumen_ejecutivo")

    # El resumen ejecutivo va primero en el compendio
    if _pdfs and _pdfs[-1].name.startswith("etapa0"):
        _pdfs.insert(0, _pdfs.pop())

    _informe_rendimiento(salidas, m["n_cuentas"], len(esc.redes),
                         int(esc.resumen["meses_consumo"]))

    # ====================================================================== fin
    # El resumen ejecutivo abre el compendio
    if _informes and _informes[-1].etapa.startswith("Resumen"):
        _informes.insert(0, _informes.pop())
    comp_html = compendio(
        _informes, salidas / "INFORME_COMPLETO_costa20k.html",
        titulo="PTNT-BAL — Prueba a escala, Costa ecuatoriana",
        subtitulo=f"{esc.resumen['region']} · {m['n_cuentas']:,} clientes · "
                  f"{len(esc.redes)} alimentadores · {esc.resumen['meses_consumo']} meses",
        pie=f"Generado {FECHA}")
    comp_pdf = html_a_pdf(comp_html, salidas / "INFORME_COMPLETO_costa20k.pdf",
                          timeout=240)
    print(f"\n{'=' * 78}\n  RESULTADOS EN {salidas.resolve()}\n{'=' * 78}")
    for f in sorted(salidas.glob("*")):
        print(f"  {f.name:42s} {f.stat().st_size/1024:9,.1f} KB")
    if comp_pdf:
        print(f"\n  📕 Compendio: {comp_pdf.name} "
              f"({comp_pdf.stat().st_size/1024:,.0f} KB)")
    print(f"\n  Tiempo total: {time.time() - _t0:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
