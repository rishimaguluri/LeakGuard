"""Classification rules. One function per rule, applied in a fixed order.

Order of precedence (first rule that fires wins):
    1. DUPLICATE_VCC        another card exists for the same reservation
    2. NO_PMS_MATCH         no reservation found
    3. CANCELLED_REVIEW     cancelled or no-show, nothing charged
    4. nothing charged      EXPIRED_UNCHARGED, UNCHARGED or NOT_YET_DUE
    5. UNDERCHARGED         charged less than the card amount
    6. OVERCHARGED          charged more than the card amount
    7. CHARGED_NOT_SETTLED  processor shows less than the PMS charge
    8. CHARGED_OK

All functions are pure: facts in, a Classification (or None) out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from leakguard.ingest.normalize import name_key


@dataclass(frozen=True)
class ReservationFacts:
    id: int
    pms_confirmation_no: str
    ota_confirmation_no: str | None
    channel: str
    payment_model: str
    status: str
    arrival_date: date
    departure_date: date
    expected_cents: int | None  # room revenue plus tax, else folio total
    guest_last_name: str | None
    currency: str = "USD"


@dataclass(frozen=True)
class VccFacts:
    ota: str
    ota_confirmation_no: str
    card_last4: str | None
    vcc_amount_cents: int
    currency: str
    activation_date: date | None
    expiry_date: date | None
    guest_last_name: str | None = None
    arrival_date: date | None = None


@dataclass
class VccCase:
    """Everything the rules need to classify one card."""

    vcc: VccFacts
    reservation: ReservationFacts | None
    charged_cents: int
    settled_cents: int | None  # None means no processor data covers the charge
    as_of: date
    tolerance_cents: int
    is_duplicate: bool = False
    match_method: str = "exact"
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Classification:
    exception_type: str
    amount_at_risk_cents: int
    rule: str


def day(d: date | None) -> str:
    """Sep 3, 2026"""
    return f"{d:%b} {d.day}, {d.year}" if d else "unknown date"


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


# Fuzzy matching ---------------------------------------------------------------


def name_similarity(a: str | None, b: str | None) -> float:
    """0 to 100. Compares letters only, so O'Brien equals OBrien."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 0.0
    return float(fuzz.ratio(ka, kb))


def fuzzy_pool_eligible(res: ReservationFacts, ota: str) -> bool:
    """Only OTA-collect bookings from the same OTA can carry this OTA's card."""
    if res.channel == ota:
        return res.payment_model in ("ota_collect", "unknown")
    return res.channel == "other" and res.payment_model == "ota_collect"


def fuzzy_criteria_met(
    vcc: VccFacts,
    res: ReservationFacts,
    min_name_score: int,
    arrival_window_days: int,
    amount_tolerance_pct: float,
    names_hashed: bool = False,
) -> bool:
    """SPEC section 7 fuzzy fallback: name, arrival and amount all close."""
    if vcc.arrival_date is None or not vcc.guest_last_name or not res.guest_last_name:
        return False
    if abs((vcc.arrival_date - res.arrival_date).days) > arrival_window_days:
        return False
    if names_hashed:
        if vcc.guest_last_name != res.guest_last_name:
            return False
    elif name_similarity(vcc.guest_last_name, res.guest_last_name) < min_name_score:
        return False
    if not res.expected_cents:
        return False
    diff = abs(vcc.vcc_amount_cents - res.expected_cents)
    return diff <= amount_tolerance_pct * vcc.vcc_amount_cents


# Rules ------------------------------------------------------------------------


def guest_stayed(res: ReservationFacts, as_of: date) -> bool:
    """Checked out, in house, or still 'reserved' after the departure date
    (a stale PMS status, which should be reviewed rather than ignored)."""
    if res.status == "checked_out":
        return True
    if res.status == "in_house":
        return res.arrival_date <= as_of
    return res.status == "reserved" and res.departure_date < as_of


def is_expired(vcc: VccFacts, as_of: date) -> bool:
    """A card is usable through its expiry date, so expiry today is not expired."""
    return vcc.expiry_date is not None and vcc.expiry_date < as_of


def rule_duplicate(case: VccCase) -> Classification | None:
    if not case.is_duplicate:
        return None
    return Classification(
        "DUPLICATE_VCC",
        0,
        f"Another virtual card already exists for {case.vcc.ota} reservation "
        f"{case.vcc.ota_confirmation_no}. Check that only one card is charged.",
    )


def rule_no_match(case: VccCase) -> Classification | None:
    if case.reservation is not None:
        return None
    return Classification(
        "NO_PMS_MATCH",
        case.vcc.vcc_amount_cents,
        f"No PMS reservation found for {case.vcc.ota} confirmation "
        f"{case.vcc.ota_confirmation_no}, and no close match on guest name, "
        "arrival date and amount. Needs investigation.",
    )


def rule_cancelled(case: VccCase) -> Classification | None:
    res = case.reservation
    if res is None or res.status not in ("cancelled", "no_show") or case.charged_cents > 0:
        return None
    word = "cancelled" if res.status == "cancelled" else "a no-show"
    return Classification(
        "CANCELLED_REVIEW",
        case.vcc.vcc_amount_cents,
        f"Reservation is {word} but the OTA issued a {money(case.vcc.vcc_amount_cents)} "
        "card, which may cover a cancellation or no-show fee. Nothing was charged.",
    )


def rule_nothing_charged(case: VccCase) -> Classification | None:
    if case.charged_cents > 0 or case.reservation is None:
        return None
    amount = case.vcc.vcc_amount_cents
    if is_expired(case.vcc, case.as_of):
        return Classification(
            "EXPIRED_UNCHARGED",
            amount,
            f"Card for {money(amount)} expired on {day(case.vcc.expiry_date)} and was never "
            "charged. Recovery needs a claim with the OTA.",
        )
    if guest_stayed(case.reservation, case.as_of):
        return Classification(
            "UNCHARGED",
            amount,
            f"Guest stayed ({day(case.reservation.arrival_date)} to "
            f"{day(case.reservation.departure_date)}) but nothing was charged to the "
            f"{money(amount)} card. Card is still active until {day(case.vcc.expiry_date)}.",
        )
    return Classification("NOT_YET_DUE", 0, "Guest has not arrived yet. Nothing to charge.")


def rule_undercharged(case: VccCase) -> Classification | None:
    expected = case.vcc.vcc_amount_cents
    if case.charged_cents >= expected - case.tolerance_cents:
        return None
    gap = expected - case.charged_cents
    return Classification(
        "UNDERCHARGED",
        gap,
        f"Card was for {money(expected)} but only {money(case.charged_cents)} was charged. "
        f"{money(gap)} left on the card.",
    )


def rule_overcharged(case: VccCase) -> Classification | None:
    expected = case.vcc.vcc_amount_cents
    if case.charged_cents <= expected + case.tolerance_cents:
        return None
    over = case.charged_cents - expected
    return Classification(
        "OVERCHARGED",
        over,
        f"Charged {money(case.charged_cents)} to a {money(expected)} card. The "
        f"{money(over)} overage is a chargeback risk.",
    )


def rule_not_settled(case: VccCase) -> Classification | None:
    if case.settled_cents is None:
        return None
    gap = case.charged_cents - case.settled_cents
    if gap <= case.tolerance_cents:
        return None
    return Classification(
        "CHARGED_NOT_SETTLED",
        gap,
        f"PMS shows {money(case.charged_cents)} charged but the processor settled only "
        f"{money(case.settled_cents)}. The charge may have been declined or never captured.",
    )


def rule_ok(case: VccCase) -> Classification:
    note = "" if case.settled_cents is not None else " No processor data covers this charge."
    return Classification("CHARGED_OK", 0, f"Charged correctly.{note}")


RULES: tuple[Callable[[VccCase], Classification | None], ...] = (
    rule_duplicate,
    rule_no_match,
    rule_cancelled,
    rule_nothing_charged,
    rule_undercharged,
    rule_overcharged,
    rule_not_settled,
)


def classify(case: VccCase) -> Classification:
    for rule in RULES:
        result = rule(case)
        if result is not None:
            return result
    return rule_ok(case)
