"""Matching rules and engine, through the real import pipeline."""

from datetime import date, timedelta

import pytest

from leakguard.matching.priority import priority_score

from .scenario import Scenario, run, use_test_portfolio

AS_OF = date(2026, 9, 30)
D = timedelta


@pytest.fixture
def sc(isolated_env):
    use_test_portfolio(isolated_env)
    return Scenario(isolated_env)


def stay(
    sc: Scenario,
    n: int,
    *,
    last4: str = "1111",
    amount: float = 300.0,
    arrival: date = date(2026, 8, 1),
    expiry_days: int = 90,
    status: str = "Checked Out",
    name: str = "Guest",
) -> tuple[str, str]:
    """A reservation plus its Expedia card. Returns (pms conf, ota conf)."""
    pms, ota = f"P{n}", f"70000{n:05d}"
    sc.res(pms, ota, name, arrival, arrival + D(2), status=status, amount=amount)
    sc.card(
        ota, last4, amount, arrival, arrival + D(expiry_days), guest=f"Pat {name}", arrival=arrival
    )
    return pms, ota


def charge(
    sc: Scenario,
    pms: str,
    amount: float,
    when: date = date(2026, 8, 1),
    last4: str = "1111",
    settle: bool = True,
    **kw,
) -> None:
    sc.pay(pms, when, amount, last4=last4, **kw)
    if settle and last4:
        sc.txn(when, last4, abs(amount), "REFUND" if kw.get("reversal") else "SALE")


def test_charged_and_settled_is_ok(sc) -> None:
    pms, ota = stay(sc, 1)
    charge(sc, pms, 300)
    row = run(sc)[ota]
    assert row.exception_type == "CHARGED_OK" and not row.is_exception
    assert row.charged_cents == row.settled_cents == 30000


def test_partial_charges_across_postings_add_up(sc) -> None:
    pms, ota = stay(sc, 1, last4="2222")
    charge(sc, pms, 120, last4="2222")
    charge(sc, pms, 180, when=date(2026, 8, 2), last4="2222")
    assert run(sc)[ota].exception_type == "CHARGED_OK"


def test_reversal_then_repost_nets_out(sc) -> None:
    pms, ota = stay(sc, 1)
    charge(sc, pms, 300)
    charge(sc, pms, 300, reversal=True)
    charge(sc, pms, 300, when=date(2026, 8, 2))
    row = run(sc)[ota]
    assert row.exception_type == "CHARGED_OK" and row.charged_cents == 30000


def test_reversal_without_repost_is_uncharged(sc) -> None:
    pms, ota = stay(sc, 1)
    charge(sc, pms, 300)
    charge(sc, pms, 300, reversal=True)
    row = run(sc)[ota]
    assert row.exception_type == "UNCHARGED" and row.amount_at_risk_cents == 30000


def test_undercharged_and_overcharged(sc) -> None:
    pms1, ota1 = stay(sc, 1, last4="1111")
    charge(sc, pms1, 200, last4="1111")
    pms2, ota2 = stay(sc, 2, last4="2222")
    charge(sc, pms2, 340, last4="2222")
    pms3, ota3 = stay(sc, 3, last4="3333")
    charge(sc, pms3, 300.75, last4="3333")  # within $1 tolerance
    rows = run(sc)
    assert rows[ota1].exception_type == "UNDERCHARGED" and rows[ota1].amount_at_risk_cents == 10000
    assert rows[ota2].exception_type == "OVERCHARGED" and rows[ota2].amount_at_risk_cents == 4000
    assert rows[ota3].exception_type == "CHARGED_OK"


def test_charged_not_settled(sc) -> None:
    pms, ota = stay(sc, 1, last4="1111")
    charge(sc, pms, 300, last4="1111", settle=False)
    pms2, _ = stay(sc, 2, last4="2222")
    charge(sc, pms2, 300, last4="2222")  # gives the processor file coverage
    row = run(sc)[ota]
    assert row.exception_type == "CHARGED_NOT_SETTLED"
    assert row.amount_at_risk_cents == 30000 and row.settled_cents == 0


def test_no_processor_data_skips_settlement_check(sc) -> None:
    pms, ota = stay(sc, 1)
    sc.pay(pms, date(2026, 8, 1), 300, last4="1111")
    row = run(sc)[ota]
    assert row.exception_type == "CHARGED_OK" and row.settled_cents is None
    assert "settlement was not checked" in row.rule


def test_other_currency_postings_are_not_counted(sc) -> None:
    pms, ota = stay(sc, 1)
    sc.pay(pms, date(2026, 8, 1), 300, last4="1111", currency="EUR")
    row = run(sc)[ota]
    assert row.exception_type == "UNCHARGED"
    assert "currency" in row.rule and row.confidence == "medium"


def test_missing_last4_falls_back_to_payment_type(sc) -> None:
    pms, ota = stay(sc, 1, last4="4321")
    sc.pay(pms, date(2026, 8, 1), 300, last4=None, method="Virtual Card - Expedia")
    sc.txn(date(2026, 8, 1), "4321", 300)
    row = run(sc)[ota]
    assert row.exception_type == "CHARGED_OK"
    assert row.confidence == "medium" and "no card last 4" in row.rule


def test_guest_card_with_missing_last4_is_not_counted(sc) -> None:
    pms, ota = stay(sc, 1)
    sc.pay(pms, date(2026, 8, 3), 300, last4=None, method="Cash")
    assert run(sc)[ota].exception_type == "UNCHARGED"


def test_expiry_today_is_still_active(sc) -> None:
    _, ota_today = stay(sc, 1, arrival=AS_OF - D(30), expiry_days=30)
    _, ota_past = stay(sc, 2, arrival=AS_OF - D(31), expiry_days=30, last4="2222")
    rows = run(sc)
    assert rows[ota_today].exception_type == "UNCHARGED"
    assert rows[ota_today].days_to_expiry == 0
    assert rows[ota_past].exception_type == "EXPIRED_UNCHARGED"
    assert rows[ota_past].days_to_expiry == -1


def test_future_arrival_is_not_yet_due(sc) -> None:
    _, ota = stay(sc, 1, arrival=AS_OF + D(5), status="Confirmed")
    row = run(sc)[ota]
    assert row.exception_type == "NOT_YET_DUE" and not row.is_exception


def test_stale_reserved_status_after_departure_is_flagged(sc) -> None:
    _, ota = stay(sc, 1, arrival=AS_OF - D(10), status="Confirmed")
    assert run(sc)[ota].exception_type == "UNCHARGED"


def test_cancelled_with_card_needs_review(sc) -> None:
    _, ota = stay(sc, 1, status="Cancelled")
    pms2, ota2 = stay(sc, 2, status="No Show", last4="2222")
    charge(sc, pms2, 300, last4="2222")  # no-show fee charged
    rows = run(sc)
    assert rows[ota].exception_type == "CANCELLED_REVIEW"
    assert rows[ota2].exception_type == "CHARGED_OK"


def test_cancelled_then_rebooked_links_to_live_reservation(sc) -> None:
    ota = "7000099999"
    sc.res("OLD1", ota, "Guest", date(2026, 7, 1), date(2026, 7, 3), status="Cancelled")
    sc.res("NEW1", ota, "Guest", date(2026, 8, 1), date(2026, 8, 3))
    sc.card(ota, "1111", 300, date(2026, 8, 1), date(2026, 11, 1))
    charge(sc, "NEW1", 300)
    row = run(sc)[ota]
    assert row.pms_confirmation_no == "NEW1" and row.exception_type == "CHARGED_OK"


def test_duplicate_card_for_same_reservation(sc) -> None:
    pms, ota = stay(sc, 1, last4="1111")
    sc.card(ota, "9999", 300, date(2026, 8, 3), date(2026, 11, 3))
    charge(sc, pms, 300)
    rows = run(sc)
    assert rows[ota].exception_type == "CHARGED_OK"
    dup = rows[f"expedia|{ota}|9999"]
    assert dup.exception_type == "DUPLICATE_VCC" and dup.amount_at_risk_cents == 0


def test_fuzzy_match_on_name_arrival_and_amount(sc) -> None:
    sc.res("P1", None, "O'Brien", date(2026, 8, 2), date(2026, 8, 4), amount=300)
    sc.card(
        "7000012345",
        "1111",
        300,
        date(2026, 8, 1),
        date(2026, 11, 1),
        guest="Pat OBrien",
        arrival=date(2026, 8, 1),
    )
    row = run(sc)["7000012345"]
    assert row.match_method == "fuzzy" and row.pms_confirmation_no == "P1"
    assert row.exception_type == "UNCHARGED" and row.confidence == "medium"
    assert "Confirm the match" in row.rule


@pytest.mark.parametrize(
    ("pms_name", "pms_arrival", "pms_amount"),
    [
        ("Thompson", date(2026, 8, 4), 300),  # arrival 3 days off
        ("Henderson", date(2026, 8, 1), 300),  # different name
        ("Thompson", date(2026, 8, 1), 420),  # amount 40% off
    ],
)
def test_fuzzy_match_that_should_not_match(sc, pms_name, pms_arrival, pms_amount) -> None:
    sc.res("P1", None, pms_name, pms_arrival, pms_arrival + D(2), amount=pms_amount)
    sc.card(
        "7000012345",
        "1111",
        300,
        date(2026, 8, 1),
        date(2026, 11, 1),
        guest="Pat Thompson",
        arrival=date(2026, 8, 1),
    )
    row = run(sc)["7000012345"]
    assert row.exception_type == "NO_PMS_MATCH" and row.match_method == "none"


def test_ambiguous_fuzzy_candidates_are_not_picked(sc) -> None:
    sc.res("P1", None, "Lee", date(2026, 8, 1), date(2026, 8, 3), amount=300)
    sc.res("P2", None, "Lee", date(2026, 8, 1), date(2026, 8, 3), amount=305)
    sc.card(
        "7000012345",
        "1111",
        300,
        date(2026, 8, 1),
        date(2026, 11, 1),
        guest="Pat Lee",
        arrival=date(2026, 8, 1),
    )
    row = run(sc)["7000012345"]
    assert row.exception_type == "NO_PMS_MATCH" and "2 reservations look similar" in row.rule


def test_hashed_names_need_exact_match(sc) -> None:
    sc.res("P1", None, "OBrien", date(2026, 8, 1), date(2026, 8, 3))
    sc.card(
        "7000012345",
        "1111",
        300,
        date(2026, 8, 1),
        date(2026, 11, 1),
        guest="Pat O'Brien",
        arrival=date(2026, 8, 1),
    )
    sc.res("P2", None, "Thompson", date(2026, 8, 1), date(2026, 8, 3))
    sc.card(
        "7000054321",
        "2222",
        300,
        date(2026, 8, 1),
        date(2026, 11, 1),
        guest="Pat Thomson",
        arrival=date(2026, 8, 1),
    )
    rows = run(sc, hash_guest_names=True)
    assert rows["7000012345"].match_method == "fuzzy"  # same letters, same hash
    assert rows["7000054321"].match_method == "none"  # typo no longer matches


# Priority -------------------------------------------------------------------


def test_priority_formula_examples_from_docstring() -> None:
    assert priority_score("UNCHARGED", 100_000, 0) == 3000
    assert priority_score("UNCHARGED", 100_000, 30) == 1000
    assert priority_score("UNCHARGED", 100_000, 90) == 1000
    assert priority_score("EXPIRED_UNCHARGED", 100_000, -5) == 500
    assert priority_score("UNCHARGED", 100_000, 15) == 2000


def test_expired_ranks_below_active_of_similar_value() -> None:
    active = priority_score("UNCHARGED", 50_000, 60)
    expired = priority_score("EXPIRED_UNCHARGED", 60_000, -1)
    assert active > expired


def test_bigger_and_sooner_rank_higher() -> None:
    assert priority_score("UNCHARGED", 200_000, 20) > priority_score("UNCHARGED", 100_000, 20)
    assert priority_score("UNCHARGED", 100_000, 2) > priority_score("UNCHARGED", 100_000, 20)


def test_non_exceptions_score_zero_and_duplicates_score_low() -> None:
    assert priority_score("CHARGED_OK", 0, 10) == 0
    assert priority_score("NOT_YET_DUE", 0, 10) == 0
    assert (
        0
        < priority_score("DUPLICATE_VCC", 0, 10, 100_000)
        < priority_score("UNCHARGED", 100_000, 90)
    )
