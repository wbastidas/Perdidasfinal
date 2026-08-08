"""Pruebas de topología: grafo radial, trazas, asignación a transformadores."""

import pytest

from ptnt.topology.graph import Edge, NetworkModel, TopologyError, build_radial_graph


def _linea_simple() -> NetworkModel:
    # SRC - A - B - C  (línea), transformador en B, clientes en C
    edges = [
        Edge("s1", "SRC", "A", "COO0004", 0.3),
        Edge("s2", "A", "B", "COO0004", 0.3),
        Edge("s3", "B", "C", "COO0050", 0.05, is_lv=True),
    ]
    return NetworkModel(
        feeder_code="F001", source_node="SRC", edges=edges,
        transformer_sites={"B": {"site_id": "TS1", "kvas": [50], "config": "UNIDAD_SIMPLE"}},
        customer_nodes={"C": [
            {"customer_id": "c1", "energy_kwh": 100},
            {"customer_id": "c2", "energy_kwh": 200},
        ]},
    )


@pytest.mark.unit
def test_grafo_construye_y_traza_upstream():
    g = build_radial_graph(_linea_simple())
    assert g.n_nodes == 4
    camino = g.trace_upstream("C")
    assert camino == ["C", "B", "A", "SRC"]


@pytest.mark.unit
def test_trace_downstream():
    g = build_radial_graph(_linea_simple())
    aguas_abajo = g.trace_downstream("A")
    assert aguas_abajo == {"A", "B", "C"}


@pytest.mark.unit
def test_path_to_source_distancia():
    g = build_radial_graph(_linea_simple())
    camino, dist = g.path_to_source("C")
    assert dist == pytest.approx(0.3 + 0.3 + 0.05)


@pytest.mark.unit
def test_subtree_load():
    g = build_radial_graph(_linea_simple())
    assert g.subtree_load_kwh("SRC") == pytest.approx(300)


@pytest.mark.unit
def test_asignacion_cliente_a_transformador_por_traza():
    g = build_radial_graph(_linea_simple())
    asign = g.assign_customers_to_transformers()
    assert asign["c1"] == "TS1"
    assert asign["c2"] == "TS1"


@pytest.mark.unit
def test_deteccion_ciclo():
    # agrega una arista que cierra un ciclo A-C
    edges = [
        Edge("s1", "SRC", "A", "COO0004", 0.3),
        Edge("s2", "A", "B", "COO0004", 0.3),
        Edge("s3", "B", "C", "COO0004", 0.3),
        Edge("s4", "C", "A", "COO0004", 0.3),  # malla
    ]
    g = build_radial_graph(NetworkModel("F", "SRC", edges))
    assert g.has_cycle() is True


@pytest.mark.unit
def test_isla_detectada():
    edges = [
        Edge("s1", "SRC", "A", "COO0004", 0.3),
        Edge("s2", "X", "Y", "COO0004", 0.3),  # componente desconectada
    ]
    g = build_radial_graph(NetworkModel("F", "SRC", edges))
    assert "X" in g.islands and "Y" in g.islands


@pytest.mark.unit
def test_fuente_inexistente_falla():
    edges = [Edge("s1", "A", "B", "COO0004", 0.3)]
    with pytest.raises(TopologyError):
        build_radial_graph(NetworkModel("F", "NOEXISTE", edges))
