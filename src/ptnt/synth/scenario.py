"""Escenario ficticio completo para probar el proceso de punta a punta.

Genera de una sola vez todos los insumos que el sistema consume en producción:

* ``consumos_36m.csv`` — padrón comercial con 36 meses y hurtos inyectados.
* ``cabecera.csv``     — energía de cabecera por alimentador y mes, con una
                          **transferencia de carga** inyectada entre dos
                          alimentadores (para probar la detección §10.4).
* ``multados.csv``     — base de clientes multados: una **fracción** de los hurtos
                          reales, porque la distribuidora nunca detecta todos.
* ``red`` (NetworkModel) — red radial con transformadores, ramales BT, luminarias,
                          semáforos, seccionadores, capacitores y totalizador.
* ``sig_clientes.csv`` — clientes presentes en el SIG; deliberadamente **no
                          incluye a todos** los del padrón, para probar el análisis
                          de clientes faltantes.

Todo con **coordenadas reales UTM 17S**, para que la focalización geográfica y la
identidad estable de ubicaciones se puedan verificar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ptnt.synth.generator import generate_commercial_csv
from ptnt.synth.network import generate_radial_network
from ptnt.topology.graph import NetworkModel


@dataclass
class Escenario:
    """Escenario ficticio completo, con la verdad conocida para poder validar."""

    directorio: Path
    csv_consumos: Path
    csv_cabecera: Path
    csv_multados: Path
    csv_sig: Path
    red: NetworkModel
    head_energy_kwh: float
    # Verdad conocida (para comparar contra lo que detecta el sistema)
    hurtos_reales: list[str] = field(default_factory=list)
    multados: list[str] = field(default_factory=list)
    transferencia: dict = field(default_factory=dict)
    clientes_sin_sig: list[str] = field(default_factory=list)
    resumen: dict = field(default_factory=dict)


def build_scenario(
    directorio: str | Path = "data/demo",
    *,
    n_clientes: int = 1200,
    n_meses: int = 36,
    pct_hurto: float = 0.06,
    pct_multados_de_hurtos: float = 0.6,
    n_transformadores: int = 8,
    clientes_por_trafo: int = 20,
    n_alimentadores_cabecera: int = 4,
    meses_cabecera: int = 8,
    pct_sin_sig: float = 0.03,
    semilla: int = 20260808,
) -> Escenario:
    """Construye el escenario ficticio completo y lo escribe en disco."""

    d = Path(directorio)
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(semilla)

    # --- 1. Padrón comercial con hurtos inyectados --------------------------
    csv_consumos = d / "consumos_36m.csv"
    ds = generate_commercial_csv(
        str(csv_consumos), n_clientes=n_clientes, n_meses=n_meses,
        pct_hurto=pct_hurto, semilla=semilla,
    )
    meta = ds.df
    hurtos = meta.loc[meta["_hurto"] == 1, "CUENTACONTRATO"].astype(str).tolist()

    # --- 2. Base de multados: solo una fracción de los hurtos reales --------
    # La distribuidora no detecta todos: por eso los no multados NO son negativos
    # confiables y el aprendizaje debe ser PU.
    n_mult = max(1, int(len(hurtos) * pct_multados_de_hurtos))
    multados = sorted(rng.choice(hurtos, size=n_mult, replace=False).tolist())
    csv_multados = d / "multados.csv"
    pd.DataFrame({
        "contract_account": multados,
        "fecha_multa": pd.date_range("2023-06-01", periods=len(multados),
                                     freq="5D").astype(str),
        "kwh_recuperado": rng.uniform(400, 9000, len(multados)).round(0),
        "tipo_hallazgo": rng.choice(
            ["Conexión directa", "Medidor manipulado", "Puente en acometida",
             "Alteración de sellos"], len(multados)),
    }).to_csv(csv_multados, index=False)

    # --- 3. Energía de cabecera con una transferencia inyectada -------------
    alimentadores = [f"F{i:03d}" for i in range(1, n_alimentadores_cabecera + 1)]
    base_kwh = {f: float(rng.uniform(300_000, 600_000)) for f in alimentadores}
    periodos = pd.date_range("2025-10-01", periods=meses_cabecera, freq="MS")
    # transferencia: el alimentador 2 pasa carga al 3 a mitad del histórico
    f_origen, f_destino = alimentadores[1], alimentadores[2]
    mes_transfer = periodos[len(periodos) // 2]
    magnitud = 75_000.0

    filas = []
    for p in periodos:
        for f in alimentadores:
            v = base_kwh[f] * (1 + rng.normal(0, 0.015))
            if p >= mes_transfer:
                if f == f_origen:
                    v -= magnitud
                elif f == f_destino:
                    v += magnitud
            filas.append({"feeder_code": f, "period": p.date().isoformat(),
                          "kwh_delivered": round(v, 1)})
    csv_cabecera = d / "cabecera.csv"
    pd.DataFrame(filas).to_csv(csv_cabecera, index=False)

    # --- 4. Red radial con todos los elementos ------------------------------
    net = generate_radial_network(
        feeder_code=alimentadores[0], n_transformers=n_transformadores,
        customers_per_tx=clientes_por_trafo, seed=semilla,
    )

    # --- 5. Clientes del SIG: deliberadamente incompleto --------------------
    cuentas = sorted(meta["CUENTACONTRATO"].astype(str).tolist())
    n_sin = max(1, int(len(cuentas) * pct_sin_sig))
    sin_sig = sorted(rng.choice(cuentas, size=n_sin, replace=False).tolist())
    en_sig = [c for c in cuentas if c not in set(sin_sig)]
    # además, unos pocos en el SIG que no facturan (retiros no depurados)
    fantasmas = [f"9999{i:08d}" for i in range(5)]
    csv_sig = d / "sig_clientes.csv"
    pd.DataFrame({"contract_account": en_sig + fantasmas}).to_csv(csv_sig, index=False)

    resumen = {
        "clientes": n_clientes,
        "meses_consumo": n_meses,
        "hurtos_inyectados": len(hurtos),
        "multados_registrados": len(multados),
        "pct_hurtos_detectados_historicamente": round(
            len(multados) / max(len(hurtos), 1) * 100, 1),
        "alimentadores_cabecera": len(alimentadores),
        "meses_cabecera": meses_cabecera,
        "transferencia": f"{f_origen} → {f_destino} en {mes_transfer.date()} "
                         f"({magnitud:,.0f} kWh)",
        "transformadores": n_transformadores,
        "clientes_sin_sig": len(sin_sig),
        "clientes_sig_sin_facturacion": len(fantasmas),
    }

    return Escenario(
        directorio=d, csv_consumos=csv_consumos, csv_cabecera=csv_cabecera,
        csv_multados=csv_multados, csv_sig=csv_sig,
        red=net.model, head_energy_kwh=net.head_energy_kwh,
        hurtos_reales=hurtos, multados=multados,
        transferencia={"origen": f_origen, "destino": f_destino,
                       "periodo": str(mes_transfer.date()), "magnitud_kwh": magnitud},
        clientes_sin_sig=sin_sig, resumen=resumen,
    )


def modify_topology(
    model: NetworkModel, *, cambio: str = "conductor", seed: int = 7
) -> tuple[NetworkModel, str]:
    """Aplica una modificación de topología para probar el versionado.

    ``cambio``:
      * ``conductor``   — se repotencia un tramo (cambia ``attribute_hash``).
      * ``nuevo_ramal`` — se agrega un ramal con clientes (cambia ``topology_hash``).
      * ``maniobra``    — se abre un seccionador (cambia ``switch_state_hash``).
    """

    import copy

    from ptnt.topology.graph import Edge

    m = copy.deepcopy(model)
    rng = np.random.default_rng(seed)

    if cambio == "conductor":
        idx = int(rng.integers(0, len(m.edges)))
        viejo = m.edges[idx]
        m.edges[idx] = Edge(
            segment_id=viejo.segment_id, from_node=viejo.from_node,
            to_node=viejo.to_node, conductor_code="COO0250",   # calibre mayor
            length_km=viejo.length_km, n_phases=viejo.n_phases,
            voltage_v=viejo.voltage_v, is_lv=viejo.is_lv,
        )
        desc = (f"Repotenciación: el tramo {viejo.segment_id} pasa de "
                f"{viejo.conductor_code} a COO0250")
    elif cambio == "nuevo_ramal":
        lv = next((n for n in m.customer_nodes), None)
        anclaje = "LV0" if any(e.to_node == "LV0" for e in m.edges) else lv
        for j in range(3):
            poste = f"BTNEW_{j}"
            m.edges.append(Edge(f"segNEW{j}", anclaje if j == 0 else f"BTNEW_{j-1}",
                                poste, "COO0050", 0.05, n_phases=3,
                                voltage_v=220.0, is_lv=True))
            cn = f"CNEW_{j}"
            m.edges.append(Edge(f"segACNEW{j}", poste, cn, "COO0050", 0.01,
                                n_phases=1, voltage_v=220.0, is_lv=True))
            m.customer_nodes[cn] = [{
                "customer_id": f"NUEVO-{j}", "energy_kwh": float(rng.uniform(150, 350)),
                "phase": int(rng.integers(1, 4)), "tariff": "BT Residencial",
                "meter_type": "Electrónico",
            }]
        desc = f"Ampliación: nuevo ramal BT con 3 clientes colgando de {anclaje}"
    elif cambio == "maniobra":
        if not m.switch_nodes:
            return m, "Sin seccionadores que maniobrar"
        nodo = next(iter(m.switch_nodes))
        m.switch_nodes[nodo] = {**m.switch_nodes[nodo], "current_pos": "ABIERTO"}
        desc = f"Maniobra: se abre el seccionador de {nodo}"
    else:
        desc = "Sin cambios"
    return m, desc
