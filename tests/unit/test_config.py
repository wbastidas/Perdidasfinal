"""Pruebas de la capa de configuración: validación estricta y fallo por
parámetro obligatorio ausente."""

import pytest

from ptnt.config.loader import ConfigError, config_hash, load_config


@pytest.mark.unit
def test_config_valida_carga(cfg):
    assert cfg.proyecto.unidad_negocio == "GYE"
    assert "BT Residencial" in cfg.carga.clases


@pytest.mark.unit
def test_falta_parametro_obligatorio(tmp_path):
    yaml = tmp_path / "malo.yaml"
    yaml.write_text(
        # falta proyecto.unidad_negocio (OBLIGATORIO)
        """
proyecto:
  nombre: X
  version_config: "1"
  codigo_empresa: Y
comercial:
  parseo_columnas: {kwh: {}, coordenadas: {}}
  orden_meses: antiguo_primero
  mes_final: "2026-05-01"
  columnas: {division: D, cuenta_contrato: C, nombre: N, tarifa: T, x: X, y: Y, prefijo_kwh: KWH_}
carga:
  clases:
    R: {a: 0.1, b: 0.1, A: 0.2, B: 0.8, cos_phi: 0.9, factor_carga: 0.4, k_perdidas: 0.3}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(yaml)
    assert "unidad_negocio" in str(exc.value)


@pytest.mark.unit
def test_clave_desconocida_falla(tmp_path):
    yaml = tmp_path / "extra.yaml"
    yaml.write_text(
        """
proyecto:
  nombre: X
  version_config: "1"
  unidad_negocio: GYE
  codigo_empresa: Y
  clave_inventada: 1
comercial:
  parseo_columnas: {kwh: {}, coordenadas: {}}
  orden_meses: antiguo_primero
  mes_final: "2026-05-01"
  columnas: {division: D, cuenta_contrato: C, nombre: N, tarifa: T, x: X, y: Y, prefijo_kwh: KWH_}
carga:
  clases:
    R: {a: 0.1, b: 0.1, A: 0.2, B: 0.8, cos_phi: 0.9, factor_carga: 0.4, k_perdidas: 0.3}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(yaml)


@pytest.mark.unit
def test_coincidencia_A_mas_B_debe_ser_1(tmp_path):
    yaml = tmp_path / "ab.yaml"
    yaml.write_text(
        """
proyecto: {nombre: X, version_config: "1", unidad_negocio: GYE, codigo_empresa: Y}
comercial:
  parseo_columnas: {kwh: {}, coordenadas: {}}
  orden_meses: antiguo_primero
  mes_final: "2026-05-01"
  columnas: {division: D, cuenta_contrato: C, nombre: N, tarifa: T, x: X, y: Y, prefijo_kwh: KWH_}
carga:
  clases:
    R: {a: 0.1, b: 0.1, A: 0.5, B: 0.9, cos_phi: 0.9, factor_carga: 0.4, k_perdidas: 0.3}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(yaml)


@pytest.mark.unit
def test_config_hash_estable(cfg):
    assert config_hash(cfg) == config_hash(cfg)
    assert len(config_hash(cfg)) == 64
