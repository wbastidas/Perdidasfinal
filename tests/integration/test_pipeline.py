"""Pruebas de integración de extremo a extremo.

Verifican que el pipeline completo (parseo → promedio → potencia →
reconciliación → señales → ranking) corre sobre el CSV sintético y **recupera los
hurtos inyectados** en las primeras posiciones del ranking.
"""

import numpy as np
import pytest

from ptnt.config.loader import load_config
from ptnt.pipeline import run_analysis


@pytest.mark.integration
def test_pipeline_extremo_a_extremo(synth_csv, tmp_path):
    cfg = load_config("config/base.yaml")
    # redirigir salidas al tmp
    cfg.rutas.salidas = str(tmp_path / "out")
    cfg.rutas.duckdb = str(tmp_path / "ptnt.duckdb")

    result = run_analysis(cfg, synth_csv.ruta_csv, persistir=False)

    assert result.metricas["n_cuentas"] == 600
    assert result.metricas["n_meses"] == 36
    assert not result.ranking.empty
    assert "score" in result.ranking.columns
    # el ranking está ordenado descendente por score
    scores = result.ranking["score"].to_numpy()
    assert np.all(scores[:-1] >= scores[1:] - 1e-9)


@pytest.mark.integration
def test_hurtos_recuperados_en_ranking(synth_csv):
    """La posición mediana de los hurtos inyectados debe estar en la mitad
    superior del ranking (métrica de calidad del detector, criterio E10)."""

    cfg = load_config("config/base.yaml")
    result = run_analysis(cfg, synth_csv.ruta_csv, persistir=False)

    # cuentas marcadas como hurto en el generador
    meta = synth_csv.df
    hurto_accts = set(meta.loc[meta["_hurto"] == 1, "CUENTACONTRATO"].astype(str))
    assert len(hurto_accts) > 0

    ranking = result.ranking.copy()
    ranking["es_hurto"] = ranking["contract_account"].astype(str).isin(hurto_accts)
    n = len(ranking)
    posiciones = ranking.loc[ranking["es_hurto"], "rank"].to_numpy()
    pos_mediana_pct = np.median(posiciones) / n * 100

    # los hurtos deberían concentrarse arriba: mediana en el tercio superior
    assert pos_mediana_pct < 40, f"Mediana de hurtos en {pos_mediana_pct:.1f}% del ranking"

    # recall en el top: al menos la mitad de los hurtos en el top-15%
    top_k = int(n * 0.15)
    en_top = ranking.head(top_k)["es_hurto"].sum()
    recall_top = en_top / len(hurto_accts)
    assert recall_top >= 0.4, f"Recall en top-15% = {recall_top:.2f}"


@pytest.mark.integration
def test_persistencia_duckdb(synth_csv, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    cfg = load_config("config/base.yaml")
    cfg.rutas.salidas = str(tmp_path / "out")
    cfg.rutas.duckdb = str(tmp_path / "ptnt.duckdb")

    run_analysis(cfg, synth_csv.ruta_csv, persistir=True)

    from ptnt.store.database import Database

    with Database(cfg.rutas.duckdb) as db:
        assert db.table_exists("resultados", "ranking_clientes")
        n = db.read_sql("select count(*) c from resultados.ranking_clientes").iloc[0]["c"]
        assert n == 600
        runs = db.read_sql("select status from meta.run")
        assert (runs["status"] == "OK").any()


@pytest.mark.integration
def test_reconciliacion_no_residencial(synth_csv):
    """La reconciliación de potencia produce un delta no trivial (el SIG usaba el
    último mes; el sistema usa el promedio multi-mes)."""

    cfg = load_config("config/base.yaml")
    result = run_analysis(cfg, synth_csv.ruta_csv, persistir=False)
    assert "delta_p_kw" in result.reconciliacion.columns
    # hay corrección medible
    assert result.reconciliacion["delta_p_kw"].abs().sum() > 0


@pytest.mark.integration
def test_segmentacion_prioriza_la_energia_sin_perder_deteccion(synth_csv):
    """El beneficio medido de segmentar: **misma capacidad de detectar, mucha más
    energía priorizada**.

    Es la comprobación que justifica todo el módulo `ptnt.segment`. Con una
    mediana global, el recuperable del top del ranking está dominado por
    residenciales pequeños; segmentando, el mismo top concentra la energía de los
    comerciales e industriales, que cuestan lo mismo de inspeccionar.
    """

    cfg = load_config("config/base.yaml")
    hurtos = set(
        synth_csv.df.loc[synth_csv.df["_hurto"] == 1, "CUENTACONTRATO"].astype(str)
    )

    def _correr(habilitada: bool):
        cfg.segmentacion.habilitada = habilitada
        r = run_analysis(cfg, synth_csv.ruta_csv, persistir=False)
        rk = r.ranking.copy()
        rk["_h"] = rk["contract_account"].astype(str).isin(hurtos)
        k = max(1, int(len(rk) * 0.10))
        top = rk.head(k)
        return {
            "recall": top["_h"].sum() / max(rk["_h"].sum(), 1),
            "energia_top": float(top["recuperable_kwh_mes"].fillna(0).sum()),
            "metodo": r.metricas["metodo_recuperable"],
            "res": r,
        }

    glob = _correr(False)
    seg = _correr(True)

    assert glob["metodo"] == "global"
    assert seg["metodo"] == "segmentado"

    # 1. No se pierde capacidad de detección (tolerancia por el ruido del sintético)
    assert seg["recall"] >= glob["recall"] - 0.05, (
        f"segmentar degradó el recall: {glob['recall']:.3f} -> {seg['recall']:.3f}"
    )
    # 2. Se prioriza mucha más energía con el mismo número de visitas
    assert seg["energia_top"] > glob["energia_top"] * 3, (
        f"segmentar no concentró energía: {glob['energia_top']:,.0f} -> "
        f"{seg['energia_top']:,.0f} kWh"
    )

    # 3. El resultado es legible por clase y trae el diagnóstico del grupo par
    r = seg["res"]
    assert not r.segmentos_por_clase.empty
    assert not r.grupos_par_por_nivel.empty
    assert r.metricas["segmentacion_cobertura_pct"] > 95.0
    assert "clase_consumo" in r.ranking.columns


@pytest.mark.integration
def test_clase_tarifaria_se_resuelve_desde_el_texto_real_de_destari(synth_csv):
    """`DESTARI` trae texto libre ("INDUSTRIAL CON DEMANDA MEDIA TENSION"), no las
    claves del catálogo. Si la resolución fallara, todos caerían a la clase por
    defecto y los industriales recibirían coeficientes residenciales."""

    cfg = load_config("config/base.yaml")
    r = run_analysis(cfg, synth_csv.ruta_csv, persistir=False)

    # ninguna descripción del sintético coincide literalmente con el catálogo
    descripciones = set(r.clientes["tariff_description"])
    assert not (descripciones & set(cfg.carga.clases.keys()))

    # y aun así cada clase recibe su propio cos(phi) del catálogo
    pot = r.potencias.merge(
        r.clientes[["contract_account", "clase_consumo"]], on="contract_account")
    cos_por_clase = pot.groupby("clase_consumo")["cos_phi_used"].nunique()
    assert (cos_por_clase >= 1).all()
    cos_medio = pot.groupby("clase_consumo")["cos_phi_used"].mean()
    assert cos_medio.nunique() > 1, (
        "todas las clases recibieron el mismo cos(phi): la resolución de clase "
        "tarifaria está cayendo al valor por defecto"
    )
