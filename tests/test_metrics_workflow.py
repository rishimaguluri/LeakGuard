"""metrics.py numbers and the recovery workflow, on a hand-checked scenario.

Cards (as of Sep 30, 2026):
  T01 (Alpha Mgmt)
    A  $300 uncharged, stayed            -> UNCHARGED, then recovered $300
    B  $300 card, $200 charged           -> UNDERCHARGED, $100 at risk, open
    C  $300 charged and settled          -> CHARGED_OK
    E  $250 future arrival               -> NOT_YET_DUE
    F  second card on B's reservation    -> DUPLICATE_VCC, $0 at risk
    G  $120 uncharged, expires in 3 days -> UNCHARGED, open
  T02 (Beta Mgmt)
    D  $500 expired, uncharged           -> EXPIRED_UNCHARGED, written off
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from leakguard.config import load_settings
from leakguard.db import session_scope
from leakguard.models import Exception_
from leakguard.recovery import workflow as wf
from leakguard.reporting import metrics as M

from .scenario import Scenario, run, use_test_portfolio

AS_OF = date(2026, 9, 30)
D = timedelta


@pytest.fixture
def world(isolated_env):
    use_test_portfolio(isolated_env)
    beta = Scenario(isolated_env, property_code="T02")
    beta.res("D1", "7000000004", "Dee", date(2026, 3, 1), date(2026, 3, 3), amount=500)
    beta.card("7000000004", "4444", 500, date(2026, 3, 1), date(2026, 4, 30))
    beta.write()

    sc = Scenario(isolated_env)
    sc.res("A1", "7000000001", "Ames", date(2026, 8, 1), date(2026, 8, 3))
    sc.card("7000000001", "1111", 300, date(2026, 8, 1), date(2026, 11, 1))
    sc.res("B1", "7000000002", "Bell", date(2026, 8, 5), date(2026, 8, 7))
    sc.card("7000000002", "2222", 300, date(2026, 8, 5), date(2026, 11, 5))
    sc.card("7000000002", "6666", 300, date(2026, 8, 6), date(2026, 11, 6))
    sc.pay("B1", date(2026, 8, 5), 200, last4="2222")
    sc.txn(date(2026, 8, 5), "2222", 200)
    sc.res("C1", "7000000003", "Cole", date(2026, 8, 9), date(2026, 8, 11))
    sc.card("7000000003", "3333", 300, date(2026, 8, 9), date(2026, 11, 9))
    sc.pay("C1", date(2026, 8, 9), 300, last4="3333")
    sc.txn(date(2026, 8, 9), "3333", 300)
    sc.res("E1", "7000000005", "Eddy", AS_OF + D(10), AS_OF + D(12), status="Confirmed", amount=250)
    sc.card("7000000005", "5555", 250, AS_OF + D(10), AS_OF + D(100))
    sc.res("G1", "7000000007", "Gray", date(2026, 7, 1), date(2026, 7, 3), amount=120)
    sc.card("7000000007", "7777", 120, date(2026, 7, 1), AS_OF + D(3))
    rows = run(sc)
    settings = load_settings()
    with session_scope(settings) as s:
        wf.record_recovery(s, rows["7000000001"].id, 30000, date(2026, 9, 10), "tester")
        wf.change_status(s, rows["7000000004"].id, "written_off", "tester", "OTA declined")
    return settings, rows


def _frames(settings):
    with session_scope(settings) as s:
        pf = M.portfolios(s)[0]
        df = M.load_frame(s, M.Filters(pf.id), settings.as_of())
    start, end = M.default_period(settings.as_of())
    return df, M.in_period(df, start, end), start, end


def test_classification_of_scenario(world) -> None:
    _, rows = world
    assert rows["7000000001"].exception_type == "UNCHARGED"
    assert rows["7000000002"].exception_type == "UNDERCHARGED"
    assert rows["expedia|7000000002|6666"].exception_type == "DUPLICATE_VCC"
    assert rows["7000000003"].exception_type == "CHARGED_OK"
    assert rows["7000000004"].exception_type == "EXPIRED_UNCHARGED"
    assert rows["7000000005"].exception_type == "NOT_YET_DUE"


def test_summary_numbers(world) -> None:
    settings, _ = world
    df, per, _, _ = _frames(settings)
    s = M.summary(per, df, settings)
    # E is due in October (future arrival), outside the period ending Sep 30.
    assert s.vcc_volume_cents == 30000 + 30000 + 30000 + 50000 + 12000
    assert s.leaked_cents == 30000 + 10000 + 50000 + 12000
    assert s.recovered_cents == 30000
    assert s.written_off_cents == 50000
    assert s.open_at_risk_cents == 10000 + 12000
    assert s.open_count == 3  # B, F, G
    assert s.leak_rate == pytest.approx(102000 / 152000)
    assert s.recovery_rate == pytest.approx(30000 / 102000)
    assert s.fee_cents == 6000 and s.owner_net_cents == 24000
    assert s.expiring_count == 1 and s.expiring_cents == 12000
    # recovered + B $100 x 0.85 + G $120 x 0.90
    assert s.recoverable_estimate_cents == 30000 + 8500 + 10800


def test_not_an_issue_is_excluded_from_leaked(world) -> None:
    settings, rows = world
    with session_scope(settings) as s:
        wf.change_status(s, rows["7000000002"].id, "not_an_issue", "tester", "Rate changed")
    df, per, _, _ = _frames(settings)
    assert M.summary(per, df, settings).leaked_cents == 30000 + 50000 + 12000


def test_scorecard_ranks_worst_operator_first(world) -> None:
    settings, _ = world
    _, per, _, _ = _frames(settings)
    card = M.scorecard(per)
    assert list(card["company"]) == ["Beta Mgmt", "Alpha Mgmt"]
    beta, alpha = card.iloc[0], card.iloc[1]
    assert beta["leak_rate"] == pytest.approx(1.0)
    assert alpha["leak_rate"] == pytest.approx(52000 / 102000)
    assert beta["expired_share"] == pytest.approx(1.0)
    assert alpha["avg_days_to_resolve"] == (date(2026, 9, 10) - date(2026, 8, 3)).days
    assert M.operator_takeaway(card).startswith("Beta Mgmt properties leak 2.0x more")


def test_breakdowns_add_up_to_summary(world) -> None:
    settings, _ = world
    df, per, start, end = _frames(settings)
    s = M.summary(per, df, settings)
    assert M.by_property(per)["leaked_cents"].sum() == s.leaked_cents
    assert M.by_type(per)["leaked_cents"].sum() == s.leaked_cents
    assert M.scorecard(per)["leaked_cents"].sum() == s.leaked_cents
    month = M.monthly(df, start, end)
    assert month["leaked_cents"].sum() == s.leaked_cents
    assert month["recovered_cents"].sum() == s.recovered_cents
    assert month.loc[month["month_label"] == "Sep 2026", "recovered_cents"].item() == 30000


def test_noi_impact(world) -> None:
    settings, _ = world
    df, per, _, _ = _frames(settings)
    s = M.summary(per, df, settings)
    half_year = M.noi_impact(s, date(2026, 4, 1), date(2026, 9, 27), settings)  # 180 days
    assert half_year.annualized_recovered_cents == round(30000 * 365 / 180)
    assert (
        half_year.annualized_owner_net_cents
        == half_year.annualized_recovered_cents - half_year.annualized_fee_cents
    )
    assert half_year.implied_value_cents == round(half_year.annualized_owner_net_cents / 0.08)


def test_top_actions_are_open_and_by_priority(world) -> None:
    settings, _ = world
    df, _, _, _ = _frames(settings)
    top = M.top_actions(df)
    assert list(top["ota_confirmation_no"])[0] == "7000000007"  # expires in 3 days
    assert set(top["status"]) <= {"open", "assigned", "in_progress"}


def test_formatting() -> None:
    assert M.money(123456) == "$1,235"
    assert M.money(123456, with_cents=True) == "$1,234.56"
    assert M.money(-50) == "-$1"
    assert M.money(-40) == "$0"
    assert M.pct(0.04237) == "4.2%"
    assert M.money_short(123_456_700) == "$1.2M"
    assert M.fmt_date(date(2026, 9, 3)) == "Sep 3, 2026"


# Workflow ------------------------------------------------------------------------


def _events(settings, exception_id):
    with session_scope(settings) as s:
        return [(e.event_type, e.old_value, e.new_value) for e in wf.timeline(s, exception_id)]


def test_work_an_item_from_open_to_recovered(world) -> None:
    settings, rows = world
    item = rows["7000000002"].id  # B, $100 at risk
    with session_scope(settings) as s:
        wf.assign(s, item, "T01 controller", "analyst")
        wf.add_note(s, item, "Called front desk", "analyst")
        wf.record_recovery(s, item, 4000, date(2026, 9, 28), "controller")
        assert s.get(Exception_, item).status == "in_progress"
        wf.record_recovery(s, item, 6000, date(2026, 9, 29), "controller")
        row = s.get(Exception_, item)
        assert row.status == "recovered" and row.recovered_cents == 10000
        assert "Called front desk" in row.notes
    events = _events(settings, item)
    assert [e[0] for e in events if e[0] != "detected"] == [
        "assigned",
        "status_changed",
        "note",
        "recovered",
        "status_changed",
        "recovered",
        "status_changed",
    ]
    assert ("status_changed", "in_progress", "recovered") in events


def test_recovery_cannot_exceed_card(world) -> None:
    settings, rows = world
    with session_scope(settings) as s, pytest.raises(wf.WorkflowError, match="more than"):
        wf.record_recovery(s, rows["7000000002"].id, 40000, AS_OF, "x")


def test_written_off_needs_a_note_and_a_user(world) -> None:
    settings, rows = world
    with session_scope(settings) as s:
        with pytest.raises(wf.WorkflowError, match="note"):
            wf.change_status(s, rows["7000000007"].id, "written_off", "x")
        with pytest.raises(wf.WorkflowError, match="name"):
            wf.change_status(s, rows["7000000007"].id, "in_progress", "  ")


def test_recovered_status_only_through_record_recovery(world) -> None:
    settings, rows = world
    with session_scope(settings) as s, pytest.raises(wf.WorkflowError, match="record recovery"):
        wf.change_status(s, rows["7000000007"].id, "recovered", "x")


def test_reopening_clears_recovery(world) -> None:
    settings, rows = world
    item = rows["7000000001"].id
    with session_scope(settings) as s:
        wf.change_status(s, item, "open", "x", "Charge bounced")
        row = s.get(Exception_, item)
        assert row.status == "open" and row.recovered_cents == 0
    assert ("recovery_cleared", "30000", "0") in _events(settings, item)


def test_ok_cards_cannot_be_worked(world) -> None:
    settings, rows = world
    with session_scope(settings) as s, pytest.raises(wf.WorkflowError, match="Nothing to work"):
        wf.assign(s, rows["7000000003"].id, "x", "y")


def test_fuzzy_match_must_be_confirmed_before_recovery(isolated_env) -> None:
    use_test_portfolio(isolated_env)
    sc = Scenario(isolated_env)
    sc.res("P1", None, "Lindqvist", date(2026, 8, 1), date(2026, 8, 3))
    sc.card("7000012345", "1111", 300, date(2026, 8, 1), date(2026, 11, 1), guest="Al Lindqvist")
    row = run(sc)["7000012345"]
    settings = load_settings()
    with session_scope(settings) as s:
        with pytest.raises(wf.WorkflowError, match="Confirm the match"):
            wf.record_recovery(s, row.id, 30000, AS_OF, "x")
        wf.confirm_match(s, row.id, "x")
        wf.record_recovery(s, row.id, 30000, AS_OF, "x")
        assert s.get(Exception_, row.id).status == "recovered"


def test_bulk_status_change(world) -> None:
    settings, _ = world
    with session_scope(settings) as s:
        ids = list(
            s.scalars(
                select(Exception_.id).where(Exception_.status == "open", Exception_.is_exception)
            )
        )
        assert wf.bulk_change_status(s, ids, "in_progress", "x", "Batch sent to properties") == len(
            ids
        )
    with session_scope(settings) as s:
        statuses = {s.get(Exception_, i).status for i in ids}
    assert statuses == {"in_progress"}


def test_human_status_survives_rematch(world) -> None:
    from leakguard.matching.engine import run_matching

    settings, rows = world
    run_matching(settings)
    with session_scope(settings) as s:
        assert s.get(Exception_, rows["7000000004"].id).status == "written_off"
        assert s.get(Exception_, rows["7000000001"].id).recovered_cents == 30000
