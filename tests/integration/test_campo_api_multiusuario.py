"""La API móvil con varias cuadrillas trabajando a la vez.

Es la prueba de la parte que el técnico ve: entra con su usuario, le sale **lo
suyo** y se lo descarga. Y de la parte que nadie ve pero decide si el sistema
sirve: que tres teléfonos sincronizando al mismo tiempo no se pisen.
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

TECNICOS = ["ana", "beto", "carla"]
CLAVE = "campo-2026-cnel"
N = 18


def _ordenes() -> pd.DataFrame:
    rng = np.random.default_rng(4321)
    barrios = ((630000.0, 9760000.0), (633500.0, 9761500.0), (631000.0, 9764000.0))
    return pd.DataFrame([{
        "orden_trabajo": f"OT-{i + 1:04d}", "nivel": "SECTOR",
        "entidad": f"SEC-{i + 1}", "accion": "Recorrido",
        "clientes_a_revisar": int(rng.integers(6, 45)),
        "recuperable_kwh_mes": float(rng.integers(800, 16000)),
        "x": barrios[i % 3][0] + float(rng.normal(0, 350)),
        "y": barrios[i % 3][1] + float(rng.normal(0, 350)),
    } for i in range(N)])


@pytest.fixture
def despacho(tmp_path: Path):
    """Jornada repartida, paquetes generados y API en pie."""

    from fastapi.testclient import TestClient

    registro_ruta = tmp_path / "campo" / "registro.json"
    reg = RegistroCampo(registro_ruta)
    for t in TECNICOS:
        reg.crear_usuario(t, t.title(), CLAVE, unidad_negocio="CNEL-GYE")

    rep = repartir_ordenes(_ordenes(), TECNICOS, criterio="kwh")
    asignar_reparto(reg, rep, asignado_por="supervisor")

    capas, conexiones = red_de_demostracion(list(reg.asignaciones.values()))
    construir_paquetes(tmp_path / "campo" / "paquetes", registro=reg,
                       red=capas, conexiones=conexiones, version_red="test")

    app = crear_app(registro_ruta=registro_ruta,
                    paquetes_dir=tmp_path / "campo" / "paquetes",
                    entrantes_dir=tmp_path / "campo" / "entrantes",
                    lotes_dir=tmp_path / "campo" / "lotes")
    cliente = TestClient(app)
    tokens = {}
    for t in TECNICOS:
        r = cliente.post("/movil/vincular", json={
            "usuario": t, "password": CLAVE, "dispositivo_id": f"TEL-{t}"})
        assert r.status_code == 200, r.text
        tokens[t] = r.json()["token"]
    return cliente, tokens, rep, registro_ruta


@pytest.mark.integration
def test_cada_tecnico_ve_solo_su_trabajo(despacho):
    """Que una cuadrilla vea las órdenes de otra termina en dos visitas al mismo
    sitio, o en ninguna."""

    cliente, tokens, rep, _ = despacho
    total = 0
    for usuario, token in tokens.items():
        r = cliente.get("/movil/ordenes",
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        vistas = {o["orden_trabajo"] for o in r.json()["ordenes"]}
        assert vistas == set(rep.por_usuario[usuario]["orden_trabajo"])
        total += len(vistas)
    assert total == N


@pytest.mark.integration
def test_descarga_simultanea_no_pierde_ordenes(despacho):
    """Tres teléfonos bajando su paquete a la vez: las 18 órdenes quedan
    DESCARGADA, no las del último en escribir."""

    import threading

    cliente, tokens, _, registro_ruta = despacho
    barrera = threading.Barrier(len(tokens))
    codigos: dict[str, int] = {}

    def baja(usuario: str, token: str) -> None:
        barrera.wait()
        r = cliente.get("/movil/paquete",
                        headers={"Authorization": f"Bearer {token}"})
        codigos[usuario] = r.status_code

    hilos = [threading.Thread(target=baja, args=(u, t)) for u, t in tokens.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert set(codigos.values()) == {200}, codigos
    final = RegistroCampo(registro_ruta)
    descargadas = [a for a in final.asignaciones.values()
                   if a.estado is EstadoOrden.DESCARGADA]
    assert len(descargadas) == N


@pytest.mark.integration
def test_token_ajeno_no_abre_el_trabajo_de_otro(despacho):
    """El token identifica al equipo, no solo a la sesión: sin él no se ve nada."""

    cliente, _, _, _ = despacho
    assert cliente.get("/movil/ordenes").status_code == 401
    assert cliente.get(
        "/movil/ordenes",
        headers={"Authorization": "Bearer inventado"}).status_code == 401


@pytest.mark.integration
def test_revocar_el_equipo_lo_deja_fuera(despacho, tmp_path):
    """Si el teléfono se pierde, deja de sincronizar sin tocar la cuenta."""

    cliente, tokens, _, registro_ruta = despacho
    reg = RegistroCampo(registro_ruta)
    reg.revocar_dispositivo("ana")

    r = cliente.get("/movil/ordenes",
                    headers={"Authorization": f"Bearer {tokens['ana']}"})
    assert r.status_code == 401
    # Los demás siguen trabajando.
    assert cliente.get(
        "/movil/ordenes",
        headers={"Authorization": f"Bearer {tokens['beto']}"}).status_code == 200
