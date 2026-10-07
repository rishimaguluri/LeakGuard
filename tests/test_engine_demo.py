"""Engine against the demo ground truth, plus stability across runs."""

import csv
from collections import Counter
from datetime import datetime, timedelta

from openpyxl import load_workbook
from sqlalchemy import select

from leakguard.db import session_scope
from leakguard.ingest.importer import import_all
from leakguard.matching.engine import run_matching
from leakguard.models import Exception_, ExceptionEvent, Property


def _results(settings) -> dict[tuple[str, str], Exception_]:
    with session_scope(settings) as s:
        rows = s.execute(
            select(Property.code, Exception_).join(
                Exception_, Exception_.property_id == Property.id
            )
        ).all()
        return {(code, e.vcc_key): e for code, e in rows}


def test_every_injected_case_is_found(imported_demo) -> None:
    _, truth, settings, _, _ = imported_demo
    got = _results(settings)
    wrong = Counter()
    for code, prop in truth["properties"].items():
        for key, (expected_type, expected_method) in prop["results"].items():
            row = got[(code, key)]
            if row.exception_type != expected_type or row.match_method != expected_method:
                wrong[(expected_type, expected_method, row.exception_type, row.match_method)] += 1
    assert not wrong, wrong


def test_injected_counts_by_type(imported_demo) -> None:
    _, truth, _, _, match = imported_demo
    expected = Counter(t for p in truth["properties"].values() for t, _ in p["results"].values())
    assert dict(match.by_type) == dict(expected)


def test_rerun_changes_nothing(demo_copy) -> None:
    _, _, settings = demo_copy
    before = {k: (r.id, r.exception_type, r.status) for k, r in _results(settings).items()}
    summary = run_matching(settings)
    after = {k: (r.id, r.exception_type, r.status) for k, r in _results(settings).items()}
    assert before == after
    assert summary.new_exceptions == summary.type_changes == summary.auto_resolved == 0


def test_rerun_never_overwrites_human_status(demo_copy) -> None:
    _, _, settings = demo_copy
    with session_scope(settings) as s:
        rows = s.scalars(select(Exception_).where(Exception_.is_exception).limit(3)).all()
        for row, status in zip(rows, ["recovered", "written_off", "not_an_issue"], strict=True):
            row.status = status
            row.recovered_cents = 123 if status == "recovered" else 0
        ids = {r.id: r.status for r in rows}
    run_matching(settings)
    with session_scope(settings) as s:
        for row_id, status in ids.items():
            row = s.get(Exception_, row_id)
            assert row.status == status
            if status == "recovered":
                assert row.recovered_cents == 123


def test_late_charge_auto_resolves(demo_copy) -> None:
    """Charge an open UNCHARGED card by adding rows to the raw files, then rerun."""
    _, _, settings = demo_copy
    with session_scope(settings) as s:
        row, code = s.execute(
            select(Exception_, Property.code)
            .join(Property, Property.id == Exception_.property_id)
            .where(Exception_.exception_type == "UNCHARGED", Property.pms_name != "Opera")
            .order_by(Exception_.id)
        ).first()
        row_id, pms, last4 = row.id, row.pms_confirmation_no, row.card_last4
        amount, at_risk = row.expected_cents, row.amount_at_risk_cents
    folder = settings.raw_dir / "summit_ridge" / code
    pay = next((folder / "pms_payments").glob("*.csv"))
    encoding = "cp1252" if code == "CLT01" else "utf-8"
    delimiter = ";" if code == "CLT01" else ","
    with pay.open("a", newline="", encoding=encoding) as fh:
        csv.writer(fh, delimiter=delimiter).writerow(
            [
                pms,
                "09/29/2026",
                "Virtual Card - Expedia",
                last4,
                f"${amount / 100:,.2f}",
                "N",
                "fd01",
            ]
        )
    settle = next((folder / "processor_settlement").glob("*.xlsx"))
    wb = load_workbook(settle)
    wb.active.append(
        [
            datetime(2026, 9, 29),
            datetime(2026, 9, 30),
            "MASTERCARD",
            f"************{last4}",
            amount / 100,
            "SALE",
            "123456",
            "",
            "B260930",
            "T01",
        ]
    )
    wb.save(settle)
    import_all(settings)
    summary = run_matching(settings)
    assert summary.auto_resolved >= 1
    with session_scope(settings) as s:
        row = s.get(Exception_, row_id)
        assert row.status == "recovered"
        assert row.recovered_cents == at_risk
        assert row.exception_type == "UNCHARGED"  # the leak stays on record
        event = s.scalar(
            select(ExceptionEvent).where(
                ExceptionEvent.exception_id == row_id, ExceptionEvent.event_type == "auto_resolved"
            )
        )
        assert event.note == "auto-resolved: charge detected"


def test_card_expiring_changes_type_on_same_row(demo_copy) -> None:
    _, _, settings = demo_copy
    with session_scope(settings) as s:
        row = s.scalar(
            select(Exception_)
            .where(Exception_.exception_type == "UNCHARGED", Exception_.days_to_expiry < 20)
            .order_by(Exception_.id)
        )
        row_id = row.id
    later = settings.model_copy(
        update={"demo_as_of_date": settings.demo_as_of_date + timedelta(days=30)}
    )
    run_matching(later)
    with session_scope(settings) as s:
        row = s.get(Exception_, row_id)
        assert row.exception_type == "EXPIRED_UNCHARGED"
        assert any(e.event_type == "type_changed" for e in row.events)
