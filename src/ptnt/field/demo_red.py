"""Red de demostración para probar el ciclo de campo sin la base real.

Genera una red pequeña y **topológicamente coherente** alrededor de cada orden
asignada: un puesto de transformación con sus unidades, postes, tramos de baja
tensión y clientes con su acometida. Es lo mínimo para ejercitar el snap, la
propagación y la relación Puesto→Unidad en un dispositivo real.
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd


def red_de_demostracion(asignaciones, *, clientes_por_puesto: int = 12,
                        semilla: int = 20260809):
    """Devuelve ``(capas, conexiones)`` alrededor de las órdenes asignadas."""

    rng = np.random.default_rng(semilla)
    puestos, unidades, clientes, postes, tramos, conexiones = [], [], [], [], [], []

    for a in asignaciones:
        if a.x is None or a.y is None:
            continue
        g_tx = str(uuid.uuid4())
        puestos.append({
            "guid": g_tx, "codigo": f"TS-{a.orden_trabajo[-4:]}",
            "x": a.x, "y": a.y, "feeder_code": a.feeder_code,
            "codigo_estructura": "TRO0120", "potencia_nominal_kva": 50.0,
            "configuracion_banco": "BANCO_3", "fases": "ABC",
            "tiene_totalizador": False, "n_unidades": 3, "estado": "OPERATIVO",
            "origen_edicion": "SIG",
        })
        for i, fase in enumerate("ABC"):
            g_u = str(uuid.uuid4())
            unidades.append({
                "guid": g_u, "puesto_guid": g_tx, "codigo": f"U{i+1}",
                "fase": fase, "potencia_kva": 16.7,
                "codigo_estructura": "TRO0050", "estado": "OPERATIVO",
                "origen_edicion": "SIG",
            })
            conexiones.append({
                "guid_origen": g_u, "guid_destino": g_tx,
                "tipo_relacion": "PERTENECE_A",
                "capa_origen": "ptnt_unidad_transformacion",
                "capa_destino": "ptnt_puesto_transformacion"})

        anterior = (a.x, a.y)
        for k in range(max(3, clientes_por_puesto // 4)):
            g_p = str(uuid.uuid4())
            px = a.x + (k + 1) * 35.0
            py = a.y + rng.uniform(-12, 12)
            postes.append({
                "guid": g_p, "codigo": f"P{k+1}", "x": px, "y": py,
                "feeder_code": a.feeder_code, "codigo_estructura": "POO0012",
                "material": "HORMIGON", "altura_m": 11.0, "estado": "OPERATIVO",
                "origen_edicion": "SIG"})
            g_t = str(uuid.uuid4())
            tramos.append({
                "guid": g_t, "codigo": f"BT{k+1}", "feeder_code": a.feeder_code,
                "coords": [anterior, (px, py)], "codigo_estructura": "COO0050",
                "longitud_m": 35.0, "n_fases": 3, "tension_v": 220.0,
                "es_baja_tension": True, "estado": "OPERATIVO",
                "origen_edicion": "SIG"})
            conexiones.append({
                "guid_origen": g_t, "guid_destino": g_p,
                "tipo_relacion": "COMPARTE_VERTICE",
                "capa_origen": "ptnt_tramo", "capa_destino": "ptnt_poste"})
            anterior = (px, py)

            for j in range(3):
                g_c = str(uuid.uuid4())
                cx = px + rng.uniform(-18, 18)
                cy = py + rng.uniform(15, 30)
                clientes.append({
                    "guid": g_c, "cuenta_contrato": f"30{rng.integers(1e8, 9e8)}",
                    "nombre": f"CLIENTE {k}-{j}", "x": cx, "y": cy,
                    "feeder_code": a.feeder_code, "puesto_guid": g_tx,
                    "tarifa": "RESIDENCIAL BAJA TENSION",
                    "ruta_lectura": f"{a.feeder_code}-R01", "n_fases": 1,
                    "tipo_medidor": "ELECTRONICO",
                    "tipo_acometida": "AEREA", "estado": "OPERATIVO",
                    "consumo_promedio_kwh": float(rng.uniform(60, 320)),
                    "score_sospecha": float(rng.uniform(0.3, 0.99)),
                    "inspeccionado": False, "origen_edicion": "SIG"})
                g_ac = str(uuid.uuid4())
                tramos.append({
                    "guid": g_ac, "codigo": f"ACO{k}-{j}",
                    "feeder_code": a.feeder_code, "coords": [(px, py), (cx, cy)],
                    "codigo_estructura": "COO0050", "longitud_m": 25.0,
                    "n_fases": 1, "tension_v": 220.0, "es_baja_tension": True,
                    "estado": "OPERATIVO", "origen_edicion": "SIG"})
                conexiones += [
                    {"guid_origen": g_c, "guid_destino": g_ac,
                     "tipo_relacion": "ACOMETIDA",
                     "capa_origen": "ptnt_cliente", "capa_destino": "ptnt_tramo"},
                    {"guid_origen": g_ac, "guid_destino": g_p,
                     "tipo_relacion": "COMPARTE_VERTICE",
                     "capa_origen": "ptnt_tramo", "capa_destino": "ptnt_poste"},
                    {"guid_origen": g_tx, "guid_destino": g_c,
                     "tipo_relacion": "ALIMENTA",
                     "capa_origen": "ptnt_puesto_transformacion",
                     "capa_destino": "ptnt_cliente"},
                ]

    capas = {
        "ptnt_puesto_transformacion": pd.DataFrame(puestos),
        "ptnt_unidad_transformacion": pd.DataFrame(unidades),
        "ptnt_cliente": pd.DataFrame(clientes),
        "ptnt_poste": pd.DataFrame(postes),
        "ptnt_tramo": pd.DataFrame(tramos),
    }
    return capas, pd.DataFrame(conexiones)
