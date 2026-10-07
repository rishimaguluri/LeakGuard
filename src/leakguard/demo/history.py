"""Simulated recovery history for the demo portfolio.

The demo pretends LeakGuard was switched on six months before the demo's
as-of date. Since then, each management company's team has worked part of
the queue: assigning items, confirming fuzzy matches, recording recoveries
and writing off cards the OTA would not honor. Lakeview works faster and
recovers more than Northpoint, so the scorecard tells the same story.

All of this goes through recovery/workflow.py with backdated timestamps,
so the audit log looks exactly like it would after six months of real use.
Story items stay open on purpose: cards expiring soon and the largest
expired cards.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.config import Settings
from leakguard.db import session_scope
from leakguard.models import Exception_, ManagementCompany, Property
from leakguard.recovery import workflow as wf

ONBOARDING_DAYS_BEFORE_AS_OF = 183
KEEP_OPEN_ABOVE_CENTS = 300_000
KEEP_OPEN_EXPIRY_DAYS = 14

# Share of worked items that end each way, per type: (recovered, written_off, not_an_issue)
OUTCOMES = {
    "UNCHARGED": (0.90, 0.02, 0.03),
    "UNDERCHARGED": (0.85, 0.05, 0.05),
    "CHARGED_NOT_SETTLED": (0.80, 0.05, 0.10),
    "EXPIRED_UNCHARGED": (0.35, 0.35, 0.05),
    "OVERCHARGED": (0.0, 0.10, 0.70),
    "CANCELLED_REVIEW": (0.40, 0.10, 0.40),
    "NO_PMS_MATCH": (0.30, 0.20, 0.40),
    "DUPLICATE_VCC": (0.0, 0.0, 0.90),
}

TEAMS = {
    "Lakeview Hospitality": {"user": "Lakeview AR team", "work_rate": 0.85, "speed": (5, 28)},
    "Northpoint Hotel Group": {"user": "Northpoint AR team", "work_rate": 0.55, "speed": (14, 75)},
}
DEFAULT_TEAM = {"user": "Property AR team", "work_rate": 0.6, "speed": (10, 45)}
ANALYST = "LeakGuard analyst"

CLOSE_NOTES = {
    "written_off": [
        "OTA declined the claim, card past expiry.",
        "Card expired before the claim window. Written off with owner approval.",
        "Claim denied by OTA support.",
    ],
    "not_an_issue": [
        "Guest relocated to sister property. Charged there.",
        "Second card was voided by the OTA. Nothing to collect.",
        "Overage refunded to the card the same week.",
        "Cancellation was inside the free window. No fee due.",
    ],
}


def _at(day: date, rng: random.Random) -> datetime:
    return datetime.combine(day, time(rng.randint(13, 22), rng.randint(0, 59)))


def seed_history(settings: Settings, seed: int = 11) -> dict[str, int]:
    """Work part of the demo queue through the workflow. Returns counts."""
    rng = random.Random(seed)
    as_of = settings.as_of()
    onboarding = as_of - timedelta(days=ONBOARDING_DAYS_BEFORE_AS_OF)
    counts = {"recovered": 0, "written_off": 0, "not_an_issue": 0, "assigned": 0, "in_progress": 0}
    with session_scope(settings) as session:
        rows = session.execute(
            select(Exception_, Property.code, ManagementCompany.name)
            .join(Property, Property.id == Exception_.property_id)
            .join(ManagementCompany, ManagementCompany.id == Property.management_company_id)
            .where(Exception_.is_exception, Exception_.status == "open")
            .order_by(Exception_.id)
        ).all()
        for row, code, company in rows:
            _work_one(
                session, row, code, TEAMS.get(company, DEFAULT_TEAM), rng, as_of, onboarding, counts
            )
    return counts


def _work_one(
    session: Session,
    row: Exception_,
    code: str,
    team: dict,
    rng: random.Random,
    as_of: date,
    onboarding: date,
    counts: dict[str, int],
) -> None:
    due = row.due_date or as_of
    active_and_urgent = (
        row.exception_type in ("UNCHARGED", "UNDERCHARGED")
        and row.days_to_expiry is not None
        and 0 <= row.days_to_expiry <= KEEP_OPEN_EXPIRY_DAYS
    )
    if active_and_urgent or row.amount_at_risk_cents > KEEP_OPEN_ABOVE_CENTS:
        return
    if due > as_of - timedelta(days=7) or rng.random() > team["work_rate"]:
        return

    start = max(
        due + timedelta(days=rng.randint(1, 5)), onboarding + timedelta(days=rng.randint(0, 20))
    )
    if start >= as_of:
        return
    user = team["user"]
    assignee = rng.choice(
        [f"{code} front office manager", f"{code} controller", "Regional revenue manager"]
    )
    wf.assign(session, row.id, assignee, ANALYST, at=_at(start, rng))

    finish = start + timedelta(days=rng.randint(*team["speed"]))
    if finish >= as_of:
        if rng.random() < 0.5:
            wf.change_status(
                session,
                row.id,
                "in_progress",
                user,
                "Contacted OTA support, waiting on reply.",
                at=_at(min(start + timedelta(days=2), as_of - timedelta(days=1)), rng),
            )
            counts["in_progress"] += 1
        else:
            counts["assigned"] += 1
        return

    recovered_p, written_p, not_issue_p = OUTCOMES.get(row.exception_type, (0.3, 0.2, 0.3))
    roll = rng.random()
    if roll < recovered_p and row.amount_at_risk_cents > 0:
        if row.match_method == "fuzzy":
            wf.confirm_match(
                session,
                row.id,
                user,
                "Guest name and dates checked against folio.",
                at=_at(start + timedelta(days=1), rng),
            )
        amount = row.amount_at_risk_cents
        if rng.random() < 0.1:
            amount = round(amount * rng.uniform(0.8, 0.95))
        wf.record_recovery(
            session,
            row.id,
            amount,
            finish,
            user,
            rng.choice(
                ["Card charged in PMS.", "OTA paid the claim.", "Charged remaining balance."]
            ),
            at=_at(finish, rng),
        )
        counts["recovered" if row.status == "recovered" else "in_progress"] += 1
    elif roll < recovered_p + written_p:
        wf.change_status(
            session,
            row.id,
            "written_off",
            user,
            rng.choice(CLOSE_NOTES["written_off"]),
            at=_at(finish, rng),
        )
        counts["written_off"] += 1
    elif roll < recovered_p + written_p + not_issue_p:
        wf.change_status(
            session,
            row.id,
            "not_an_issue",
            user,
            rng.choice(CLOSE_NOTES["not_an_issue"]),
            at=_at(finish, rng),
        )
        counts["not_an_issue"] += 1
    else:
        wf.change_status(
            session, row.id, "in_progress", user, "Following up with the OTA.", at=_at(finish, rng)
        )
        counts["in_progress"] += 1
