"""Orquestador del pipeline de análisis de consumo y hurto.

Encadena las etapas que constituyen el flujo de valor inmediato del proyecto:

    ingesta comercial  →  promedio multi-mes  →  recálculo de potencia
                       →  reconciliación (SIG vs corregido)
                       →  señales de hurto  →  scoring y ranking
                       →  persistencia (DuckDB + salidas)

Está diseñado para ejecutarse en un servidor único, leyendo desde la fuente
comercial configurada (CSV o base SQL) y publicando resultados que las dos
interfaces web consumen sin recalcular.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from ptnt.config.loader import config_hash
from ptnt.config.models import AppConfig
from ptnt.io.commercial_parser import parse_commercial_csv
from ptnt.load.averaging import average_consumption
from ptnt.load.demand import recompute_customer_power
from ptnt.ntl.scoring import score_customers
from ptnt.ntl.signals import compute_signals
from ptnt.quality.reconciliation import reconcile_power


@dataclass
class PipelineResult:
    consumo: pd.DataFrame
    clientes: pd.DataFrame
    promedios: pd.DataFrame
    potencias: pd.DataFrame
    reconciliacion: pd.DataFrame
    reconciliacion_causa: dict
    señales: pd.DataFrame
    ranking: pd.DataFrame
    metricas: dict = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)


def _clientes_extendidos(clientes: pd.DataFrame, raw_wide: pd.DataFrame | None,
                          cfg: AppConfig) -> pd.DataFrame:
    """Enriquece la tabla de clientes con estado de servicio, fases y previos del SIG.

    Cuando el parser proviene del CSV, esos campos vienen en el archivo crudo
    (leído aparte para no perderlos). Si no existen, se aplican valores neutros.
    """

    df = clientes.copy()
    if raw_wide is not None:
        extra = raw_wide.drop_duplicates("CUENTACONTRATO").set_index("CUENTACONTRATO")
        cuentas = df["contract_account"]
        if "EDCCOD" in extra.columns:
            df["service_active"] = (
                cuentas.map(extra["EDCCOD"]).fillna("ACT").astype(str).str.upper().eq("ACT")
            )
        if "CDAFAS" in extra.columns:
            df["phases_count"] = pd.to_numeric(
                cuentas.map(extra["CDAFAS"]), errors="coerce"
            ).fillna(1)
        if "POTENCIAACTIVA" in extra.columns:
            df["active_power_kw"] = pd.to_numeric(
                cuentas.map(extra["POTENCIAACTIVA"]), errors="coerce"
            )
        if "POTENCIAREACTIVA" in extra.columns:
            df["reactive_power_kvar"] = pd.to_numeric(
                cuentas.map(extra["POTENCIAREACTIVA"]), errors="coerce"
            )
    if "service_active" not in df.columns:
        df["service_active"] = True
    if "phases_count" not in df.columns:
        df["phases_count"] = 1
    return df


def run_analysis(
    cfg: AppConfig,
    csv_path: str,
    *,
    persistir: bool = False,
) -> PipelineResult:
    """Ejecuta el pipeline completo a partir de un CSV comercial."""

    logger.info("Parseando archivo comercial: {}", csv_path)
    parsed = parse_commercial_csv(csv_path, cfg.comercial)
    advertencias = list(parsed.advertencias)
    logger.info("Cuentas: {} | meses: {}", parsed.n_cuentas, len(parsed.meses))

    # Releer crudo para conservar columnas auxiliares (estado, fases, previos SIG)
    raw_wide = pd.read_csv(
        csv_path, sep=cfg.comercial.separador, encoding=cfg.comercial.encoding, dtype=str
    )
    clientes = _clientes_extendidos(parsed.clientes, raw_wide, cfg)

    # Clientes suspendidos (para excluir ceros del promedio)
    suspendidos = set(
        clientes.loc[~clientes["service_active"], "contract_account"]
    ) if "service_active" in clientes.columns else set()

    logger.info("Promediando consumo (método={})", cfg.promedio.metodo.value)
    avg = average_consumption(parsed.consumo, cfg.promedio, suspendidos=suspendidos)

    # Unir promedio y atributos para el recálculo de potencia
    base = clientes.merge(avg.por_cliente, on="contract_account", how="left")

    logger.info("Recalculando potencia (método={})", cfg.carga.metodo_demanda_maxima.value)
    demanda = recompute_customer_power(base, cfg.carga)

    logger.info("Generando informe de reconciliación")
    recon = reconcile_power(base, demanda.por_cliente, cfg.carga)

    logger.info("Calculando señales de hurto")
    señales = compute_signals(parsed.consumo, clientes, cfg.senales)

    consumo_medio = avg.por_cliente.set_index("contract_account")["kwh_representativo"]
    logger.info("Puntuando y rankeando sospecha de PNT")
    ranking = score_customers(
        señales.señales,
        cfg.senales,
        columnas_senal=señales.columnas_senal,
        consumo_medio=consumo_medio,
        evidencia=señales.evidencia,
    )

    metricas = {
        "n_cuentas": parsed.n_cuentas,
        "n_meses": len(parsed.meses),
        "metodo_promedio": cfg.promedio.metodo.value,
        "ventana_meses": cfg.promedio.ventana_meses,
        "metodo_demanda": cfg.carga.metodo_demanda_maxima.value,
        "delta_p_total_kw": recon.resumen.get("delta_p_total_kw"),
        "delta_p_total_pct": recon.resumen.get("delta_p_total_pct"),
        "n_sospechosos": ranking.n_sospechosos,
        "config_hash": config_hash(cfg),
    }

    result = PipelineResult(
        consumo=parsed.consumo,
        clientes=clientes,
        promedios=avg.por_cliente,
        potencias=demanda.por_cliente,
        reconciliacion=recon.por_cliente,
        reconciliacion_causa=recon.causa_global,
        señales=señales.señales,
        ranking=ranking.ranking,
        metricas=metricas,
        advertencias=advertencias,
    )

    if persistir:
        _persistir(cfg, result)

    return result


def _persistir(cfg: AppConfig, result: PipelineResult) -> None:
    """Persiste resultados en DuckDB y en la carpeta de salidas (Parquet + JSON)."""

    salidas = Path(cfg.rutas.salidas)
    salidas.mkdir(parents=True, exist_ok=True)
    # Parquet de resultados (consumido por el visor web sin recalcular)
    try:
        result.ranking.to_parquet(salidas / "ranking_clientes.parquet", index=False)
        result.reconciliacion.to_parquet(salidas / "reconciliacion.parquet", index=False)
        result.potencias.to_parquet(salidas / "potencias.parquet", index=False)
    except Exception as exc:  # pragma: no cover - depende de pyarrow
        logger.warning("No se pudo escribir Parquet ({}); se usa CSV", exc)
        result.ranking.to_csv(salidas / "ranking_clientes.csv", index=False)
    (salidas / "metricas.json").write_text(
        json.dumps(result.metricas, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    try:
        from ptnt.store.database import Database

        with Database(cfg.rutas.duckdb) as db:
            db.apply_schema()
            run_id = db.start_run(
                "ntl_analysis",
                config_hash=config_hash(cfg),
                config_snapshot=cfg.model_dump(mode="json"),
            )
            db.write_dataframe(result.ranking, "ranking_clientes")
            db.write_dataframe(result.reconciliacion, "reconciliacion")
            db.finish_run(run_id, status="OK", metrics=result.metricas)
        logger.info("Resultados persistidos en {}", cfg.rutas.duckdb)
    except Exception as exc:  # pragma: no cover - duckdb opcional
        logger.warning("No se persistió en DuckDB ({}). Salidas en {}", exc, salidas)
