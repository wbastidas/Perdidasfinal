"""Generador de una red radial sintética para las etapas E4–E10.

Produce un ``NetworkModel`` (fuente + tramos MT/BT + puestos de transformación +
clientes + luminarias) con topología radial conocida, más la energía de cabecera
del alimentador, de modo que el pipeline de red (topología → flujo → pérdidas →
balance) corra de punta a punta. Permite inyectar una PNT de magnitud conocida
(energía de cabecera por encima de lo facturado + pérdidas técnicas) para validar
el cierre del balance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ptnt.topology.graph import Edge, NetworkModel


@dataclass
class SyntheticNetwork:
    model: NetworkModel
    head_energy_kwh: float
    injected_ntl_kwh: float
    billed_kwh: float


def generate_radial_network(
    feeder_code: str = "F001",
    *,
    n_transformers: int = 8,
    customers_per_tx: int = 20,
    seed: int = 20260807,
    mt_conductor: str = "COO0004",
    lv_conductor: str = "COO0050",
    neutral_conductor: str = "COO0300",
    mt_voltage_v: float = 13800.0,
    lv_voltage_v: float = 220.0,
    ntl_fraction: float = 0.08,
) -> SyntheticNetwork:
    """Genera una red radial: tronco MT con ``n_transformers`` puestos, cada uno
    con ``customers_per_tx`` clientes en BT."""

    rng = np.random.default_rng(seed)
    edges: list[Edge] = []
    transformer_sites: dict[str, dict] = {}
    customer_nodes: dict[str, list[dict]] = {}
    streetlight_nodes: dict[str, list[dict]] = {}

    source = "SRC"
    prev = source
    billed_total = 0.0

    for t in range(n_transformers):
        mt_node = f"MT{t}"
        # tramo MT del tronco
        edges.append(Edge(
            segment_id=f"segMT{t}", from_node=prev, to_node=mt_node,
            conductor_code=mt_conductor, length_km=float(rng.uniform(0.2, 0.6)),
            n_phases=3, voltage_v=mt_voltage_v, is_lv=False,
        ))
        prev = mt_node

        # puesto de transformación en el nodo MT
        kva = float(rng.choice([50, 75, 112.5, 150, 225]))
        site_id = f"TS{t}"
        transformer_sites[mt_node] = {
            "site_id": site_id, "kvas": [kva], "config": "UNIDAD_SIMPLE",
            "kva": kva, "fases": 3,
        }
        # nodo BT secundario del puesto
        lv_bus = f"LV{t}"
        edges.append(Edge(
            segment_id=f"segTX{t}", from_node=mt_node, to_node=lv_bus,
            conductor_code=lv_conductor, length_km=0.02,
            n_phases=3, voltage_v=lv_voltage_v, is_lv=True,
        ))

        # clientes colgando de ramales BT
        clientes = []
        n_cli = customers_per_tx
        for c in range(n_cli):
            cust_node = f"C{t}_{c}"
            edges.append(Edge(
                segment_id=f"segBT{t}_{c}", from_node=lv_bus, to_node=cust_node,
                conductor_code=lv_conductor, length_km=float(rng.uniform(0.01, 0.05)),
                n_phases=1, voltage_v=lv_voltage_v, is_lv=True,
            ))
            energia = float(rng.uniform(80, 400))
            billed_total += energia
            clientes.append({
                "customer_id": f"{feeder_code}-{t}-{c}",
                "energy_kwh": energia,
                "phase": int(rng.integers(1, 4)),
                "tariff": "BT Residencial",
                "meter_type": "Electrónico",
            })
            customer_nodes[cust_node] = [clientes[-1]]

        # un par de luminarias por puesto
        streetlight_nodes[lv_bus] = [
            {"streetlight_id": f"L{t}_{k}", "lamp_w": 100.0, "technology": "LED",
             "hours": 12.0, "days_month": 30, "is_metered": False}
            for k in range(2)
        ]

    model = NetworkModel(
        feeder_code=feeder_code,
        source_node=source,
        edges=edges,
        feeder_type="U",
        transformer_sites=transformer_sites,
        customer_nodes=customer_nodes,
        streetlight_nodes=streetlight_nodes,
    )

    # Cabecera = facturado / (1 - fracción_pnt) => inyecta PNT conocida
    head = billed_total / max(1e-9, (1.0 - ntl_fraction))
    injected_ntl = head - billed_total

    return SyntheticNetwork(
        model=model,
        head_energy_kwh=head,
        injected_ntl_kwh=injected_ntl,
        billed_kwh=billed_total,
    )
