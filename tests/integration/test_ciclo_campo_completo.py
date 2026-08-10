"""El ciclo completo de campo, del paquete al recálculo.

Lo que se fija aquí es el **contrato entre el móvil y el backend**: que lo que la
aplicación escribe en el GeoPackage, el backend lo lea, lo valide y lo entienda.
Es la parte del sistema donde un error no se ve — un cambio mal numerado o una
geometría con otra envolvente no fallan al escribirse, fallan semanas después al
sincronizar, cuando el técnico ya no está en el sitio.

El simulador ejecuta las mismas operaciones que la app Android sobre el mismo
archivo. No dice nada del render ni del GPS; dice que el contrato se cumple.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fastapi")

from ptnt.field import (                                    # noqa: E402
    EstadoOrden,
    RegistroCampo,
    asignar_reparto,
    construir_paquetes,
    repartir_ordenes,
)
from ptnt.field.api import crear_app                        # noqa: E402
from ptnt.field.demo_red import red_de_demostracion         # noqa: E402
from ptnt.field.gpkg import leer_geometria                  # noqa: E402
from ptnt.field.simulator import SimuladorCampo             # noqa: E402
from ptnt.field.sync import aplicar, recibir_paquete, revisar  # noqa: E402

CLAVE = "campo-2026-cnel"


def _ordenes(n: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(99)
    return pd.DataFrame([{
        "orden_trabajo": f"OT-{i + 1:04d}", "nivel": "SECTOR",
        "entidad": f"SEC-{i + 1}", "accion": "Recorrido",
        "clientes_a_revisar": int(rng.integers(8, 30)),
        "recuperable_kwh_mes": float(rng.integers(900, 9000)),
        "x": 630000.0 + float(rng.normal(0, 300)),
        "y": 9760000.0 + float(rng.normal(0, 300)),
    } for i in range(n)])


@pytest.fixture
def jornada(tmp_path: Path):
    """Un técnico con su paquete descargado y la API en pie."""

    from fastapi.testclient import TestClient

    registro_ruta = tmp_path / "campo" / "registro.json"
    reg = RegistroCampo(registro_ruta)
    reg.crear_usuario("ana", "Ana Vera", CLAVE)

    rep = repartir_ordenes(_ordenes(), ["ana"], criterio="kwh")
    asignar_reparto(reg, rep, asignado_por="supervisor")

    capas, conexiones = red_de_demostracion(list(reg.asignaciones.values()))
    construir_paquetes(tmp_path / "campo" / "paquetes", registro=reg,
                       red=capas, conexiones=conexiones, version_red="v1")

    app = crear_app(registro_ruta=registro_ruta,
                    paquetes_dir=tmp_path / "campo" / "paquetes",
                    entrantes_dir=tmp_path / "campo" / "entrantes",
                    lotes_dir=tmp_path / "campo" / "lotes")
    cliente = TestClient(app)
    r = cliente.post("/movil/vincular", json={
        "usuario": "ana", "password": CLAVE, "dispositivo_id": "TEL-1"})
    token = r.json()["token"]
    cliente.get("/movil/paquete", headers={"Authorization": f"Bearer {token}"})

    import shutil

    retorno = tmp_path / "retorno.gpkg"
    shutil.copy(tmp_path / "campo" / "paquetes" / "ana.gpkg", retorno)
    return cliente, token, retorno, registro_ruta, reg


@pytest.mark.integration
def test_lo_que_el_movil_escribe_el_backend_lo_entiende(jornada):
    """Una jornada real de edición, leída por el validador del backend."""

    cliente, token, retorno, _, _ = jornada

    with SimuladorCampo(retorno, usuario="ana") as sim:
        ot = str(sim.ordenes()[0]["orden_trabajo"])
        sim.abrir_orden(ot)
        clientes = sim.elementos("ptnt_cliente", limite=3)
        for c in clientes:
            sim.editar_atributos("ptnt_cliente", str(c["guid"]),
                                 {"hallazgo": "SIN_NOVEDAD", "inspeccionado": 1},
                                 motivo="Inspección en sitio")
            sim.fotografiar(str(c["guid"]), "ptnt_cliente",
                            descripcion="Medidor")
        sim.cerrar_orden(ot, "Sin novedad")
        esperados = sim.resumen.cambios
        fotos = sim.resumen.fotos

    lote = recibir_paquete(retorno, usuario_esperado="ana")
    assert not lote.bloqueado, [h.detalle for h in lote.hallazgos]
    assert len(lote.cambios) == esperados
    assert len(lote.fotos) == fotos
    # Todo cambio tiene autor y posición: sin eso no es auditable.
    assert all(c.autor == "ana" for c in lote.cambios)
    assert all(c.precision_m is not None for c in lote.cambios)
    # Y la secuencia no tiene huecos: un hueco significa ediciones perdidas.
    secuencias = sorted(c.secuencia for c in lote.cambios)
    assert secuencias == list(range(secuencias[0], secuencias[0] + len(secuencias)))


@pytest.mark.integration
def test_mover_un_cliente_arrastra_su_acometida_y_lo_dice(jornada):
    """La propagación tiene que llegar al supervisor marcada como tal: un cambio
    que el técnico no hizo a propósito se revisa distinto."""

    cliente, token, retorno, _, _ = jornada

    with SimuladorCampo(retorno, usuario="ana") as sim:
        sim.abrir_orden(str(sim.ordenes()[0]["orden_trabajo"]))
        c = sim.elementos("ptnt_cliente", limite=1)[0]
        geo = leer_geometria(c["geom"])
        x, y = geo["coords"][0]
        movidos = sim.mover("ptnt_cliente", str(c["guid"]), x + 12.0, y + 8.0,
                            motivo="Medidor en la vereda de enfrente")

    assert movidos >= 2, "el cliente y al menos su acometida"

    lote = recibir_paquete(retorno, usuario_esperado="ana")
    propagados = [c for c in lote.cambios if c.propagado_de]
    assert propagados, "los arrastres deben llegar marcados"
    assert all(c.operacion == "MOVER" for c in propagados)
    assert all(c.geom_antes and c.geom_despues for c in propagados)


@pytest.mark.integration
def test_una_reconexion_recorre_el_ciclo_y_llega_al_recalculo(jornada):
    """El caso que más vale: mueve consumo de una zona a otra."""

    cliente, token, retorno, _, _ = jornada

    with SimuladorCampo(retorno, usuario="ana") as sim:
        sim.abrir_orden(str(sim.ordenes()[0]["orden_trabajo"]))
        clientes = sim.elementos("ptnt_cliente", limite=2)
        puestos = sim.elementos("ptnt_puesto_transformacion")
        actual = str(clientes[0].get("puesto_guid") or "")
        destino = next(str(p["guid"]) for p in puestos
                       if str(p["guid"]) != actual)
        assert sim.reconectar(str(clientes[0]["guid"]), destino,
                              motivo="Seguimiento de acometida")

        # El vínculo viejo no puede sobrevivir: el consumo se contaría dos veces.
        vinculos = [c for c in sim.elementos("ptnt_conexion")
                    if str(c["guid_destino"]) == str(clientes[0]["guid"])
                    and str(c["tipo_relacion"]) == "ALIMENTA"]
        assert len(vinculos) == 1
        assert str(vinculos[0]["guid_origen"]) == destino

    lote = recibir_paquete(retorno, usuario_esperado="ana")
    assert not lote.bloqueado, [h.detalle for h in lote.hallazgos]
    recon = [c for c in lote.cambios if c.operacion == "RECONECTAR"]
    assert len(recon) == 1
    assert recon[0].valor_antes == actual or not actual
    assert recon[0].valor_despues == destino

    revisar(lote, aceptar_todo=True, revisor="supervisor")
    res = aplicar(lote, feeder_por_elemento={actual: "F001", destino: "F002"})
    assert res.reconexiones == 1
    assert {"topologia", "balance", "focalizacion", "ranking"} <= set(
        res.etapas_a_recalcular)
    # Las dos zonas, no solo la del cliente.
    assert res.alimentadores_afectados == {"F001", "F002"}


@pytest.mark.integration
def test_dos_jornadas_no_duplican_el_historico(jornada):
    """El diario acumula durante todo el trabajo; lo enviado no se reprocesa."""

    cliente, token, retorno, registro_ruta, _ = jornada

    with SimuladorCampo(retorno, usuario="ana") as sim:
        ot = str(sim.ordenes()[0]["orden_trabajo"])
        sim.abrir_orden(ot)
        for c in sim.elementos("ptnt_cliente", limite=2):
            sim.editar_atributos("ptnt_cliente", str(c["guid"]),
                                 {"inspeccionado": 1}, motivo="Día 1")

    with retorno.open("rb") as f:
        r1 = cliente.post("/movil/sincronizar",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"archivo": ("a.gpkg", f, "application/octet-stream")})
    assert r1.status_code == 200, r1.text
    lote1 = r1.json()
    n_dia1 = lote1["resumen"]["cambios"]
    assert n_dia1 > 0
    assert lote1["ordenes_en_curso"], "la orden sigue abierta"

    # El cliente móvil marca lo enviado DESPUÉS del 200 del servidor.
    with SimuladorCampo(retorno, usuario="ana") as sim:
        assert sim.marcar_sincronizados(lote1["lote_id"]) == n_dia1
        assert sim.pendientes() == 0
        # Segunda jornada.
        sim.abrir_orden(str(sim.ordenes()[0]["orden_trabajo"]))
        c = sim.elementos("ptnt_cliente", limite=4)[3]
        sim.editar_atributos("ptnt_cliente", str(c["guid"]),
                             {"hallazgo": "SIN_NOVEDAD"}, motivo="Día 2")
        assert sim.pendientes() == 1

    with retorno.open("rb") as f:
        r2 = cliente.post("/movil/sincronizar",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"archivo": ("a.gpkg", f, "application/octet-stream")})
    assert r2.status_code == 200
    res2 = r2.json()["resumen"]
    assert res2["cambios"] == 1, "solo lo nuevo"
    assert res2["ya_sincronizados"] == n_dia1

    # Y la orden acumuló dos jornadas sin cerrarse.
    final = RegistroCampo(registro_ruta)
    a = final.obtener(str(lote1["ordenes_en_curso"][0]))
    assert a.estado is EstadoOrden.EN_PROCESO
    assert a.visitas == 2


@pytest.mark.integration
def test_eliminar_un_puesto_con_clientes_se_impide_en_el_dispositivo(jornada):
    """Se impide en campo y se explica, en vez de aceptarlo y que reviente al
    sincronizar — cuando el técnico ya no está en el sitio para arreglarlo."""

    cliente, token, retorno, _, _ = jornada

    with SimuladorCampo(retorno, usuario="ana") as sim:
        puestos = sim.elementos("ptnt_puesto_transformacion")
        con_dependientes = None
        for p in puestos:
            colgando = [c for c in sim.elementos("ptnt_conexion")
                        if str(c["guid_origen"]) == str(p["guid"])]
            if colgando:
                con_dependientes = p
                break
        assert con_dependientes is not None

        ok = sim.eliminar("ptnt_puesto_transformacion",
                          str(con_dependientes["guid"]),
                          motivo="Ya no existe")
        assert not ok
        assert sim.resumen.advertencias
        assert sim.resumen.bajas == 0
        # Y el elemento sigue ahí: no se borró "a medias".
        assert sim.elementos("ptnt_puesto_transformacion",
                             limite=None), "el puesto no se eliminó"
