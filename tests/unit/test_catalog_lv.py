"""Pruebas del catálogo de estructuras (CATALOGOESTRUCTURA), la agregación de baja
tensión al transformador, el totalizador y semáforos/cámaras como AP no medido."""

import pytest

from ptnt.ref.structure_catalog import (
    ElementCategory,
    classify_structure,
    load_structure_catalog,
)
from ptnt.losses.transformers import BankConfig
from ptnt.grid.lv_aggregation import aggregate_to_transformers
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph


# --------------------------------------------------------------------------- #
# Catálogo de estructuras
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("code,cat", [
    ("APO0521", ElementCategory.ALUMBRADO),
    ("AOD0087", ElementCategory.ALUMBRADO),
    ("CSP0007", ElementCategory.SEMAFORO_CAMARA),
    ("TRV0385", ElementCategory.TRANSFORMADOR),
    ("TUT0085", ElementCategory.TRANSFORMADOR),
    ("COO0005", ElementCategory.CONDUCTOR),
    ("ECT0067", ElementCategory.CAPACITOR),
    ("SPT0119", ElementCategory.SECCIONADOR),
    ("MED0010", ElementCategory.MEDIDOR),
    ("POO9004", ElementCategory.POSTE),
    ("SDD0036", ElementCategory.GENERACION),
])
def test_clasificacion_por_prefijo(code, cat):
    assert classify_structure(code) == cat


@pytest.mark.unit
def test_catalogo_carga_y_potencias():
    cat = load_structure_catalog("config/catalogo_estructura.csv")
    assert len(cat) > 5000
    # transformador: kVA y configuración de banco desde la descripción
    assert cat.transformer_kva("TRV0385") == 960.0
    assert cat.get("TUT0085").bank_config() == BankConfig.BANCO_3
    # alumbrado: potencia de lámpara y pérdida de balastro
    assert cat.lamp_power_w("APO0521") == 400.0
    assert cat.ballast_loss_w("APO0521") == 48.0
    # semáforo/cámara
    assert cat.category("CSP0007") == ElementCategory.SEMAFORO_CAMARA
    assert cat.lamp_power_w("CSP0007") == 50.0
    # capacitor: kVAR
    assert cat.capacitor_kvar("ECT0067") == 1200.0


@pytest.mark.unit
def test_doble_nivel_detectado():
    cat = load_structure_catalog("config/catalogo_estructura.csv")
    item = cat.get("APO0371")
    assert item.is_double_level
    assert item.power2 > 0


# --------------------------------------------------------------------------- #
# Agregación de baja tensión al transformador
# --------------------------------------------------------------------------- #
def _red_dos_trafos_en_serie() -> NetworkModel:
    # SRC - MT0(TS0) - MT1(TS1) ; clientes bajo cada uno
    edges = [
        Edge("s0", "SRC", "MT0", "COO0004", 0.3),
        Edge("s1", "MT0", "C0", "COO0050", 0.05, is_lv=True),
        Edge("s2", "MT0", "MT1", "COO0004", 0.3),
        Edge("s3", "MT1", "C1", "COO0050", 0.05, is_lv=True),
    ]
    return NetworkModel(
        "F", "SRC", edges,
        transformer_sites={
            "MT0": {"site_id": "TS0", "kvas": [50], "config": "UNIDAD_SIMPLE", "kva": 50},
            "MT1": {"site_id": "TS1", "kvas": [50], "config": "UNIDAD_SIMPLE", "kva": 50},
        },
        customer_nodes={
            "C0": [{"customer_id": "c0", "energy_kwh": 100}],
            "C1": [{"customer_id": "c1", "energy_kwh": 200}],
        },
    )


@pytest.mark.unit
def test_agregacion_sin_doble_conteo_en_tronco():
    """Con dos trafos en serie, cada cliente cuenta solo en SU puesto (el primero
    aguas arriba), no en todos los de aguas arriba."""

    g = build_radial_graph(_red_dos_trafos_en_serie())
    zones = aggregate_to_transformers(g)
    assert zones["TS0"].energy_billed_kwh == pytest.approx(100)
    assert zones["TS1"].energy_billed_kwh == pytest.approx(200)
    # total sin doble conteo
    total = sum(z.energy_billed_kwh for z in zones.values())
    assert total == pytest.approx(300)


@pytest.mark.unit
def test_totalizador_no_doble_cuenta():
    """Un edificio con totalizador + individuales: solo el totalizador entra en
    facturado; los individuales bajo él no se re-suman."""

    edges = [
        Edge("s0", "SRC", "MT0", "COO0004", 0.3),
        Edge("s1", "MT0", "BLD", "COO0050", 0.02, is_lv=True),
    ]
    model = NetworkModel(
        "F", "SRC", edges,
        transformer_sites={"MT0": {"site_id": "TS0", "kvas": [50], "config": "UNIDAD_SIMPLE", "kva": 50}},
        customer_nodes={"BLD": [
            {"customer_id": "tot", "energy_kwh": 900, "totalizador": True},
            {"customer_id": "u1", "energy_kwh": 250, "is_under_totalizer": True},
            {"customer_id": "u2", "energy_kwh": 250, "is_under_totalizer": True},
            {"customer_id": "u3", "energy_kwh": 250, "is_under_totalizer": True},
        ]},
    )
    g = build_radial_graph(model)
    z = aggregate_to_transformers(g)["TS0"]
    # facturado = solo el totalizador (900), no 900+750
    assert z.energy_billed_kwh == pytest.approx(900)
    assert z.energy_totalizer_kwh == pytest.approx(900)
    assert z.energy_individuals_under_totalizer_kwh == pytest.approx(750)
    # residual N3 = 900 - 750 = 150
    assert z.totalizer_residual_kwh == pytest.approx(150)


@pytest.mark.unit
def test_semaforos_camaras_como_ap_no_medido():
    """Semáforos y cámaras se agregan al AP no medido de la zona."""

    edges = [Edge("s0", "SRC", "MT0", "COO0004", 0.3),
             Edge("s1", "MT0", "LV", "COO0050", 0.02, is_lv=True)]
    model = NetworkModel(
        "F", "SRC", edges,
        transformer_sites={"MT0": {"site_id": "TS0", "kvas": [50], "config": "UNIDAD_SIMPLE", "kva": 50}},
        traffic_light_nodes={"LV": [
            {"streetlight_id": "SEM1", "lamp_w": 50, "hours": 24, "days_month": 30, "is_metered": False},
        ]},
    )
    g = build_radial_graph(model)
    z = aggregate_to_transformers(g)["TS0"]
    assert z.traffic_camera_count == 1
    # 50 W * 24 h * 30 d / 1000 = 36 kWh
    assert z.energy_ap_unmetered_kwh == pytest.approx(36.0)


@pytest.mark.unit
def test_ap_usa_balastro_del_catalogo():
    """Una luminaria con structure_code toma potencia + balastro del catálogo."""

    cat = load_structure_catalog("config/catalogo_estructura.csv")
    edges = [Edge("s0", "SRC", "MT0", "COO0004", 0.3),
             Edge("s1", "MT0", "LV", "COO0050", 0.02, is_lv=True)]
    model = NetworkModel(
        "F", "SRC", edges,
        transformer_sites={"MT0": {"site_id": "TS0", "kvas": [50], "config": "UNIDAD_SIMPLE", "kva": 50}},
        streetlight_nodes={"LV": [
            {"streetlight_id": "L1", "structure_code": "APO0521", "hours": 12, "days_month": 30, "is_metered": False},
        ]},
    )
    g = build_radial_graph(model)
    z = aggregate_to_transformers(g, cat)["TS0"]
    # (400 lámpara + 48 balastro) * 12 * 30 / 1000 = 161.28 kWh
    assert z.energy_ap_unmetered_kwh == pytest.approx((400 + 48) * 12 * 30 / 1000)
