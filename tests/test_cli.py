from pathlib import Path

import pytest

from leakguard import __main__ as cli
from leakguard.config import Settings


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings.db_path at a temp file so reset never touches real data."""
    db = tmp_path / "leakguard_demo.db"
    monkeypatch.setattr(Settings, "db_path", property(lambda self: db))
    return db


def test_reset_with_yes_deletes_db(temp_db: Path) -> None:
    temp_db.write_text("x")
    assert cli.main(["reset", "--mode", "demo", "--yes"]) == 0
    assert not temp_db.exists()


def test_reset_cancels_on_wrong_confirmation(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_db.write_text("x")
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main(["reset", "--mode", "demo"]) == 1
    assert temp_db.exists()


def test_reset_when_nothing_exists(temp_db: Path) -> None:
    assert cli.main(["reset", "--mode", "demo", "--yes"]) == 0


def test_report_requires_portfolio() -> None:
    with pytest.raises(SystemExit):
        cli.main(["report"])
