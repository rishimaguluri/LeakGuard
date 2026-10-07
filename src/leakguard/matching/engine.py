"""Matching engine. Runs per property, deterministic, no network calls.

Steps (SPEC section 7):
  1. link each virtual card to a PMS reservation: exact OTA confirmation,
     then a fuzzy fallback on guest name, arrival date and amount
  2. charged = PMS postings on that reservation with the card's last 4
     (reversals negative); settled = matching processor transactions
  3. classify with matching/rules.py
  4. score with matching/priority.py
  5. upsert one exceptions row per card, keyed on property + ota +
     confirmation + last 4, never overwriting a status a person set
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from leakguard.config import Settings
from leakguard.db import session_scope
from leakguard.matching.priority import priority_score
from leakguard.matching.rules import (
    ReservationFacts,
    VccCase,
    VccFacts,
    classify,
    fuzzy_criteria_met,
    fuzzy_pool_eligible,
    money,
)
from leakguard.models import (
    Exception_,
    ExceptionEvent,
    PmsPayment,
    Portfolio,
    ProcessorTransaction,
    Property,
    Reservation,
    VccRecord,
)
from leakguard.schemas import HUMAN_CLOSED_STATUSES, OK_TYPES

SYSTEM_USER = "LeakGuard"
AUTO_RESOLVED_NOTE = "auto-resolved: charge detected"


@dataclass
class CardResult:
    vcc: VccRecord
    reservation: Reservation | None
    match_method: str
    exception_type: str
    amount_at_risk_cents: int
    charged_cents: int
    settled_cents: int | None
    confidence: str
    rule: str
    due_date: date | None
    days_to_expiry: int | None
    priority: float


@dataclass
class MatchSummary:
    cards: int = 0
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    new_exceptions: int = 0
    type_changes: int = 0
    auto_resolved: int = 0
    deactivated: int = 0
    fuzzy_matches: int = 0

    def lines(self) -> list[str]:
        out = [f"Checked {self.cards:,} virtual cards."]
        for t, n in sorted(self.by_type.items(), key=lambda kv: -kv[1]):
            out.append(f"    {t}: {n:,}")
        out.append(
            f"New exceptions {self.new_exceptions:,}, type changes {self.type_changes:,}, "
            f"auto-resolved {self.auto_resolved:,}, fuzzy matches {self.fuzzy_matches:,}, "
            f"no longer in data {self.deactivated:,}."
        )
        return out


def vcc_key(ota: str, conf: str, last4: str | None) -> str:
    return f"{ota}|{conf}|{last4 or ''}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# Facts ----------------------------------------------------------------------------


def res_facts(r: Reservation) -> ReservationFacts:
    expected = None
    if r.room_revenue_cents is not None:
        expected = r.room_revenue_cents + (r.tax_cents or 0)
    elif r.folio_total_cents is not None:
        expected = r.folio_total_cents
    return ReservationFacts(
        id=r.id,
        pms_confirmation_no=r.pms_confirmation_no,
        ota_confirmation_no=r.ota_confirmation_no,
        channel=r.channel,
        payment_model=r.payment_model,
        status=r.status,
        arrival_date=r.arrival_date,
        departure_date=r.departure_date,
        expected_cents=expected,
        guest_last_name=r.guest_last_name,
        currency=r.currency,
    )


def vcc_facts(v: VccRecord) -> VccFacts:
    return VccFacts(
        ota=v.ota,
        ota_confirmation_no=v.ota_confirmation_no,
        card_last4=v.card_last4,
        vcc_amount_cents=v.vcc_amount_cents,
        currency=v.currency,
        activation_date=v.activation_date,
        expiry_date=v.expiry_date,
        guest_last_name=v.guest_last_name,
        arrival_date=v.arrival_date,
    )


def _latest_by(rows: list, key) -> list:  # noqa: ANN001
    """Keep one row per key, preferring the most recently imported file."""
    best: dict = {}
    for row in rows:
        k = key(row)
        if k not in best or row.source_file_id > best[k].source_file_id:
            best[k] = row
    return list(best.values())


def _pick_reservation(candidates: list[Reservation]) -> Reservation:
    """Cancelled then rebooked under the same OTA number: prefer the live one."""
    live = [r for r in candidates if r.status not in ("cancelled", "no_show")]
    pool = live or candidates
    return max(pool, key=lambda r: (r.arrival_date, r.id))


# Property run -------------------------------------------------------------------


class PropertyMatcher:
    def __init__(self, session: Session, prop: Property, settings: Settings, as_of: date):
        self.session = session
        self.prop = prop
        self.settings = settings
        self.as_of = as_of
        self.tolerance = settings.charge_tolerance_cents
        keywords = [re.escape(k.lower()) for k in settings.virtual_card_keywords]
        self.virtual_re = re.compile("|".join(keywords)) if keywords else None

    def load(self) -> None:
        pid = self.prop.id
        s = self.session
        self.reservations = _latest_by(
            list(s.scalars(select(Reservation).where(Reservation.property_id == pid))),
            lambda r: r.pms_confirmation_no,
        )
        self.vccs = sorted(
            _latest_by(
                list(s.scalars(select(VccRecord).where(VccRecord.property_id == pid))),
                lambda v: (v.ota, v.ota_confirmation_no, v.card_last4),
            ),
            key=lambda v: (v.activation_date or date.min, v.card_last4 or "", v.id),
        )
        self.payments: dict[str, list[PmsPayment]] = defaultdict(list)
        for p in s.scalars(select(PmsPayment).where(PmsPayment.property_id == pid)):
            self.payments[p.pms_confirmation_no].append(p)
        self.txns: dict[str, list[ProcessorTransaction]] = defaultdict(list)
        dates = []
        for t in s.scalars(
            select(ProcessorTransaction).where(ProcessorTransaction.property_id == pid)
        ):
            if t.card_last4 and t.transaction_type != "void":
                self.txns[t.card_last4].append(t)
            dates.append(t.transaction_date)
        self.proc_range = (min(dates), max(dates)) if dates else None
        self.used_txns: set[int] = set()

        self.by_ota_conf: dict[str, list[Reservation]] = defaultdict(list)
        for r in self.reservations:
            if r.ota_confirmation_no:
                self.by_ota_conf[r.ota_confirmation_no].append(r)

    # Step 1 ---------------------------------------------------------------------

    def link(self) -> dict[int, tuple[Reservation | None, str, list[str]]]:
        links: dict[int, tuple[Reservation | None, str, list[str]]] = {}
        linked_res: set[int] = set()
        for v in self.vccs:
            candidates = self.by_ota_conf.get(v.ota_confirmation_no)
            if candidates:
                res = _pick_reservation(candidates)
                links[v.id] = (res, "exact", [])
                linked_res.add(res.id)

        by_arrival: dict[date, list[Reservation]] = defaultdict(list)
        facts = {r.id: res_facts(r) for r in self.reservations}
        for r in self.reservations:
            if r.id not in linked_res:
                by_arrival[r.arrival_date].append(r)
        window = self.settings.fuzzy_arrival_window_days

        for v in self.vccs:
            if v.id in links:
                continue
            vf = vcc_facts(v)
            found: list[Reservation] = []
            if vf.arrival_date:
                for offset in range(-window, window + 1):
                    for r in by_arrival.get(vf.arrival_date + timedelta(days=offset), []):
                        if r.id in linked_res or not fuzzy_pool_eligible(facts[r.id], v.ota):
                            continue
                        if fuzzy_criteria_met(
                            vf,
                            facts[r.id],
                            self.settings.fuzzy_name_min_score,
                            window,
                            self.settings.fuzzy_amount_tolerance_pct,
                            names_hashed=self.settings.hash_guest_names,
                        ):
                            found.append(r)
            if len(found) == 1:
                links[v.id] = (
                    found[0],
                    "fuzzy",
                    [
                        "Matched on guest name, arrival date and amount because the OTA confirmation "
                        "number was not found in the PMS. Confirm the match before recovery."
                    ],
                )
                linked_res.add(found[0].id)
            elif len(found) > 1:
                links[v.id] = (
                    None,
                    "none",
                    [
                        f"{len(found)} reservations look similar on name, arrival and amount, so none "
                        "was picked automatically."
                    ],
                )
            else:
                links[v.id] = (None, "none", [])
        return links

    # Step 2 ---------------------------------------------------------------------

    def _looks_virtual(self, payment_type: str | None) -> bool:
        return bool(
            payment_type and self.virtual_re and self.virtual_re.search(payment_type.lower())
        )

    def card_postings(
        self, v: VccRecord, res: Reservation
    ) -> tuple[list[PmsPayment], list[str], bool]:
        notes: list[str] = []
        lower_confidence = False
        postings = []
        other_currency = 0
        for p in self.payments.get(res.pms_confirmation_no, []):
            if v.card_last4 and p.card_last4 == v.card_last4:
                ok = True
            elif (p.card_last4 is None or v.card_last4 is None) and self._looks_virtual(
                p.payment_type
            ):
                ok = True
                lower_confidence = True
            else:
                ok = False
            if not ok:
                continue
            if p.currency != v.currency:
                other_currency += 1
                continue
            postings.append(p)
        if lower_confidence:
            notes.append(
                "Some postings had no card last 4, so they were matched by payment type. "
                "Lower confidence."
            )
        if other_currency:
            notes.append(
                f"{other_currency} posting(s) in a currency other than {v.currency} were not counted."
            )
            lower_confidence = True
        return postings, notes, lower_confidence

    def settled_for(self, v: VccRecord, res: Reservation, postings: list[PmsPayment]) -> int | None:
        if not postings or self.proc_range is None:
            return None if self.proc_range is None else 0
        start, end = self.proc_range
        if any(not (start <= p.posting_date <= end) for p in postings):
            return None
        window = self.settings.settlement_window_days
        settled = 0
        for p in postings:
            last4 = p.card_last4 or v.card_last4
            if not last4:
                return None
            best = None
            for t in self.txns.get(last4, []):
                if t.id in self.used_txns or t.currency != p.currency:
                    continue
                if abs(t.amount_cents - p.amount_cents) > self.tolerance:
                    continue
                if not (
                    p.posting_date - timedelta(days=1)
                    <= t.transaction_date
                    <= p.posting_date + timedelta(days=window)
                ):
                    continue
                rank = (
                    0 if t.reference and t.reference == res.pms_confirmation_no else 1,
                    abs((t.transaction_date - p.posting_date).days),
                )
                if best is None or rank < best[0]:
                    best = (rank, t)
            if best is not None:
                self.used_txns.add(best[1].id)
                settled += best[1].amount_cents
        return settled

    # Steps 3 and 4 --------------------------------------------------------------------

    def evaluate(self) -> list[CardResult]:
        self.load()
        links = self.link()
        seen_confs: dict[tuple[str, str], int] = {}
        results = []
        for v in self.vccs:
            res, method, notes = links[v.id]
            key = (v.ota, v.ota_confirmation_no)
            is_duplicate = key in seen_confs
            seen_confs.setdefault(key, v.id)

            charged, settled, lower = 0, None, False
            if res is not None:
                postings, post_notes, lower = self.card_postings(v, res)
                notes = notes + post_notes
                charged = sum(p.amount_cents for p in postings)
                if charged > 0:
                    settled = self.settled_for(v, res, postings)
                    if settled is None:
                        notes.append(
                            "No processor data covers this charge, so settlement was not checked."
                        )

            case = VccCase(
                vcc=vcc_facts(v),
                reservation=res_facts(res) if res else None,
                charged_cents=charged,
                settled_cents=settled,
                as_of=self.as_of,
                tolerance_cents=self.tolerance,
                is_duplicate=is_duplicate,
                match_method=method,
            )
            result = classify(case)
            confidence = "high"
            if method == "fuzzy":
                confidence = "medium"
            if lower:
                confidence = "low" if confidence == "medium" else "medium"

            days = (v.expiry_date - self.as_of).days if v.expiry_date else None
            rule = " ".join([result.rule, *notes]).strip()
            results.append(
                CardResult(
                    vcc=v,
                    reservation=res,
                    match_method=method,
                    exception_type=result.exception_type,
                    amount_at_risk_cents=result.amount_at_risk_cents,
                    charged_cents=charged,
                    settled_cents=settled,
                    confidence=confidence,
                    rule=rule,
                    due_date=_due_date(v, res),
                    days_to_expiry=days,
                    priority=priority_score(
                        result.exception_type,
                        result.amount_at_risk_cents,
                        days,
                        v.vcc_amount_cents,
                    ),
                )
            )
        return results


def _due_date(v: VccRecord, res: Reservation | None) -> date | None:
    """When the money became collectible. Drives monthly trends."""
    if res is not None:
        if res.status in ("cancelled", "no_show"):
            return res.arrival_date
        return res.departure_date
    return v.arrival_date or v.activation_date or v.expiry_date


# Step 5: stable upsert ---------------------------------------------------------------


def _event(
    row: Exception_,
    event_type: str,
    old: str | None,
    new: str | None,
    note: str | None,
    now: datetime,
) -> ExceptionEvent:
    return ExceptionEvent(
        portfolio_id=row.portfolio_id,
        exception=row,
        event_type=event_type,
        old_value=old,
        new_value=new,
        user=SYSTEM_USER,
        timestamp=now,
        note=note,
    )


def _apply_result(row: Exception_, r: CardResult, now: datetime) -> None:
    v, res = r.vcc, r.reservation
    row.vcc_record_id = v.id
    row.reservation_id = res.id if res else None
    row.ota, row.ota_confirmation_no, row.card_last4 = v.ota, v.ota_confirmation_no, v.card_last4
    row.pms_confirmation_no = res.pms_confirmation_no if res else None
    row.guest_last_name = (res.guest_last_name if res else None) or v.guest_last_name
    row.exception_type = r.exception_type
    row.is_exception = r.exception_type not in OK_TYPES
    row.match_method = r.match_method
    row.confidence = r.confidence
    row.rule = r.rule
    row.amount_at_risk_cents = r.amount_at_risk_cents
    row.expected_cents = v.vcc_amount_cents
    row.charged_cents = r.charged_cents
    row.settled_cents = r.settled_cents
    row.currency = v.currency
    row.arrival_date = res.arrival_date if res else v.arrival_date
    row.departure_date = res.departure_date if res else None
    row.due_date = r.due_date
    row.expiry_date = v.expiry_date
    row.days_to_expiry = r.days_to_expiry
    row.priority_score = r.priority
    row.is_active = True
    row.last_seen_at = now


def upsert_results(
    session: Session, prop: Property, results: list[CardResult], as_of: date, summary: MatchSummary
) -> None:
    now = _now()
    existing = {
        row.vcc_key: row
        for row in session.scalars(select(Exception_).where(Exception_.property_id == prop.id))
    }
    seen: set[str] = set()
    for r in results:
        key = vcc_key(r.vcc.ota, r.vcc.ota_confirmation_no, r.vcc.card_last4)
        if key in seen:
            continue
        seen.add(key)
        summary.cards += 1
        summary.by_type[r.exception_type] += 1
        if r.match_method == "fuzzy":
            summary.fuzzy_matches += 1
        row = existing.get(key)

        if row is None:
            row = Exception_(
                portfolio_id=prop.portfolio_id,
                property_id=prop.id,
                vcc_key=key,
                status="open",
                first_detected_at=now,
                recovered_cents=0,
                match_confirmed=False,
            )
            _apply_result(row, r, now)
            session.add(row)
            if row.is_exception:
                summary.new_exceptions += 1
                session.add(_event(row, "detected", None, r.exception_type, r.rule, now))
            continue

        if row.status in HUMAN_CLOSED_STATUSES:
            # A person closed this. Refresh links and dates only; never the outcome.
            row.vcc_record_id = r.vcc.id
            row.reservation_id = r.reservation.id if r.reservation else row.reservation_id
            row.days_to_expiry = r.days_to_expiry
            row.is_active = True
            row.last_seen_at = now
            continue

        new_is_exception = r.exception_type not in OK_TYPES
        if row.is_exception and not new_is_exception and r.exception_type == "CHARGED_OK":
            # The card was charged after we flagged it. Keep the leak on record.
            row.status = "recovered"
            row.recovered_cents = row.amount_at_risk_cents
            row.recovered_date = as_of
            row.charged_cents, row.settled_cents = r.charged_cents, r.settled_cents
            row.vcc_record_id = r.vcc.id
            row.is_active = True
            row.last_seen_at = now
            row.priority_score = 0.0
            summary.auto_resolved += 1
            session.add(_event(row, "auto_resolved", "open", "recovered", AUTO_RESOLVED_NOTE, now))
            continue

        old_type = row.exception_type
        was_exception = row.is_exception
        _apply_result(row, r, now)
        if new_is_exception and not was_exception:
            summary.new_exceptions += 1
            session.add(_event(row, "detected", old_type, r.exception_type, r.rule, now))
        elif new_is_exception and old_type != r.exception_type:
            summary.type_changes += 1
            session.add(_event(row, "type_changed", old_type, r.exception_type, r.rule, now))

    for key, row in existing.items():
        if key not in seen and row.is_active:
            row.is_active = False
            summary.deactivated += 1


def run_matching(settings: Settings, portfolio_slug: str | None = None) -> MatchSummary:
    """Match every property in the current mode's database."""
    summary = MatchSummary()
    as_of = settings.as_of()
    with session_scope(settings) as session:
        query = select(Property).join(Portfolio)
        if portfolio_slug:
            query = query.where(Portfolio.slug == portfolio_slug)
        for prop in session.scalars(query).all():
            matcher = PropertyMatcher(session, prop, settings, as_of)
            results = matcher.evaluate()
            upsert_results(session, prop, results, as_of, summary)
            session.flush()
    return summary


__all__ = ["run_matching", "MatchSummary", "vcc_key", "money"]
