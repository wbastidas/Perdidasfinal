"""Pruebas de identidad geográfica estable y versionado de topología.

La estabilidad de las ubicaciones es **crítica para el trabajo de campo**: una
orden emitida para un sector debe seguir apuntando al mismo sitio físico después de
cargar datos nuevos o de modificar la red.
"""

import pandas as pd
import pytest

from ptnt.survey.locations import (
    LocationRegistry,
    geo_code,
    register_plan,
)
from ptnt.survey.sectors import cluster_sectors
from ptnt.survey.targeting import TargetLevel, build_survey_plan
from ptnt.synth.scenario import modify_topology
from ptnt.topology.versioning import (
    VersionAction,
    VersionStore,
    attribute_hash,
    switch_state_hash,
    topology_hash,
)


# --------------------------------------------------------------------------- #
# Identidad geográfica estable
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_geo_code_es_determinista_y_legible():
    c = geo_code(620134.0, 9755412.0, prefijo="SEC")
    assert c == geo_code(620134.0, 9755412.0, prefijo="SEC")
    assert c.startswith("SEC-E620.1-N9755.4")


@pytest.mark.unit
def test_geo_code_agrupa_coordenadas_cercanas():
    """Dos centroides a menos de la resolución producen el mismo código: el mismo
    lugar no debe cambiar de identificador por variaciones menores."""

    assert geo_code(620100, 9755400) == geo_code(620130, 9755380)


@pytest.mark.unit
def test_geo_code_sin_coordenada():
    assert geo_code(None, None).endswith("SIN-COORD")


def _clientes(n_extra: int = 0) -> pd.DataFrame:
    base = pd.DataFrame({
        "contract_account": [f"C{i}" for i in range(12)],
        "score": [0.9] * 12,
        "recuperable_kwh_mes": [100.0 + i for i in range(12)],
        "x": [620000 + i * 40 for i in range(6)] + [625000 + i * 40 for i in range(6)],
        "y": [9755000 + i * 40 for i in range(6)] + [9760000 + i * 40 for i in range(6)],
    })
    if n_extra:
        extra = pd.DataFrame({
            "contract_account": [f"N{i}" for i in range(n_extra)],
            "score": [0.99] * n_extra,
            "recuperable_kwh_mes": [900.0] * n_extra,
            "x": [610000 + i * 40 for i in range(n_extra)],
            "y": [9750000 + i * 40 for i in range(n_extra)],
        })
        return pd.concat([extra, base], ignore_index=True)
    return base


@pytest.mark.unit
def test_sectores_conservan_identidad_al_cargar_datos_nuevos():
    """CRÍTICO PARA CAMPO: una orden emitida para un sector debe seguir apuntando
    al mismo sitio tras cargar el mes siguiente."""

    s1 = cluster_sectors(_clientes(), min_cluster_size=3, usar_hdbscan=False)
    s2 = cluster_sectors(_clientes(n_extra=3), min_cluster_size=3, usar_hdbscan=False)
    m1 = {s.sector_id: round(s.centroid_x) for s in s1}
    m2 = {s.sector_id: round(s.centroid_x) for s in s2}

    comunes = set(m1) & set(m2)
    assert comunes, "algún sector debe persistir entre corridas"
    for sid in comunes:
        assert m1[sid] == m2[sid], f"{sid} cambió de ubicación entre corridas"
    # el área nueva aparece como sector nuevo, no renumerando los existentes
    assert set(m2) - set(m1), "la zona nueva debe generar un sector nuevo"


@pytest.mark.unit
def test_sectores_estables_ante_reordenamiento():
    df = _clientes()
    s1 = cluster_sectors(df, min_cluster_size=3, usar_hdbscan=False)
    s2 = cluster_sectors(df.sample(frac=1, random_state=3).reset_index(drop=True),
                          min_cluster_size=3, usar_hdbscan=False)
    assert {s.sector_id for s in s1} == {s.sector_id for s in s2}


# --------------------------------------------------------------------------- #
# Registro persistente de ubicaciones
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_registro_acumula_veces_priorizado(tmp_path):
    reg = LocationRegistry(tmp_path / "ubi.json")
    reg.register("SEC-E620.0-N9755.0", "SECTOR", 620000, 9755000, 0.8, fecha="2026-01-01")
    reg.register("SEC-E620.0-N9755.0", "SECTOR", 620000, 9755000, 0.9, fecha="2026-02-01")
    reg.register("SEC-E620.0-N9755.0", "SECTOR", 620000, 9755000, 0.7, fecha="2026-03-01")
    r = reg.get("SEC-E620.0-N9755.0")
    assert r.veces_priorizado == 3
    assert r.primera_deteccion == "2026-01-01"
    assert r.ultima_deteccion == "2026-03-01"
    assert r.prioridad_max == pytest.approx(0.9)
    assert r.reincidente is True          # 3 veces sin inspeccionar


@pytest.mark.unit
def test_reejecutar_el_mismo_dia_no_infla_el_conteo(tmp_path):
    reg = LocationRegistry(tmp_path / "u.json")
    for _ in range(4):
        reg.register("SEC-X", "SECTOR", 1.0, 2.0, 0.5, fecha="2026-01-01")
    assert reg.get("SEC-X").veces_priorizado == 1


@pytest.mark.unit
def test_inspeccion_cierra_el_ciclo(tmp_path):
    reg = LocationRegistry(tmp_path / "u.json")
    for f in ("2026-01-01", "2026-02-01", "2026-03-01"):
        reg.register("SEC-Y", "SECTOR", 1.0, 2.0, 0.9, fecha=f)
    assert reg.get("SEC-Y").reincidente is True
    assert reg.marcar_inspeccionado("SEC-Y", "Hurto confirmado") is True
    r = reg.get("SEC-Y")
    assert r.inspeccionado is True
    assert r.reincidente is False          # ya se atendió
    assert r.resultado_inspeccion == "Hurto confirmado"


@pytest.mark.unit
def test_registro_persiste_en_disco(tmp_path):
    ruta = tmp_path / "ubi.json"
    reg = LocationRegistry(ruta)
    reg.register("SEC-Z", "SECTOR", 620000, 9755000, 0.8, fecha="2026-01-01")
    reg.save()
    otro = LocationRegistry(ruta)          # se reabre en otra corrida
    assert otro.get("SEC-Z") is not None
    assert otro.get("SEC-Z").primera_deteccion == "2026-01-01"


@pytest.mark.unit
def test_register_plan_registra_objetivos_con_coordenada(tmp_path):
    plan = build_survey_plan(
        customer_ranking=pd.DataFrame({
            "contract_account": [f"C{i}" for i in range(8)],
            "score": [0.9] * 8, "recuperable_kwh_mes": [100.0] * 8,
            "razones": [[]] * 8,
        }),
        customer_coords=pd.DataFrame({
            "contract_account": [f"C{i}" for i in range(8)],
            "x": [620000 + i * 30 for i in range(8)],
            "y": [9755000 + i * 30 for i in range(8)],
        }),
        min_cluster_size=3,
    )
    reg = LocationRegistry(tmp_path / "u.json")
    n = register_plan(plan, reg)
    assert n > 0 and len(reg) > 0
    assert not reg.to_dataframe().empty


# --------------------------------------------------------------------------- #
# Versionado de topología
# --------------------------------------------------------------------------- #
@pytest.fixture
def red():
    from ptnt.synth.network import generate_radial_network

    return generate_radial_network(n_transformers=4, customers_per_tx=8).model


@pytest.mark.unit
def test_primera_carga_es_alta(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    r = st.register(red)
    assert r.action == VersionAction.ALTA
    assert r.version_id == 1
    assert r.change_summary["altas"]["tramos"] > 0


@pytest.mark.unit
def test_misma_red_no_se_reprocesa(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    r = st.register(red)
    assert r.action == VersionAction.SIN_CAMBIO
    assert r.etapas_a_recalcular == []


@pytest.mark.unit
def test_cambio_de_conductor_solo_invalida_atributos(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    mod, _ = modify_topology(red, cambio="conductor")
    r = st.register(mod)
    assert r.action == VersionAction.ACTUALIZACION
    assert r.hashes_cambiados == ["attribute_hash"]
    assert "topologia" not in r.etapas_a_recalcular   # la conectividad no cambió
    assert "perdidas" in r.etapas_a_recalcular


@pytest.mark.unit
def test_nuevo_ramal_invalida_topologia_y_focalizacion(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    mod, _ = modify_topology(red, cambio="nuevo_ramal")
    r = st.register(mod)
    assert "topology_hash" in r.hashes_cambiados
    assert "topologia" in r.etapas_a_recalcular
    assert "focalizacion" in r.etapas_a_recalcular
    assert r.change_summary["delta"]["clientes"] == 3


@pytest.mark.unit
def test_maniobra_solo_invalida_estado_dinamico(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    mod, _ = modify_topology(red, cambio="maniobra")
    r = st.register(mod)
    assert r.hashes_cambiados == ["switch_state_hash"]
    assert set(r.etapas_a_recalcular) == {"topologia_dinamica", "balance"}


@pytest.mark.unit
def test_version_anterior_se_conserva(tmp_path, red):
    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    mod, _ = modify_topology(red, cambio="nuevo_ramal")
    st.register(mod)
    hist = st.history(red.feeder_code)
    assert len(hist) == 2
    assert hist[0].is_current is False          # la anterior se conserva
    assert hist[1].is_current is True


@pytest.mark.unit
def test_hashes_son_independientes(red):
    """Cada hash debe reaccionar solo a su propio dominio."""

    t0, a0, s0 = topology_hash(red), attribute_hash(red), switch_state_hash(red)
    cond, _ = modify_topology(red, cambio="conductor")
    assert topology_hash(cond) == t0        # la conectividad no cambió
    assert attribute_hash(cond) != a0
    man, _ = modify_topology(red, cambio="maniobra")
    assert switch_state_hash(man) != s0
    assert topology_hash(man) == t0


@pytest.mark.unit
def test_ubicaciones_sobreviven_al_cambio_de_topologia(tmp_path, red):
    """CRÍTICO: cambiar la red no debe borrar las ubicaciones ya priorizadas."""

    reg = LocationRegistry(tmp_path / "u.json")
    reg.register("SEC-E620.1-N9755.4", "SECTOR", 620100, 9755400, 0.9,
                 fecha="2026-01-01")
    reg.save()

    st = VersionStore(tmp_path / "v.json")
    st.register(red)
    mod, _ = modify_topology(red, cambio="nuevo_ramal")
    r = st.register(mod)
    assert r.ubicaciones_conservadas is True

    reg2 = LocationRegistry(tmp_path / "u.json")
    rec = reg2.get("SEC-E620.1-N9755.4")
    assert rec is not None, "la ubicación debe sobrevivir al cambio de topología"
    assert rec.primera_deteccion == "2026-01-01"
