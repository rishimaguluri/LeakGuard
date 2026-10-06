"""Shared fixtures.

`isolated_env` points data, config and mappings at temp folders so tests
never touch data/ or config/ in the repo. `small_demo` builds a scaled-down
demo portfolio once per test session.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from leakguard.config import (
    CONFIG_DIR_ENV_VAR,
    DATA_DIR_ENV_VAR,
    MAPPINGS_DIR_ENV_VAR,
    MODE_ENV_VAR,
    PROJECT_ROOT,
    load_portfolios,
    load_settings,
)
from leakguard.demo.generator import GenConfig, generate_portfolio

AS_OF = date(2026, 9, 30)


def make_env(root: Path, mode: str = "demo") -> dict[str, str]:
    (root / "data").mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "config", root / "config", dirs_exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "mappings", root / "mappings", dirs_exist_ok=True)
    return {
        DATA_DIR_ENV_VAR: str(root / "data"),
        CONFIG_DIR_ENV_VAR: str(root / "config"),
        MAPPINGS_DIR_ENV_VAR: str(root / "mappings"),
        MODE_ENV_VAR: mode,
    }


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in make_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    return tmp_path


@pytest.fixture(scope="session")
def small_demo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    """A 10% scale demo portfolio written to a temp raw folder."""
    root = tmp_path_factory.mktemp("small_demo")
    env = make_env(root)
    mp = pytest.MonkeyPatch()
    for key, value in env.items():
        mp.setenv(key, value)
    settings = load_settings()
    raw = settings.raw_dir
    truth = generate_portfolio(
        load_portfolios("demo")[0], raw, GenConfig(as_of=AS_OF, scale=0.1), settings
    )
    mp.undo()
    return root, truth
