"""Barrido hacia atrás/adelante (backward-forward sweep) para redes radiales.

Algoritmo (potencia constante, monofásico equivalente balanceado):

1. Inicializar todas las tensiones nodales a la tensión de la fuente (V_LN).
2. **Barrido hacia atrás**: la corriente inyectada en cada nodo es
   ``I_nodo = conj(S_nodo / V_nodo)``; la corriente de cada tramo es la suma de
   las corrientes de su subárbol (se acumula recorriendo en orden BFS inverso).
3. **Barrido hacia adelante**: ``V_hijo = V_padre − I_tramo · Z_tramo``.
4. Repetir hasta que el mayor cambio de tensión sea menor que la tolerancia.

Salida: tensiones nodales, corrientes por tramo y pérdida trifásica total (kW).
"""

from __future__ import annotations

from dataclasses import dataclass

from ptnt.config.models import FlujoConfig
from ptnt.ref.catalogs import ConductorCatalog
from ptnt.topology.graph import RadialGraph


@dataclass
class PowerFlowResult:
    voltages_ln: dict[str, complex]     # tensión LN por nodo (V)
    branch_current_a: dict[str, float]  # |I| por segment_id (A)
    loss_kw_peak: float                 # pérdida trifásica pico (kW)
    iterations: int
    converged: bool
    v_min_pu: float                     # tensión mínima en pu (calidad)


def run_powerflow(
    graph: RadialGraph,
    node_load_kw: dict[str, float],
    node_pf: dict[str, float],
    conductors: ConductorCatalog,
    cfg: FlujoConfig,
    *,
    base_voltage_ln: float | None = None,
    t_op: float = 50.0,
    alpha: float = 0.00403,
    t_ref: float = 20.0,
) -> PowerFlowResult:
    """Ejecuta el flujo de potencia radial balanceado.

    ``node_load_kw`` es la demanda **trifásica** (kW) por nodo; ``node_pf`` el
    factor de potencia por nodo. La tensión base LN se deduce del tramo raíz si no
    se pasa explícita.
    """

    model = graph.model
    orden = graph.bfs_order()
    if not orden:
        raise ValueError("grafo vacío")

    # Tensión base LN
    if base_voltage_ln is None:
        raiz_edges = [e for e in model.edges if e.from_node == graph.source or e.to_node == graph.source]
        v_ll = raiz_edges[0].voltage_v if raiz_edges else 13800.0
        base_voltage_ln = v_ll / (3 ** 0.5)
    v_src = complex(base_voltage_ln, 0.0)

    # Impedancia por tramo (Ω), destino = nodo hijo
    z_edge: dict[str, complex] = {}
    seg_by_child: dict[str, str] = {}
    for n in orden:
        e = graph.parent_edge(n)
        if e is None:
            continue
        cond = conductors.get_or_none(e.conductor_code)
        if cond is None:
            # sin catálogo: impedancia despreciable (se reporta en calidad R05)
            r = 0.01
            x = 0.01
        else:
            r = cond.r_at_temp(t_op, t_ref, alpha)
            x = cond.x_ohm_km
        z_edge[n] = complex(r, x) * e.length_km
        seg_by_child[n] = e.segment_id

    # Potencia compleja por nodo (VA, trifásica -> por fase se divide entre 3)
    s_node: dict[str, complex] = {}
    for n in orden:
        p_kw = node_load_kw.get(n, 0.0)
        pf = min(max(node_pf.get(n, 0.92), 0.1), 1.0)
        p = p_kw * 1000.0
        tan_phi = ((1.0 - pf**2) ** 0.5) / pf  # Q = P·tan(arccos(pf))
        q = p * tan_phi
        # potencia por fase (balanceada): dividir entre 3
        s_node[n] = complex(p, q) / 3.0

    voltages = {n: v_src for n in orden}
    orden_inverso = list(reversed(orden))

    branch_current: dict[str, complex] = {}
    converged = False
    it = 0
    for it in range(1, cfg.max_iteraciones + 1):
        # --- barrido hacia atrás: corrientes de nodo y de tramo ---
        node_current: dict[str, complex] = {}
        for n in orden:
            v = voltages[n]
            if abs(v) < 1e-6:
                node_current[n] = 0j
            else:
                node_current[n] = (s_node[n] / v).conjugate()
        # acumular corriente de subárbol hacia el padre
        accum = dict(node_current)
        for n in orden_inverso:
            padre = graph._parent.get(n)
            if padre is not None:
                accum[padre] = accum.get(padre, 0j) + accum[n]
                branch_current[n] = accum[n]  # corriente del tramo padre->n

        # --- barrido hacia adelante: actualizar tensiones ---
        max_dv = 0.0
        for n in orden:
            padre = graph._parent.get(n)
            if padre is None:
                voltages[n] = v_src
                continue
            z = z_edge.get(n, 0j)
            nueva = voltages[padre] - branch_current.get(n, 0j) * z
            max_dv = max(max_dv, abs(nueva - voltages[n]))
            voltages[n] = nueva

        if max_dv < cfg.tolerancia_convergencia * base_voltage_ln:
            converged = True
            break

    # --- pérdidas: 3 · |I|² · R por tramo ---
    loss_w = 0.0
    branch_mag: dict[str, float] = {}
    for n, seg in seg_by_child.items():
        i = branch_current.get(n, 0j)
        z = z_edge.get(n, 0j)
        r = z.real
        loss_w += 3.0 * (abs(i) ** 2) * r
        branch_mag[seg] = abs(i)

    v_min_pu = min((abs(v) / base_voltage_ln for v in voltages.values()), default=1.0)

    return PowerFlowResult(
        voltages_ln=voltages,
        branch_current_a=branch_mag,
        loss_kw_peak=loss_w / 1000.0,
        iterations=it,
        converged=converged,
        v_min_pu=v_min_pu,
    )
