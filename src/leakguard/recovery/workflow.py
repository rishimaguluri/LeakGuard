"""Recovery workflow: assign, change status, record recovery, add notes.

Every action writes an exception_events row, so the audit log is complete.
Rules:
  - only real exceptions can be worked (not CHARGED_OK or NOT_YET_DUE)
  - a fuzzy match must be confirmed by a person before recovery is recorded
  - "recovered" is set by record_recovery, which needs an amount
  - recoveries can be partial; the status becomes recovered once the total
    reaches the amount at risk, otherwise in_progress
  - reopening a recovered item clears the recovered amount, with an event
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.models import Exception_, ExceptionEvent
from leakguard.schemas import STATUSES


class WorkflowError(ValueError):
    """An action that is not allowed, with a message for the person."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _get(session: Session, exception_id: int) -> Exception_:
    row = session.get(Exception_, exception_id)
    if row is None:
        raise WorkflowError(f"Exception {exception_id} not found.")
    if not row.is_exception:
        raise WorkflowError("This card was charged correctly or is not due yet. Nothing to work.")
    return row


def _log(
    session: Session,
    row: Exception_,
    event_type: str,
    old: object,
    new: object,
    user: str,
    note: str | None,
    at: datetime | None,
) -> ExceptionEvent:
    event = ExceptionEvent(
        portfolio_id=row.portfolio_id,
        exception_id=row.id,
        event_type=event_type,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        user=user,
        timestamp=at or _now(),
        note=note or None,
    )
    session.add(event)
    return event


def _require_user(user: str) -> str:
    user = (user or "").strip()
    if not user:
        raise WorkflowError("Enter your name so the audit log shows who made the change.")
    return user


def assign(
    session: Session,
    exception_id: int,
    assignee: str,
    user: str,
    note: str | None = None,
    at: datetime | None = None,
) -> Exception_:
    user = _require_user(user)
    assignee = (assignee or "").strip()
    if not assignee:
        raise WorkflowError("Choose who to assign this to.")
    row = _get(session, exception_id)
    _log(session, row, "assigned", row.assigned_to, assignee, user, note, at)
    row.assigned_to = assignee
    if row.status == "open":
        _log(session, row, "status_changed", "open", "assigned", user, None, at)
        row.status = "assigned"
    return row


def change_status(
    session: Session,
    exception_id: int,
    new_status: str,
    user: str,
    note: str | None = None,
    at: datetime | None = None,
) -> Exception_:
    user = _require_user(user)
    if new_status not in STATUSES:
        raise WorkflowError(f"Unknown status '{new_status}'.")
    if new_status == "recovered":
        raise WorkflowError(
            "Use 'record recovery' to mark an item recovered with the amount collected."
        )
    row = _get(session, exception_id)
    if row.status == new_status:
        return row
    if new_status in ("written_off", "not_an_issue") and not (note or "").strip():
        raise WorkflowError("Add a short note explaining why, for the audit log.")
    old = row.status
    if old == "recovered":
        _log(session, row, "recovery_cleared", row.recovered_cents, 0, user, "Reopened", at)
        row.recovered_cents = 0
        row.recovered_date = None
    _log(session, row, "status_changed", old, new_status, user, note, at)
    row.status = new_status
    return row


def confirm_match(
    session: Session,
    exception_id: int,
    user: str,
    note: str | None = None,
    at: datetime | None = None,
) -> Exception_:
    user = _require_user(user)
    row = _get(session, exception_id)
    if row.match_method != "fuzzy":
        raise WorkflowError("Only fuzzy matches need confirming.")
    if not row.match_confirmed:
        _log(session, row, "match_confirmed", "unconfirmed", "confirmed", user, note, at)
        row.match_confirmed = True
    return row


def record_recovery(
    session: Session,
    exception_id: int,
    amount_cents: int,
    recovered_on: date,
    user: str,
    note: str | None = None,
    at: datetime | None = None,
) -> Exception_:
    user = _require_user(user)
    row = _get(session, exception_id)
    if amount_cents <= 0:
        raise WorkflowError("Enter the amount collected, above zero.")
    if row.match_method == "fuzzy" and not row.match_confirmed:
        raise WorkflowError(
            "This card was matched on name, arrival and amount, not confirmation number. "
            "Confirm the match before recording recovery."
        )
    cap = max(row.amount_at_risk_cents, row.expected_cents)
    total = row.recovered_cents + amount_cents
    if total > cap:
        raise WorkflowError(
            f"That would bring recovery to ${total / 100:,.2f}, more than the "
            f"${cap / 100:,.2f} on this card."
        )
    _log(session, row, "recovered", row.recovered_cents, total, user, note, at)
    row.recovered_cents = total
    row.recovered_date = recovered_on
    new_status = "recovered" if total >= row.amount_at_risk_cents else "in_progress"
    if new_status != row.status:
        _log(session, row, "status_changed", row.status, new_status, user, None, at)
        row.status = new_status
    return row


def add_note(
    session: Session, exception_id: int, text: str, user: str, at: datetime | None = None
) -> Exception_:
    user = _require_user(user)
    text = (text or "").strip()
    if not text:
        raise WorkflowError("The note is empty.")
    row = _get(session, exception_id)
    stamp = (at or _now()).strftime("%Y-%m-%d")
    row.notes = ((row.notes + "\n") if row.notes else "") + f"{stamp} {user}: {text}"
    _log(session, row, "note", None, None, user, text, at)
    return row


def bulk_change_status(
    session: Session, ids: Iterable[int], new_status: str, user: str, note: str | None = None
) -> int:
    count = 0
    for exception_id in ids:
        change_status(session, exception_id, new_status, user, note)
        count += 1
    return count


def bulk_assign(session: Session, ids: Iterable[int], assignee: str, user: str) -> int:
    count = 0
    for exception_id in ids:
        assign(session, exception_id, assignee, user)
        count += 1
    return count


def timeline(session: Session, exception_id: int) -> list[ExceptionEvent]:
    return list(
        session.scalars(
            select(ExceptionEvent)
            .where(ExceptionEvent.exception_id == exception_id)
            .order_by(ExceptionEvent.timestamp, ExceptionEvent.id)
        )
    )
