"""Despacho de una jornada a varias cuadrillas, de principio a fin.

Recorre exactamente lo que ocurre un día normal, y lo hace contra la API real
(no contra los objetos internos):

    1. El backend crea los usuarios móviles.
    2. El supervisor reparte las órdenes entre las cuadrillas.
    3. Se generan todos los paquetes .gpkg de una vez.
    4. Cada técnico entra desde su teléfono, ve **lo suyo** y se lo descarga.
    5. Los tres sincronizan **al mismo tiempo** al volver a la base.
    6. Se comprueba que no se perdió ninguna actualización.

Ejecutar:  python scripts/demo_campo_multiusuario.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptnt.field import (                                        # noqa: E402
    EstadoOrden,
    RegistroCampo,
    asignar_reparto,
    construir_paquetes,
    repartir_ordenes,
    resumen_paquetes,
)
from ptnt.field.api import crear_app                            # noqa: E402
from ptnt.field.demo_red import red_de_demostracion             # noqa: E402

TECNICOS = [("ana", "Ana Vera"), ("beto", "Beto Ruiz"), ("carla", "Carla Mora")]
CLAVE = "campo-2026-cnel"
N_ORDENES = 24
BARRIOS = ((630000.0, 9760000.0), (633500.0, 9761500.0), (631000.0, 9764000.0))


def _titulo(n: int, texto: str) -> None:
    print(f"\n{'=' * 72}\n {n}. {texto}\n{'=' * 72}")


def _ordenes() -> pd.DataFrame:
    rng = np.random.default_rng(20260809)
    filas = []
    for i in range(N_ORDENES):
        cx, cy = BARRIOS[i % len(BARRIOS)]
        filas.append({
            "orden_trabajo": f"OT-{i + 1:04d}",
            "nivel": "SECTOR" if i % 3 else "TRANSFORMADOR",
            "entidad": f"SEC-{i + 1}",
            "accion": "Recorrido de acometidas",
            "motivo_principal": "PNT alta en la zona",
            "clientes_a_revisar": int(rng.integers(6, 45)),
            "recuperable_kwh_mes": float(rng.integers(800, 16000)),
            "x": cx + float(rng.normal(0, 350)),
            "y": cy + float(rng.normal(0, 350)),
        })
    return pd.DataFrame(filas)


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="ptnt_campo_"))
    dir_campo = base / "campo"
    registro_ruta = dir_campo / "registro.json"

    # --- 1. usuarios ------------------------------------------------------
    _titulo(1, "El backend crea los usuarios móviles")
    reg = RegistroCampo(registro_ruta)
    for usuario, nombre in TECNICOS:
        reg.crear_usuario(usuario, nombre, CLAVE, unidad_negocio="CNEL-GYE")
        print(f"   ✓ {usuario:6s}  {nombre}")
    print("   Las contraseñas se guardan solo como hash; el token del "
          "dispositivo se emite al vincular.")

    # --- 2. reparto -------------------------------------------------------
    _titulo(2, "El supervisor reparte la jornada entre las tres cuadrillas")
    ordenes = _ordenes()
    rep = repartir_ordenes(ordenes, [u for u, _ in TECNICOS], criterio="kwh")
    print(rep.resumen().to_string(index=False))
    print(f"\n   Desbalance entre la más y la menos cargada: "
          f"{rep.desbalance_pct:.1f} %")
    for a in rep.advertencias():
        print(f"   ⚠ {a}")
    asignar_reparto(reg, rep, asignado_por="supervisor")

    # --- 3. paquetes ------------------------------------------------------
    _titulo(3, "Se generan todos los paquetes de una vez")
    capas, conexiones = red_de_demostracion(list(reg.asignaciones.values()))
    resultados = construir_paquetes(
        dir_campo / "paquetes", registro=reg, red=capas, conexiones=conexiones,
        version_red="demo-multiusuario")
    res = resumen_paquetes(resultados)
    print(res[["usuario", "estado", "ordenes", "elementos", "area_km2",
               "tamano_mb"]].to_string(index=False))

    # --- 4 y 5. los técnicos entran y sincronizan a la vez -----------------
    _titulo(4, "Cada técnico entra desde su teléfono y descarga lo suyo")
    from fastapi.testclient import TestClient

    app = crear_app(registro_ruta=registro_ruta,
                    paquetes_dir=dir_campo / "paquetes",
                    entrantes_dir=dir_campo / "entrantes",
                    lotes_dir=dir_campo / "lotes")
    cliente = TestClient(app)

    tokens: dict[str, str] = {}
    for usuario, _ in TECNICOS:
        r = cliente.post("/movil/vincular", json={
            "usuario": usuario, "password": CLAVE,
            "dispositivo_id": f"TEL-{usuario.upper()}"})
        r.raise_for_status()
        tokens[usuario] = r.json()["token"]

    vistas: dict[str, list[str]] = {}
    for usuario, token in tokens.items():
        r = cliente.get("/movil/ordenes",
                        headers={"Authorization": f"Bearer {token}"})
        vistas[usuario] = [o["orden_trabajo"] for o in r.json()["ordenes"]]
        print(f"   {usuario:6s} ve {len(vistas[usuario]):2d} orden(es): "
              f"{', '.join(vistas[usuario][:4])}…")

    # Nadie ve el trabajo de otro.
    for usuario in vistas:
        ajenas = set(vistas[usuario]) - set(rep.por_usuario[usuario]["orden_trabajo"])
        assert not ajenas, f"{usuario} ve órdenes ajenas: {ajenas}"
    print("   ✓ Cada técnico ve exactamente su parte, nada más.")

    _titulo(5, "Los tres descargan el paquete SIMULTÁNEAMENTE")
    barrera = threading.Barrier(len(tokens))
    fallos: list[str] = []

    def descarga(usuario: str, token: str) -> None:
        barrera.wait()
        try:
            r = cliente.get("/movil/paquete",
                            headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                fallos.append(f"{usuario}: HTTP {r.status_code}")
        except Exception as exc:                                  # noqa: BLE001
            fallos.append(f"{usuario}: {exc!r}")

    hilos = [threading.Thread(target=descarga, args=(u, t))
             for u, t in tokens.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    if fallos:
        print(f"   ✗ Fallos en la descarga concurrente: {fallos}")
        return 1

    # --- 6. verificación --------------------------------------------------
    _titulo(6, "Verificación: ninguna actualización se perdió")
    final = RegistroCampo(registro_ruta)
    por_estado = pd.Series(
        [a.estado.value for a in final.asignaciones.values()]).value_counts()
    print(por_estado.to_string())

    descargadas = int(por_estado.get(EstadoOrden.DESCARGADA.value, 0))
    print(f"\n   Esperado: {N_ORDENES} órdenes DESCARGADA")
    print(f"   Obtenido: {descargadas}")

    print("\n   Carga por técnico tras la descarga simultánea:")
    print(final.resumen_por_usuario()[
        ["usuario", "ordenes", "ASIGNADA", "DESCARGADA"]].to_string(index=False))

    print("\n   Bitácora (quién hizo qué):")
    b = final.bitacora(12)
    print(b[["operacion", "actor", "detalle"]].to_string(index=False))

    ok = descargadas == N_ORDENES
    print(f"\n{'✓ TODO COHERENTE' if ok else '✗ SE PERDIERON ACTUALIZACIONES'}"
          f" — {descargadas}/{N_ORDENES}")
    shutil.rmtree(base, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
