"""Importer: expected rejects, card warning, idempotency, replacement."""

import shutil

from sqlalchemy import func, select

from leakguard.db import session_scope
from leakguard.ingest.importer import import_all
from leakguard.models import ImportIssue, PmsPayment, Reservation, SourceFile, VccRecord


def _count(settings, model) -> int:
    with session_scope(settings) as s:
        return s.scalar(select(func.count()).select_from(model))


def test_demo_imports_with_expected_rejects(imported_demo) -> None:
    _, truth, _, summary, _ = imported_demo
    assert not [r for r in summary.reports if r.status == "failed"]
    rejects = {r.relative_path: r.rows_rejected for r in summary.reports if r.rows_rejected}
    assert rejects == truth["rejects"]
    assert summary.problems == []


def test_full_card_number_file_warns(imported_demo) -> None:
    _, truth, settings, summary, _ = imported_demo
    [path] = truth["full_card_number_files"]
    report = next(r for r in summary.reports if r.relative_path == path)
    assert any(w.startswith("Full card numbers found in") for w in report.warnings)
    with session_scope(settings) as s:
        lengths = s.scalars(select(func.length(VccRecord.card_last4)).distinct()).all()
        assert set(lengths) <= {4, None}
        warning = s.scalar(
            select(ImportIssue).where(ImportIssue.message.like("Full card numbers found%"))
        )
        assert warning is not None and warning.severity == "warning"


def test_every_vcc_in_files_is_imported(imported_demo) -> None:
    _, truth, settings, _, _ = imported_demo
    expected = sum(p["cards"] for p in truth["properties"].values())
    assert _count(settings, VccRecord) == expected


def test_reimport_creates_no_duplicates(demo_copy) -> None:
    _, _, settings = demo_copy
    before = (
        _count(settings, Reservation),
        _count(settings, PmsPayment),
        _count(settings, VccRecord),
    )
    summary = import_all(settings)
    assert {r.status for r in summary.reports} == {"unchanged"}
    after = (
        _count(settings, Reservation),
        _count(settings, PmsPayment),
        _count(settings, VccRecord),
    )
    assert before == after


def test_corrected_file_replaces_old_rows(demo_copy) -> None:
    root, _, settings = demo_copy
    path = next((settings.raw_dir / "summit_ridge" / "CMH01" / "booking_vcc").glob("*.csv"))
    lines = path.read_text(encoding="utf-8").splitlines()
    before = _count(settings, VccRecord)
    path.write_text("\n".join(lines[:-5]) + "\n", encoding="utf-8")  # drop 5 cards
    summary = import_all(settings)
    report = next(
        r
        for r in summary.reports
        if r.relative_path.endswith(path.name) and "CMH01" in r.relative_path
    )
    assert report.status == "replaced"
    assert _count(settings, VccRecord) == before - 5


def test_copied_file_is_not_imported_twice(demo_copy) -> None:
    _, _, settings = demo_copy
    folder = settings.raw_dir / "summit_ridge" / "IND01" / "booking_vcc"
    original = next(folder.glob("*.csv"))
    shutil.copy(original, folder / "copy_of_cards.csv")
    before = _count(settings, VccRecord)
    summary = import_all(settings)
    copy = next(r for r in summary.reports if r.relative_path.endswith("copy_of_cards.csv"))
    assert copy.status == "skipped" and "Same content" in copy.message
    assert _count(settings, VccRecord) == before


def test_removed_file_rows_are_deleted(demo_copy) -> None:
    _, _, settings = demo_copy
    path = next((settings.raw_dir / "summit_ridge" / "RAL01" / "pms_payments").glob("*.csv"))
    before = _count(settings, PmsPayment)
    path.unlink()
    summary = import_all(settings)
    assert any("RAL01/pms_payments" in p for p in summary.removed_files)
    assert _count(settings, PmsPayment) < before


def test_pdf_and_unknown_folders_are_reported(demo_copy) -> None:
    _, _, settings = demo_copy
    raw = settings.raw_dir / "summit_ridge"
    (raw / "CMH01" / "expedia_vcc" / "statement.pdf").write_bytes(b"%PDF-1.4")
    (raw / "ZZZ99" / "expedia_vcc").mkdir(parents=True)
    (raw / "CMH01" / "random_stuff").mkdir()
    summary = import_all(settings)
    pdf = next(r for r in summary.reports if r.relative_path.endswith("statement.pdf"))
    assert pdf.status == "skipped" and "PDF" in pdf.message
    assert any("ZZZ99" in p for p in summary.problems)
    assert any("random_stuff" in p for p in summary.problems)
    with session_scope(settings) as s:
        record = s.scalar(select(SourceFile).where(SourceFile.file_name == "statement.pdf"))
        assert record.status == "skipped"


def test_unknown_format_fails_with_wizard_hint(demo_copy) -> None:
    _, _, settings = demo_copy
    folder = settings.raw_dir / "summit_ridge" / "CMH01" / "processor_settlement"
    (folder / "other_gateway.csv").write_text("Foo,Bar\n1,2\n", encoding="utf-8")
    summary = import_all(settings)
    report = next(r for r in summary.reports if r.relative_path.endswith("other_gateway.csv"))
    assert report.status == "failed" and "Mapping wizard" in report.message
