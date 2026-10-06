"""Settings and portfolio config loading.

Everything that depends on the data mode (raw folder, database file) is
resolved here so no other module needs to know which mode is active.

Folder locations can be overridden with environment variables. Tests use
this to run the real-data pipeline inside a temp folder.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DataMode = Literal["demo", "real"]

MODE_ENV_VAR = "LEAKGUARD_DATA_MODE"
DATA_DIR_ENV_VAR = "LEAKGUARD_DATA_DIR"
CONFIG_DIR_ENV_VAR = "LEAKGUARD_CONFIG_DIR"
MAPPINGS_DIR_ENV_VAR = "LEAKGUARD_MAPPINGS_DIR"

DEFAULT_VCC_KEYWORDS = ["virtual", "vcc", "expedia", "booking", "bcom", "ota", "eva"]


def data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_ENV_VAR) or PROJECT_ROOT / "data")


def config_dir() -> Path:
    return Path(os.environ.get(CONFIG_DIR_ENV_VAR) or PROJECT_ROOT / "config")


def mappings_dir() -> Path:
    return Path(os.environ.get(MAPPINGS_DIR_ENV_VAR) or PROJECT_ROOT / "mappings")


def exports_dir() -> Path:
    return PROJECT_ROOT / "exports"


class Settings(BaseModel):
    data_mode: DataMode = "demo"
    fee_pct: float = Field(0.20, ge=0, le=1)
    charge_tolerance_cents: int = Field(100, ge=0)
    upcoming_expiry_days: int = Field(7, ge=0, le=90)
    fuzzy_name_min_score: int = Field(90, ge=0, le=100)
    fuzzy_arrival_window_days: int = Field(1, ge=0, le=7)
    fuzzy_amount_tolerance_pct: float = Field(0.10, ge=0, le=1)
    settlement_window_days: int = Field(5, ge=0, le=60)
    hash_guest_names: bool = False
    default_timezone: str = "America/New_York"
    cap_rate: float = Field(0.08, gt=0, le=0.25)
    as_of_date: date | None = None
    demo_as_of_date: date = date(2026, 9, 30)
    virtual_card_keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_VCC_KEYWORDS))

    @property
    def raw_dir(self) -> Path:
        return data_dir() / ("demo_raw" if self.data_mode == "demo" else "raw")

    @property
    def db_path(self) -> Path:
        return data_dir() / f"leakguard_{self.data_mode}.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    def as_of(self) -> date:
        """The date matching and dashboards treat as today.

        Demo data is generated around a fixed date so it never goes stale.
        Real mode uses today unless as_of_date is set.
        """
        if self.data_mode == "demo":
            return self.demo_as_of_date
        return self.as_of_date or date.today()


class PropertyConfig(BaseModel):
    code: str
    name: str
    brand: str | None = None
    segment: Literal["select_service", "full_service"] | None = None
    city: str | None = None
    state: str | None = None
    room_count: int | None = None
    management_company: str
    pms_name: str | None = None
    timezone: str | None = None


class PortfolioConfig(BaseModel):
    slug: str
    mode: DataMode
    name: str
    owner_name: str
    owner_type: Literal["pe_fund", "reit", "family_office", "independent"]
    management_companies: list[str]
    properties: list[PropertyConfig]


SETTINGS_HEADER = """\
# LeakGuard settings. The Settings page in the app writes back to this file.
# data_mode: demo reads data/demo_raw/ and uses data/leakguard_demo.db
#            real reads data/raw/ and uses data/leakguard_real.db
# Amounts are in cents. Percentages are fractions (0.20 means 20%).
"""


def settings_path() -> Path:
    return config_dir() / "settings.yaml"


def load_settings(path: Path | None = None) -> Settings:
    """Read settings.yaml. The LEAKGUARD_DATA_MODE env var overrides data_mode."""
    path = path or settings_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    env_mode = os.environ.get(MODE_ENV_VAR)
    if env_mode:
        raw["data_mode"] = env_mode
    return Settings.model_validate(raw)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    body = yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False)
    path.write_text(SETTINGS_HEADER + "\n" + body, encoding="utf-8")


def load_portfolios(mode: DataMode, path: Path | None = None) -> list[PortfolioConfig]:
    """Return only the portfolios that belong to the given data mode."""
    path = path or config_dir() / "portfolios.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    portfolios = [PortfolioConfig.model_validate(p) for p in raw.get("portfolios", [])]
    return [p for p in portfolios if p.mode == mode]
