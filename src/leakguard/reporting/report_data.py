"""Everything the audit report needs, assembled from metrics.py.

The Excel workbook, the PDF and the on-screen preview all read this, so
they always show the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.config import Settings
from leakguard.models import Portfolio
from leakguard.reporting import metrics as M
from leakguard.schemas import TYPE_LABELS

METHOD_TEXT = [
    (
        "What was checked",
        "Every Expedia Collect and Payments by Booking.com virtual card in the OTA reports was matched "
        "to a PMS reservation, first by OTA confirmation number and, if that failed, by guest last name, "
        "arrival date (within {arrival} day) and amount (within {amount:.0f}%). For each card we added up "
        "the PMS payment postings made to that card, net of reversals, and looked for the matching "
        "processor settlements.",
    ),
    (
        "How cards are classified",
        "A card is flagged when it was never charged, charged less or more than the card amount (by more "
        "than {tol}), charged in the PMS but not settled by the processor, issued for a cancelled or "
        "no-show booking, issued twice for one booking, or has no PMS reservation at all. Cards charged "
        "and settled within tolerance are counted as correct.",
    ),
    (
        "Data coverage",
        "Results only cover the dates and properties for which all exports were provided. Where an export "
        "is missing or ends early, cards in that gap were not checked. See the coverage notes in the "
        "Data sources page.",
    ),
    (
        "Matches that need review",
        "Fuzzy matches (by name, arrival and amount) are likely but not certain. Each one must be "
        "confirmed by a person before recovery is recorded.",
    ),
    (
        "Recovery is not guaranteed",
        "Expired cards can only be recovered through a claim with the OTA, which may be declined. The "
        "recoverable estimate applies typical recovery odds by exception type and is an estimate, not a "
        "promise. Our fee applies only to cash actually recovered.",
    ),
    (
        "Data handling",
        "Only the last 4 digits of card numbers are stored. Any full card numbers found in the exports "
        "are dropped on import and never written to disk or the database.",
    ),
]


@dataclass
class ReportData:
    portfolio_name: str
    owner_name: str
    slug: str
    start: date
    end: date
    as_of: date
    generated_at: datetime
    fee_pct: float
    summary: M.Summary
    noi: M.NoiImpact
    by_type: pd.DataFrame
    by_property: pd.DataFrame
    scorecard: pd.DataFrame
    takeaway: str
    line_items: pd.DataFrame
    method: list[tuple[str, str]]
    data_mode: str


def build_report(
    session: Session, settings: Settings, portfolio_id: int, start: date, end: date
) -> ReportData:
    pf = session.get(Portfolio, portfolio_id)
    as_of = settings.as_of()
    df_all = M.load_frame(session, M.Filters(portfolio_id), as_of)
    df = M.in_period(df_all, start, end)
    s = M.summary(df, df_all, settings)
    card = M.scorecard(df)
    items = M.queue(df)
    if not items.empty:
        items = items.assign(type_label=items["exception_type"].map(TYPE_LABELS))
    method = [
        (
            title,
            body.format(
                arrival=settings.fuzzy_arrival_window_days,
                amount=settings.fuzzy_amount_tolerance_pct * 100,
                tol=M.money(settings.charge_tolerance_cents, True),
            ),
        )
        for title, body in METHOD_TEXT
    ]
    return ReportData(
        portfolio_name=pf.name,
        owner_name=pf.owner_name,
        slug=pf.slug,
        start=start,
        end=end,
        as_of=as_of,
        generated_at=datetime.now(UTC).replace(tzinfo=None),
        fee_pct=settings.fee_pct,
        summary=s,
        noi=M.noi_impact(s, start, end, settings),
        by_type=M.by_type(df),
        by_property=M.by_property(df),
        scorecard=card,
        takeaway=M.operator_takeaway(card),
        line_items=items,
        method=method,
        data_mode=settings.data_mode,
    )


def find_portfolio(session: Session, slug: str) -> Portfolio | None:
    return session.scalar(select(Portfolio).where(Portfolio.slug == slug))
