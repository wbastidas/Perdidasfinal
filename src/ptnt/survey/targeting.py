"""Focalización multinivel: dónde ir a hacer el levantamiento (§11.5).

Produce un **objetivo de levantamiento** (`SurveyTarget`) por cada entidad de cada
nivel, con:

* ``priority_score`` — prioridad 0–1 combinando sospecha y energía en juego.
* ``recoverable_kwh_month`` — energía recuperable estimada (lo que justifica el viaje).
* ``customers_count`` / ``network_km`` — dimensionamiento del trabajo de campo.
* ``reasons`` — 3 razones en lenguaje operativo (por qué ir ahí).
* ``confidence`` y ``data_problem_flag`` — si el caso es probable problema de datos
  en vez de hurto, se **degrada** la prioridad de inspección y se eleva a la lista
  de corrección de datos (§10.7). Enviar cuadrillas a artefactos de datos es gastar
  presupuesto en nada.

Niveles (de mayor a menor agregación):
``ALIMENTADOR`` → ``ZONA_PROTECCION`` → ``RAMAL`` → ``PUESTO_TRANSFORMACION``
→ ``SECTOR`` (geográfico) → ``CLIENTE``.

El plan resultante es ordenable, exportable (XLSX/CSV), reportable (HTML) y visible
en el tablero y el visor web.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class TargetLevel(str, Enum):
    ALIMENTADOR = "ALIMENTADOR"
    ZONA_PROTECCION = "ZONA_PROTECCION"
    RAMAL = "RAMAL"
    PUESTO_TRANSFORMACION = "PUESTO_TRANSFORMACION"
    RUTA_COMERCIAL = "RUTA_COMERCIAL"   # CLIRLSCOD: ruta de lectura
    SECTOR = "SECTOR"
    CLIENTE = "CLIENTE"


# Costo/beneficio: un levantamiento cuesta lo mismo en cualquier nivel, pero la
# energía recuperable difiere en órdenes de magnitud. Estos pesos equilibran
# "qué tan sospechoso es" contra "cuánta energía hay en juego".
PESO_SOSPECHA = 0.55
PESO_ENERGIA = 0.45


@dataclass
class SurveyTarget:
    """Un objetivo concreto de levantamiento de campo."""

    level: TargetLevel
    entity_id: str
    feeder_code: str | None
    priority_score: float                 # 0–1
    suspicion: float                      # 0–1 (señal/residuo normalizado)
    recoverable_kwh_month: float
    customers_count: int
    network_km: float = 0.0
    confidence_index: float = 100.0       # 0–100 confiabilidad del modelo/datos
    data_problem_flag: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    centroid_x: float | None = None
    centroid_y: float | None = None

    @property
    def action(self) -> str:
        """Acción recomendada en lenguaje operativo."""

        if self.data_problem_flag:
            return "CORREGIR DATOS antes de inspeccionar"
        if self.level == TargetLevel.CLIENTE:
            return "Inspección individual de acometida y medidor"
        if self.level == TargetLevel.PUESTO_TRANSFORMACION:
            return "Censo de carga del transformador y revisión de acometidas"
        if self.level == TargetLevel.RUTA_COMERCIAL:
            return "Relectura y verificación de toda la ruta comercial"
        if self.level == TargetLevel.SECTOR:
            return "Recorrido de sector (barrido casa por casa)"
        if self.level == TargetLevel.RAMAL:
            return "Recorrido del ramal con medición de frontera"
        if self.level == TargetLevel.ZONA_PROTECCION:
            return "Instalar medición de frontera y seccionalizar la zona"
        return "Campaña de alimentador y verificación de cabecera"


@dataclass
class SurveyPlan:
    """Plan de levantamientos: objetivos por nivel, ordenados por prioridad."""

    targets: list[SurveyTarget] = field(default_factory=list)
    generated_at: str = ""
    resumen: dict = field(default_factory=dict)

    def by_level(self, level: TargetLevel) -> list[SurveyTarget]:
        return [t for t in self.targets if t.level == level]

    def to_dataframe(self, level: TargetLevel | None = None) -> pd.DataFrame:
        objetivos = self.targets if level is None else self.by_level(level)
        return pd.DataFrame([
            {
                "orden": i + 1,
                "nivel": t.level.value,
                "entidad": t.entity_id,
                "alimentador": t.feeder_code,
                "prioridad": round(t.priority_score, 4),
                "sospecha": round(t.suspicion, 4),
                "recuperable_kwh_mes": round(t.recoverable_kwh_month, 1),
                "clientes": t.customers_count,
                "red_km": round(t.network_km, 3),
                "confiabilidad": round(t.confidence_index, 1),
                "problema_datos": t.data_problem_flag,
                "accion": t.action,
                "razon_1": t.reasons[0] if len(t.reasons) > 0 else "",
                "razon_2": t.reasons[1] if len(t.reasons) > 1 else "",
                "razon_3": t.reasons[2] if len(t.reasons) > 2 else "",
                "x": t.centroid_x, "y": t.centroid_y,
            }
            for i, t in enumerate(objetivos)
        ])

    def work_orders(
        self,
        top_n: int = 50,
        niveles: list[TargetLevel] | None = None,
        *,
        evitar_solape: bool = True,
    ) -> pd.DataFrame:
        """Órdenes de levantamiento para gestión de campo.

        El orden **no** es el ``priority_score`` (que se normaliza dentro de cada
        nivel y por tanto no es comparable entre niveles), sino el **rendimiento
        por visita**: energía recuperable que cubre una salida de cuadrilla. Así un
        sector con 19 clientes agrupados se antepone a 19 visitas individuales, que
        es la decisión logística correcta.

        Con ``evitar_solape`` se descartan los clientes ya cubiertos por un sector
        de mayor rango, para no emitir dos órdenes por el mismo predio.
        """

        niveles = niveles or [
            TargetLevel.PUESTO_TRANSFORMACION, TargetLevel.RUTA_COMERCIAL,
            TargetLevel.SECTOR, TargetLevel.CLIENTE
        ]
        elegibles = [
            t for t in self.targets if t.level in niveles and not t.data_problem_flag
        ]
        # Rendimiento por visita: energía absoluta recuperable, con la sospecha
        # como desempate. Comparable entre niveles.
        elegibles.sort(
            key=lambda t: (t.recoverable_kwh_month, t.suspicion), reverse=True
        )

        cubiertos: set[str] = set()
        filas = []
        for t in elegibles:
            if len(filas) >= top_n:
                break
            if evitar_solape:
                if t.level == TargetLevel.CLIENTE and t.entity_id in cubiertos:
                    continue
                # un sector/puesto cubre a sus clientes
                miembros = t.evidence.get("customers_list") or []
                cubiertos.update(str(m) for m in miembros)
            filas.append({
                "orden_trabajo": f"OT-{len(filas)+1:04d}",
                "prioridad": round(t.priority_score, 4),
                "nivel": t.level.value,
                "entidad": t.entity_id,
                "alimentador": t.feeder_code,
                "accion": t.action,
                "clientes_a_revisar": t.customers_count,
                "recuperable_kwh_mes": round(t.recoverable_kwh_month, 1),
                "kwh_por_visita": round(t.recoverable_kwh_month, 1),
                "motivo_principal": t.reasons[0] if t.reasons else "",
                "x": t.centroid_x, "y": t.centroid_y,
            })
        return pd.DataFrame(filas)


# --------------------------------------------------------------------------- #
# Normalización y prioridad
# --------------------------------------------------------------------------- #
def _norm(vals: np.ndarray) -> np.ndarray:
    """Normaliza a [0,1] por rango.

    Con una sola entidad (o todas iguales) la normalización por rango daría 0 y
    borraría la prioridad; en ese caso se devuelve 1 para las positivas, de modo
    que la energía siga contando cuando hay un único alimentador cargado (D06).
    """

    v = np.asarray(vals, dtype=float)
    v = np.where(np.isfinite(v), v, 0.0)
    lo, hi = float(np.min(v)), float(np.max(v))
    if hi - lo < 1e-12:
        return np.where(v > 0, 1.0, 0.0)
    return (v - lo) / (hi - lo)


def _id_unico(entidad: object, feeder: object, prefijo_defecto: str) -> str:
    """Identificador único en todo el sistema para un objetivo de campo.

    Los identificadores de nodo (``BT3_0_0``, ``TS9``) solo son únicos **dentro**
    de un alimentador: dos alimentadores distintos tienen su propio ``TS9``. Si el
    plan los usara tal cual, dos objetivos con energías completamente distintas
    aparecerían con el mismo nombre y una orden de trabajo emitida para ``TS9``
    sería ambigua — la cuadrilla no sabría a cuál de los dos ir.

    Por eso el identificador se cualifica con el alimentador, salvo que ya lo
    contenga (el nivel ALIMENTADOR y las rutas comerciales ya vienen calificados).
    """

    ent = str(entidad or prefijo_defecto)
    f = str(feeder or "").strip()
    if not f or ent.startswith(f):
        return ent
    return f"{f}/{ent}"


def _priority(
    suspicion: np.ndarray,
    energy: np.ndarray,
    confidence: np.ndarray,
    *,
    exigir_sospecha: bool = False,
) -> np.ndarray:
    """Prioridad = sospecha·w1 + energía_normalizada·w2, penalizada por baja
    confiabilidad de datos (§10.7).

    ``exigir_sospecha`` degrada fuertemente los objetivos **sin ninguna señal**:
    una entidad con mucha energía pero cero indicios no es un objetivo de
    inspección de hurto (mandar cuadrilla ahí es gastar presupuesto). Se mantiene
    en la lista con prioridad residual, no se elimina.
    """

    susp = np.clip(np.asarray(suspicion, dtype=float), 0, 1)
    e_n = _norm(energy)
    base = PESO_SOSPECHA * susp + PESO_ENERGIA * e_n
    if exigir_sospecha:
        base = np.where(susp > 0, base, base * 0.15)
    factor = np.clip(np.asarray(confidence, dtype=float) / 100.0, 0.2, 1.0)
    return np.clip(base * factor, 0, 1)


# --------------------------------------------------------------------------- #
# Construcción del plan
# --------------------------------------------------------------------------- #
def build_survey_plan(
    *,
    feeder_balances: list[dict] | None = None,
    zone_signals: list[dict] | None = None,
    branch_stats: list[dict] | None = None,
    transformer_stats: list[dict] | None = None,
    route_stats: list[dict] | None = None,
    customer_ranking: pd.DataFrame | None = None,
    customer_coords: pd.DataFrame | None = None,
    umbral_confiabilidad: float = 50.0,
    top_clientes: int = 200,
    min_cluster_size: int = 5,
    feeders_con_transferencia: set[str] | None = None,
) -> SurveyPlan:
    """Construye el plan de levantamientos a partir de los resultados del análisis.

    Cada argumento es opcional: el plan se arma con los niveles disponibles, de modo
    que funcione tanto con el universo completo como con una carga parcial (D06).

    * ``feeder_balances``: dicts con ``feeder_code``, ``ntl_kwh``, ``ntl_pct``,
      ``customers``, ``network_km``, ``confidence`` y ``balance_type``.
    * ``zone_signals``: dicts con ``zone_id``, ``feeder_code``, ``residual_kwh``,
      ``signal_value``, ``customers``.
    * ``branch_stats``: dicts con ``branch_id``, ``feeder_code``, ``customers``,
      ``network_km``, ``suspect_customers``, ``energy_kwh``.
    * ``transformer_stats``: dicts con ``site_id``, ``feeder_code``, ``customers``,
      ``loading_ratio``, ``totalizer_residual_kwh``, ``suspect_customers``,
      ``energy_kwh``.
    * ``customer_ranking``: salida del scoring de PNT (``contract_account``,
      ``score``, ``recuperable_kwh_mes``, ``razones``).
    """

    from datetime import datetime

    targets: list[SurveyTarget] = []

    # --- Nivel ALIMENTADOR ---------------------------------------------------
    if feeder_balances:
        df = pd.DataFrame(feeder_balances)
        susp = _norm(df.get("ntl_pct", pd.Series(np.zeros(len(df)))).to_numpy())
        energia = df.get("ntl_kwh", pd.Series(np.zeros(len(df)))).to_numpy(dtype=float)
        conf = df.get("confidence", pd.Series(np.full(len(df), 100.0))).to_numpy(dtype=float)
        prio = _priority(susp, energia, conf)
        for i, r in df.iterrows():
            razones = []
            ntl_pct = float(r.get("ntl_pct", 0) or 0)
            razones.append(f"PNT del alimentador {ntl_pct:.1f}% de la energía de entrada")
            if str(r.get("balance_type", "")) == "INDICATIVO":
                razones.append("Balance INDICATIVO: sin medición de cabecera, PNT no verificable")
            if float(r.get("confidence", 100) or 100) < umbral_confiabilidad:
                razones.append("Baja confiabilidad del modelo: revisar datos primero")
            targets.append(SurveyTarget(
                level=TargetLevel.ALIMENTADOR,
                entity_id=str(r.get("feeder_code", f"F{i}")),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(r.get("ntl_kwh", 0) or 0),
                customers_count=int(r.get("customers", 0) or 0),
                network_km=float(r.get("network_km", 0) or 0),
                confidence_index=float(r.get("confidence", 100) or 100),
                data_problem_flag=float(r.get("confidence", 100) or 100) < umbral_confiabilidad,
                reasons=razones[:3],
                evidence={k: r.get(k) for k in ("ntl_kwh", "ntl_pct", "balance_type") if k in r},
            ))

    # --- Nivel ZONA DE PROTECCIÓN -------------------------------------------
    if zone_signals:
        df = pd.DataFrame(zone_signals)
        susp = df.get("signal_value", pd.Series(np.zeros(len(df)))).to_numpy(dtype=float)
        energia = df.get("residual_kwh", pd.Series(np.zeros(len(df)))).to_numpy(dtype=float)
        conf = df.get("confidence_index", pd.Series(np.full(len(df), 100.0))).to_numpy(dtype=float)
        prio = _priority(susp, energia, conf)
        for i, r in df.iterrows():
            targets.append(SurveyTarget(
                level=TargetLevel.ZONA_PROTECCION,
                entity_id=_id_unico(r.get("zone_id"), r.get("feeder_code"), f"Z{i}"),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(r.get("residual_kwh", 0) or 0),
                customers_count=int(r.get("customers", 0) or 0),
                network_km=float(r.get("network_km", 0) or 0),
                confidence_index=float(r.get("confidence_index", 100) or 100),
                reasons=[
                    f"Residuo de balance de zona: {float(r.get('residual_kwh',0) or 0):,.0f} kWh "
                    "no explicado por consumo ni pérdidas técnicas",
                    "Zona aislable: candidata a medición de frontera",
                ][:3],
                evidence={"residual_kwh": r.get("residual_kwh")},
            ))

    # --- Nivel RAMAL ---------------------------------------------------------
    if branch_stats:
        df = pd.DataFrame(branch_stats)
        n = len(df)
        sosp_cli = df.get("suspect_customers", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        cli = np.maximum(df.get("customers", pd.Series(np.ones(n))).to_numpy(dtype=float), 1)
        # Sospecha = densidad de sospechosos + volumen absoluto. Solo la densidad
        # haría que un ramal de 1 cliente con 1 señal (100 %) superara a uno de 20
        # con 10 señales, que es el que realmente justifica una salida de cuadrilla.
        densidad = np.clip(sosp_cli / cli, 0, 1)
        susp = np.clip(0.5 * densidad + 0.5 * _norm(sosp_cli), 0, 1)
        # La energía que justifica el viaje es la RECUPERABLE, no la facturada:
        # un ramal con mucho consumo pero sin indicios no es objetivo de inspección.
        recup = df.get("recoverable_kwh", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        prio = _priority(susp, recup, np.full(n, 100.0), exigir_sospecha=True)
        for i, r in df.iterrows():
            dens = float(densidad[i]) * 100.0
            km = float(r.get("network_km", 0) or 0)
            if sosp_cli[i] > 0:
                razones = [
                    f"{int(sosp_cli[i])} de {int(cli[i])} clientes del ramal con señal de PNT "
                    f"({dens:.0f}% de densidad)",
                    f"Ramal de {km:.2f} km recorrible en una salida",
                ]
            else:
                razones = [
                    "Sin señales de PNT en el ramal: prioridad residual "
                    "(no es objetivo de inspección)",
                    f"Ramal de {km:.2f} km con {int(cli[i])} clientes",
                ]
            targets.append(SurveyTarget(
                level=TargetLevel.RAMAL,
                entity_id=_id_unico(r.get("branch_id"), r.get("feeder_code"), f"R{i}"),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(recup[i]),
                customers_count=int(r.get("customers", 0) or 0),
                network_km=km,
                reasons=razones[:3],
                evidence={"suspect_customers": int(sosp_cli[i]), "customers": int(cli[i]),
                          "energy_kwh": float(r.get("energy_kwh", 0) or 0)},
                centroid_x=r.get("x"), centroid_y=r.get("y"),
            ))

    # --- Nivel PUESTO DE TRANSFORMACIÓN -------------------------------------
    if transformer_stats:
        df = pd.DataFrame(transformer_stats)
        n = len(df)
        sosp_cli = df.get("suspect_customers", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        cli = np.maximum(df.get("customers", pd.Series(np.ones(n))).to_numpy(dtype=float), 1)
        tot_res = df.get("totalizer_residual_kwh", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        carga = df.get("loading_ratio", pd.Series(np.full(n, 0.5))).to_numpy(dtype=float)
        # sospecha: densidad de clientes con señal + residuo de totalizador +
        # incoherencia de cargabilidad (muy subutilizado = carga no contabilizada)
        s_dens = np.clip(sosp_cli / cli, 0, 1)
        s_tot = _norm(tot_res)
        s_carga = np.clip((0.30 - carga) / 0.30, 0, 1)
        susp = np.clip(0.45 * s_dens + 0.35 * s_tot + 0.20 * s_carga, 0, 1)
        energia = df.get("recoverable_kwh", df.get("energy_kwh", pd.Series(np.zeros(n)))).to_numpy(dtype=float)
        conf = df.get("confidence_index", pd.Series(np.full(n, 100.0))).to_numpy(dtype=float)
        prio = _priority(susp, energia, conf)
        for i, r in df.iterrows():
            razones = []
            if sosp_cli[i] > 0:
                razones.append(
                    f"{int(sosp_cli[i])} de {int(cli[i])} clientes del transformador con señal de PNT"
                )
            if tot_res[i] > 0:
                razones.append(
                    f"Balance de totalizador: {tot_res[i]:,.0f} kWh de diferencia frente a los "
                    "medidores individuales (evidencia directa)"
                )
            if carga[i] < 0.30:
                razones.append(
                    f"Cargabilidad incoherente ({carga[i]*100:.0f}%): capacidad instalada muy "
                    "superior a la carga contabilizada"
                )
            if not razones:
                razones.append("Priorizado por energía en juego del puesto")
            targets.append(SurveyTarget(
                level=TargetLevel.PUESTO_TRANSFORMACION,
                entity_id=_id_unico(r.get("site_id"), r.get("feeder_code"), f"TS{i}"),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(energia[i]),
                customers_count=int(cli[i]),
                network_km=float(r.get("network_km", 0) or 0),
                confidence_index=float(conf[i]),
                data_problem_flag=bool(conf[i] < umbral_confiabilidad),
                reasons=razones[:3],
                evidence={
                    "loading_ratio": float(carga[i]),
                    "totalizer_residual_kwh": float(tot_res[i]),
                    "suspect_customers": int(sosp_cli[i]),
                },
                centroid_x=r.get("x"), centroid_y=r.get("y"),
            ))

    # --- Nivel RUTA COMERCIAL (CLIRLSCOD) -----------------------------------
    if route_stats:
        df = pd.DataFrame(route_stats)
        n = len(df)
        sosp_cli = df.get("suspect_customers", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        cli = np.maximum(df.get("customers", pd.Series(np.ones(n))).to_numpy(dtype=float), 1)
        incoh = df.get("incoherencia", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        densidad = np.clip(sosp_cli / cli, 0, 1)
        # Una ruta se selecciona por sospecha O por incoherencia: la ruta con
        # lecturas en cero o estimadas masivas es un problema de gestión que hay
        # que levantar igual, aunque no dispare señales de hurto por cliente.
        susp = np.clip(np.maximum(0.5 * densidad + 0.5 * _norm(sosp_cli), incoh), 0, 1)
        recup = df.get("recoverable_kwh", pd.Series(np.zeros(n))).to_numpy(dtype=float)
        prio = _priority(susp, recup, np.full(n, 100.0))
        for i, r in df.iterrows():
            motivos = r.get("motivos")
            motivos = list(motivos) if isinstance(motivos, (list, tuple)) else []
            if not motivos:
                motivos = ["Ruta priorizada por concentración de señales"]
            targets.append(SurveyTarget(
                level=TargetLevel.RUTA_COMERCIAL,
                entity_id=str(r.get("route_id", f"RT{i}")),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(recup[i]),
                customers_count=int(cli[i]),
                reasons=motivos[:3],
                evidence={
                    "densidad_sospecha": float(densidad[i]),
                    "incoherencia": float(incoh[i]),
                    "pct_ceros": float(r.get("pct_ceros", 0) or 0),
                    "pct_estimadas": float(r.get("pct_estimadas", 0) or 0),
                    "clientes_sin_sig": int(r.get("n_sin_sig", 0) or 0),
                },
            ))

    # --- Nivel CLIENTE y SECTOR ---------------------------------------------
    if customer_ranking is not None and not customer_ranking.empty:
        cr = customer_ranking.head(top_clientes).copy()
        if customer_coords is not None and not customer_coords.empty:
            cr = cr.merge(customer_coords, on="contract_account", how="left")
        susp = cr["score"].to_numpy(dtype=float) if "score" in cr.columns else np.zeros(len(cr))
        energia = (
            cr["recuperable_kwh_mes"].to_numpy(dtype=float)
            if "recuperable_kwh_mes" in cr.columns else np.zeros(len(cr))
        )
        prio = _priority(susp, energia, np.full(len(cr), 100.0))
        for i, (_, r) in enumerate(cr.iterrows()):
            razones = r.get("razones")
            razones = list(razones) if isinstance(razones, (list, tuple)) else []
            targets.append(SurveyTarget(
                level=TargetLevel.CLIENTE,
                entity_id=str(r.get("contract_account", "")),
                feeder_code=str(r.get("feeder_code", "")) or None,
                priority_score=float(prio[i]), suspicion=float(susp[i]),
                recoverable_kwh_month=float(energia[i]),
                customers_count=1,
                reasons=razones[:3] or ["Score de consenso de señales de comportamiento"],
                evidence={"score": float(susp[i])},
                centroid_x=r.get("x"), centroid_y=r.get("y"),
            ))

        # Sectores geográficos a partir de los clientes priorizados
        if "x" in cr.columns and "y" in cr.columns and cr["x"].notna().any():
            from ptnt.survey.sectors import cluster_sectors

            sectores = cluster_sectors(cr, min_cluster_size=min_cluster_size)
            if sectores:
                energ = np.array([s.suspect_energy_kwh for s in sectores])
                susp_s = np.array([s.mean_score for s in sectores])
                prio_s = _priority(susp_s, energ, np.full(len(sectores), 100.0))
                for i, s in enumerate(sectores):
                    targets.append(SurveyTarget(
                        level=TargetLevel.SECTOR,
                        entity_id=s.sector_id, feeder_code=s.feeder_code,
                        priority_score=float(prio_s[i]), suspicion=float(s.mean_score),
                        recoverable_kwh_month=s.suspect_energy_kwh,
                        customers_count=s.n_customers,
                        reasons=[
                            f"{s.n_customers} clientes sospechosos concentrados en "
                            f"{s.radius_m:.0f} m: recorrido eficiente para una cuadrilla",
                            f"Energía bajo sospecha del sector: {s.suspect_energy_kwh:,.0f} kWh/mes",
                        ][:3],
                        evidence={"radio_m": s.radius_m, "algoritmo": s.algorithm,
                                  "customers_list": s.customers},
                        centroid_x=s.centroid_x, centroid_y=s.centroid_y,
                    ))

    # --- Coherencia con el diagnóstico de transferencias ---------------------
    # Si el diagnóstico detectó una maniobra de carga no reportada entre dos
    # alimentadores, su PNT está contaminada: parte de la energía "faltante" se
    # fue al vecino, no la robó nadie. Mandar cuadrillas ahí es gastar el
    # presupuesto persiguiendo un artefacto de red. Se marcan como problema de
    # datos —lo que degrada su prioridad y los saca de las órdenes de trabajo—
    # en vez de eliminarlos, para que el analista vea por qué no están.
    afectados = {str(f) for f in (feeders_con_transferencia or set())}
    n_degradados = 0
    if afectados:
        for t in targets:
            if t.feeder_code and str(t.feeder_code) in afectados:
                t.data_problem_flag = True
                t.reasons = ([
                    "Transferencia de carga no reportada en este alimentador: la "
                    "PNT no es atribuible a hurto hasta aclarar la maniobra"
                ] + t.reasons)[:3]
                n_degradados += 1

    targets.sort(key=lambda t: t.priority_score, reverse=True)

    resumen = {
        "n_objetivos": len(targets),
        "objetivos_por_transferencia": n_degradados,
        "alimentadores_con_transferencia": sorted(afectados),
        "por_nivel": {
            lvl.value: sum(1 for t in targets if t.level == lvl) for lvl in TargetLevel
        },
        "recuperable_total_kwh_mes": round(
            sum(t.recoverable_kwh_month for t in targets
                if t.level == TargetLevel.CLIENTE), 1
        ),
        "objetivos_con_problema_datos": sum(1 for t in targets if t.data_problem_flag),
    }
    return SurveyPlan(
        targets=targets,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        resumen=resumen,
    )
