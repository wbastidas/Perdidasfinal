"""Reporte de segmentación: dónde está la energía y dónde conviene inspeccionar.

Responde con números la pregunta operativa de fondo: **¿a quién conviene ir a
revisar primero?** La respuesta casi nunca es "a los que encabezan el ranking",
porque el ranking ordena por *probabilidad* y no por *valor*. Un residencial con
score 0,99 y 60 kWh recuperables al mes y un comercial con score 0,72 y 4 800 kWh
cuestan lo mismo de inspeccionar; el segundo rinde ochenta veces más.

Este módulo cruza ambas cosas por segmento y produce el orden por **rendimiento
esperado por visita**, que es el criterio con el que se arman las cuadrillas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class RendimientoSegmento:
    """Rendimiento esperado de inspeccionar dentro de un segmento."""

    tabla: pd.DataFrame
    recomendaciones: list[str] = field(default_factory=list)
    concentracion: dict = field(default_factory=dict)


def rendimiento_por_segmento(
    ranking: pd.DataFrame,
    *,
    col_segmento: str = "clase_consumo",
    top_pct: float = 5.0,
    costo_visita: float = 1.0,
) -> RendimientoSegmento:
    """Compara los segmentos por lo que rinde inspeccionar dentro de cada uno.

    ``top_pct`` define la fracción de cada segmento que se inspeccionaría. El
    cálculo se hace **dentro** de cada segmento, no sobre el ranking global: si se
    tomara el top global, los residenciales —que son la enorme mayoría— coparían
    la lista y los pocos comerciales sospechosos no aparecerían nunca, aunque
    valgan mucho más por visita.

    Devuelve, por segmento: clientes, sospechosos, energía recuperable del top y
    **kWh recuperables por visita**, que es el número con el que se decide.
    """

    if ranking.empty or col_segmento not in ranking.columns:
        return RendimientoSegmento(
            tabla=pd.DataFrame(),
            recomendaciones=[
                "El ranking no trae segmentación: no se puede comparar el "
                "rendimiento por clase. Active `segmentacion.habilitada`."
            ],
        )

    filas = []
    for seg, sub in ranking.groupby(col_segmento):
        sub = sub.sort_values("score", ascending=False)
        n = len(sub)
        n_top = max(1, int(round(n * top_pct / 100.0)))
        top = sub.head(n_top)
        rec_top = float(top["recuperable_kwh_mes"].fillna(0).sum())
        filas.append({
            col_segmento: seg,
            "clientes": n,
            "visitas": n_top,
            "recuperable_top_kwh_mes": round(rec_top, 1),
            "kwh_por_visita": round(rec_top / max(n_top * costo_visita, 1e-9), 1),
            "score_medio_top": round(float(top["score"].mean()), 3),
            "recuperable_total_kwh_mes": round(
                float(sub["recuperable_kwh_mes"].fillna(0).sum()), 1),
        })

    tabla = pd.DataFrame(filas).sort_values("kwh_por_visita", ascending=False)
    tabla = tabla.reset_index(drop=True)

    # --- concentración: qué fracción del recuperable está en qué fracción de clientes
    total_rec = float(ranking["recuperable_kwh_mes"].fillna(0).sum())
    total_cli = len(ranking)
    concentracion: dict = {}
    recomendaciones: list[str] = []

    if not tabla.empty and total_rec > 0:
        mejor = tabla.iloc[0]
        peor = tabla.iloc[-1]
        if mejor["kwh_por_visita"] > 0 and peor["kwh_por_visita"] > 0:
            ratio = mejor["kwh_por_visita"] / peor["kwh_por_visita"]
            recomendaciones.append(
                f"Una visita en '{mejor[col_segmento]}' rinde {ratio:,.0f}× más "
                f"energía que una en '{peor[col_segmento]}' "
                f"({mejor['kwh_por_visita']:,.0f} vs {peor['kwh_por_visita']:,.0f} "
                "kWh/mes por visita)."
            )

        # No residenciales: pocos clientes, mucha energía
        if col_segmento == "clase_consumo":
            no_res = tabla[tabla[col_segmento] != "RESIDENCIAL"]
            if not no_res.empty:
                cli_nr = int(no_res["clientes"].sum())
                rec_nr = float(no_res["recuperable_total_kwh_mes"].sum())
                pct_cli = cli_nr / max(total_cli, 1) * 100
                pct_rec = rec_nr / total_rec * 100
                concentracion = {
                    "pct_clientes_no_residenciales": round(pct_cli, 1),
                    "pct_recuperable_no_residencial": round(pct_rec, 1),
                }
                if pct_rec > pct_cli * 1.5:
                    recomendaciones.append(
                        f"Los no residenciales son el {pct_cli:.1f}% de los clientes "
                        f"pero concentran el {pct_rec:.1f}% de la energía "
                        "recuperable: conviene una cuadrilla dedicada a ellos, "
                        "separada del barrido masivo residencial."
                    )
                else:
                    recomendaciones.append(
                        f"Los no residenciales ({pct_cli:.1f}% de clientes) aportan "
                        f"el {pct_rec:.1f}% del recuperable: sin concentración "
                        "marcada, el barrido geográfico masivo sigue siendo la "
                        "estrategia más eficiente."
                    )

    return RendimientoSegmento(
        tabla=tabla, recomendaciones=recomendaciones, concentracion=concentracion,
    )


def grandes_clientes_a_revisar(
    ranking: pd.DataFrame, *, top: int = 25, score_min: float = 0.0
) -> pd.DataFrame:
    """Grandes clientes con cualquier indicio, para revisión **individual**.

    Se listan aparte del ranking general por una razón económica: su posición
    relativa los esconde. Un industrial con score 0,55 queda en la mitad de una
    lista de miles y nunca se visita, pero un desvío del 10 % en él equivale a
    cientos de residenciales completos. La regla operativa estándar es revisar el
    universo de grandes clientes de forma **censal y periódica**, no por ranking.
    """

    if ranking.empty or "es_gran_cliente" not in ranking.columns:
        return pd.DataFrame()
    gc = ranking[ranking["es_gran_cliente"].fillna(False)].copy()
    if gc.empty:
        return gc
    gc = gc[gc["score"] >= score_min]

    # "Con indicios" tiene que significar algo: un gran cliente sin ninguna señal
    # activa y sin energía recuperable estimada no pertenece a esta lista. Sin
    # este filtro la tabla se llena de filas con recuperable 0 que desplazan a los
    # casos que sí ameritan una visita.
    tiene_indicio = pd.Series(True, index=gc.index)
    if "n_senales_activas" in gc.columns:
        tiene_indicio &= gc["n_senales_activas"].fillna(0) > 0
    if "recuperable_kwh_mes" in gc.columns:
        tiene_indicio |= gc["recuperable_kwh_mes"].fillna(0) > 0
    gc = gc[tiene_indicio]
    if gc.empty:
        return gc

    # Se ordena por energía en juego, no por score: es el criterio de valor.
    orden = (
        ["recuperable_kwh_mes", "score"]
        if "recuperable_kwh_mes" in gc.columns else ["score"]
    )
    gc = gc.sort_values(orden, ascending=False).head(top)
    gc["accion"] = "Verificación individual de medición y contraste de demanda"
    return gc.reset_index(drop=True)


def cobertura_grupos_par(clientes: pd.DataFrame) -> pd.DataFrame:
    """Diagnóstico de la calidad de los grupos par asignados.

    Sirve para responder "¿cuánto vale la señal S5 en esta corrida?". Si la mayoría
    de los clientes quedó en el nivel más general, la comparación contra pares está
    aportando poco y conviene revisar la calidad de ``DESTARI`` y ``CLIRLSCOD``
    antes que ajustar umbrales.
    """

    if "grupo_par_nivel" not in clientes.columns:
        return pd.DataFrame()
    tot = len(clientes)
    out = (
        clientes.groupby("grupo_par_nivel", dropna=False)
        .agg(clientes=("contract_account", "count"),
             confianza=("grupo_par_confianza", "first"),
             tam_medio=("grupo_par_n", "mean"))
        .reset_index()
    )
    out["pct"] = (out["clientes"] / max(tot, 1) * 100).round(1)
    out["tam_medio"] = out["tam_medio"].round(1)
    return out.sort_values("confianza", ascending=False).reset_index(drop=True)
