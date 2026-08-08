"""Grafo radial y trazas (§E4).

El modelo de red (``NetworkModel``) es agnóstico del origen: lo produce tanto el
generador sintético como (en producción) la capa Silver a partir del FGDB. El
``RadialGraph`` lo enraíza en la fuente y ofrece las trazas.

Se usa una representación de adyacencia en diccionarios (sin dependencia dura de
``rustworkx``); para alimentadores reales muy grandes puede sustituirse el motor
sin cambiar la interfaz.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


class TopologyError(Exception):
    """Error de topología (sin fuente, ciclo en red radial, isla)."""


@dataclass(frozen=True)
class Edge:
    """Tramo de red: arista entre dos nodos con atributos eléctricos."""

    segment_id: str
    from_node: str
    to_node: str
    conductor_code: str
    length_km: float
    n_phases: int = 3
    voltage_v: float = 13800.0
    is_lv: bool = False  # baja tensión (secundario)


@dataclass
class NetworkModel:
    """Modelo de red de un alimentador (nodos + tramos + cargas).

    Incluye las clases del modelo CNEL EP: transformadores, clientes, luminarias,
    **semáforos/cámaras** (consumo no medido), **seccionadores/puestos de
    protección**, **bancos de capacitores**, **postes** (estructura soporte) y la
    **cabecera** (PuestoProteccionDinamico con CIRCUITSOURCEGUID).
    """

    feeder_code: str
    source_node: str
    edges: list[Edge]
    feeder_type: str = "U"  # U=Urbano, R=Rural (parametriza k, LF)
    transformer_sites: dict[str, dict] = field(default_factory=dict)  # node -> {site_id, kvas, config, circuit_source_guid}
    customer_nodes: dict[str, list[dict]] = field(default_factory=dict)  # node -> [customers]
    streetlight_nodes: dict[str, list[dict]] = field(default_factory=dict)  # node -> [lights]
    # Semáforos y cámaras: consumo de alumbrado público NO medido (por regulación)
    traffic_light_nodes: dict[str, list[dict]] = field(default_factory=dict)  # node -> [semaforos/camaras]
    # Dispositivos de maniobra / protección (seccionadores, PPD, PSF)
    switch_nodes: dict[str, dict] = field(default_factory=dict)  # node -> {switch_id, type, normal_pos}
    # Bancos de capacitores (corrección de factor de potencia)
    capacitor_nodes: dict[str, dict] = field(default_factory=dict)  # node -> {cap_id, kvar}
    # Postes / estructuras soporte (informativo, logística)
    pole_nodes: dict[str, dict] = field(default_factory=dict)  # node -> {pole_id, type}
    # Cabecera del alimentador (PuestoProteccionDinamico fuente)
    feeder_head: dict = field(default_factory=dict)  # {protection_id, circuit_source_guid, is_source}


class RadialGraph:
    """Grafo radial enraizado en la fuente, con trazas."""

    def __init__(self, model: NetworkModel):
        self.model = model
        self.source = model.source_node
        self._adj: dict[str, list[Edge]] = defaultdict(list)  # undirected adjacency
        self._nodes: set[str] = set()
        for e in model.edges:
            self._adj[e.from_node].append(e)
            self._adj[e.to_node].append(e)
            self._nodes.add(e.from_node)
            self._nodes.add(e.to_node)
        self._parent: dict[str, str | None] = {}
        self._parent_edge: dict[str, Edge] = {}
        self._children: dict[str, list[str]] = defaultdict(list)
        self._order: list[str] = []  # BFS order desde la fuente
        self._build_tree()

    # -- construcción --------------------------------------------------------
    def _build_tree(self) -> None:
        if self.source not in self._nodes:
            raise TopologyError(f"fuente '{self.source}' no está en la red")
        visited = {self.source}
        self._parent[self.source] = None
        q = deque([self.source])
        while q:
            n = q.popleft()
            self._order.append(n)
            for e in self._adj[n]:
                vecino = e.to_node if e.from_node == n else e.from_node
                if vecino not in visited:
                    visited.add(vecino)
                    self._parent[vecino] = n
                    self._parent_edge[vecino] = e
                    self._children[n].append(vecino)
                    q.append(vecino)
                elif self._parent.get(n) != vecino:
                    # arista adicional a un nodo ya visitado -> ciclo (malla)
                    pass
        # islas: nodos no alcanzados desde la fuente
        self._islands = self._nodes - visited

    # -- propiedades ---------------------------------------------------------
    @property
    def n_nodes(self) -> int:
        return len(self._nodes)

    @property
    def islands(self) -> set[str]:
        """Nodos no alcanzables desde la fuente (validación T02)."""
        return set(self._islands)

    def has_cycle(self) -> bool:
        """Detección de ciclo en red declarada radial (validación T01).

        En un árbol, nº de aristas = nº de nodos − 1 (por componente conexa
        alcanzable). Si hay más aristas entre nodos alcanzables, hay malla.
        """

        alcanzables = set(self._order)
        aristas_internas = sum(
            1
            for e in self.model.edges
            if e.from_node in alcanzables and e.to_node in alcanzables
        )
        return aristas_internas > len(alcanzables) - 1

    # -- trazas --------------------------------------------------------------
    def trace_upstream(self, node: str) -> list[str]:
        """Camino desde ``node`` hasta la fuente (incluye ambos extremos)."""

        if node not in self._parent:
            raise TopologyError(f"nodo '{node}' no alcanzable desde la fuente")
        camino = []
        cur: str | None = node
        while cur is not None:
            camino.append(cur)
            cur = self._parent[cur]
        return camino

    def trace_downstream(self, node: str) -> set[str]:
        """Conjunto de nodos aguas abajo de ``node`` (lo incluye)."""

        out = set()
        q = deque([node])
        while q:
            n = q.popleft()
            out.add(n)
            q.extend(self._children[n])
        return out

    def path_to_source(self, node: str) -> tuple[list[str], float]:
        """Camino a la fuente y distancia (km) acumulada."""

        camino = self.trace_upstream(node)
        dist = 0.0
        for n in camino:
            e = self._parent_edge.get(n)
            if e is not None:
                dist += e.length_km
        return camino, dist

    def parent_edge(self, node: str) -> Edge | None:
        return self._parent_edge.get(node)

    def children(self, node: str) -> list[str]:
        return list(self._children[node])

    def bfs_order(self) -> list[str]:
        """Orden BFS desde la fuente (útil para barridos)."""
        return list(self._order)

    # -- cargas de subárbol --------------------------------------------------
    def subtree_customers(self, node: str) -> list[dict]:
        """Clientes aguas abajo de ``node``."""

        nodos = self.trace_downstream(node)
        clientes = []
        for n in nodos:
            clientes.extend(self.model.customer_nodes.get(n, []))
        return clientes

    def subtree_load_kwh(self, node: str) -> float:
        """Energía facturada aguas abajo de ``node``."""

        return sum(c.get("energy_kwh", 0.0) for c in self.subtree_customers(node))

    # -- asignación de clientes a transformadores (§7.4) ---------------------
    def assign_customers_to_transformers(self) -> dict[str, str]:
        """Asigna cada cliente al primer puesto de transformación aguas arriba.

        Devuelve ``{customer_id: transformer_site_id}``. Recorre desde cada nodo
        de cliente hacia la fuente hasta encontrar un nodo con transformador.
        """

        trafo_por_nodo = self.model.transformer_sites
        asignacion: dict[str, str] = {}
        for node, clientes in self.model.customer_nodes.items():
            camino = self.trace_upstream(node)
            site_id = None
            for n in camino:
                if n in trafo_por_nodo:
                    site_id = trafo_por_nodo[n]["site_id"]
                    break
            for c in clientes:
                asignacion[c["customer_id"]] = site_id
        return asignacion

    # -- zonas de protección (§10.5) -----------------------------------------
    def protection_zones(self, switch_nodes: set[str]) -> dict[str, set[str]]:
        """Árbol de zonas: elementos entre un dispositivo de maniobra y el
        siguiente. Cada zona se identifica por su dispositivo de cabecera."""

        zonas: dict[str, set[str]] = {}
        # la fuente inicia la primera zona
        limites = set(switch_nodes) | {self.source}
        for limite in limites:
            zona = {limite}
            q = deque(self._children[limite])
            while q:
                n = q.popleft()
                if n in limites and n != limite:
                    continue  # comienza otra zona
                zona.add(n)
                q.extend(self._children[n])
            zonas[limite] = zona
        return zonas


def build_radial_graph(model: NetworkModel) -> RadialGraph:
    """Construye el grafo radial y valida que la fuente exista."""

    if not model.edges:
        raise TopologyError(f"alimentador '{model.feeder_code}' sin tramos")
    return RadialGraph(model)
