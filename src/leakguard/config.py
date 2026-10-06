"""Settings and portfolio config loading.

Everything that depends on the data mode (raw folder, database file) is
resolved here so no other module needs to know which mode is active.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

DataMode = Literal["demo", "real"]

# Lets tests and scripts override the mode without editing settings.yaml.
MODE_ENV_VAR = "LEAKGUARD_DATA_MODE"


class Settings(BaseModel):
    data_mode: DataMode = "demo"
    fee_pct: float = Field(0.20, ge=0, le=1)
    charge_tolerance_cents: int = Field(100, ge=0)
    upcoming_expiry_days: int = Field(7, ge=0)
    fuzzy_name_min_score: int = Field(90, ge=0, le=100)
    fuzzy_arrival_window_days: int = Field(1, ge=0)
    fuzzy_amount_tolerance_pct: float = Field(0.10, ge=0, le=1)
    settlement_window_days: int = Field(5, ge=0)
    hash_guest_names: bool = False
    default_timezone: str = "America/New_York"

    @property
    def raw_dir(self) -> Path:
        return DATA_DIR / ("demo_raw" if self.data_mode == "demo" else "raw")

    @property
    def db_path(self) -> Path:
        return DATA_DIR / f"leakguard_{self.data_mode}.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


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


def load_settings(path: Path | None = None) -> Settings:
    """Read settings.yaml. The LEAKGUARD_DATA_MODE env var overrides data_mode."""
    path = path or CONFIG_DIR / "settings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    env_mode = os.environ.get(MODE_ENV_VAR)
    if env_mode:
        raw["data_mode"] = env_mode
    return Settings.model_validate(raw)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or CONFIG_DIR / "settings.yaml"
    path.write_text(yaml.safe_dump(settings.model_dump(), sort_keys=False), encoding="utf-8")


def load_portfolios(mode: DataMode, path: Path | None = None) -> list[PortfolioConfig]:
    """Return only the portfolios that belong to the given data mode."""
    path = path or CONFIG_DIR / "portfolios.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    portfolios = [PortfolioConfig.model_validate(p) for p in raw.get("portfolios", [])]
    return [p for p in portfolios if p.mode == mode]
