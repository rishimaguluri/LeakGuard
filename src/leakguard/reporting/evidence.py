"""Evidence behind one exception: the card, the reservation, PMS postings,
processor transactions and the audit timeline. Used by the queue's
evidence panel and the Excel line items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.models import (
    Exception_,
    ExceptionEvent,
    PmsPayment,
    ProcessorTransaction,
    Reservation,
    SourceFile,
    VccRecord,
)

WINDOW_DAYS = 7


@dataclass
class Evidence:
    exception: Exception_
    vcc: VccRecord | None
    vcc_file: str | None
    reservation: Reservation | None
    reservation_file: str | None
    postings: list[PmsPayment]
    transactions: list[ProcessorTransaction]
    events: list[ExceptionEvent]


def _file(session: Session, source_file_id: int | None) -> str | None:
    if source_file_id is None:
        return None
    f = session.get(SourceFile, source_file_id)
    return f.relative_path if f else None


def load_evidence(session: Session, exception_id: int, settlement_window_days: int = 5) -> Evidence:
    row = session.get(Exception_, exception_id)
    if row is None:
        raise KeyError(exception_id)
    vcc = session.get(VccRecord, row.vcc_record_id) if row.vcc_record_id else None
    res = session.get(Reservation, row.reservation_id) if row.reservation_id else None

    postings: list[PmsPayment] = []
    if res is not None:
        postings = list(
            session.scalars(
                select(PmsPayment)
                .where(
                    PmsPayment.property_id == row.property_id,
                    PmsPayment.pms_confirmation_no == res.pms_confirmation_no,
                )
                .order_by(PmsPayment.posting_date, PmsPayment.id)
            )
        )

    last4s = {row.card_last4} | {p.card_last4 for p in postings}
    last4s.discard(None)
    anchors = [p.posting_date for p in postings] + [
        d for d in (vcc.activation_date if vcc else None, res.arrival_date if res else None) if d
    ]
    transactions: list[ProcessorTransaction] = []
    if last4s and anchors:
        lo = min(anchors) - timedelta(days=WINDOW_DAYS)
        hi = max(anchors) + timedelta(days=settlement_window_days + WINDOW_DAYS)
        transactions = list(
            session.scalars(
                select(ProcessorTransaction)
                .where(
                    ProcessorTransaction.property_id == row.property_id,
                    ProcessorTransaction.card_last4.in_(last4s),
                    ProcessorTransaction.transaction_date.between(lo, hi),
                )
                .order_by(ProcessorTransaction.transaction_date, ProcessorTransaction.id)
            )
        )

    events = list(
        session.scalars(
            select(ExceptionEvent)
            .where(ExceptionEvent.exception_id == exception_id)
            .order_by(ExceptionEvent.timestamp, ExceptionEvent.id)
        )
    )
    return Evidence(
        exception=row,
        vcc=vcc,
        vcc_file=_file(session, vcc.source_file_id if vcc else None),
        reservation=res,
        reservation_file=_file(session, res.source_file_id if res else None),
        postings=postings,
        transactions=transactions,
        events=events,
    )
