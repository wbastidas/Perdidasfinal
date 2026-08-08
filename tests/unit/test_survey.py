"""Pruebas de la focalización de levantamientos (§11.5) y de la derivación de
impedancias de conductor.

Validan el **requerimiento general** del proyecto: decir dónde hay que ir a hacer
el levantamiento, por alimentador, ramal, transformador y sectores, con resultado
visible y reportable.
"""

import pandas as pd
import pytest

from ptnt.ref.conductor_derive import (
    awg_to_mm2,
    derive_conductor,
    kcmil_to_mm2,
    parse_conductor_description,
)
from ptnt.survey.collect import bind_commercial_customers, collect_branch_stats
from ptnt.survey.sectors import cluster_sectors
from ptnt.survey.targeting import TargetLevel, build_survey_plan
from ptnt.topology.graph import Edge, NetworkModel, build_radial_graph


# --------------------------------------------------------------------------- #
# Derivación de impedancias de conductor (precisión)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("awg,esperado", [
    ("2", 33.6), ("1/0", 53.5), ("2/0", 67.4), ("4/0", 107.2), ("12", 3.31),
])
def test_awg_a_mm2(awg, esperado):
    assert awg_to_mm2(awg) == pytest.approx(esperado, rel=0.01)


@pytest.mark.unit
def test_kcmil_a_mm2():
    assert kcmil_to_mm2(336.4) == pytest.approx(170.5, rel=0.01)
    assert kcmil_to_mm2(795) == pytest.approx(402.8, rel=0.01)


@pytest.mark.unit
@pytest.mark.parametrize("desc,r_publicada", [
    ("CONDUCTOR ACSR 4/0 AWG", 0.2676),
    ("CONDUCTOR ACSR 336.4 MCM", 0.1701),
    ("CONDUCTOR ACSR 795 MCM", 0.0718),
    ("CONDUCTOR AAAC 6201 #397.5 MCM", 0.1655),
    ("Conductor TW Cu # 2 AWG", 0.5316),
    ("Conductor TW Cu # 4/0 AWG", 0.1662),
])
def test_resistencia_derivada_coincide_con_fabricante(desc, r_publicada):
    """La resistencia derivada debe estar dentro del 3 % del valor publicado."""

    c = derive_conductor("COOX", desc)
    assert c is not None
    error = abs(c.r_ohm_km_20c - r_publicada) / r_publicada
    assert error < 0.03, f"{desc}: {c.r_ohm_km_20c} vs {r_publicada} ({error*100:.1f}%)"


@pytest.mark.unit
def test_reactancia_en_rango_tipico_distribucion():
    c = derive_conductor("COOX", "CONDUCTOR ACSR 4/0 AWG")
    assert 0.25 <= c.x_ohm_km <= 0.50   # rango típico de distribución MT


@pytest.mark.unit
def test_material_y_clase_detectados():
    p = parse_conductor_description("Conductor Clase 15kV Al 4/0 AWG")
    assert p.material == "AL" and p.voltage_class == "MT"
    p = parse_conductor_description("CONDUCTOR AAAC 6201 #397.5 MCM")
    assert p.material == "AAAC"
    p = parse_conductor_description("Conductor TW Cu # 3/0 AWG")
    assert p.material == "CU" and p.voltage_class == "BT"


@pytest.mark.unit
def test_kcmil_mal_etiquetado_como_awg():
    """'#266 AWG' no existe como AWG: se interpreta como kcmil."""

    c = derive_conductor("COOX", "Conductor AAAC 6201 #266 AWG")
    assert c.seccion_mm2 == pytest.approx(134.8, rel=0.02)


@pytest.mark.unit
def test_cobertura_total_del_catalogo():
    """Los 415 conductores del catálogo del cliente deben quedar cubiertos."""

    from ptnt.ref.conductor_derive import derive_from_structure_catalog
    from ptnt.ref.structure_catalog import ElementCategory, load_structure_catalog

    sc = load_structure_catalog("config/catalogo_estructura.csv")
    total = len(sc.items_by_category(ElementCategory.CONDUCTOR))
    derivados = derive_from_structure_catalog(sc)
    assert total > 400
    assert len(derivados) == total, "todo conductor del catálogo debe tener impedancia"


@pytest.mark.unit
def test_catalogo_ampliado_prioriza_fabricante():
    from ptnt.ref.catalogs import load_conductor_catalog_full

    cat = load_conductor_catalog_full("config/catalogo_conductores.yaml")
    assert len(cat) > 400
    assert not cat.is_derived("COO0004")   # está en el YAML explícito
    assert cat.is_derived("COO0246")       # solo en el catálogo de estructuras


# --------------------------------------------------------------------------- #
# Focalización: dónde ir a hacer el levantamiento
# --------------------------------------------------------------------------- #
def _red_con_dos_ramales() -> NetworkModel:
    """Tronco MT con un puesto y dos ramales BT de 3 clientes cada uno."""

    edges = [
        Edge("s0", "SRC", "MT0", "COO0004", 0.4),
        Edge("s1", "MT0", "LV0", "COO0050", 0.02, is_lv=True),
    ]
    customer_nodes = {}
    for ram in range(2):
        prev = "LV0"
        for j in range(3):
            poste = f"BT{ram}_{j}"
            edges.append(Edge(f"sb{ram}{j}", prev, poste, "COO0050", 0.04, is_lv=True))
            prev = poste
            cn = f"C{ram}_{j}"
            edges.append(Edge(f"sa{ram}{j}", poste, cn, "COO0050", 0.01, is_lv=True))
            customer_nodes[cn] = [{"customer_id": f"CU{ram}{j}", "energy_kwh": 200.0,
                                   "phase": 1, "tariff": "BT Residencial"}]
    return NetworkModel(
        "F001", "SRC", edges,
        transformer_sites={"MT0": {"site_id": "TS0", "kvas": [50], "kva": 50,
                                   "config": "UNIDAD_SIMPLE"}},
        customer_nodes=customer_nodes,
    )


@pytest.mark.unit
def test_descomposicion_en_ramales_ignora_acometidas():
    """Una acometida de cliente NO bifurca la red: los ramales deben tener varios
    clientes, no uno por acometida."""

    g = build_radial_graph(_red_con_dos_ramales())
    stats = collect_branch_stats(g, min_customers=2)
    assert len(stats) == 2, f"se esperaban 2 ramales, se obtuvieron {len(stats)}"
    assert all(s["customers"] == 3 for s in stats)
    assert all(s["network_km"] > 0 for s in stats)


@pytest.mark.unit
def test_plan_cubre_todos_los_niveles():
    """El plan debe responder 'dónde inspeccionar' en alimentador, ramal,
    transformador, sector y cliente."""

    g = build_radial_graph(_red_con_dos_ramales())
    sospechosos = {"CU00", "CU10"}
    recuperable = {"CU00": 500.0, "CU10": 400.0}
    ranking = pd.DataFrame({
        "contract_account": ["CU00", "CU10", "CU01"],
        "score": [0.95, 0.80, 0.10],
        "recuperable_kwh_mes": [500.0, 400.0, 0.0],
        "razones": [["Consumo en cero con servicio activo"], ["Ruptura de nivel"], []],
    })
    coords = pd.DataFrame({
        "contract_account": ["CU00", "CU10", "CU01"],
        "x": [620000.0, 620050.0, 623000.0],
        "y": [9755000.0, 9755040.0, 9758000.0],
    })
    plan = build_survey_plan(
        feeder_balances=[{"feeder_code": "F001", "ntl_kwh": 1200, "ntl_pct": 9.5,
                          "customers": 6, "network_km": 0.7, "confidence": 100.0,
                          "balance_type": "MEDIDO"}],
        branch_stats=collect_branch_stats(
            g, suspect_customers=sospechosos, recoverable_by_customer=recuperable,
            min_customers=2),
        transformer_stats=[{"site_id": "TS0", "feeder_code": "F001", "customers": 6,
                            "suspect_customers": 2, "energy_kwh": 1200,
                            "recoverable_kwh": 900, "totalizer_residual_kwh": 0,
                            "loading_ratio": 0.2}],
        customer_ranking=ranking, customer_coords=coords, min_cluster_size=2,
    )
    niveles = {t.level for t in plan.targets}
    assert TargetLevel.ALIMENTADOR in niveles
    assert TargetLevel.RAMAL in niveles
    assert TargetLevel.PUESTO_TRANSFORMACION in niveles
    assert TargetLevel.CLIENTE in niveles
    # cada objetivo lleva acción y motivo operativo
    for t in plan.targets:
        assert t.action, "todo objetivo debe indicar la acción de campo"
        assert t.reasons, "todo objetivo debe indicar por qué ir ahí"


@pytest.mark.unit
def test_ramal_sin_señales_no_es_prioritario():
    """Un ramal con mucha energía pero sin indicios no debe encabezar el plan:
    mandar cuadrilla ahí es gastar presupuesto."""

    plan = build_survey_plan(branch_stats=[
        {"branch_id": "R_sin", "feeder_code": "F", "customers": 20,
         "suspect_customers": 0, "energy_kwh": 50000, "recoverable_kwh": 0,
         "network_km": 1.0},
        {"branch_id": "R_con", "feeder_code": "F", "customers": 10,
         "suspect_customers": 6, "energy_kwh": 5000, "recoverable_kwh": 800,
         "network_km": 0.5},
    ])
    ramales = plan.by_level(TargetLevel.RAMAL)
    top = max(ramales, key=lambda t: t.priority_score)
    assert top.entity_id == "R_con"


@pytest.mark.unit
def test_baja_confiabilidad_se_marca_problema_de_datos():
    """Score alto en zona de baja confiabilidad = problema de datos, no hurto."""

    plan = build_survey_plan(feeder_balances=[
        {"feeder_code": "F_malo", "ntl_kwh": 9000, "ntl_pct": 45,
         "customers": 100, "confidence": 20.0, "balance_type": "INDICATIVO"},
    ], umbral_confiabilidad=50.0)
    t = plan.by_level(TargetLevel.ALIMENTADOR)[0]
    assert t.data_problem_flag is True
    assert "CORREGIR DATOS" in t.action


@pytest.mark.unit
def test_sectores_agrupan_por_cercania():
    df = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(6)],
        "score": [0.9] * 6,
        "recuperable_kwh_mes": [100.0] * 6,
        # tres cerca del origen y tres a 5 km
        "x": [620000, 620050, 620100, 625000, 625050, 625100],
        "y": [9755000, 9755050, 9755010, 9760000, 9760050, 9760010],
    })
    sectores = cluster_sectors(df, min_cluster_size=2, cell_m=1000.0, usar_hdbscan=False)
    assert len(sectores) == 2
    assert all(s.n_customers == 3 for s in sectores)


@pytest.mark.unit
def test_ordenes_priorizan_rendimiento_por_visita():
    """Un sector que agrupa 20 clientes debe ir antes que una casa suelta:
    la logística de campo se ordena por energía cubierta por visita."""

    ranking = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(20)] + ["SOLO"],
        "score": [0.9] * 20 + [0.99],
        "recuperable_kwh_mes": [300.0] * 20 + [500.0],
        "razones": [[]] * 21,
    })
    coords = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(20)] + ["SOLO"],
        "x": [620000 + i * 10 for i in range(20)] + [640000.0],
        "y": [9755000 + i * 10 for i in range(20)] + [9770000.0],
    })
    plan = build_survey_plan(customer_ranking=ranking, customer_coords=coords,
                             min_cluster_size=3)
    ot = plan.work_orders(top_n=5)
    assert not ot.empty
    # la primera orden debe ser el sector agrupado (mayor kWh por visita)
    assert ot.iloc[0]["nivel"] == "SECTOR"
    assert ot.iloc[0]["clientes_a_revisar"] > 1


@pytest.mark.unit
def test_plan_exportable_y_reportable():
    """El resultado debe ser exportable (DataFrame/CSV) y reportable (HTML)."""

    from ptnt.survey.report import survey_report_html

    plan = build_survey_plan(transformer_stats=[
        {"site_id": "TS9", "feeder_code": "F", "customers": 12,
         "suspect_customers": 5, "energy_kwh": 8000, "recoverable_kwh": 1200,
         "totalizer_residual_kwh": 300, "loading_ratio": 0.15},
    ])
    df = plan.to_dataframe()
    assert not df.empty
    for col in ("nivel", "entidad", "prioridad", "recuperable_kwh_mes", "accion", "razon_1"):
        assert col in df.columns
    html = survey_report_html(plan)
    assert "TS9" in html and "Órdenes de levantamiento" in html


@pytest.mark.unit
def test_vinculacion_cuentas_comerciales_a_red():
    model = _red_con_dos_ramales()
    cuentas = [f"20000{i:07d}" for i in range(6)]
    mapa = bind_commercial_customers(model, cuentas)
    assert len(mapa) == 6
    ids = [c["customer_id"] for cls in model.customer_nodes.values() for c in cls]
    assert set(ids) == set(cuentas)
