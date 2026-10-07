"""Import orchestration.

For every file under the current mode's raw folder:
  1. hash it; skip if the same file was already imported
  2. if a file at the same path changed, delete its old rows first
  3. read, pick a mapping, scan for card data, map and validate
  4. write rows, issues and a source_files record

The database mirrors the folder: if a file is removed from the folder, its
rows are removed on the next import.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from leakguard.config import Settings, load_portfolios, mappings_dir
from leakguard.db import session_scope
from leakguard.ingest.discover import DiscoveredFile, discover
from leakguard.ingest.mapping import Mapping, apply_mapping, choose_mapping, load_all_mappings
from leakguard.ingest.readers import UnsupportedFile, read_table
from leakguard.models import (
    DATA_TABLES,
    ImportIssue,
    ManagementCompany,
    PmsPayment,
    Portfolio,
    ProcessorTransaction,
    Property,
    Reservation,
    SourceFile,
    VccRecord,
)
from leakguard.schemas import SOURCE_LABELS, SOURCE_TARGETS, TARGETS

TARGET_MODELS = {
    "reservations": Reservation,
    "pms_payments": PmsPayment,
    "vcc_records": VccRecord,
    "processor_transactions": ProcessorTransaction,
}


@dataclass
class FileReport:
    relative_path: str
    source_name: str
    property_code: str
    status: str  # imported / replaced / unchanged / skipped / failed
    mapping_name: str | None = None
    rows_read: int = 0
    rows_imported: int = 0
    rows_rejected: int = 0
    date_min: date | None = None
    date_max: date | None = None
    top_issues: list[tuple[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class ImportSummary:
    reports: list[FileReport]
    problems: list[str]
    mapping_errors: list[str]
    removed_files: list[str] = field(default_factory=list)

    @property
    def rows_imported(self) -> int:
        return sum(r.rows_imported for r in self.reports)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# Portfolio config -> database --------------------------------------------------


def sync_portfolios(
    session: Session, settings: Settings
) -> dict[str, tuple[Portfolio, dict[str, Property]]]:
    """Upsert portfolios, management companies and properties for this mode."""
    out: dict[str, tuple[Portfolio, dict[str, Property]]] = {}
    for cfg in load_portfolios(settings.data_mode):
        portfolio = session.scalar(select(Portfolio).where(Portfolio.slug == cfg.slug))
        if portfolio is None:
            portfolio = Portfolio(slug=cfg.slug)
            session.add(portfolio)
        portfolio.name, portfolio.owner_name, portfolio.owner_type = (
            cfg.name,
            cfg.owner_name,
            cfg.owner_type,
        )
        session.flush()

        companies: dict[str, ManagementCompany] = {}
        for name in cfg.management_companies:
            mc = session.scalar(
                select(ManagementCompany).where(
                    ManagementCompany.portfolio_id == portfolio.id, ManagementCompany.name == name
                )
            )
            if mc is None:
                mc = ManagementCompany(portfolio_id=portfolio.id, name=name)
                session.add(mc)
                session.flush()
            companies[name] = mc

        props: dict[str, Property] = {}
        for p in cfg.properties:
            prop = session.scalar(
                select(Property).where(
                    Property.portfolio_id == portfolio.id, Property.code == p.code
                )
            )
            if prop is None:
                prop = Property(portfolio_id=portfolio.id, code=p.code)
                session.add(prop)
            if p.management_company not in companies:
                raise ValueError(
                    f"{p.code}: management company '{p.management_company}' is not listed for {cfg.slug}"
                )
            prop.management_company_id = companies[p.management_company].id
            prop.name, prop.brand, prop.segment = p.name, p.brand, p.segment
            prop.city, prop.state, prop.room_count = p.city, p.state, p.room_count
            prop.pms_name = p.pms_name
            prop.timezone = p.timezone or settings.default_timezone
            session.flush()
            props[p.code] = prop
        out[cfg.slug] = (portfolio, props)
    return out


# Single file ----------------------------------------------------------------------


def _delete_file_rows(session: Session, source_file_id: int) -> None:
    for model in (*DATA_TABLES, ImportIssue):
        session.execute(delete(model).where(model.source_file_id == source_file_id))
    session.execute(delete(SourceFile).where(SourceFile.id == source_file_id))


def _coverage(rows: list[dict], target: str) -> tuple[date | None, date | None]:
    col = TARGETS[target].coverage_date_field
    dates = [r.get(col) or r.get("expiry_date") or r.get("arrival_date") for r in rows]
    dates = [d for d in dates if d]
    return (min(dates), max(dates)) if dates else (None, None)


def _top_issues(issues: list, limit: int = 5) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for issue in issues:
        label = f"{issue.field}: {issue.message}" if issue.field else issue.message
        counts[label] += 1
    return counts.most_common(limit)


def import_file(
    session: Session,
    settings: Settings,
    found: DiscoveredFile,
    portfolio: Portfolio,
    prop: Property,
    mappings: list[Mapping],
    force_mapping: Mapping | None = None,
) -> FileReport:
    report = FileReport(found.relative_path, found.source_name, found.property_code, "imported")
    digest = file_hash(found.path)

    existing = session.scalar(
        select(SourceFile).where(
            SourceFile.property_id == prop.id, SourceFile.relative_path == found.relative_path
        )
    )
    if existing is not None:
        if existing.file_hash == digest and existing.status == "imported" and force_mapping is None:
            report.status = "unchanged"
            report.mapping_name = existing.mapping_name
            report.rows_imported = existing.rows_imported
            return report
        _delete_file_rows(session, existing.id)
        report.status = "replaced" if existing.file_hash != digest else "imported"

    twin = session.scalar(
        select(SourceFile).where(
            SourceFile.property_id == prop.id,
            SourceFile.source_name == found.source_name,
            SourceFile.file_hash == digest,
            SourceFile.status == "imported",
        )
    )

    record = SourceFile(
        portfolio_id=portfolio.id,
        property_id=prop.id,
        source_name=found.source_name,
        file_name=found.path.name,
        relative_path=found.relative_path,
        file_hash=digest,
        imported_at=_now(),
        data_mode=settings.data_mode,
    )

    def finish(status: str, message: str) -> FileReport:
        record.status, record.message = status, message
        session.add(record)
        session.flush()
        report.status, report.message = status, message
        return report

    if twin is not None:
        return finish("skipped", f"Same content as {twin.file_name}, which is already imported.")

    target_mappings = [m for m in mappings if m.source_name == found.source_name]
    known = set().union(*(m.known_headers() for m in target_mappings)) if target_mappings else None
    try:
        table = read_table(
            found.path,
            known_headers=known,
            sheet=force_mapping.sheet if force_mapping else None,
        )
    except UnsupportedFile as exc:
        return finish("skipped", str(exc))
    except Exception as exc:  # unreadable file: report it, keep importing others
        return finish("failed", f"Could not read the file: {exc}")

    mapping = force_mapping or choose_mapping(mappings, found.source_name, table.header)
    if mapping is None:
        label = SOURCE_LABELS.get(found.source_name, found.source_name)
        return finish(
            "failed",
            f"No mapping fits this {label} file. Its columns are: {', '.join(h for h in table.header if h)}. "
            "Use the Mapping wizard to create one.",
        )
    if mapping.sheet and table.sheet != mapping.sheet:
        table = read_table(found.path, known_headers=mapping.known_headers(), sheet=mapping.sheet)

    mapped = apply_mapping(mapping, table, found.path.name, settings.hash_guest_names)
    record.mapping_name = mapping.name
    record.rows_read = mapped.rows_read
    record.rows_imported = len(mapped.rows)
    record.rows_rejected = mapped.rows_rejected
    record.date_min, record.date_max = _coverage(mapped.rows, mapping.target)
    record.status = "imported"
    record.message = " ".join(mapped.warnings) or None
    session.add(record)
    session.flush()

    model = TARGET_MODELS[mapping.target]
    payload = []
    for row in mapped.rows:
        row = dict(row)
        row.pop("_row_number", None)
        row.update(portfolio_id=portfolio.id, property_id=prop.id, source_file_id=record.id)
        payload.append(row)
    if payload:
        session.execute(insert(model), payload)

    issue_rows = [
        {
            "portfolio_id": portfolio.id,
            "source_file_id": record.id,
            "row_number": i.row_number,
            "severity": i.severity,
            "field": i.field,
            "message": i.message,
        }
        for i in mapped.issues
    ] + [
        {
            "portfolio_id": portfolio.id,
            "source_file_id": record.id,
            "row_number": None,
            "severity": "warning",
            "field": "card_last4",
            "message": w,
        }
        for w in mapped.warnings
    ]
    if issue_rows:
        session.execute(insert(ImportIssue), issue_rows)

    report.mapping_name = mapping.name
    report.rows_read = mapped.rows_read
    report.rows_imported = len(mapped.rows)
    report.rows_rejected = mapped.rows_rejected
    report.date_min, report.date_max = record.date_min, record.date_max
    report.top_issues = _top_issues([i for i in mapped.issues if i.severity == "error"])
    report.warnings = mapped.warnings + [
        i.message for i in mapped.issues if i.severity == "warning"
    ]
    return report


# Whole folder -----------------------------------------------------------------------


def _remove_missing(session: Session, raw_dir: Path, portfolio_ids: list[int]) -> list[str]:
    removed = []
    records = session.scalars(select(SourceFile).where(SourceFile.portfolio_id.in_(portfolio_ids)))
    for record in list(records):
        if not (raw_dir / record.relative_path).exists():
            removed.append(record.relative_path)
            _delete_file_rows(session, record.id)
    return removed


def import_all(settings: Settings, progress: Callable[[str], None] | None = None) -> ImportSummary:
    mappings, mapping_errors = load_all_mappings(mappings_dir())
    with session_scope(settings) as session:
        portfolios = sync_portfolios(session, settings)
        known = {slug: set(props) for slug, (_, props) in portfolios.items()}
        found = discover(settings.raw_dir, known)
        removed = _remove_missing(session, settings.raw_dir, [p.id for p, _ in portfolios.values()])
        reports = []
        for f in found.files:
            portfolio, props = portfolios[f.portfolio_slug]
            if progress:
                progress(f.relative_path)
            reports.append(
                import_file(session, settings, f, portfolio, props[f.property_code], mappings)
            )
        return ImportSummary(reports, found.problems, mapping_errors, removed)


def format_summary(summary: ImportSummary) -> str:
    """Plain text import report for the terminal."""
    lines = []
    for r in summary.reports:
        head = f"[{r.status}] {r.relative_path}"
        if r.status in ("imported", "replaced"):
            span = f"{r.date_min} to {r.date_max}" if r.date_min else "no dates"
            head += (
                f"\n    mapping {r.mapping_name}: read {r.rows_read:,}, imported "
                f"{r.rows_imported:,}, rejected {r.rows_rejected:,}, covers {span}"
            )
            for message, count in r.top_issues:
                head += f"\n    issue x{count}: {message}"
            for w in r.warnings[:5]:
                head += f"\n    warning: {w}"
        elif r.message:
            head += f"\n    {r.message}"
        lines.append(head)
    for p in summary.problems + summary.mapping_errors:
        lines.append(f"[problem] {p}")
    for path in summary.removed_files:
        lines.append(f"[removed] {path} is no longer in the folder, its rows were deleted")
    totals = Counter(r.status for r in summary.reports)
    lines.append(
        "Done. "
        + ", ".join(f"{n} {s}" for s, n in sorted(totals.items()))
        + f". {summary.rows_imported:,} rows in new or changed files."
    )
    return "\n".join(lines)


def required_sources() -> list[str]:
    return list(SOURCE_TARGETS)
