"""Asignación a varias cuadrillas y sincronización simultánea.

Estas pruebas cubren lo que ocurre cuando el equipo de campo es un equipo y no
una persona: el supervisor reparte la jornada entre varios técnicos, y al volver
a la base todos sincronizan a la vez. Antes de existir el almacén transaccional,
esa concurrencia perdía trabajo en silencio —el último en guardar pisaba a los
demás—, que es la peor clase de fallo: nadie se entera hasta que la orden
"reaparece" pendiente días después.
"""

import threading

import numpy as np
import pandas as pd
import pytest

from ptnt.field import (
    EstadoOrden,
    RegistroCampo,
    TransicionInvalida,
    asignar_reparto,
    repartir_ordenes,
)
from ptnt.field.store import AlmacenCampo, ConflictoConcurrencia


def _ordenes(n: int, *, centros=((630000.0, 9760000.0),), semilla: int = 3
             ) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    filas = []
    for i in range(n):
        cx, cy = centros[i % len(centros)]
        filas.append({
            "orden_trabajo": f"OT-{i:04d}", "nivel": "SECTOR",
            "entidad": f"SEC-{i}", "accion": "Recorrido",
            "clientes_a_revisar": int(rng.integers(5, 60)),
            "recuperable_kwh_mes": float(rng.integers(500, 15000)),
            "x": cx + float(rng.normal(0, 300)),
            "y": cy + float(rng.normal(0, 300)),
        })
    return pd.DataFrame(filas)


def _registro(tmp_path, tecnicos: list[str]) -> RegistroCampo:
    reg = RegistroCampo(tmp_path / "registro.json")
    for t in tecnicos:
        reg.crear_usuario(t, t.title(), "clave-larga-2026")
    return reg


# --------------------------------------------------------------------------- #
# Concurrencia
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_sincronizacion_simultanea_no_pierde_actualizaciones(tmp_path):
    """Tres cuadrillas bajando su paquete a la vez: las nueve órdenes cambian.

    Es la prueba que motivó el almacén transaccional. Con persistencia en JSON
    sobrevivían 3 de 9 actualizaciones: cada proceso reescribía el archivo entero
    con la foto que tenía al empezar.
    """

    tecnicos = ["ana", "beto", "carla"]
    reg = _registro(tmp_path, tecnicos)
    ordenes = _ordenes(9)
    for i, t in enumerate(tecnicos):
        reg.asignar(ordenes.iloc[i * 3:(i + 1) * 3], t, asignado_por="supervisor")

    barrera = threading.Barrier(len(tecnicos))
    fallos: list[str] = []

    def sincroniza(usuario: str) -> None:
        propio = RegistroCampo(tmp_path / "registro.json")   # conexión propia
        barrera.wait()
        try:
            propio.marcar_descargadas(usuario, actor=usuario)
        except Exception as exc:                              # noqa: BLE001
            fallos.append(f"{usuario}: {exc!r}")

    hilos = [threading.Thread(target=sincroniza, args=(t,)) for t in tecnicos]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not fallos, fallos
    final = RegistroCampo(tmp_path / "registro.json")
    descargadas = [a for a in final.asignaciones.values()
                   if a.estado is EstadoOrden.DESCARGADA]
    assert len(descargadas) == 9
    assert all(a.fecha_descarga for a in descargadas)


@pytest.mark.unit
def test_dos_procesos_no_aplican_la_misma_transicion(tmp_path):
    """La segunda escritura sobre el estado ya consumido no surte efecto.

    Es la garantía concreta del ``UPDATE … WHERE estado = <el que se leyó>``: el
    segundo proceso afecta cero filas y se entera, en vez de reescribir encima la
    transición del primero con sus propias fechas.
    """

    reg = _registro(tmp_path, ["ana"])
    reg.asignar(_ordenes(1), "ana")

    assert reg.almacen.transicionar("OT-0000", "ASIGNADA", "DESCARGADA") is True
    # Un segundo proceso que aún creía la orden ASIGNADA:
    assert reg.almacen.transicionar("OT-0000", "ASIGNADA", "DESCARGADA") is False

    # Y por encima del almacén, la máquina de estados lo dice con nombre propio.
    otro = RegistroCampo(tmp_path / "registro.json")
    with pytest.raises(TransicionInvalida):
        otro.transicionar("OT-0000", EstadoOrden.DESCARGADA)


@pytest.mark.unit
def test_transicion_perdida_se_reporta_como_conflicto(tmp_path):
    """Si la orden se mueve entre la lectura y la escritura, se avisa.

    El caso real: el técnico cierra la orden desde el móvil justo cuando el
    supervisor la está rechazando. Alguien tiene que perder, y tiene que saberlo.
    """

    reg = _registro(tmp_path, ["ana"])
    reg.asignar(_ordenes(1), "ana")

    almacen = reg.almacen
    original = almacen.transicionar

    def carrera(orden, desde, hacia, **kw):
        # Simula al otro proceso adelantándose justo antes de nuestra escritura.
        original(orden, desde, EstadoOrden.RECHAZADA.value, actor="supervisor")
        return original(orden, desde, hacia, **kw)

    almacen.transicionar = carrera
    with pytest.raises(ConflictoConcurrencia, match="cambió de estado"):
        reg.transicionar("OT-0000", EstadoOrden.DESCARGADA)
    almacen.transicionar = original

    assert reg.obtener("OT-0000").estado is EstadoOrden.RECHAZADA


@pytest.mark.unit
def test_asignacion_en_conflicto_no_deja_nada_escrito(tmp_path):
    """O entra la jornada completa o no entra nada.

    Media jornada asignada es peor que ninguna: nadie sabe qué falta.
    """

    reg = _registro(tmp_path, ["ana", "beto"])
    ordenes = _ordenes(4)
    reg.asignar(ordenes.iloc[:1], "ana")

    with pytest.raises(ValueError, match="ya están asignadas"):
        reg.asignar(ordenes, "beto")

    # Ninguna de las tres órdenes libres quedó a nombre de beto.
    assert reg.de_usuario("beto") == []
    assert len(reg.de_usuario("ana")) == 1


@pytest.mark.unit
def test_la_bitacora_explica_quien_movio_cada_orden(tmp_path):
    """Una orden que cambió de manos es imposible de explicar sin registro."""

    reg = _registro(tmp_path, ["ana"])
    reg.asignar(_ordenes(2), "ana", asignado_por="supervisor")
    reg.marcar_descargadas("ana", actor="ana")

    b = reg.bitacora()
    assert set(b["operacion"]) >= {"ASIGNAR", "TRANSICION_LOTE"}
    assert "supervisor" in set(b["actor"])


@pytest.mark.unit
def test_cerrar_orden_es_idempotente(tmp_path):
    """Un reenvío del mismo paquete no debe fallar ni contar dos veces."""

    reg = _registro(tmp_path, ["ana"])
    reg.asignar(_ordenes(1), "ana")
    reg.marcar_descargadas("ana")

    a = reg.cerrar_orden("OT-0000", resultado="Medidor manipulado")
    assert a is not None and a.estado is EstadoOrden.COMPLETADA
    assert reg.cerrar_orden("OT-0000", resultado="Medidor manipulado") is None
    assert reg.obtener("OT-0000").resultado == "Medidor manipulado"


@pytest.mark.unit
def test_el_registro_sobrevive_al_proceso(tmp_path):
    """Lo que se asignó tiene que seguir ahí cuando el servidor se reinicie."""

    reg = _registro(tmp_path, ["ana"])
    reg.asignar(_ordenes(3), "ana")
    reg.marcar_descargadas("ana")
    del reg

    otro = RegistroCampo(tmp_path / "registro.json")
    assert len(otro.de_usuario("ana", estados={EstadoOrden.DESCARGADA})) == 3


@pytest.mark.unit
def test_migra_un_registro_json_anterior(tmp_path):
    """Una instalación en marcha no puede perder sus asignaciones al actualizar."""

    import json

    ruta = tmp_path / "registro.json"
    ruta.write_text(json.dumps({
        "usuarios": [{"usuario": "ana", "nombre": "Ana", "rol": "TECNICO",
                      "activo": True, "password_hash": "x", "creado_en": "2026-01-01"}],
        "asignaciones": [{"orden_trabajo": "OT-9", "asignado_a": "ana",
                          "nivel": "SECTOR", "entidad": "S9",
                          "estado": "DESCARGADA", "clientes_a_revisar": 5,
                          "recuperable_kwh_mes": 100.0}],
    }), encoding="utf-8")

    reg = RegistroCampo(ruta)
    assert "ana" in reg.usuarios
    assert reg.obtener("OT-9").estado is EstadoOrden.DESCARGADA


# --------------------------------------------------------------------------- #
# Reparto entre cuadrillas
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_reparto_equilibra_la_carga():
    """Un técnico con 18 000 kWh y otro con 3 000 significa que uno no termina."""

    rep = repartir_ordenes(_ordenes(40), ["ana", "beto", "carla", "dario"],
                           criterio="kwh")
    assert rep.total_asignadas == 40
    assert rep.desbalance_pct < 25.0
    assert all(not df.empty for df in rep.por_usuario.values())


@pytest.mark.unit
def test_reparto_mantiene_juntas_las_ordenes_de_cada_cuadrilla():
    """El tiempo de traslado se come las visitas, y las visitas son lo que
    recupera energía."""

    barrios = ((630000.0, 9760000.0), (636000.0, 9760000.0),
               (630000.0, 9766000.0), (636000.0, 9766000.0))
    ordenes = _ordenes(40, centros=barrios)

    agrupado = repartir_ordenes(ordenes, ["a", "b", "c", "d"],
                                agrupar_geografico=True)
    disperso = repartir_ordenes(ordenes, ["a", "b", "c", "d"],
                                agrupar_geografico=False)

    d_agr = agrupado.resumen()["dispersion_km"].mean()
    d_dis = disperso.resumen()["dispersion_km"].mean()
    # Agrupar debe reducir el recorrido de forma clara, no marginal.
    assert d_agr < d_dis / 2, (d_agr, d_dis)


@pytest.mark.unit
def test_el_tope_de_jornada_deja_fuera_lo_de_menor_energia():
    """Una cuadrilla con más órdenes de las que puede hacer no las hace: las
    arrastra. Lo que no cabe se dice, no se reparte igual."""

    rep = repartir_ordenes(_ordenes(30), ["ana", "beto"], criterio="kwh",
                           max_por_usuario=8)
    assert rep.total_asignadas == 16
    assert len(rep.sin_asignar) == 14
    assert any("sin asignar" in a for a in rep.advertencias())

    dentro = pd.concat(rep.por_usuario.values())["recuperable_kwh_mes"].min()
    fuera = rep.sin_asignar["recuperable_kwh_mes"].max()
    assert dentro >= fuera


@pytest.mark.unit
def test_reparto_sin_coordenadas_sigue_siendo_parejo():
    """Sin geografía en la que apoyarse, al menos la carga debe quedar pareja."""

    ordenes = _ordenes(24).drop(columns=["x", "y"])
    rep = repartir_ordenes(ordenes, ["ana", "beto", "carla"], criterio="clientes")
    assert rep.total_asignadas == 24
    assert rep.desbalance_pct < 15.0


@pytest.mark.unit
def test_reparto_es_determinista():
    """El supervisor que reparte dos veces la misma lista espera el mismo
    resultado; si no, no puede comparar ni explicar lo que hizo."""

    ordenes = _ordenes(30)
    a = repartir_ordenes(ordenes, ["x", "y", "z"])
    b = repartir_ordenes(ordenes, ["x", "y", "z"])
    for u in ("x", "y", "z"):
        assert list(a.por_usuario[u]["orden_trabajo"]) == \
            list(b.por_usuario[u]["orden_trabajo"])


@pytest.mark.unit
def test_aplicar_el_reparto_deja_a_cada_tecnico_con_lo_suyo(tmp_path):
    """Lo que el técnico ve al conectarse tiene que ser exactamente su parte."""

    tecnicos = ["ana", "beto", "carla"]
    reg = _registro(tmp_path, tecnicos)
    rep = repartir_ordenes(_ordenes(30), tecnicos, criterio="kwh")
    asignar_reparto(reg, rep, asignado_por="supervisor")

    total = 0
    for t in tecnicos:
        propias = {a.orden_trabajo for a in reg.de_usuario(t)}
        assert propias == set(rep.por_usuario[t]["orden_trabajo"])
        total += len(propias)
    assert total == 30
    # Ninguna orden en dos manos.
    assert len(reg.asignaciones) == 30


@pytest.mark.unit
def test_criterio_desconocido_falla_claro():
    with pytest.raises(ValueError, match="Criterio"):
        repartir_ordenes(_ordenes(3), ["ana"], criterio="metros")


@pytest.mark.unit
def test_carga_por_usuario_sale_de_una_consulta(tmp_path):
    """El resumen del supervisor debe reflejar el estado real, no una caché."""

    reg = _registro(tmp_path, ["ana", "beto"])
    reg.asignar(_ordenes(4).iloc[:3], "ana")
    reg.marcar_descargadas("ana")

    res = reg.resumen_por_usuario().set_index("usuario")
    assert res.loc["ana", "DESCARGADA"] == 3
    assert res.loc["ana", "ASIGNADA"] == 0
    assert res.loc["beto", "ordenes"] == 0


@pytest.mark.unit
def test_el_almacen_no_acepta_ordenes_de_un_usuario_inexistente(tmp_path):
    """Una orden a nombre de nadie no se puede entregar ni reclamar."""

    import sqlite3

    almacen = AlmacenCampo(tmp_path / "campo.db")
    with pytest.raises(sqlite3.IntegrityError):
        almacen.asignar_lote([{"orden_trabajo": "OT-1", "guid": "g",
                               "asignado_a": "fantasma", "estado": "ASIGNADA"}])
