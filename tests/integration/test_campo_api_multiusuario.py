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


# --------------------------------------------------------------------------- #
# Trabajo de varios días y subida simultánea
# --------------------------------------------------------------------------- #
def _paquete_editado(ruta: Path, *, ordenes_completadas: list[str],
                     ordenes_en_proceso: list[str] | None = None,
                     desde_secuencia: int = 1, n_cambios: int = 2,
                     ya_sincronizados: int = 0) -> Path:
    """Simula lo que devuelve el móvil: diario de cambios y estado de órdenes."""

    import sqlite3

    con = sqlite3.connect(ruta)
    seq = desde_secuencia
    for i in range(ya_sincronizados):
        con.execute(
            "INSERT INTO ptnt_cambio (guid, secuencia, capa, elemento_guid, "
            "operacion, campo, valor_despues, autor, ocurrido_en, "
            "estado_revision, sincronizado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"viejo-{seq}", seq, "ptnt_cliente", f"CLI-{seq}", "MODIFICAR",
             "hallazgo", "NORMAL", "ana", "2026-08-08T10:00:00+00:00",
             "PENDIENTE", 1))
        seq += 1
    for i in range(n_cambios):
        con.execute(
            "INSERT INTO ptnt_cambio (guid, secuencia, capa, elemento_guid, "
            "operacion, campo, valor_despues, autor, ocurrido_en, "
            "estado_revision, sincronizado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"nuevo-{seq}", seq, "ptnt_cliente", f"CLI-{seq}", "MODIFICAR",
             "hallazgo", "MEDIDOR_MANIPULADO", "ana",
             "2026-08-09T10:00:00+00:00", "PENDIENTE", 0))
        seq += 1
    for ot in ordenes_completadas:
        con.execute(
            "UPDATE ptnt_orden_trabajo SET estado = 'COMPLETADA', "
            "resultado = 'Hurto confirmado' WHERE orden_trabajo = ?", (ot,))
    # Abrir una orden en el móvil la pone EN_PROCESO: es lo que distingue una
    # orden empezada de una que nadie tocó.
    for ot in (ordenes_en_proceso or []):
        con.execute(
            "UPDATE ptnt_orden_trabajo SET estado = 'EN_PROCESO' "
            "WHERE orden_trabajo = ?", (ot,))
    con.commit()
    con.close()
    return ruta


@pytest.mark.integration
def test_sincronizar_no_cierra_las_ordenes_que_siguen_abiertas(despacho, tmp_path):
    """Una revisión puede llevar días. Cerrar todo el paquete al sincronizar
    daría por terminadas órdenes que ni se han empezado."""

    import shutil

    cliente, tokens, rep, registro_ruta = despacho
    usuario = "ana"
    mias = list(rep.por_usuario[usuario]["orden_trabajo"])
    assert len(mias) >= 2

    # El técnico baja el paquete y trabaja solo la primera orden.
    cliente.get("/movil/paquete",
                headers={"Authorization": f"Bearer {tokens[usuario]}"})
    copia = tmp_path / "retorno_dia1.gpkg"
    shutil.copy(tmp_path / "campo" / "paquetes" / f"{usuario}.gpkg", copia)
    # Cierra la primera y deja la segunda empezada: el resto ni se abrió.
    _paquete_editado(copia, ordenes_completadas=[mias[0]],
                     ordenes_en_proceso=[mias[1]])

    with copia.open("rb") as f:
        r = cliente.post(
            "/movil/sincronizar",
            headers={"Authorization": f"Bearer {tokens[usuario]}"},
            files={"archivo": ("retorno.gpkg", f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    cuerpo = r.json()

    assert cuerpo["ordenes_cerradas"] == [mias[0]]
    # La empezada sigue abierta y con la jornada anotada.
    assert cuerpo["ordenes_en_curso"] == [mias[1]]
    assert "continuar mañana" in cuerpo["mensaje"]

    final = RegistroCampo(registro_ruta)
    assert final.obtener(mias[0]).estado is EstadoOrden.COMPLETADA

    empezada = final.obtener(mias[1])
    assert empezada.estado is EstadoOrden.DESCARGADA
    assert empezada.visitas == 1, "la jornada trabajada debe quedar anotada"
    assert empezada.fecha_ultimo_avance

    # Las que nadie abrió no cuentan jornada: inflarían el indicador y harían
    # parecer trabajada una orden que no se visitó.
    for ot in mias[2:]:
        intacta = final.obtener(ot)
        assert intacta.estado is EstadoOrden.DESCARGADA
        assert intacta.visitas == 0


@pytest.mark.integration
def test_el_avance_ya_enviado_no_se_reprocesa_al_dia_siguiente(despacho, tmp_path):
    """El diario acumula durante todo el trabajo. Sin marcar lo enviado, cada
    sincronización reenviaría lo anterior y el histórico contaría el mismo
    cambio tantas veces como días duró la orden."""

    import shutil

    cliente, tokens, rep, _ = despacho
    cliente.get("/movil/paquete",
                headers={"Authorization": f"Bearer {tokens['ana']}"})

    copia = tmp_path / "retorno_dia2.gpkg"
    shutil.copy(tmp_path / "campo" / "paquetes" / "ana.gpkg", copia)
    # 3 cambios de ayer (ya enviados) + 2 de hoy.
    _paquete_editado(copia, ordenes_completadas=[], n_cambios=2,
                     ya_sincronizados=3)

    with copia.open("rb") as f:
        r = cliente.post(
            "/movil/sincronizar",
            headers={"Authorization": f"Bearer {tokens['ana']}"},
            files={"archivo": ("retorno.gpkg", f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    resumen = r.json()["resumen"]
    assert resumen["cambios"] == 2, "solo lo nuevo"
    assert resumen["ya_sincronizados"] == 3


@pytest.mark.integration
def test_tres_tecnicos_suben_su_jornada_a_la_vez(despacho, tmp_path):
    """Al volver a la base sincronizan todos juntos. Cada lote tiene que llegar
    entero y a nombre de quien lo hizo."""

    import shutil
    import threading

    cliente, tokens, rep, registro_ruta = despacho
    for u, t in tokens.items():
        cliente.get("/movil/paquete", headers={"Authorization": f"Bearer {t}"})

    copias = {}
    for u in tokens:
        c = tmp_path / f"retorno_{u}.gpkg"
        shutil.copy(tmp_path / "campo" / "paquetes" / f"{u}.gpkg", c)
        suyas = list(rep.por_usuario[u]["orden_trabajo"])
        _paquete_editado(c, ordenes_completadas=suyas[:1],
                         ordenes_en_proceso=suyas[1:2], n_cambios=3)
        copias[u] = c

    barrera = threading.Barrier(len(tokens))
    respuestas: dict[str, dict] = {}

    def sube(usuario: str, token: str) -> None:
        barrera.wait()
        with copias[usuario].open("rb") as f:
            r = cliente.post(
                "/movil/sincronizar",
                headers={"Authorization": f"Bearer {token}"},
                files={"archivo": ("r.gpkg", f, "application/octet-stream")})
        respuestas[usuario] = {"codigo": r.status_code, **r.json()}

    hilos = [threading.Thread(target=sube, args=(u, t)) for u, t in tokens.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert {r["codigo"] for r in respuestas.values()} == {200}, respuestas
    # Cada lote llegó completo y con su propio identificador.
    assert len({r["lote_id"] for r in respuestas.values()}) == len(tokens)
    for u, r in respuestas.items():
        assert r["resumen"]["cambios"] == 3
        assert r["resumen"]["usuario"] == u

    final = RegistroCampo(registro_ruta)
    cerradas = [a for a in final.asignaciones.values()
                if a.estado is EstadoOrden.COMPLETADA]
    assert len(cerradas) == len(tokens), "una por técnico, ni más ni menos"
