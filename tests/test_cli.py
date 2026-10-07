from pathlib import Path

import pytest

from leakguard import __main__ as cli


@pytest.fixture
def temp_db(isolated_env: Path) -> Path:
    """A demo database in a temp folder, holding one portfolio row."""
    from leakguard.config import load_settings
    from leakguard.db import session_scope
    from leakguard.models import Portfolio

    settings = load_settings()
    with session_scope(settings) as s:
        s.add(Portfolio(slug="x", name="X", owner_name="X", owner_type="independent"))
    return settings.db_path


def _portfolios() -> int:
    from leakguard.config import load_settings
    from leakguard.db import session_scope
    from leakguard.models import Portfolio

    with session_scope(load_settings()) as s:
        return s.query(Portfolio).count()


def test_reset_with_yes_empties_db(temp_db: Path) -> None:
    assert cli.main(["reset", "--mode", "demo", "--yes"]) == 0
    assert _portfolios() == 0


def test_reset_cancels_on_wrong_confirmation(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main(["reset", "--mode", "demo"]) == 1
    assert _portfolios() == 1


def test_reset_when_nothing_exists(isolated_env: Path) -> None:
    assert cli.main(["reset", "--mode", "demo", "--yes"]) == 0


def test_report_requires_portfolio() -> None:
    with pytest.raises(SystemExit):
        cli.main(["report"])
