"""Flujo de potencia trifásico desbalanceado con neutro (§8.7).

Motor de barrido hacia atrás/adelante de **4 hilos** (a, b, c, n) para redes
radiales. A diferencia del monofásico equivalente, modela:

* Cargas **por fase** (cada cliente conectado a su fase real).
* Matriz de impedancia de fase 3×3 con **acoples mutuos** (aproximación:
  auto = r+jx, mutua = j·x_m).
* **Corriente de neutro** ``I_n = −(I_a + I_b + I_c)`` y su pérdida
  ``|I_n|²·R_n`` — término clave en BT desbalanceada, que el modelo balanceado
  ignora.
* **Desbalance** por tramo y nodo.

Convenciones: tensiones nodales línea-neutro complejas por fase; fuente
balanceada de secuencia positiva ``[V, V·a², V·a]`` con ``a = e^{j120°}``.
"""

from __future__ import annotations

import cmath
from dataclasses import dataclass, field

import numpy as np

from ptnt.config.models import FlujoConfig
from ptnt.ref.catalogs import ConductorCatalog
from ptnt.topology.graph import RadialGraph

_A = cmath.exp(1j * 2 * cmath.pi / 3)  # operador de fase 120°
_PHASE_IDX = {1: 0, 2: 1, 3: 2}        # CDAFAS/SECUENCIA -> índice a/b/c


@dataclass
class PowerFlow3phResult:
    voltages_ln: dict[str, np.ndarray]       # nodo -> [Va, Vb, Vc] complejas (V)
    branch_current_a: dict[str, np.ndarray]  # segment_id -> |Ia,Ib,Ic| (A)
    neutral_current_a: dict[str, float]      # segment_id -> |In| (A)
    loss_kw_peak: float                      # pérdida total (fases + neutro) kW
    loss_neutral_kw: float                   # pérdida solo en el neutro kW
    iterations: int
    converged: bool
    v_min_pu: float
    imbalance_pct_max: float                 # desbalance máx. en tramos TRIFÁSICOS
    metrics: dict = field(default_factory=dict)


def _phase_impedance_matrix(r: float, x: float, x_mutual: float) -> np.ndarray:
    """Matriz 3×3 de impedancia de fase (Ω/km)."""

    z_self = complex(r, x)
    z_mut = complex(0.0, x_mutual)
    return np.array(
        [[z_self, z_mut, z_mut],
         [z_mut, z_self, z_mut],
         [z_mut, z_mut, z_self]],
        dtype=complex,
    )


def _customer_phase(c: dict) -> int:
    """Índice de fase (0/1/2) del cliente a partir de su campo ``phase``."""

    p = c.get("phase")
    try:
        return _PHASE_IDX.get(int(p), 0)
    except (TypeError, ValueError):
        return 0


def run_powerflow_3ph(
    graph: RadialGraph,
    node_load_kw_by_phase: dict[str, np.ndarray],
    node_pf: dict[str, float],
    conductors: ConductorCatalog,
    cfg: FlujoConfig,
    *,
    base_voltage_ln: float | None = None,
    t_op: float = 50.0,
    alpha: float = 0.00403,
    t_ref: float = 20.0,
    x_mutual_factor: float = 0.4,
    r_neutral_ohm_km: float = 0.86,
    umbral_corriente_desbalance: float = 0.10,
) -> PowerFlow3phResult:
    """Ejecuta el flujo trifásico desbalanceado de 4 hilos.

    ``node_load_kw_by_phase`` mapea nodo → vector [Pa, Pb, Pc] (kW por fase).

    ``umbral_corriente_desbalance`` filtra qué tramos entran en la métrica
    ``imbalance_pct_max``: solo los trifásicos que llevan al menos esa fracción de
    la corriente del tramo más cargado (ver el cálculo más abajo).
    """

    model = graph.model
    orden = graph.bfs_order()
    if not orden:
        raise ValueError("grafo vacío")

    if base_voltage_ln is None:
        raiz = [e for e in model.edges if graph.source in (e.from_node, e.to_node)]
        v_ll = raiz[0].voltage_v if raiz else 13800.0
        base_voltage_ln = v_ll / (3 ** 0.5)
    v_src = np.array([base_voltage_ln, base_voltage_ln * _A**2, base_voltage_ln * _A], dtype=complex)

    # Impedancia de fase (3×3) y resistencia de neutro por tramo (destino=hijo)
    z_edge: dict[str, np.ndarray] = {}
    rn_edge: dict[str, float] = {}
    seg_by_child: dict[str, str] = {}
    trifasico: dict[str, bool] = {}
    for n in orden:
        e = graph.parent_edge(n)
        if e is None:
            continue
        cond = conductors.get_or_none(e.conductor_code)
        if cond is None:
            r, x = 0.01, 0.01
        else:
            r = cond.r_at_temp(t_op, t_ref, alpha)
            x = cond.x_ohm_km
        z_edge[n] = _phase_impedance_matrix(r, x, x_mutual_factor * x) * e.length_km
        rn_edge[n] = r_neutral_ohm_km * e.length_km
        seg_by_child[n] = e.segment_id
        trifasico[n] = e.n_phases == 3

    # Potencia compleja por nodo y fase (VA)
    s_node: dict[str, np.ndarray] = {}
    for n in orden:
        p_vec = node_load_kw_by_phase.get(n, np.zeros(3))
        pf = min(max(node_pf.get(n, 0.92), 0.1), 1.0)
        tan_phi = ((1.0 - pf**2) ** 0.5) / pf
        s_node[n] = np.array([complex(p * 1000.0, p * 1000.0 * tan_phi) for p in p_vec])

    voltages = {n: v_src.copy() for n in orden}
    orden_inverso = list(reversed(orden))
    branch_current: dict[str, np.ndarray] = {}

    converged = False
    it = 0
    for it in range(1, cfg.max_iteraciones + 1):
        # --- barrido hacia atrás: corriente por fase ---
        node_current: dict[str, np.ndarray] = {}
        for n in orden:
            v = voltages[n]
            ic = np.zeros(3, dtype=complex)
            for ph in range(3):
                if abs(s_node[n][ph]) > 0 and abs(v[ph]) > 1e-6:
                    ic[ph] = np.conjugate(s_node[n][ph] / v[ph])
            node_current[n] = ic
        accum = {n: node_current[n].copy() for n in orden}
        for n in orden_inverso:
            padre = graph._parent.get(n)
            if padre is not None:
                accum[padre] = accum[padre] + accum[n]
                branch_current[n] = accum[n]

        # --- barrido hacia adelante: tensiones por fase ---
        max_dv = 0.0
        for n in orden:
            padre = graph._parent.get(n)
            if padre is None:
                voltages[n] = v_src.copy()
                continue
            z = z_edge.get(n)
            i = branch_current.get(n, np.zeros(3, dtype=complex))
            vdrop = z @ i
            nueva = voltages[padre] - vdrop
            max_dv = max(max_dv, float(np.max(np.abs(nueva - voltages[n]))))
            voltages[n] = nueva

        if max_dv < cfg.tolerancia_convergencia * base_voltage_ln:
            converged = True
            break

    # --- pérdidas: fases (Σ|I|²R) + neutro (|In|²Rn) ---
    loss_w = 0.0
    loss_neutral_w = 0.0
    branch_mag: dict[str, np.ndarray] = {}
    neutral_mag: dict[str, float] = {}
    imbalance_max = 0.0
    imbalance_seg = ""
    candidatos_desb: list[tuple[float, np.ndarray, str]] = []
    for n, seg in seg_by_child.items():
        i = branch_current.get(n, np.zeros(3, dtype=complex))
        z = z_edge.get(n)
        r_phase = np.real(np.diag(z))
        loss_w += float(np.sum((np.abs(i) ** 2) * r_phase))
        i_n = -(i[0] + i[1] + i[2])
        loss_neutral_w += float((abs(i_n) ** 2) * rn_edge.get(n, 0.0))
        branch_mag[seg] = np.abs(i)
        neutral_mag[seg] = abs(i_n)
        # Desbalance de corriente: solo tiene sentido en tramos **trifásicos** y
        # **cargados**. Una acometida monofásica lleva corriente en una sola fase y
        # da siempre 200 % (el máximo teórico); un tramo trifásico de cola que
        # alimenta a un único cliente monofásico, también. Tomar el máximo sobre
        # todos los tramos devuelve 200 % en cualquier red real y no señala nada.
        if trifasico.get(n, False):
            prom = float(np.mean(np.abs(i)))
            if prom > 0:
                candidatos_desb.append((prom, np.abs(i), seg))

    # El desbalance que importa es el de los tramos que **transportan** corriente:
    # el beneficio de rebalancear escala con I², de modo que un tramo de cola muy
    # desbalanceado pero con corriente despreciable no representa pérdida alguna
    # (§11.6). Se descartan los tramos por debajo de ``umbral_corriente_desbalance``
    # veces la corriente del tramo más cargado.
    if candidatos_desb:
        prom_max = max(p for p, _, _ in candidatos_desb)
        for prom, mags, seg in candidatos_desb:
            if prom < umbral_corriente_desbalance * prom_max:
                continue
            desb = float(np.max(np.abs(mags - prom)) / prom * 100.0)
            if desb > imbalance_max:
                imbalance_max, imbalance_seg = desb, seg

    v_min_pu = min(
        (float(np.min(np.abs(v))) / base_voltage_ln for v in voltages.values()),
        default=1.0,
    )
    total_loss_kw = (loss_w + loss_neutral_w) / 1000.0

    return PowerFlow3phResult(
        voltages_ln=voltages,
        branch_current_a=branch_mag,
        neutral_current_a=neutral_mag,
        loss_kw_peak=total_loss_kw,
        loss_neutral_kw=loss_neutral_w / 1000.0,
        iterations=it,
        converged=converged,
        v_min_pu=v_min_pu,
        imbalance_pct_max=imbalance_max,
        metrics={
            "loss_phases_kw": loss_w / 1000.0,
            "loss_neutral_kw": loss_neutral_w / 1000.0,
            "imbalance_segment": imbalance_seg,
            "n_segmentos_trifasicos": sum(1 for v in trifasico.values() if v),
        },
    )


def customer_loads_by_phase(
    graph: RadialGraph, node_energy_to_kw
) -> dict[str, np.ndarray]:
    """Construye el vector [Pa,Pb,Pc] por nodo a partir de los clientes y su fase.

    ``node_energy_to_kw`` es una función energía_kwh -> demanda_kw (p.ej. Velander).
    """

    out: dict[str, np.ndarray] = {}
    for node, clientes in graph.model.customer_nodes.items():
        vec = np.zeros(3)
        for c in clientes:
            ph = _customer_phase(c)
            vec[ph] += float(node_energy_to_kw(c.get("energy_kwh", 0.0)))
        out[node] = vec
    return out
