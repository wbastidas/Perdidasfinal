"""Señales de PNT de nivel red (§11.2).

Orientadas a topología, complementan las señales de cliente (S1–S10). Se calculan
sobre el resultado del balance por zona/puesto:

  N1  Residuo de balance de zona   — entrada estimada de la zona vs. consumo + técnicas
  N3  Balance de totalizador       — E_totalizador − Σ E_individuales (la más limpia)
  N4  Cargabilidad incoherente     — carga aguas abajo ≪ capacidad y corriente inferida
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NetworkSignal:
    signal_code: str      # N1..N6
    level: str            # ZONA | PUESTO
    entity_id: str
    value: float          # intensidad normalizada 0..1
    confidence: float
    evidence: dict


def n1_zone_balance_residual(
    zone_id: str, e_input_zone_kwh: float, e_billed_zone_kwh: float,
    loss_tech_zone_kwh: float,
) -> NetworkSignal:
    """N1 — residuo de balance de zona, normalizado por la entrada de la zona.

    Un residuo alto (energía que entra y no se explica por consumo + técnicas) es
    señal de PNT en la zona.
    """

    residuo = e_input_zone_kwh - e_billed_zone_kwh - loss_tech_zone_kwh
    intensidad = max(0.0, residuo) / e_input_zone_kwh if e_input_zone_kwh > 0 else 0.0
    return NetworkSignal(
        "N1", "ZONA", zone_id, min(intensidad, 1.0), 0.7,
        {"residuo_kwh": round(residuo, 1), "entrada_kwh": round(e_input_zone_kwh, 1)},
    )


def n3_totalizer_balance(
    site_id: str, e_totalizer_kwh: float, e_individuals_kwh: float,
) -> NetworkSignal:
    """N3 — balance de totalizador: ``E_totalizador − Σ E_individuales``.

    La señal más limpia del sistema: no requiere inferencia de pérdidas técnicas
    porque la red entre totalizador e individuales es despreciable.
    """

    diff = e_totalizer_kwh - e_individuals_kwh
    intensidad = max(0.0, diff) / e_totalizer_kwh if e_totalizer_kwh > 0 else 0.0
    return NetworkSignal(
        "N3", "PUESTO", site_id, min(intensidad, 1.0), 0.95,
        {"totalizador_kwh": round(e_totalizer_kwh, 1),
         "suma_individuales_kwh": round(e_individuals_kwh, 1),
         "diferencia_kwh": round(diff, 1)},
    )


def n4_loading_incoherence(
    site_id: str, loading_ratio: float, declared_loading_pct: float | None = None,
) -> NetworkSignal:
    """N4 — cargabilidad incoherente: carga aguas abajo muy inferior a la capacidad.

    Un transformador de gran capacidad con carga trazada muy baja puede indicar
    consumo no contabilizado (clientes no facturados aguas abajo).
    """

    # muy subutilizado según la traza -> sospecha de carga no contabilizada
    intensidad = max(0.0, (0.30 - loading_ratio) / 0.30) if loading_ratio < 0.30 else 0.0
    ev = {"loading_ratio": round(loading_ratio, 3)}
    if declared_loading_pct is not None:
        ev["declared_loading_pct"] = declared_loading_pct
    return NetworkSignal("N4", "PUESTO", site_id, min(intensidad, 1.0), 0.5, ev)
