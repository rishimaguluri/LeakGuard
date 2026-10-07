"""Load every dashboard page against the small demo and fail on any error."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from leakguard.config import PROJECT_ROOT
from leakguard.demo.history import seed_history

HOME = str(PROJECT_ROOT / "app" / "Home.py")
PAGES = [
    "views/0_Home.py",
    "views/1_Portfolio.py",
    "views/2_Operator_Scorecard.py",
    "views/3_Hotel_Detail.py",
    "views/4_Exception_Queue.py",
    "views/5_Recovery_Tracker.py",
    "views/6_Audit_Report.py",
    "views/7_Data_Sources.py",
    "views/8_Mapping_Wizard.py",
    "views/9_Settings.py",
]


@pytest.fixture
def seeded(demo_copy):
    _, _, settings = demo_copy
    seed_history(settings)
    return settings


def _errors(at: AppTest) -> list[str]:
    return [e.value for e in at.exception] + [e.value for e in at.error]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(seeded, page: str) -> None:
    at = AppTest.from_file(HOME, default_timeout=120)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page)
        at.run()
    assert not _errors(at), _errors(at)
    text = " ".join(m.value for m in at.markdown)
    assert "Demo data" in text  # mode badge on every page
    assert "coming soon" not in text.lower()
    assert "—" not in text  # no em dashes


def test_home_shows_operator_takeaway(seeded) -> None:
    at = AppTest.from_file(HOME, default_timeout=120)
    at.run()
    text = " ".join(m.value for m in at.markdown)
    assert "more per dollar of OTA-collect revenue" in text
    assert "Top 5 actions this week" in text


def test_queue_opens_evidence_for_focused_item(seeded) -> None:
    from leakguard.db import session_scope
    from leakguard.models import Exception_

    with session_scope(seeded) as s:
        item = (
            s.query(Exception_).filter(Exception_.is_exception, Exception_.status == "open").first()
        )
        item_id = item.id
    at = AppTest.from_file(HOME, default_timeout=120)
    at.session_state["queue_focus"] = item_id
    at.run()
    at.switch_page("views/4_Exception_Queue.py")
    at.run()
    assert not _errors(at), _errors(at)
    text = " ".join(m.value for m in at.markdown)
    assert "Why it was flagged" in text


def test_empty_database_shows_guidance(isolated_env: Path) -> None:
    at = AppTest.from_file(HOME, default_timeout=60)
    at.run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "python -m leakguard demo" in text


def test_password_gate_blocks_until_signed_in(seeded) -> None:
    at = AppTest.from_file(HOME, default_timeout=60)
    at.secrets["app_password"] = "s3cret"
    at.run()
    assert "Top 5 actions" not in " ".join(m.value for m in at.markdown)
    at.text_input[0].input("wrong")
    at.button[0].click()
    at.run()
    assert at.error
    at.text_input[0].input("s3cret")
    at.button[0].click()
    at.run()
    assert "Top 5 actions" in " ".join(m.value for m in at.markdown)
