"""Pruebas de transferencias, clientes faltantes, incoherencias, base de multados
y focalización por ruta comercial (CLIRLSCOD)."""

import numpy as np
import pandas as pd
import pytest

from ptnt.anomalies.coherence import Severidad, analyze_feeder_coherence
from ptnt.anomalies.transfers import TransferDetectionStatus, detect_transfers
from ptnt.anomalies.unmatched import analyze_unmatched_customers
from ptnt.ntl.confirmed import (
    calibrate_signal_thresholds,
    load_confirmed_theft,
    pu_learning,
    validate_against_confirmed,
)
from ptnt.survey.routes import analyze_commercial_routes, routes_to_target_input
from ptnt.survey.targeting import TargetLevel, build_survey_plan


# --------------------------------------------------------------------------- #
# Transferencias entre alimentadores (§10.4)
# --------------------------------------------------------------------------- #
def _cabecera_con_transferencia() -> pd.DataFrame:
    filas = []
    base = {"F001": 500_000.0, "F002": 400_000.0, "F003": 350_000.0}
    periodos = pd.date_range("2025-10-01", periods=8, freq="MS")
    for p in periodos:
        for f, b in base.items():
            v = b
            if p >= pd.Timestamp("2026-03-01"):
                if f == "F002":
                    v -= 80_000        # pierde
                if f == "F003":
                    v += 80_000        # el vecino gana
            filas.append({"feeder_code": f, "period": p.date().isoformat(),
                          "kwh_delivered": v})
    return pd.DataFrame(filas)


@pytest.mark.unit
def test_detecta_transferencia_inyectada():
    rep = detect_transfers(_cabecera_con_transferencia())
    assert rep.status == TransferDetectionStatus.OK
    assert rep.candidates, "debe detectar la transferencia inyectada"
    c = rep.candidates[0]
    assert {c.feeder_a, c.feeder_b} == {"F002", "F003"}
    assert c.period.startswith("2026-03")
    assert c.similarity > 0.8
    assert {"F002", "F003"} <= rep.feeders_afectados


def _cabecera_realista(magnitud: float = 140_000.0) -> pd.DataFrame:
    """Alimentadores de tamaños muy distintos, con estacionalidad y ruido.

    Reproduce lo que rompió la detección en el escenario de 20 000 clientes: el
    fixture simple usa alimentadores parecidos y sin estacionalidad, y ahí
    cualquier criterio de simetría funciona. En la realidad se descarga el
    alimentador saturado sobre el que tiene margen, que casi siempre es de otro
    tamaño.
    """

    rng = np.random.default_rng(20260810)
    tam = {"GYE-01": 1_050_000.0, "GYE-02": 2_800_000.0, "GYE-03": 900_000.0,
           "GYE-04": 1_600_000.0, "GYE-05": 2_100_000.0}
    estacion = [1.00, 1.06, 1.14, 1.18, 1.10, 0.98, 0.92, 0.90, 0.93, 0.97, 1.02, 1.05]
    periodos = pd.date_range("2025-06-01", periods=12, freq="MS")
    corte = pd.Timestamp("2025-12-01")

    filas = []
    for p in periodos:
        est = estacion[p.month - 1]
        for f, b in tam.items():
            v = b * est * (1 + rng.normal(0, 0.012))
            if p >= corte:
                if f == "GYE-01":
                    v -= magnitud
                elif f == "GYE-02":
                    v += magnitud
            filas.append({"feeder_code": f, "period": p.date().isoformat(),
                          "kwh_delivered": round(v, 1)})
    return pd.DataFrame(filas)


@pytest.mark.unit
def test_detecta_la_transferencia_entre_un_alimentador_grande_y_uno_chico():
    """Regresión del escenario de 20 000 clientes.

    La simetría se medía sobre kWh absolutos, y un alimentador de 2,8 GWh se
    desvía del patrón común por su cuenta en cientos de miles de kWh. Con eso, el
    par real daba simetría 0,46 contra el 0,60 exigido y **se descartaba**: la
    maniobra existía, era visible en el residuo, y el detector la tiraba.

    Es justo el caso habitual: se descarga el alimentador saturado sobre el que
    tiene margen, y rara vez son del mismo tamaño.
    """

    # Umbral por defecto: la transferencia inyectada (140 000 kWh ≈ 13 % del
    # alimentador chico) está claramente por encima del piso de ruido.
    rep = detect_transfers(_cabecera_realista())

    par = [c for c in rep.candidates
           if {c.feeder_a, c.feeder_b} == {"GYE-01", "GYE-02"}
           and c.period.startswith("2025-12")]
    assert par, (
        "no encontró la transferencia inyectada entre un alimentador de "
        f"1,05 GWh y uno de 2,8 GWh. Candidatos: "
        f"{[(c.feeder_a, c.feeder_b, c.period) for c in rep.candidates]}")
    c = par[0]
    # La magnitud pondera cada alimentador por su propio ruido: el grande mide
    # la misma transferencia con mucho menos precisión y no puede pesar igual.
    assert 100_000 <= c.magnitude_kwh <= 190_000, (
        f"magnitud estimada {c.magnitude_kwh:,.0f} lejos de los 140 000 reales")


@pytest.mark.unit
def test_una_serie_sin_maniobras_no_produce_candidatos():
    """La holgura por ruido no puede convertirse en una puerta abierta: si nadie
    transfirió nada, no puede aparecer ningún par."""

    limpia = _cabecera_realista(magnitud=0.0)
    rep = detect_transfers(limpia)
    assert not rep.candidates, (
        f"inventó {len(rep.candidates)} transferencia(s) donde no hubo ninguna: "
        f"{[(c.feeder_a, c.feeder_b, c.period) for c in rep.candidates]}")


@pytest.mark.unit
def test_el_piso_de_deteccion_se_declara():
    """«No se detectó nada» es ambiguo sin el piso: puede ser que no hubo
    maniobras, o que las hubo y eran más chicas que el ruido. Solo lo segundo
    justifica pedir más meses de cabecera."""

    rep = detect_transfers(_cabecera_realista(magnitud=0.0), cambio_min_pct=8.0)

    assert rep.piso is not None
    assert rep.piso.ruido_pct > 0
    assert rep.piso.detectable_pct >= 8.0, "nunca por debajo del umbral exigido"
    assert rep.piso.detectable_kwh > 0
    assert "pasaría inadvertida" in rep.detail


@pytest.mark.unit
def test_un_solo_mes_no_permite_detectar():
    """Con un solo mes de cabecera la detección no es posible: debe declararlo,
    no inventar candidatos (§10.4)."""

    df = pd.DataFrame([
        {"feeder_code": "F001", "period": "2026-05-01", "kwh_delivered": 100.0},
        {"feeder_code": "F002", "period": "2026-05-01", "kwh_delivered": 200.0},
    ])
    rep = detect_transfers(df)
    assert rep.status == TransferDetectionStatus.NO_APLICABLE_POR_DATOS
    assert not rep.candidates


@pytest.mark.unit
def test_sin_cabecera_no_aplica():
    rep = detect_transfers(pd.DataFrame())
    assert rep.status == TransferDetectionStatus.NO_APLICABLE_POR_DATOS


@pytest.mark.unit
def test_pico_transitorio_no_es_transferencia():
    """Un cambio que revierte al mes siguiente es un pico, no una transferencia."""

    filas = []
    for i, p in enumerate(pd.date_range("2025-10-01", periods=6, freq="MS")):
        a, b = 400_000.0, 350_000.0
        if i == 3:                      # solo un mes
            a -= 80_000
            b += 80_000
        filas.append({"feeder_code": "FA", "period": p.date().isoformat(), "kwh_delivered": a})
        filas.append({"feeder_code": "FB", "period": p.date().isoformat(), "kwh_delivered": b})
    rep = detect_transfers(pd.DataFrame(filas), exigir_sostenido=True)
    assert not rep.candidates


# --------------------------------------------------------------------------- #
# Clientes faltantes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_clientes_faltantes_en_ambos_sentidos():
    comercial = pd.DataFrame({
        "contract_account": ["A", "B", "C", "D"],
        "kwh_representativo": [100.0, 200.0, 300.0, 400.0],
        "grupo_lectura": ["R1", "R1", "R2", "R2"],
    })
    sig = pd.DataFrame({"contract_account": ["A", "B", "X"]})
    rep = analyze_unmatched_customers(comercial, sig)
    # C y D facturan pero no están en el SIG
    assert set(rep.csv_sin_sig["contract_account"]) == {"C", "D"}
    # X está en el SIG pero no factura
    assert set(rep.sig_sin_csv["contract_account"]) == {"X"}
    # energía vinculada = (100+200)/1000 = 30 %
    assert rep.pct_energia_vinculada == pytest.approx(30.0)
    assert rep.energia_sin_vincular_kwh == pytest.approx(700.0)


@pytest.mark.unit
def test_energia_vinculada_baja_bloquea_balance_medido():
    comercial = pd.DataFrame({
        "contract_account": ["A", "B"], "kwh_representativo": [100.0, 900.0],
    })
    sig = pd.DataFrame({"contract_account": ["A"]})     # solo el 10 % de la energía
    rep = analyze_unmatched_customers(comercial, sig, umbral_energia_vinculada_pct=95.0)
    assert rep.apto_balance_medido is False
    assert "INDICATIVO" in rep.detail


@pytest.mark.unit
def test_faltantes_se_concentran_por_ruta():
    comercial = pd.DataFrame({
        "contract_account": ["A", "B", "C", "D"],
        "kwh_representativo": [100.0, 100.0, 100.0, 100.0],
        "grupo_lectura": ["R1", "R1", "R2", "R2"],
    })
    sig = pd.DataFrame({"contract_account": ["C"]})
    rep = analyze_unmatched_customers(comercial, sig)
    assert not rep.por_ruta.empty
    top = rep.por_ruta.iloc[0]
    assert top["grupo_lectura"] == "R1" and top["cuentas_sin_sig"] == 2


# --------------------------------------------------------------------------- #
# Alimentadores incoherentes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_pnt_negativa_bloquea_publicacion():
    from ptnt.balance.energy_balance import compute_balance
    from ptnt.config.models import BalanceConfig

    b = compute_balance("F001", cfg=BalanceConfig(), e_billed_kwh=900,
                        loss_technical_kwh=200, e_input_kwh=1000,
                        e_own_use_kwh=0, e_not_supplied_kwh=0)
    rep = analyze_feeder_coherence([b])
    f = rep.feeders[0]
    assert f.es_incoherente
    assert f.publicable is False
    assert f.excluir_de_ranking is True
    assert f.severidad_maxima == Severidad.BLOQUEANTE.value
    # la causa probable debe encabezarse por la transferencia (§10.6)
    c01 = next(i for i in f.incoherencias if i.codigo == "C01")
    assert "transferencia" in c01.causa_probable.lower()


@pytest.mark.unit
def test_transferencia_excluye_del_ranking():
    from ptnt.balance.energy_balance import compute_balance
    from ptnt.config.models import BalanceConfig

    b = compute_balance("F002", cfg=BalanceConfig(), e_billed_kwh=800,
                        loss_technical_kwh=100, e_input_kwh=1000)
    tr = detect_transfers(_cabecera_con_transferencia())
    rep = analyze_feeder_coherence([b], transfer_report=tr)
    f = rep.feeders[0]
    assert f.excluir_de_ranking is True
    assert any(i.codigo == "TRANSFER" for i in f.incoherencias)


# --------------------------------------------------------------------------- #
# Base de multados: validación y calibración
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_validacion_mide_lift_real():
    """Con un detector que sí ordena bien, el lift debe ser claramente > 1."""

    n = 1000
    cuentas = [f"C{i:04d}" for i in range(n)]
    # los 50 primeros del ranking son hurtos confirmados
    ranking = pd.DataFrame({
        "contract_account": cuentas,
        "score": np.linspace(1.0, 0.0, n),
    })
    confirmados = load_confirmed_theft(
        pd.DataFrame({"contract_account": cuentas[:50]}), fecha_col=None
    )
    m = validate_against_confirmed(ranking, confirmados)
    assert m.n_confirmados_en_universo == 50
    assert m.precision_at_k[5] == pytest.approx(1.0)   # los 50 caben en el top 5 %
    assert m.lift_at_k[5] > 5
    assert m.auc_aproximada > 0.9
    assert m.posicion_mediana_pct < 10


@pytest.mark.unit
def test_detector_aleatorio_tiene_lift_cercano_a_uno():
    """Control negativo: un ranking aleatorio no debe mostrar lift alto."""

    rng = np.random.default_rng(1)
    n = 2000
    cuentas = [f"C{i:04d}" for i in range(n)]
    ranking = pd.DataFrame({"contract_account": cuentas, "score": rng.random(n)})
    confirmados = load_confirmed_theft(
        pd.DataFrame({"contract_account": list(rng.choice(cuentas, 100, replace=False))}),
        fecha_col=None,
    )
    m = validate_against_confirmed(ranking, confirmados)
    assert 0.3 < m.lift_at_k[10] < 2.5     # alrededor de 1
    assert 0.4 < m.auc_aproximada < 0.6


@pytest.mark.unit
def test_fecha_de_corte_evita_fuga_temporal():
    df = pd.DataFrame({
        "contract_account": ["A", "B", "C"],
        "fecha_multa": ["2023-05-01", "2024-06-01", "2026-01-01"],
    })
    conf = load_confirmed_theft(df, fecha_corte="2025-01-01")
    assert conf.cuentas == {"A", "B"}       # C es posterior al corte
    assert any("fuga temporal" in a for a in conf.advertencias)


@pytest.mark.unit
def test_calibracion_identifica_senal_que_no_discrimina():
    n = 400
    cuentas = [f"C{i}" for i in range(n)]
    hurtos = set(cuentas[:40])
    rng = np.random.default_rng(2)
    señales = pd.DataFrame({
        "contract_account": cuentas,
        # S_buena se activa casi solo en los hurtos
        "S_buena": [1.0 if c in hurtos else 0.0 for c in cuentas],
        # S_inutil se activa al azar por igual
        "S_inutil": rng.integers(0, 2, n).astype(float),
    })
    conf = load_confirmed_theft(pd.DataFrame({"contract_account": list(hurtos)}),
                                fecha_col=None)
    calib = calibrate_signal_thresholds(señales, conf,
                                        columnas_senal=["S_buena", "S_inutil"])
    buena = calib[calib["senal"] == "S_buena"].iloc[0]
    inutil = calib[calib["senal"] == "S_inutil"].iloc[0]
    assert buena["lift"] is None or buena["lift"] > 5
    assert inutil["lift"] < 2
    assert "desactivar" in inutil["recomendacion"] or "Revisar" in inutil["recomendacion"]


@pytest.mark.unit
def test_pu_learning_no_trata_no_etiquetados_como_negativos():
    """El estimador PU debe devolver una propensión < 1: los no multados no son
    negativos confiables."""

    pytest.importorskip("sklearn")
    rng = np.random.default_rng(4)
    n = 600
    cuentas = [f"C{i}" for i in range(n)]
    # feature informativa: los primeros 120 son "de riesgo", pero solo 60 multados
    riesgo = np.concatenate([rng.normal(3, 1, 120), rng.normal(0, 1, n - 120)])
    features = pd.DataFrame({"contract_account": cuentas, "riesgo": riesgo,
                             "ruido": rng.normal(0, 1, n)})
    conf = load_confirmed_theft(pd.DataFrame({"contract_account": cuentas[:60]}),
                                fecha_col=None)
    res = pu_learning(features, conf)
    assert res is not None
    assert res.n_positivos == 60
    assert 0 < res.c_propension <= 1.0
    assert len(res.scores) == n


# --------------------------------------------------------------------------- #
# Ruta comercial (CLIRLSCOD) como nivel de levantamiento
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rutas_por_sospecha():
    clientes = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(20)],
        "grupo_lectura": ["R1"] * 10 + ["R2"] * 10,
        "kwh_representativo": [200.0] * 20,
    })
    sospechosos = {f"C{i}" for i in range(6)}      # 6 de 10 en R1
    rutas = analyze_commercial_routes(clientes, None, suspect_customers=sospechosos)
    r1 = next(r for r in rutas if r.route_id == "R1")
    r2 = next(r for r in rutas if r.route_id == "R2")
    assert r1.densidad_sospecha == pytest.approx(0.6)
    assert r2.densidad_sospecha == pytest.approx(0.0)
    assert rutas[0].route_id == "R1"


@pytest.mark.unit
def test_ruta_incoherente_por_ceros_y_estimadas():
    """Una ruta con lecturas masivas en cero es un problema de gestión que hay que
    levantar aunque no dispare señales de hurto por cliente."""

    cuentas = [f"C{i}" for i in range(10)]
    clientes = pd.DataFrame({
        "contract_account": cuentas,
        "grupo_lectura": ["R9"] * 10,
        "kwh_representativo": [100.0] * 10,
    })
    consumo = pd.DataFrame({
        "contract_account": np.repeat(cuentas, 12),
        "is_zero": [True] * 60 + [False] * 60,      # 50 % de ceros
        "is_estimated": [True] * 60 + [False] * 60,
    })
    rutas = analyze_commercial_routes(clientes, consumo, suspect_customers=set())
    r = rutas[0]
    assert r.incoherencia > 0
    assert any("cero" in m for m in r.motivos)
    assert any("estimadas" in m for m in r.motivos)


@pytest.mark.unit
def test_ruta_comercial_es_nivel_del_plan():
    clientes = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(12)],
        "grupo_lectura": ["RT1"] * 12,
        "kwh_representativo": [150.0] * 12,
    })
    rutas = analyze_commercial_routes(
        clientes, None, suspect_customers={"C0", "C1", "C2"},
        recoverable_by_customer={"C0": 300.0, "C1": 200.0, "C2": 100.0},
    )
    plan = build_survey_plan(route_stats=routes_to_target_input(rutas))
    objetivos = plan.by_level(TargetLevel.RUTA_COMERCIAL)
    assert objetivos, "la ruta comercial debe ser un nivel del plan"
    t = objetivos[0]
    assert t.entity_id == "RT1"
    assert "ruta comercial" in t.action.lower()
    assert t.recoverable_kwh_month == pytest.approx(600.0)
