from pathlib import Path

import pytest
from pydantic import ValidationError

from leakguard.config import (
    MODE_ENV_VAR,
    load_portfolios,
    load_settings,
    save_settings,
)


def test_default_settings_load_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)
    settings = load_settings()
    assert settings.data_mode == "demo"
    assert settings.fee_pct == 0.20
    assert settings.charge_tolerance_cents == 100


def test_each_mode_has_its_own_folder_and_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "demo")
    demo = load_settings()
    monkeypatch.setenv(MODE_ENV_VAR, "real")
    real = load_settings()
    assert demo.raw_dir.name == "demo_raw"
    assert real.raw_dir.name == "raw"
    assert demo.db_path != real.db_path
    assert real.db_path.name == "leakguard_real.db"


def test_invalid_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "production")
    with pytest.raises(ValidationError):
        load_settings()


def test_settings_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)
    path = tmp_path / "settings.yaml"
    original = load_settings().model_copy(update={"fee_pct": 0.25})
    save_settings(original, path)
    assert load_settings(path).fee_pct == 0.25


def test_demo_portfolio_matches_spec() -> None:
    portfolios = load_portfolios("demo")
    assert len(portfolios) == 1
    demo = portfolios[0]
    assert demo.name == "Summit Ridge Capital"
    assert len(demo.properties) == 8
    assert len(demo.management_companies) == 2
    for prop in demo.properties:
        assert prop.management_company in demo.management_companies
        assert 90 <= prop.room_count <= 350
    codes = [p.code for p in demo.properties]
    assert len(codes) == len(set(codes))


def test_no_real_portfolios_committed() -> None:
    assert load_portfolios("real") == []
