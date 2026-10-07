"""Every number shown in the dashboard, Excel report and PDF comes from here.

Definitions (period = card due date within the selected date range):
  OTA-collect revenue   sum of virtual card amounts (duplicates excluded)
  Leaked                sum of amount at risk on exceptions, excluding items a
                        person marked "not an issue"
  Recovered             sum of recovered amounts on those exceptions
  Written off           amount at risk on exceptions marked written off
  Open at risk          amount at risk on exceptions still open, assigned or
                        in progress
  Leak rate             leaked / OTA-collect revenue
  Recovery rate         recovered / leaked
  Expiring soon         open items on cards not yet charged in full, expiring
                        within the upcoming-expiry window (ignores the period
                        filter, because it is about today)
  Days to resolve       recovered date minus due date (checkout for stays)
  Annualized recovery   recovered x 365 / days in period
  Fee                   recovered x fee percentage
  Owner net gain        recovered minus fee
  Implied value         annualized owner net / cap rate (an estimate)
  Recoverable estimate  recovered plus open at risk weighted by RECOVERY_ODDS
                        (an estimate, never a promise)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.config import Settings
from leakguard.models import (
    Exception_,
    ImportIssue,
    ManagementCompany,
    Portfolio,
    Property,
    SourceFile,
)
from leakguard.schemas import (
    OPEN_STATUSES,
    REQUIRED_SOURCES,
    SOURCE_LABELS,
    STATUS_LABELS,
    TYPE_LABELS,
)

# Rough odds that open dollars turn into cash, used only for the labeled estimate.
RECOVERY_ODDS = {
    "UNCHARGED": 0.90,
    "UNDERCHARGED": 0.85,
    "CHARGED_NOT_SETTLED": 0.80,
    "EXPIRED_UNCHARGED": 0.35,
    "CANCELLED_REVIEW": 0.40,
    "NO_PMS_MATCH": 0.30,
    "OVERCHARGED": 0.0,
    "DUPLICATE_VCC": 0.0,
}
EXPIRING_TYPES = ("UNCHARGED", "UNDERCHARGED", "NOT_YET_DUE", "CANCELLED_REVIEW", "NO_PMS_MATCH")
COVERAGE_GAP_DAYS = 14

COLUMNS = [
    "id",
    "property_id",
    "property_code",
    "property_name",
    "company_id",
    "company",
    "room_count",
    "ota",
    "ota_confirmation_no",
    "pms_confirmation_no",
    "card_last4",
    "guest_last_name",
    "exception_type",
    "is_exception",
    "match_method",
    "match_confirmed",
    "confidence",
    "rule",
    "amount_at_risk_cents",
    "expected_cents",
    "charged_cents",
    "settled_cents",
    "currency",
    "arrival_date",
    "departure_date",
    "due_date",
    "expiry_date",
    "priority_score",
    "status",
    "assigned_to",
    "recovered_cents",
    "recovered_date",
    "notes",
    "vcc_record_id",
    "reservation_id",
    "first_detected_at",
]


@dataclass(frozen=True)
class Filters:
    portfolio_id: int
    start: date | None = None
    end: date | None = None
    company_ids: tuple[int, ...] = ()
    property_ids: tuple[int, ...] = ()


def default_period(as_of: date, months: int = 12) -> tuple[date, date]:
    """The last N full or partial months ending on as_of."""
    year, month = as_of.year, as_of.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1), as_of


# Loading -----------------------------------------------------------------------


def portfolios(session: Session) -> list[Portfolio]:
    return list(session.scalars(select(Portfolio).order_by(Portfolio.name)))


def companies(session: Session, portfolio_id: int) -> list[ManagementCompany]:
    return list(
        session.scalars(
            select(ManagementCompany)
            .where(ManagementCompany.portfolio_id == portfolio_id)
            .order_by(ManagementCompany.name)
        )
    )


def properties(session: Session, portfolio_id: int) -> list[Property]:
    return list(
        session.scalars(
            select(Property).where(Property.portfolio_id == portfolio_id).order_by(Property.code)
        )
    )


def load_frame(session: Session, filters: Filters, as_of: date) -> pd.DataFrame:
    """One row per virtual card for the portfolio, company and property filters.

    The period filter is not applied here; use in_period(). Only active rows
    and rows a person already recovered are included.
    """
    q = (
        select(
            Exception_,
            Property.code,
            Property.name,
            Property.room_count,
            ManagementCompany.id,
            ManagementCompany.name,
        )
        .join(Property, Property.id == Exception_.property_id)
        .join(ManagementCompany, ManagementCompany.id == Property.management_company_id)
        .where(Exception_.portfolio_id == filters.portfolio_id)
        .where(Exception_.is_active | (Exception_.status == "recovered"))
    )
    if filters.company_ids:
        q = q.where(ManagementCompany.id.in_(filters.company_ids))
    if filters.property_ids:
        q = q.where(Property.id.in_(filters.property_ids))
    records = []
    for e, code, pname, rooms, cid, cname in session.execute(q):
        records.append(
            {
                "id": e.id,
                "property_id": e.property_id,
                "property_code": code,
                "property_name": pname,
                "company_id": cid,
                "company": cname,
                "room_count": rooms,
                "ota": e.ota,
                "ota_confirmation_no": e.ota_confirmation_no,
                "pms_confirmation_no": e.pms_confirmation_no,
                "card_last4": e.card_last4,
                "guest_last_name": e.guest_last_name,
                "exception_type": e.exception_type,
                "is_exception": e.is_exception,
                "match_method": e.match_method,
                "match_confirmed": e.match_confirmed,
                "confidence": e.confidence,
                "rule": e.rule,
                "amount_at_risk_cents": e.amount_at_risk_cents,
                "expected_cents": e.expected_cents,
                "charged_cents": e.charged_cents,
                "settled_cents": e.settled_cents,
                "currency": e.currency,
                "arrival_date": e.arrival_date,
                "departure_date": e.departure_date,
                "due_date": e.due_date,
                "expiry_date": e.expiry_date,
                "priority_score": e.priority_score,
                "status": e.status,
                "assigned_to": e.assigned_to,
                "recovered_cents": e.recovered_cents,
                "recovered_date": e.recovered_date,
                "notes": e.notes,
                "vcc_record_id": e.vcc_record_id,
                "reservation_id": e.reservation_id,
                "first_detected_at": e.first_detected_at,
            }
        )
    df = pd.DataFrame.from_records(records, columns=COLUMNS)
    if df.empty:
        return df
    df["days_to_expiry"] = [
        (d - as_of).days if isinstance(d, date) else None for d in df["expiry_date"]
    ]
    df["type_label"] = df["exception_type"].map(TYPE_LABELS)
    df["status_label"] = df["status"].map(STATUS_LABELS)
    df["is_open"] = df["is_exception"] & df["status"].isin(OPEN_STATUSES)
    df["counts_as_leak"] = df["is_exception"] & (df["status"] != "not_an_issue")
    return df


def in_period(df: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    if df.empty or (start is None and end is None):
        return df
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= df["due_date"].map(lambda d: d is not None and d >= start)
    if end is not None:
        mask &= df["due_date"].map(lambda d: d is not None and d <= end)
    return df[mask]


def exceptions_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["is_exception"]] if not df.empty else df


# Core numbers --------------------------------------------------------------------


@dataclass
class Summary:
    vcc_count: int = 0
    vcc_volume_cents: int = 0
    exception_count: int = 0
    leaked_cents: int = 0
    recovered_cents: int = 0
    written_off_cents: int = 0
    open_at_risk_cents: int = 0
    open_count: int = 0
    expiring_count: int = 0
    expiring_cents: int = 0
    fee_cents: int = 0
    owner_net_cents: int = 0
    recoverable_estimate_cents: int = 0
    leak_rate: float = 0.0
    recovery_rate: float = 0.0
    by_type_cents: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.vcc_count == 0


def _sum(df: pd.DataFrame, col: str) -> int:
    return int(df[col].sum()) if not df.empty else 0


def leaked_cents(df: pd.DataFrame) -> int:
    return _sum(df[df["counts_as_leak"]], "amount_at_risk_cents") if not df.empty else 0


def vcc_volume_cents(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    return _sum(df[df["exception_type"] != "DUPLICATE_VCC"], "expected_cents")


def expiring(df_all: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Open items on cards not fully charged that expire within the window."""
    if df_all.empty:
        return df_all
    days = df_all["days_to_expiry"]
    mask = (
        df_all["exception_type"].isin(EXPIRING_TYPES)
        & (df_all["status"].isin(OPEN_STATUSES))
        & days.notna()
        & (days >= 0)
        & (days <= window_days)
    )
    out = df_all[mask].copy()
    out["uncollected_cents"] = (out["expected_cents"] - out["charged_cents"]).clip(lower=0)
    return out.sort_values(["days_to_expiry", "uncollected_cents"], ascending=[True, False])


def summary(df_period: pd.DataFrame, df_all: pd.DataFrame, settings: Settings) -> Summary:
    s = Summary()
    if df_period.empty and df_all.empty:
        return s
    exc = df_period[df_period["counts_as_leak"]] if not df_period.empty else df_period
    s.vcc_count = (
        int((df_period["exception_type"] != "DUPLICATE_VCC").sum()) if not df_period.empty else 0
    )
    s.vcc_volume_cents = vcc_volume_cents(df_period)
    s.exception_count = len(exc)
    s.leaked_cents = _sum(exc, "amount_at_risk_cents")
    s.recovered_cents = _sum(exc, "recovered_cents")
    s.written_off_cents = (
        _sum(exc[exc["status"] == "written_off"], "amount_at_risk_cents") if not exc.empty else 0
    )
    open_rows = exc[exc["status"].isin(OPEN_STATUSES)] if not exc.empty else exc
    s.open_at_risk_cents = _sum(open_rows, "amount_at_risk_cents") - _sum(
        open_rows, "recovered_cents"
    )
    s.open_count = len(open_rows)
    soon = expiring(df_all, settings.upcoming_expiry_days)
    s.expiring_count = len(soon)
    s.expiring_cents = _sum(soon, "uncollected_cents")
    s.fee_cents = round(s.recovered_cents * settings.fee_pct)
    s.owner_net_cents = s.recovered_cents - s.fee_cents
    s.leak_rate = s.leaked_cents / s.vcc_volume_cents if s.vcc_volume_cents else 0.0
    s.recovery_rate = s.recovered_cents / s.leaked_cents if s.leaked_cents else 0.0
    if not open_rows.empty:
        odds = open_rows["exception_type"].map(RECOVERY_ODDS).fillna(0)
        remaining = open_rows["amount_at_risk_cents"] - open_rows["recovered_cents"]
        s.recoverable_estimate_cents = s.recovered_cents + int((remaining * odds).sum())
    else:
        s.recoverable_estimate_cents = s.recovered_cents
    if not exc.empty:
        s.by_type_cents = {
            str(k): int(v)
            for k, v in exc.groupby("exception_type")["amount_at_risk_cents"].sum().items()
        }
    return s


@dataclass
class NoiImpact:
    period_days: int
    recovered_cents: int
    annualized_recovered_cents: int
    fee_pct: float
    annualized_fee_cents: int
    annualized_owner_net_cents: int
    cap_rate: float
    implied_value_cents: int
    annualized_leak_cents: int


def noi_impact(s: Summary, start: date, end: date, settings: Settings) -> NoiImpact:
    days = max((end - start).days + 1, 1)
    factor = 365 / days
    annual = round(s.recovered_cents * factor)
    fee = round(annual * settings.fee_pct)
    net = annual - fee
    return NoiImpact(
        period_days=days,
        recovered_cents=s.recovered_cents,
        annualized_recovered_cents=annual,
        fee_pct=settings.fee_pct,
        annualized_fee_cents=fee,
        annualized_owner_net_cents=net,
        cap_rate=settings.cap_rate,
        implied_value_cents=round(net / settings.cap_rate) if settings.cap_rate else 0,
        annualized_leak_cents=round(s.leaked_cents * factor),
    )


# Breakdowns ---------------------------------------------------------------------


def _group_table(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    leak = df[df["counts_as_leak"]]
    base = df[df["exception_type"] != "DUPLICATE_VCC"]
    out = base.groupby(keys).agg(cards=("id", "count"), vcc_volume_cents=("expected_cents", "sum"))
    lk = leak.groupby(keys).agg(
        exceptions=("id", "count"),
        leaked_cents=("amount_at_risk_cents", "sum"),
        recovered_cents=("recovered_cents", "sum"),
    )
    open_rows = leak[leak["status"].isin(OPEN_STATUSES)]
    op = (
        open_rows.assign(
            open_cents=open_rows["amount_at_risk_cents"] - open_rows["recovered_cents"]
        )
        .groupby(keys)
        .agg(open_cents=("open_cents", "sum"), open_count=("id", "count"))
    )
    expired = (
        leak[leak["exception_type"] == "EXPIRED_UNCHARGED"]
        .groupby(keys)
        .agg(expired_cents=("amount_at_risk_cents", "sum"))
    )
    out = out.join(lk, how="outer").join(op, how="outer").join(expired, how="outer").fillna(0)
    for col in out.columns:
        out[col] = out[col].astype("int64")
    out["leak_rate"] = (out["leaked_cents"] / out["vcc_volume_cents"]).where(
        out["vcc_volume_cents"] > 0, 0.0
    )
    out["recovery_rate"] = (out["recovered_cents"] / out["leaked_cents"]).where(
        out["leaked_cents"] > 0, 0.0
    )
    out["expired_share"] = (out["expired_cents"] / out["leaked_cents"]).where(
        out["leaked_cents"] > 0, 0.0
    )
    return out.reset_index()


def by_property(df: pd.DataFrame) -> pd.DataFrame:
    table = _group_table(
        df, ["property_id", "property_code", "property_name", "company", "room_count"]
    )
    return table.sort_values("leaked_cents", ascending=False) if not table.empty else table


def by_type(df: pd.DataFrame) -> pd.DataFrame:
    leak = df[df["counts_as_leak"]] if not df.empty else df
    if leak.empty:
        return pd.DataFrame(
            columns=["exception_type", "label", "count", "leaked_cents", "recovered_cents"]
        )
    out = (
        leak.groupby("exception_type")
        .agg(
            count=("id", "count"),
            leaked_cents=("amount_at_risk_cents", "sum"),
            recovered_cents=("recovered_cents", "sum"),
        )
        .reset_index()
    )
    out["label"] = out["exception_type"].map(TYPE_LABELS)
    out["leaked_cents"] = out["leaked_cents"].astype("int64")
    out["recovered_cents"] = out["recovered_cents"].astype("int64")
    return out.sort_values("leaked_cents", ascending=False)


def days_to_resolve(df: pd.DataFrame) -> pd.Series:
    rec = (
        df[(df["status"] == "recovered") & df["recovered_date"].notna() & df["due_date"].notna()]
        if not df.empty
        else df
    )
    if rec.empty:
        return pd.Series(dtype="float64")
    return pd.Series(
        [(r - d).days for r, d in zip(rec["recovered_date"], rec["due_date"], strict=True)],
        index=rec.index,
    )


def scorecard(df: pd.DataFrame) -> pd.DataFrame:
    """Management companies ranked by leak rate, worst first."""
    table = _group_table(df, ["company_id", "company"])
    if table.empty:
        return table
    props = df.groupby("company_id")["property_id"].nunique()
    table["properties"] = table["company_id"].map(props).astype("int64")
    resolve = days_to_resolve(df)
    if not resolve.empty:
        avg = resolve.groupby(df.loc[resolve.index, "company_id"]).mean()
        table["avg_days_to_resolve"] = table["company_id"].map(avg)
    else:
        table["avg_days_to_resolve"] = float("nan")
    table = table.sort_values("leak_rate", ascending=False).reset_index(drop=True)
    table["rank"] = range(1, len(table) + 1)
    return table


def operator_takeaway(card: pd.DataFrame) -> str:
    """One plain sentence comparing operators."""
    if card.empty:
        return ""
    if len(card) == 1:
        row = card.iloc[0]
        return f"All properties are run by {row['company']}. Leak rate is {pct(row['leak_rate'])} of OTA-collect revenue."
    worst, best = card.iloc[0], card.iloc[-1]
    if best["leak_rate"] <= 0:
        return f"{worst['company']} properties leak {pct(worst['leak_rate'])} of OTA-collect revenue. Other operators show no leakage."
    ratio = worst["leak_rate"] / best["leak_rate"]
    if ratio < 1.15:
        return f"Operators leak at similar rates, between {pct(best['leak_rate'])} and {pct(worst['leak_rate'])} of OTA-collect revenue."
    return (
        f"{worst['company']} properties leak {ratio:.1f}x more per dollar of OTA-collect revenue "
        f"than {best['company']} ({pct(worst['leak_rate'])} vs {pct(best['leak_rate'])})."
    )


def _month_index(start: date, end: date) -> pd.PeriodIndex:
    return pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M")


def monthly(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Leaked by due month and recovered by recovered month."""
    months = _month_index(start, end)
    out = pd.DataFrame(index=months)
    out["leaked_cents"] = 0
    out["recovered_cents"] = 0
    out["vcc_volume_cents"] = 0
    if not df.empty:
        leak = in_period(df[df["counts_as_leak"]], start, end)
        if not leak.empty:
            g = leak.groupby(leak["due_date"].map(lambda d: pd.Period(d, "M")))[
                "amount_at_risk_cents"
            ].sum()
            out["leaked_cents"] = g.reindex(months, fill_value=0).astype("int64")
        vol = in_period(df[df["exception_type"] != "DUPLICATE_VCC"], start, end)
        if not vol.empty:
            g = vol.groupby(vol["due_date"].map(lambda d: pd.Period(d, "M")))[
                "expected_cents"
            ].sum()
            out["vcc_volume_cents"] = g.reindex(months, fill_value=0).astype("int64")
        rec = df[(df["recovered_cents"] > 0) & df["recovered_date"].notna()]
        rec = rec[rec["recovered_date"].map(lambda d: start <= d <= end)]
        if not rec.empty:
            g = rec.groupby(rec["recovered_date"].map(lambda d: pd.Period(d, "M")))[
                "recovered_cents"
            ].sum()
            out["recovered_cents"] = g.reindex(months, fill_value=0).astype("int64")
    out["month"] = [p.to_timestamp() for p in out.index]
    out["month_label"] = [p.strftime("%b %Y") for p in out.index]
    out["month_short"] = [
        p.strftime("%b<br>%Y") if p.month == 1 or i == 0 else p.strftime("%b")
        for i, p in enumerate(out.index)
    ]
    return out.reset_index(drop=True)


def recovered_breakdown(df: pd.DataFrame, by: str) -> pd.DataFrame:
    rec = df[df["recovered_cents"] > 0] if not df.empty else df
    if rec.empty:
        return pd.DataFrame(columns=[by, "recovered_cents", "count"])
    out = (
        rec.groupby(by)
        .agg(recovered_cents=("recovered_cents", "sum"), count=("id", "count"))
        .reset_index()
    )
    out["recovered_cents"] = out["recovered_cents"].astype("int64")
    return out.sort_values("recovered_cents", ascending=False)


@dataclass
class RecoveryStats:
    recovered_cents: int
    fee_cents: int
    owner_net_cents: int
    open_pipeline_cents: int
    pipeline_estimate_cents: int
    recovery_rate: float
    median_days: float | None
    recovered_count: int
    written_off_cents: int


def recovery_stats(s: Summary, df_period: pd.DataFrame) -> RecoveryStats:
    resolve = (
        days_to_resolve(df_period[df_period["counts_as_leak"]])
        if not df_period.empty
        else pd.Series(dtype=float)
    )
    recovered_count = int((df_period["status"] == "recovered").sum()) if not df_period.empty else 0
    return RecoveryStats(
        recovered_cents=s.recovered_cents,
        fee_cents=s.fee_cents,
        owner_net_cents=s.owner_net_cents,
        open_pipeline_cents=s.open_at_risk_cents,
        pipeline_estimate_cents=s.recoverable_estimate_cents - s.recovered_cents,
        recovery_rate=s.recovery_rate,
        median_days=float(resolve.median()) if not resolve.empty else None,
        recovered_count=recovered_count,
        written_off_cents=s.written_off_cents,
    )


def queue(df: pd.DataFrame) -> pd.DataFrame:
    """Exceptions sorted by priority, highest first."""
    exc = exceptions_only(df)
    if exc.empty:
        return exc
    return exc.sort_values(["priority_score", "amount_at_risk_cents"], ascending=False)


def top_actions(df_all: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    exc = queue(df_all)
    if exc.empty:
        return exc
    return exc[exc["status"].isin(OPEN_STATUSES)].head(n)


def next_steps(
    s: Summary,
    df_all: pd.DataFrame,
    card: pd.DataFrame,
    coverage_gaps: int,
    window_days: int = 7,
) -> list[str]:
    """Short, plain to-do list for the home page, built only from the numbers."""
    steps: list[str] = []
    if s.expiring_count:
        steps.append(
            f"Charge the {s.expiring_count} card{'s' if s.expiring_count != 1 else ''} expiring in the next {window_days} days "
            f"({money(s.expiring_cents)}). After expiry, recovery needs an OTA claim."
        )
    exc = df_all[df_all["is_open"]] if not df_all.empty else df_all
    if not exc.empty:
        unconfirmed = exc[(exc["match_method"] == "fuzzy") & (~exc["match_confirmed"])]
        if len(unconfirmed):
            steps.append(
                f"Confirm {len(unconfirmed)} fuzzy matches so their recovery can be recorded."
            )
        unassigned = exc[exc["status"] == "open"]
        if len(unassigned):
            steps.append(
                f"Assign {len(unassigned)} open items ({money(int(unassigned['amount_at_risk_cents'].sum()))}) to property staff."
            )
        expired = exc[exc["exception_type"] == "EXPIRED_UNCHARGED"]
        if len(expired):
            steps.append(
                f"File OTA claims for {len(expired)} expired cards ({money(int(expired['amount_at_risk_cents'].sum()))})."
            )
    if not card.empty and len(card) > 1:
        worst = card.iloc[0]
        steps.append(
            f"Review the operator scorecard with {worst['company']}, the highest leak rate in the portfolio."
        )
    if coverage_gaps:
        steps.append(
            f"Close {coverage_gaps} data coverage gap{'s' if coverage_gaps != 1 else ''} on the Data sources page."
        )
    return steps[:5]


# Data coverage -------------------------------------------------------------------


def source_files_frame(session: Session, portfolio_id: int) -> pd.DataFrame:
    q = (
        select(SourceFile, Property.code)
        .join(Property, Property.id == SourceFile.property_id)
        .where(SourceFile.portfolio_id == portfolio_id)
        .order_by(Property.code, SourceFile.source_name, SourceFile.file_name)
    )
    rows = []
    for f, code in session.execute(q):
        rows.append(
            {
                "id": f.id,
                "property_code": code,
                "property_id": f.property_id,
                "source_name": f.source_name,
                "source": SOURCE_LABELS.get(f.source_name, f.source_name),
                "file_name": f.file_name,
                "relative_path": f.relative_path,
                "status": f.status,
                "message": f.message,
                "mapping_name": f.mapping_name,
                "rows_read": f.rows_read,
                "rows_imported": f.rows_imported,
                "rows_rejected": f.rows_rejected,
                "date_min": f.date_min,
                "date_max": f.date_max,
                "imported_at": f.imported_at,
            }
        )
    return pd.DataFrame(rows)


def import_issues_frame(session: Session, portfolio_id: int, limit: int = 5000) -> pd.DataFrame:
    q = (
        select(ImportIssue, SourceFile.relative_path, SourceFile.file_name)
        .join(SourceFile, SourceFile.id == ImportIssue.source_file_id)
        .where(ImportIssue.portfolio_id == portfolio_id)
        .order_by(ImportIssue.severity, SourceFile.relative_path, ImportIssue.row_number)
        .limit(limit)
    )
    rows = [
        {
            "file": path,
            "file_name": name,
            "row_number": i.row_number,
            "severity": i.severity,
            "field": i.field,
            "message": i.message,
        }
        for i, path, name in session.execute(q)
    ]
    return pd.DataFrame(
        rows, columns=["file", "file_name", "row_number", "severity", "field", "message"]
    )


@dataclass
class CoverageCell:
    status: str  # ok / gap / missing / failed
    reason: str
    date_min: date | None = None
    date_max: date | None = None


def coverage(
    files: pd.DataFrame, props: list[Property], as_of: date
) -> dict[str, dict[str, CoverageCell]]:
    """Property code -> source -> coverage. PMS payments set the reference period."""
    out: dict[str, dict[str, CoverageCell]] = {}
    for prop in props:
        cells: dict[str, CoverageCell] = {}
        mine = files[files["property_code"] == prop.code] if not files.empty else files
        ok = mine[mine["status"] == "imported"] if not mine.empty else mine
        ref = ok[ok["source_name"] == "pms_payments"] if not ok.empty else ok
        ref_end = (
            min(max(ref["date_max"].dropna()), as_of)
            if not ref.empty and ref["date_max"].notna().any()
            else None
        )
        ref_start = (
            min(ref["date_min"].dropna())
            if not ref.empty and ref["date_min"].notna().any()
            else None
        )
        for source in REQUIRED_SOURCES:
            label = SOURCE_LABELS[source]
            rows = ok[ok["source_name"] == source] if not ok.empty else ok
            if rows.empty:
                others = mine[mine["source_name"] == source] if not mine.empty else mine
                if not others.empty:
                    reason = str(others.iloc[0]["message"] or "File could not be imported")
                    cells[source] = CoverageCell("failed", reason)
                else:
                    cells[source] = CoverageCell("missing", f"No {label.lower()} file yet")
                continue
            dmin = min(rows["date_min"].dropna()) if rows["date_min"].notna().any() else None
            dmax = max(rows["date_max"].dropna()) if rows["date_max"].notna().any() else None
            end = min(dmax, as_of) if dmax else None
            reason = f"{fmt_date(dmin)} to {fmt_date(end)}" if dmin and end else "Imported"
            status = "ok"
            if (
                source != "pms_payments"
                and ref_end
                and end
                and end < ref_end - timedelta(days=COVERAGE_GAP_DAYS)
            ):
                status = "gap"
                reason = f"Ends {fmt_date(end)}, PMS payments run to {fmt_date(ref_end)}"
            elif (
                source != "pms_payments"
                and ref_start
                and dmin
                and dmin > ref_start + timedelta(days=COVERAGE_GAP_DAYS)
            ):
                status = "gap"
                reason = f"Starts {fmt_date(dmin)}, PMS payments start {fmt_date(ref_start)}"
            cells[source] = CoverageCell(status, reason, dmin, end)
        out[prop.code] = cells
    return out


def coverage_gap_count(cov: dict[str, dict[str, CoverageCell]]) -> int:
    return sum(1 for cells in cov.values() for c in cells.values() if c.status != "ok")


# Formatting ----------------------------------------------------------------------


def money(cents: int | float | None, with_cents: bool = False) -> str:
    """$1,234 on summaries, $1,234.56 in line items. Never shows -$0."""
    if cents is None or (isinstance(cents, float) and cents != cents):
        return "-"
    value = Decimal(int(round(cents))) / 100
    step = Decimal("0.01") if with_cents else Decimal("1")
    rounded = abs(value).quantize(step, ROUND_HALF_UP)
    text = f"${rounded:,.2f}" if with_cents else f"${rounded:,.0f}"
    return f"-{text}" if value < 0 and rounded != 0 else text


def money_short(cents: int | float) -> str:
    value = cents / 100
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def pct(value: float | None) -> str:
    if value is None or value != value:
        return "-"
    return f"{value * 100:.1f}%"


def fmt_date(d: date | None) -> str:
    """Sep 30, 2026"""
    return f"{d:%b} {d.day}, {d.year}" if d else "-"
