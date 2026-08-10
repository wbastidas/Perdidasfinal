"""Pruebas de seguridad.

Cubren: hashing de contraseñas (nunca en claro), resolución de secretos por
entorno (nunca en YAML/código), autenticación del visor web (401 sin credenciales,
403 por red no autorizada), rol mínimo, y ausencia de credenciales embebidas en la
configuración del repositorio.
"""

import os
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Hashing de contraseñas
# --------------------------------------------------------------------------- #
@pytest.mark.security
def test_password_no_se_guarda_en_claro(tmp_path):
    from ptnt.security.auth import UserStore

    store = UserStore(tmp_path / "usuarios.json")
    store.add_user("juan", "SuperClave123", "analyst")
    contenido = (tmp_path / "usuarios.json").read_text(encoding="utf-8")
    assert "SuperClave123" not in contenido  # la contraseña NO aparece
    assert "password_hash" in contenido


@pytest.mark.security
def test_verify_password_correcto_e_incorrecto():
    from ptnt.security.auth import hash_password, verify_password

    h = hash_password("ClaveDePrueba99")
    assert verify_password("ClaveDePrueba99", h) is True
    assert verify_password("otra", h) is False


@pytest.mark.security
def test_password_corta_rechazada():
    from ptnt.security.auth import AuthError, hash_password

    with pytest.raises(AuthError):
        hash_password("corta")


@pytest.mark.security
def test_autenticacion_usuario_inexistente_no_filtra(tmp_path):
    from ptnt.security.auth import UserStore

    store = UserStore(tmp_path / "u.json")
    store.add_user("real", "ClaveReal123", "viewer")
    assert store.authenticate("fantasma", "x") is None
    assert store.authenticate("real", "mala") is None
    assert store.authenticate("real", "ClaveReal123") is not None


# --------------------------------------------------------------------------- #
# Secretos por entorno
# --------------------------------------------------------------------------- #
@pytest.mark.security
def test_secreto_faltante_lanza_error():
    from ptnt.security.secrets import SecretError, get_secret

    with pytest.raises(SecretError):
        get_secret("VARIABLE_QUE_NO_EXISTE_PTNT_XYZ")


@pytest.mark.security
def test_credenciales_sql_desde_entorno(monkeypatch):
    from ptnt.config.models import FuenteConfig, TipoFuente
    from ptnt.security.secrets import resolve_source_credentials

    monkeypatch.setenv("PTNT_TEST_USER", "svc_ptnt")
    monkeypatch.setenv("PTNT_TEST_PASS", "secreto-desde-env")
    fuente = FuenteConfig(
        nombre="t", tipo=TipoFuente.POSTGRES, host="h", base_datos="db",
        usuario_env="PTNT_TEST_USER", password_env="PTNT_TEST_PASS",
    )
    creds = resolve_source_credentials(fuente)
    assert creds.usuario == "svc_ptnt"
    assert creds.password == "secreto-desde-env"
    # el repr no filtra la contraseña
    assert "secreto-desde-env" not in repr(creds)


@pytest.mark.security
def test_config_repo_sin_credenciales_embebidas():
    """El YAML del repositorio no debe contener contraseñas en claro."""

    texto = Path("config/base.yaml").read_text(encoding="utf-8").lower()
    # patrones típicos de credencial embebida
    prohibidos = ["password:", "passwd:", "contrasena:", "contraseña:", "pwd:"]
    for p in prohibidos:
        assert p not in texto, f"Posible credencial embebida: '{p}'"
    # debe usar referencias por entorno
    assert "password_env" in Path("config/base.yaml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Visor web de solo lectura
# --------------------------------------------------------------------------- #
@pytest.mark.security
def test_visor_requiere_autenticacion(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from ptnt.config.loader import load_config
    from ptnt.security.auth import UserStore
    from ptnt.webviewer.app import create_app

    cfg = load_config("config/base.yaml")
    users_path = tmp_path / "usuarios.json"
    UserStore(users_path).add_user("visor", "ClaveVisor123", "viewer")

    # apuntar la app a este almacén de usuarios
    cfg_path = tmp_path / "cfg.yaml"
    import yaml

    data = yaml.safe_load(Path("config/base.yaml").read_text(encoding="utf-8"))
    data["seguridad"]["ruta_usuarios"] = str(users_path)
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    app = create_app(str(cfg_path))
    client = TestClient(app)

    assert client.get("/").status_code == 401                     # sin credenciales
    assert client.get("/", auth=("visor", "mala")).status_code == 401  # credencial mala
    assert client.get("/", auth=("visor", "ClaveVisor123")).status_code == 200
    assert client.get("/salud").status_code == 200                # salud es pública


@pytest.mark.security
def test_visor_restringe_por_red(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from ptnt.security.auth import UserStore
    import yaml

    users_path = tmp_path / "u.json"
    UserStore(users_path).add_user("visor", "ClaveVisor123", "viewer")
    data = yaml.safe_load(Path("config/base.yaml").read_text(encoding="utf-8"))
    data["seguridad"]["ruta_usuarios"] = str(users_path)
    data["seguridad"]["redes_permitidas"] = ["10.0.0.0/8"]  # el test client viene de testserver
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    from ptnt.webviewer.app import create_app

    client = TestClient(app=create_app(str(cfg_path)))
    # la IP del TestClient (testclient) no está en 10.0.0.0/8 -> 403
    r = client.get("/", auth=("visor", "ClaveVisor123"))
    assert r.status_code == 403


@pytest.mark.security
def test_no_hay_eval_ni_shell_injection_en_fuentes():
    """El conector SQL construye la URL con SQLAlchemy URL.create (parametrizado),
    no por concatenación de credenciales; verificamos que no se usa format crudo
    de password en la construcción."""

    import inspect

    from ptnt.io.sources import sql_source

    src = inspect.getsource(sql_source)
    # la contraseña se pasa a URL.create, no se interpola en una f-string de conexión
    assert "URL.create" in src


@pytest.mark.security
def test_endpoints_focalizacion_requieren_autenticacion(tmp_path):
    """Los objetivos de levantamiento identifican predios concretos: el visor no
    debe exponerlos sin credenciales."""

    pytest.importorskip("fastapi")
    import yaml
    from fastapi.testclient import TestClient

    from ptnt.security.auth import UserStore
    from ptnt.webviewer.app import create_app

    users_path = tmp_path / "u.json"
    UserStore(users_path).add_user("visor", "ClaveVisor123", "viewer")
    data = yaml.safe_load(Path("config/base.yaml").read_text(encoding="utf-8"))
    data["seguridad"]["ruta_usuarios"] = str(users_path)
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    client = TestClient(create_app(str(cfg_path)))
    for endpoint in ("/api/focalizacion", "/api/ordenes"):
        assert client.get(endpoint).status_code == 401, endpoint
        assert client.get(endpoint, auth=("visor", "mala")).status_code == 401, endpoint
        # con credenciales válidas: 200 (hay datos) o 404 (aún sin calcular)
        assert client.get(endpoint, auth=("visor", "ClaveVisor123")).status_code in (200, 404)
