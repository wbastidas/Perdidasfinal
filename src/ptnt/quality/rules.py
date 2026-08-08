"""Motor de reglas de calidad de datos (§E5).

Reglas determinísticas sobre el modelo de red y los catálogos. Cada hallazgo lleva
``rule_id``, ``severity``, ``element_id``, ``evidence`` y, cuando aplica,
``suggested_value``. Es un entregable de valor por sí mismo, independiente del
análisis de pérdidas.

Subconjunto implementado (el resto del catálogo R01–R32/P01–P12 se añade con el
mismo patrón):

  R05  Conductor ausente del catálogo de impedancias (bloqueante)
  R09  Ampacidad insuficiente (corriente calculada > ampacidad)
  R11  Tramo con longitud nula o desproporcionada
  R12  Tramo sin conductor declarado
  R15  Cliente asignado a puesto distinto del de la traza
  R22  Elemento no alcanzable desde la fuente (isla)
  R24  Transformador sin unidades / unidad sin transformador
  P01  Unidad sin puesto asignado, o puesto sin unidades
  P09  POTENCIAKVA del puesto ≠ capacidad de sus unidades según configuración
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ptnt.config.models import AppConfig
from ptnt.losses.transformers import BankConfig, bank_capacity_kva
from ptnt.ref.catalogs import ConductorCatalog
from ptnt.topology.graph import RadialGraph


@dataclass
class Finding:
    rule_id: str
    severity: str
    element_class: str
    element_id: str
    evidence: dict
    suggested_value: str | None = None
    confidence: float = 1.0


@dataclass
class QualityReport:
    findings: list[Finding] = field(default_factory=list)

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.rule_id] = out.get(f.rule_id, 0) + 1
        return out

    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out


def run_quality_rules(
    graph: RadialGraph,
    conductors: ConductorCatalog,
    cfg: AppConfig,
    *,
    declared_assignment: dict[str, str] | None = None,
    branch_currents_a: dict[str, float] | None = None,
) -> QualityReport:
    """Evalúa las reglas determinísticas sobre un alimentador.

    ``declared_assignment`` mapea ``customer_id -> site_id`` declarado (para R15).
    ``branch_currents_a`` mapea ``segment_id -> |I|`` del flujo (para R09).
    """

    model = graph.model
    rep = QualityReport()

    sev = {
        "R05": "CRITICA", "R09": "ALTA", "R11": "ALTA", "R12": "CRITICA",
        "R15": "ALTA", "R22": "CRITICA", "R24": "CRITICA", "P01": "CRITICA",
        "P09": "ALTA",
    }

    # --- reglas por tramo ---
    islas = graph.islands
    for e in model.edges:
        # R12 — tramo sin conductor declarado
        if not e.conductor_code:
            rep.findings.append(Finding(
                "R12", sev["R12"], "Tramo", e.segment_id,
                {"motivo": "conductor no declarado"},
            ))
            continue
        # R05 — conductor ausente del catálogo
        cond = conductors.get_or_none(e.conductor_code)
        if cond is None:
            rep.findings.append(Finding(
                "R05", sev["R05"], "Tramo", e.segment_id,
                {"conductor_code": e.conductor_code, "motivo": "ausente del catálogo"},
            ))
        # R11 — longitud nula o desproporcionada
        if e.length_km <= 0 or e.length_km > 5.0:
            rep.findings.append(Finding(
                "R11", sev["R11"], "Tramo", e.segment_id,
                {"length_km": e.length_km},
            ))
        # R09 — ampacidad insuficiente
        if cond is not None and branch_currents_a:
            i = branch_currents_a.get(e.segment_id)
            if i is not None and i > cond.ampacidad_a:
                rep.findings.append(Finding(
                    "R09", sev["R09"], "Tramo", e.segment_id,
                    {"i_calculada_a": round(i, 1), "ampacidad_a": cond.ampacidad_a},
                    suggested_value="conductor de mayor calibre",
                ))

    # R22 — elementos no alcanzables (islas)
    for nodo in islas:
        rep.findings.append(Finding(
            "R22", sev["R22"], "Nodo", nodo, {"motivo": "no alcanzable desde la fuente"},
        ))

    # --- reglas de puesto/unidad ---
    for node, ts in model.transformer_sites.items():
        kvas = ts.get("kvas", [])
        # R24 / P01 — puesto sin unidades
        if not kvas:
            rep.findings.append(Finding(
                "R24", sev["R24"], "PuestoTransfDistribucion", ts.get("site_id", node),
                {"motivo": "puesto sin unidades"},
            ))
            rep.findings.append(Finding(
                "P01", sev["P01"], "PuestoTransfDistribucion", ts.get("site_id", node),
                {"motivo": "puesto sin unidades"},
            ))
            continue
        # P09 — POTENCIAKVA declarada ≠ capacidad por configuración
        declarado = ts.get("kva")
        try:
            cfg_banco = BankConfig(ts.get("config", "UNIDAD_SIMPLE"))
        except ValueError:
            cfg_banco = BankConfig.UNIDAD_SIMPLE
        capacidad = bank_capacity_kva(kvas, cfg_banco)
        if declarado and abs(declarado - capacidad) / max(capacidad, 1e-9) > 0.10:
            rep.findings.append(Finding(
                "P09", sev["P09"], "PuestoTransfDistribucion", ts.get("site_id", node),
                {"declarado_kva": declarado, "capacidad_kva": round(capacidad, 1),
                 "config": cfg_banco.value},
                suggested_value=f"{capacidad:.1f}",
            ))

    # R15 — cliente asignado a puesto distinto del de la traza
    if declared_assignment:
        por_traza = graph.assign_customers_to_transformers()
        for cust, site_traza in por_traza.items():
            site_decl = declared_assignment.get(cust)
            if site_decl and site_traza and site_decl != site_traza:
                rep.findings.append(Finding(
                    "R15", sev["R15"], "CONEXIONCONSUMIDOR", cust,
                    {"declarado": site_decl, "por_traza": site_traza},
                    suggested_value=site_traza,
                ))

    return rep
