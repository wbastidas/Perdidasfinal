"""Fuentes sintéticas con **varios alimentadores**, como las de verdad.

``generate_radial_network`` produce un alimentador con nombres de nodo fijos
(``SRC``, ``MT0``, ``C0_3``…). Sirve para probar un alimentador aislado, pero no
para lo que ocurre en operación: una base de origen tiene la subestación entera,
y quien abre un escenario a nivel de subestación migra varios alimentadores de
la misma fuente.

Este módulo arma esa fuente. Cada alimentador recibe su propio espacio de
nombres de nodo —``F002::MT0``— porque en el SIG los identificadores son únicos
en toda la empresa; reutilizar ``MT0`` en dos alimentadores produciría un modelo
que se lee bien y está mal.
"""

from __future__ import annotations

import pandas as pd

from ptnt.io.migration import network_to_tables
from ptnt.topology.graph import NetworkModel
from ptnt.synth.network import generate_radial_network

SEPARADOR = "::"


def _renombrar(modelo: NetworkModel, prefijo: str) -> NetworkModel:
    """El mismo modelo con los nodos en el espacio de nombres del alimentador."""

    import copy
    from dataclasses import replace

    def n(nodo: str) -> str:
        return f"{prefijo}{SEPARADOR}{nodo}"

    return NetworkModel(
        feeder_code=modelo.feeder_code,
        source_node=n(modelo.source_node),
        edges=[replace(e, from_node=n(e.from_node), to_node=n(e.to_node))
               for e in modelo.edges],
        transformer_sites={n(k): copy.deepcopy(v)
                           for k, v in modelo.transformer_sites.items()},
        customer_nodes={n(k): copy.deepcopy(v)
                        for k, v in modelo.customer_nodes.items()},
        streetlight_nodes={n(k): copy.deepcopy(v)
                           for k, v in modelo.streetlight_nodes.items()},
    )


def redes_multialimentador(codigos: list[str], **kwargs) -> dict[str, NetworkModel]:
    """Un modelo por alimentador, con nodos que no chocan entre sí."""

    base = int(kwargs.pop("seed", 20260807))
    redes: dict[str, NetworkModel] = {}
    for i, codigo in enumerate(codigos):
        # Cada alimentador con su propia semilla: si todos salieran idénticos,
        # una prueba de consolidación pasaría sin comprobar nada.
        net = generate_radial_network(feeder_code=codigo, seed=base + i, **kwargs)
        redes[codigo] = _renombrar(net.model, codigo)
    return redes


def fuente_multialimentador(ruta_duckdb: str, codigos: list[str],
                            **kwargs) -> dict[str, NetworkModel]:
    """Escribe en DuckDB una fuente con todos esos alimentadores juntos.

    Devuelve los modelos originales para poder comparar contra lo migrado.
    """

    from ptnt.store.database import Database

    redes = redes_multialimentador(codigos, **kwargs)

    juntas: dict[str, list[pd.DataFrame]] = {}
    for modelo in redes.values():
        for nombre, df in network_to_tables(modelo).items():
            if df is not None and not df.empty:
                juntas.setdefault(nombre, []).append(df)

    with Database(ruta_duckdb) as db:
        db._con.execute("CREATE SCHEMA IF NOT EXISTS silver")
        for nombre, partes in juntas.items():
            todo = pd.concat(partes, ignore_index=True)
            db._con.register("_fuente_tmp", todo)
            db._con.execute(
                f"CREATE OR REPLACE TABLE silver.{nombre} AS "
                "SELECT * FROM _fuente_tmp")
            db._con.unregister("_fuente_tmp")

    return redes
