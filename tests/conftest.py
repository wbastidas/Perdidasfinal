"""Fixtures compartidas de las pruebas."""

from __future__ import annotations

from pathlib import Path

import pytest

from ptnt.config.loader import load_config


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg(repo_root: Path):
    return load_config(repo_root / "config" / "base.yaml")


@pytest.fixture
def synth_csv(tmp_path: Path):
    """Genera un CSV comercial sintético pequeño y reproducible."""

    from ptnt.synth.generator import generate_commercial_csv

    ruta = tmp_path / "consumos.csv"
    ds = generate_commercial_csv(
        str(ruta), n_clientes=600, n_meses=36, pct_hurto=0.06, semilla=123
    )
    return ds
