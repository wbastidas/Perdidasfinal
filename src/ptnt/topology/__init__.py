"""Topología eléctrica: grafo radial por alimentador, trazas y zonas (§E4).

Independiente de la red geométrica de ArcGIS. El grafo se reconstruye a partir de
la lista de tramos (aristas) y se enraíza en la fuente (cabecera). Sobre él se
calculan trazas aguas arriba/abajo, cargas de subárbol, asignación de clientes a
transformadores por traza y zonas de protección.
"""

from ptnt.topology.graph import (
    Edge,
    NetworkModel,
    RadialGraph,
    TopologyError,
    build_radial_graph,
)

__all__ = [
    "Edge",
    "NetworkModel",
    "RadialGraph",
    "TopologyError",
    "build_radial_graph",
]
