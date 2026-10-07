"""Priority score for exceptions.

    score = dollars_at_risk x urgency x type_weight

urgency, for types where the card can still be charged:
    card still active   1 + 2 x (30 - days_to_expiry) / 30, with days clamped to 0..30
                        so 1.0 at 30+ days out, rising to 3.0 on the expiry day
    card expired        0.5 (needs an OTA claim, lower odds)
    expiry unknown      1.0
Types where expiry does not drive urgency (overcharged, not settled) use 1.0.

type_weight reflects how likely the money is recoverable as cash:
    UNCHARGED, UNDERCHARGED, EXPIRED_UNCHARGED 1.0, CHARGED_NOT_SETTLED 0.9,
    OVERCHARGED 0.8, NO_PMS_MATCH 0.7, CANCELLED_REVIEW 0.6.
DUPLICATE_VCC has no dollars at risk, so it scores on 25% of the card amount
at weight 0.5, enough to show up for review without crowding out leaks.

Examples: a $1,000 card expiring today scores 3,000. The same card with 30
days left scores 1,000. Expired, it scores 500.
"""

from __future__ import annotations

EXPIRY_DRIVEN = {
    "UNCHARGED",
    "UNDERCHARGED",
    "EXPIRED_UNCHARGED",
    "NO_PMS_MATCH",
    "CANCELLED_REVIEW",
}
TYPE_WEIGHTS = {
    "UNCHARGED": 1.0,
    "UNDERCHARGED": 1.0,
    "EXPIRED_UNCHARGED": 1.0,
    "CHARGED_NOT_SETTLED": 0.9,
    "OVERCHARGED": 0.8,
    "NO_PMS_MATCH": 0.7,
    "CANCELLED_REVIEW": 0.6,
    "DUPLICATE_VCC": 0.5,
}
URGENCY_WINDOW_DAYS = 30
EXPIRED_FACTOR = 0.5


def urgency(exception_type: str, days_to_expiry: int | None) -> float:
    if exception_type not in EXPIRY_DRIVEN or days_to_expiry is None:
        return 1.0
    if days_to_expiry < 0:
        return EXPIRED_FACTOR
    days = min(days_to_expiry, URGENCY_WINDOW_DAYS)
    return 1 + 2 * (URGENCY_WINDOW_DAYS - days) / URGENCY_WINDOW_DAYS


def priority_score(
    exception_type: str,
    amount_at_risk_cents: int,
    days_to_expiry: int | None,
    card_amount_cents: int = 0,
) -> float:
    weight = TYPE_WEIGHTS.get(exception_type)
    if weight is None:
        return 0.0  # CHARGED_OK, NOT_YET_DUE
    dollars = amount_at_risk_cents / 100
    if exception_type == "DUPLICATE_VCC":
        dollars = 0.25 * card_amount_cents / 100
    return round(dollars * urgency(exception_type, days_to_expiry) * weight, 1)
