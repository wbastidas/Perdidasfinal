"""Escenario de prueba a escala: distribuidora de la **Costa ecuatoriana**.

Reproduce las características que distinguen a una unidad de negocio costera de
CNEL EP y que **cambian el resultado del análisis**, no solo la ambientación:

* **Estacionalidad invertida respecto de la Sierra.** El pico de consumo es la
  estación húmeda y calurosa (enero–abril), por climatización. Un detector
  calibrado con estacionalidad de Sierra marcaría esa subida como anomalía.
* **Tarifa Dignidad con techo de 130 kWh/mes** (Costa/Oriente/Insular; en la
  Sierra son 110). Genera una masa grande de clientes muy pequeños, que es
  justamente donde el grupo par mal armado produce falsos positivos.
* **Zonificación urbana marcada**: centro comercial denso, polígonos
  industriales, barrios residenciales y periferia con alta PNT. La composición
  por clase varía fuertemente entre rutas de lectura.
* **Coordenadas UTM 17S reales** del área Guayaquil–Durán (≈ 615–640 km E,
  9 745–9 775 km N), para que la focalización geográfica sea verificable.

A diferencia de `scenario.py` —pensado para una demo rápida de un alimentador—
aquí se generan **varios alimentadores completos** con su red, de modo que la
focalización tenga algo real que ordenar en los niveles ALIMENTADOR, RAMAL y
PUESTO_TRANSFORMACION, no solo un caso único.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ptnt.synth.network import generate_radial_network
from ptnt.topology.graph import NetworkModel

# --------------------------------------------------------------------------- #
# Parámetros del escenario costero
# --------------------------------------------------------------------------- #
# Área metropolitana Guayaquil–Durán en UTM 17S (EPSG:32717)
UTM_X0, UTM_X1 = 615_000.0, 640_000.0
UTM_Y0, UTM_Y1 = 9_745_000.0, 9_775_000.0

# Estacionalidad costera: pico en la estación húmeda/calurosa (ene–abr).
# Índice 0 = enero. Amplitud ~18 %, coherente con el uso de climatización.
ESTACIONALIDAD_COSTA = np.array([
    1.18, 1.20, 1.19, 1.14, 1.05, 0.95,
    0.88, 0.85, 0.86, 0.90, 0.98, 1.08,
])

TARIFA_DIGNIDAD_KWH = 130.0        # techo Costa/Oriente/Insular

_TARIFAS = {
    "RESIDENCIAL": [
        "RESIDENCIAL BAJA TENSION",
        "RESIDENCIAL TEMPORAL BAJA TENSION",
        "TARIFA DIGNIDAD BAJA TENSION",
    ],
    "COMERCIAL": [
        "COMERCIAL SIN DEMANDA BAJA TENSION",
        "COMERCIAL CON DEMANDA BAJA TENSION",
    ],
    "INDUSTRIAL_BT": [
        "INDUSTRIAL CON DEMANDA BAJA TENSION",
        "INDUSTRIAL ARTESANAL BAJA TENSION",
    ],
    "INDUSTRIAL_MT": [
        "INDUSTRIAL CON DEMANDA MEDIA TENSION",
        "INDUSTRIAL CON DEMANDA HORARIA MEDIA TENSION",
    ],
    "OFICIAL": ["ENTIDADES OFICIALES BAJA TENSION"],
    "ASISTENCIA": ["ASISTENCIA SOCIAL BAJA TENSION"],
    "BOMBEO": ["BOMBEO DE AGUA BAJA TENSION"],
}

# Nivel base de consumo (kWh/mes) y dispersión por clase
_NIVEL_KWH = {
    "RESIDENCIAL": (140.0, 0.55),
    "COMERCIAL": (850.0, 0.70),
    "INDUSTRIAL_BT": (7_500.0, 0.60),
    "INDUSTRIAL_MT": (95_000.0, 0.55),
    "OFICIAL": (1_800.0, 0.65),
    "ASISTENCIA": (600.0, 0.60),
    "BOMBEO": (4_200.0, 0.45),
}

_CLASES = list(_NIVEL_KWH)

# Vocación de cada zona urbana: mezcla de clases y tasa de hurto propia.
# La periferia concentra la PNT, que es el patrón real y lo que la focalización
# geográfica debe encontrar.
_ZONAS = {
    "centro_comercial": {
        "mezcla": [0.42, 0.48, 0.06, 0.010, 0.020, 0.008, 0.002],
        "hurto": 0.030,
    },
    "industrial": {
        "mezcla": [0.34, 0.24, 0.32, 0.055, 0.020, 0.010, 0.015],
        "hurto": 0.025,
    },
    "residencial_consolidado": {
        "mezcla": [0.92, 0.055, 0.004, 0.000, 0.012, 0.008, 0.001],
        "hurto": 0.020,
    },
    "periferia": {          # asentamientos con alta PNT
        "mezcla": [0.955, 0.032, 0.003, 0.000, 0.005, 0.005, 0.000],
        "hurto": 0.115,
    },
}
_ZONA_PROPORCION = {
    "centro_comercial": 0.12,
    "industrial": 0.10,
    "residencial_consolidado": 0.48,
    "periferia": 0.30,
}

_HURTO_TIPOS = ["caida_recuperacion", "cero_activo", "ruptura_nivel",
                "planitud", "bajo_grupo"]


@dataclass
class EscenarioCosta:
    """Escenario costero completo con la verdad conocida para poder validar."""

    directorio: Path
    csv_consumos: Path
    csv_cabecera: Path
    csv_multados: Path
    csv_sig: Path
    csv_jerarquia: Path
    redes: dict[str, NetworkModel]              # alimentador -> red
    head_energy_kwh: dict[str, float]           # alimentador -> kWh de cabecera
    padron: pd.DataFrame                        # con metadatos _hurto, _zona
    hurtos_reales: list[str] = field(default_factory=list)
    multados: list[str] = field(default_factory=list)
    transferencia: dict = field(default_factory=dict)
    clientes_sin_sig: list[str] = field(default_factory=list)
    zonas_por_ruta: dict[str, str] = field(default_factory=dict)
    resumen: dict = field(default_factory=dict)


def _fmt_kwh(v: float) -> str:
    """Formato del archivo real: miles con '.', sin decimales."""

    return f"{int(round(max(v, 0))):,}".replace(",", ".")


def _serie_costa(
    base: float, cv: float, n_meses: int, mes_inicial: int, rng: np.random.Generator
) -> np.ndarray:
    """Serie mensual con estacionalidad costera y ruido multiplicativo."""

    idx = (np.arange(n_meses) + mes_inicial) % 12
    estacional = ESTACIONALIDAD_COSTA[idx]
    ruido = rng.normal(1.0, cv * 0.18, n_meses)
    return np.clip(base * estacional * ruido, 0.0, None)


def _inyectar_hurto(serie: np.ndarray, tipo: str, rng: np.random.Generator) -> np.ndarray:
    n = serie.size
    s = serie.copy()
    if tipo == "caida_recuperacion":
        ini = int(rng.integers(n // 3, n // 2))
        fin = min(ini + int(rng.integers(5, 10)), n - 2)
        s[ini:fin] *= rng.uniform(0.15, 0.35)
    elif tipo == "cero_activo":
        ini = int(rng.integers(n // 2, n - 5))
        s[ini:] = 0.0
    elif tipo == "ruptura_nivel":
        pt = int(rng.integers(n // 3, 2 * n // 3))
        s[pt:] *= rng.uniform(0.30, 0.55)
    elif tipo == "planitud":
        s[:] = float(np.mean(s)) * (1 + rng.normal(0, 0.008, n))
    elif tipo == "bajo_grupo":
        s[:] *= rng.uniform(0.10, 0.25)
    return np.clip(s, 0.0, None)


def build_escenario_costa(
    directorio: str | Path = "data/costa20k",
    *,
    n_clientes: int = 20_000,
    n_meses: int = 36,
    n_alimentadores: int = 12,
    rutas_por_alimentador: int = 14,
    transformadores_por_alimentador: int = 22,
    meses_cabecera: int = 12,
    pct_multados_de_hurtos: float = 0.55,
    pct_sin_sig: float = 0.025,
    mes_final: str = "2026-05-01",
    semilla: int = 20260808,
) -> EscenarioCosta:
    """Construye el escenario costero de 20 000 clientes y lo escribe en disco."""

    d = Path(directorio)
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(semilla)

    alimentadores = [f"GYE-{i:02d}" for i in range(1, n_alimentadores + 1)]

    # Jerarquía organizacional: los alimentadores se reparten entre subestaciones
    # reales del área, y estas entre unidades de negocio. Es la estructura con la
    # que se decide el presupuesto (UN) y se coordina la operación (S/E).
    _SUBESTACIONES = [
        ("GUAYAQUIL", "Pascuales"), ("GUAYAQUIL", "Nueva Prosperina"),
        ("GUAYAQUIL", "Trinitaria"), ("GUAYAQUIL", "Esclusas"),
        ("MILAGRO", "Durán Sur"), ("MILAGRO", "Milagro Centro"),
    ]
    org: dict[str, tuple[str, str]] = {}
    for i, a in enumerate(alimentadores):
        org[a] = _SUBESTACIONES[i % len(_SUBESTACIONES)]

    # --- 1. Zonificación: cada ruta de lectura pertenece a una zona -----------
    rutas: list[str] = []
    zonas_por_ruta: dict[str, str] = {}
    nombres_zona = list(_ZONAS)
    p_zona = np.array([_ZONA_PROPORCION[z] for z in nombres_zona])
    for ali in alimentadores:
        for r in range(1, rutas_por_alimentador + 1):
            ruta = f"{ali}-R{r:02d}"
            rutas.append(ruta)
            zonas_por_ruta[ruta] = str(rng.choice(nombres_zona, p=p_zona))

    # Cada alimentador ocupa una franja del territorio: así los clientes de un
    # mismo alimentador quedan geográficamente juntos y los sectores (clustering
    # espacial) coinciden con la realidad eléctrica.
    ancho = (UTM_X1 - UTM_X0) / n_alimentadores
    centro_ali = {
        a: (UTM_X0 + ancho * (i + 0.5), (UTM_Y0 + UTM_Y1) / 2)
        for i, a in enumerate(alimentadores)
    }

    # --- 2. Padrón comercial --------------------------------------------------
    mes_ini = int(mes_final.split("-")[1]) - n_meses  # mes calendario de KWH_1
    filas = []
    for i in range(n_clientes):
        ruta = str(rng.choice(rutas))
        ali = ruta.rsplit("-R", 1)[0]
        zona = zonas_por_ruta[ruta]
        clase = str(rng.choice(_CLASES, p=_ZONAS[zona]["mezcla"]))

        base, cv = _NIVEL_KWH[clase]
        base *= float(rng.lognormal(0.0, cv * 0.5))
        serie = _serie_costa(base, cv, n_meses, mes_ini, rng)

        es_hurto = rng.random() < _ZONAS[zona]["hurto"]
        tipo = ""
        if es_hurto:
            tipo = _HURTO_TIPOS[int(rng.integers(0, len(_HURTO_TIPOS)))]
            serie = _inyectar_hurto(serie, tipo, rng)

        # Coordenada: dispersa alrededor del centro del alimentador, con un
        # desplazamiento propio de la ruta para que las rutas sean compactas.
        cx, cy = centro_ali[ali]
        r_idx = int(ruta.rsplit("R", 1)[1])
        rx = cx + (r_idx - rutas_por_alimentador / 2) * (ancho / rutas_por_alimentador)
        ry = cy + np.sin(r_idx) * 6_000.0
        x = float(np.clip(rx + rng.normal(0, 380), UTM_X0, UTM_X1))
        y = float(np.clip(ry + rng.normal(0, 380), UTM_Y0, UTM_Y1))

        fases = 1 if clase in ("RESIDENCIAL", "ASISTENCIA") else (
            3 if clase in ("INDUSTRIAL_BT", "INDUSTRIAL_MT", "BOMBEO") else 2)

        # Tarifa Dignidad solo si el consumo habitual está bajo el techo
        tarifas = list(_TARIFAS[clase])
        if clase == "RESIDENCIAL" and np.percentile(serie, 75) > TARIFA_DIGNIDAD_KWH:
            tarifas = [t for t in tarifas if "DIGNIDAD" not in t]

        suspendido = (not es_hurto) and rng.random() < 0.025
        if suspendido:
            serie[-int(rng.integers(3, 8)):] = 0.0

        fila = {
            "DIVISION": ali,
            "CUENTACONTRATO": f"30{i:09d}",
            "NOMBRE": f"CLIENTE {i:05d}",
            "DESTARI": str(rng.choice(tarifas)),
            "ZZUTM_X": f"{x:.6f}",
            "ZZUTM_Y": f"{y:.6f}",
            "CLIRLSCOD": ruta,
            "CDAFAS": str(fases),
            "EDCCOD": "SUSP" if suspendido else "ACT",
            "CLIULTCONM": _fmt_kwh(serie[-1]),
            # El SIG calcula la potencia con el ÚLTIMO mes: ese es el error que el
            # sistema corrige, así que el escenario debe reproducirlo.
            "POTENCIAACTIVA": f"{serie[-1] / (30 * 24) * rng.uniform(2.0, 6.0):.3f}",
            "POTENCIAREACTIVA": f"{serie[-1] / (30 * 24) * rng.uniform(1.0, 3.0):.3f}",
            "_hurto": int(es_hurto),
            "_hurto_tipo": tipo,
            "_zona": zona,
            "_clase_real": clase,
            "_alimentador": ali,
        }
        for m in range(n_meses):
            fila[f"KWH_{m + 1}"] = _fmt_kwh(serie[m])
        filas.append(fila)

    padron = pd.DataFrame(filas)
    csv_consumos = d / "consumos_costa_36m.csv"
    cols_reales = [c for c in padron.columns if not c.startswith("_")]
    padron[cols_reales].to_csv(csv_consumos, sep=";", index=False, encoding="latin-1")

    hurtos = padron.loc[padron["_hurto"] == 1, "CUENTACONTRATO"].astype(str).tolist()

    # --- 3. Base de multados: solo una fracción de los hurtos reales ----------
    n_mult = max(1, int(len(hurtos) * pct_multados_de_hurtos))
    multados = sorted(rng.choice(hurtos, size=n_mult, replace=False).tolist())
    csv_multados = d / "multados_costa.csv"
    pd.DataFrame({
        "contract_account": multados,
        "fecha_multa": pd.date_range("2023-01-15", periods=len(multados),
                                     freq="12h").date.astype(str),
        "kwh_recuperado": rng.uniform(300, 25_000, len(multados)).round(0),
        "tipo_hallazgo": rng.choice(
            ["Conexión directa", "Medidor manipulado", "Puente en acometida",
             "Alteración de sellos", "Medidor invertido"], len(multados)),
    }).to_csv(csv_multados, index=False)

    # --- 4. Redes por alimentador, cargadas con el padrón REAL ---------------
    # La red sintética nace con clientes propios; si se la dejara así, el balance
    # de cada alimentador sería idéntico al de los demás y la focalización por
    # ALIMENTADOR no podría discriminar nada. Se reemplaza la carga de cada nodo
    # por los clientes reales del padrón asignados a ese alimentador, de modo que
    # la PNT de cada alimentador refleje la concentración de hurto de sus zonas
    # —que es justamente lo que el análisis debe encontrar—.
    redes: dict[str, NetworkModel] = {}
    head_kwh: dict[str, float] = {}
    facturado_kwh: dict[str, float] = {}
    kwh_ultimo = (
        padron["KWH_36"].str.replace(".", "", regex=False).astype(float).to_numpy()
    )
    padron["_kwh_mes"] = kwh_ultimo

    for i, a in enumerate(alimentadores):
        sub = padron[padron["_alimentador"] == a]
        net = generate_radial_network(
            feeder_code=a,
            n_transformers=transformadores_por_alimentador,
            customers_per_tx=max(4, min(len(sub) // transformadores_por_alimentador, 45)),
            seed=semilla + i,
        )
        modelo = net.model
        nodos = list(modelo.customer_nodes)
        if nodos and len(sub) > 0:
            # Reparto por bloques contiguos: los clientes de una misma ruta caen
            # bajo el mismo transformador, como en la red real.
            registros = sub.sort_values("CLIRLSCOD")
            trozos = np.array_split(np.arange(len(registros)), len(nodos))
            for nodo, idx in zip(nodos, trozos):
                if len(idx) == 0:
                    modelo.customer_nodes[nodo] = []
                    continue
                blq = registros.iloc[idx]
                modelo.customer_nodes[nodo] = [
                    {
                        "customer_id": str(cta),
                        "energy_kwh": float(kwh),
                        "phase": int(f) if str(f).isdigit() else 1,
                        "tariff": str(tar),
                        "meter_type": "Electrónico",
                    }
                    for cta, kwh, f, tar in zip(
                        blq["CUENTACONTRATO"], blq["_kwh_mes"],
                        blq["CDAFAS"], blq["DESTARI"])
                ]

        facturado = float(sub["_kwh_mes"].sum())
        # La energía hurtada no se factura pero sí entra por la cabecera: esa es
        # la PNT que el balance debe recuperar. Se mide contra el nivel habitual
        # de CADA cliente (percentil alto de su propia serie) y no contra la media
        # del alimentador: un solo industrial grande desplazaría esa media y la
        # PNT del alimentador quedaría inventada.
        hurtados = sub[sub["_hurto"] == 1]
        pnt_real = 0.0
        if len(hurtados):
            series_h = hurtados[[f"KWH_{k}" for k in range(1, n_meses + 1)]].apply(
                lambda c: c.str.replace(".", "", regex=False).astype(float))
            base_propia = np.percentile(series_h.to_numpy(), 75, axis=1)
            pnt_real = float(
                np.clip(base_propia - hurtados["_kwh_mes"].to_numpy(), 0, None).sum())
        # Pérdidas técnicas típicas de distribución, sobre las que el flujo de
        # potencia calculará su propio valor a partir de la red.
        tecnicas = facturado * rng.uniform(0.055, 0.075)
        redes[a] = modelo
        facturado_kwh[a] = facturado
        head_kwh[a] = facturado + pnt_real + tecnicas

    # --- 5. Energía de cabecera, coherente con el balance de cada red -------
    # Se deriva de `head_kwh`, que ya incorpora el facturado real, la PNT
    # efectivamente inyectada y las pérdidas técnicas. Si la cabecera se generara
    # aparte, el balance no cerraría y la PNT calculada sería un artefacto del
    # escenario en vez de una medición.
    periodos = pd.date_range(end=mes_final, periods=meses_cabecera, freq="MS")
    # Se toman dos alimentadores del medio de la lista, acotando el índice: con
    # pocos alimentadores (escenarios reducidos de prueba) un índice fijo se sale
    # del rango.
    i_org = min(3, len(alimentadores) - 2)
    f_origen, f_destino = alimentadores[i_org], alimentadores[i_org + 1]
    mes_transfer = periodos[len(periodos) // 2]
    # La magnitud se acota para que la cabecera del origen NO caiga por debajo de
    # su facturado: como el padrón comercial no migra de alimentador junto con la
    # carga, una transferencia demasiado grande dejaría al origen con PNT negativa
    # —un artefacto del escenario, no un hallazgo—.
    margen = float(head_kwh[f_origen] - facturado_kwh[f_origen] * 1.02)
    magnitud = max(min(float(head_kwh[f_origen]) * 0.22, margen), 0.0)

    fila_cab = []
    # La estacionalidad se aplica de forma RELATIVA al mes final: `head_kwh` se
    # derivó del consumo de ese mes, que ya la trae incorporada. Multiplicarla otra
    # vez la contaría dos veces y en los meses de valle la cabecera caería por
    # debajo del facturado.
    est_final = ESTACIONALIDAD_COSTA[int(mes_final.split("-")[1]) - 1]
    for p in periodos:
        est = ESTACIONALIDAD_COSTA[p.month - 1] / est_final
        for a in alimentadores:
            v = head_kwh[a] * est * (1 + rng.normal(0, 0.012))
            if p >= mes_transfer:
                if a == f_origen:
                    v -= magnitud
                elif a == f_destino:
                    v += magnitud
            fila_cab.append({"feeder_code": a, "period": p.date().isoformat(),
                             "kwh_delivered": round(v, 1)})
    csv_cabecera = d / "cabecera_costa.csv"
    pd.DataFrame(fila_cab).to_csv(csv_cabecera, index=False)

    # --- 6. SIG deliberadamente incompleto ------------------------------------
    cuentas = sorted(padron["CUENTACONTRATO"].astype(str).tolist())
    n_sin = max(1, int(len(cuentas) * pct_sin_sig))
    sin_sig = sorted(rng.choice(cuentas, size=n_sin, replace=False).tolist())
    en_sig = [c for c in cuentas if c not in set(sin_sig)]
    fantasmas = [f"9999{i:07d}" for i in range(40)]   # retiros no depurados
    csv_sig = d / "sig_clientes_costa.csv"
    pd.DataFrame({"contract_account": en_sig + fantasmas}).to_csv(csv_sig, index=False)

    # --- 7. Catálogo organizacional ------------------------------------------
    csv_jerarquia = d / "jerarquia_costa.csv"
    pd.DataFrame([
        {"feeder_code": a, "subestacion": org[a][1], "unidad_negocio": org[a][0],
         "nombre": f"Alimentador {a}", "tension_kv": 13.8}
        for a in alimentadores
    ]).to_csv(csv_jerarquia, index=False)

    resumen = {
        "region": "Costa — área Guayaquil/Durán (UTM 17S, EPSG:32717)",
        "clientes": n_clientes,
        "meses_consumo": n_meses,
        "alimentadores": n_alimentadores,
        "rutas_comerciales": len(rutas),
        "transformadores": n_alimentadores * transformadores_por_alimentador,
        "hurtos_inyectados": len(hurtos),
        "tasa_hurto_real_pct": round(len(hurtos) / n_clientes * 100, 2),
        "multados_registrados": len(multados),
        "pct_hurtos_detectados_historicamente": round(
            len(multados) / max(len(hurtos), 1) * 100, 1),
        "meses_cabecera": meses_cabecera,
        "transferencia": f"{f_origen} → {f_destino} en {mes_transfer.date()} "
                         f"({magnitud:,.0f} kWh)",
        "clientes_sin_sig": len(sin_sig),
        "clientes_sig_sin_facturacion": len(fantasmas),
        "unidades_de_negocio": len({v[0] for v in org.values()}),
        "subestaciones": len({v[1] for v in org.values()}),
        "estacionalidad": "costera (pico ene–abr por climatización)",
        "tarifa_dignidad_kwh": TARIFA_DIGNIDAD_KWH,
    }

    return EscenarioCosta(
        directorio=d, csv_consumos=csv_consumos, csv_cabecera=csv_cabecera,
        csv_multados=csv_multados, csv_sig=csv_sig,
        csv_jerarquia=csv_jerarquia,
        redes=redes, head_energy_kwh=head_kwh, padron=padron,
        hurtos_reales=hurtos, multados=multados,
        transferencia={"origen": f_origen, "destino": f_destino,
                       "periodo": str(mes_transfer.date()),
                       "magnitud_kwh": magnitud},
        clientes_sin_sig=sin_sig, zonas_por_ruta=zonas_por_ruta, resumen=resumen,
    )
