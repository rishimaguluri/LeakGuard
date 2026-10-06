"""Canonical fields, enums and pydantic row schemas.

Each import target (reservations, pms_payments, vcc_records,
processor_transactions) has a list of canonical fields. A mapping YAML says
which raw headers feed each field; the field's kind decides the default
transform. After transforms, every row is validated by the target's
pydantic model before it is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

# Enums ----------------------------------------------------------------------

OTAS = ("expedia", "booking")
CHANNELS = ("expedia", "booking", "direct", "other")
PAYMENT_MODELS = ("ota_collect", "hotel_collect", "unknown")
RES_STATUSES = ("in_house", "checked_out", "cancelled", "no_show", "reserved")
TXN_TYPES = ("sale", "refund", "void", "chargeback")

EXCEPTION_TYPES = (
    "UNCHARGED",
    "EXPIRED_UNCHARGED",
    "UNDERCHARGED",
    "OVERCHARGED",
    "CHARGED_NOT_SETTLED",
    "NO_PMS_MATCH",
    "CANCELLED_REVIEW",
    "DUPLICATE_VCC",
)
OK_TYPES = ("CHARGED_OK", "NOT_YET_DUE")
ALL_RESULT_TYPES = EXCEPTION_TYPES + OK_TYPES

TYPE_LABELS = {
    "UNCHARGED": "Uncharged",
    "EXPIRED_UNCHARGED": "Expired, uncharged",
    "UNDERCHARGED": "Undercharged",
    "OVERCHARGED": "Overcharged",
    "CHARGED_NOT_SETTLED": "Charged, not settled",
    "NO_PMS_MATCH": "No PMS match",
    "CANCELLED_REVIEW": "Cancelled, review",
    "DUPLICATE_VCC": "Duplicate card",
    "CHARGED_OK": "Charged correctly",
    "NOT_YET_DUE": "Not yet due",
}

OPEN_STATUSES = ("open", "assigned", "in_progress")
HUMAN_CLOSED_STATUSES = ("recovered", "written_off", "not_an_issue")
STATUSES = OPEN_STATUSES + HUMAN_CLOSED_STATUSES

STATUS_LABELS = {
    "open": "Open",
    "assigned": "Assigned",
    "in_progress": "In progress",
    "recovered": "Recovered",
    "written_off": "Written off",
    "not_an_issue": "Not an issue",
}

# Built-in synonyms for enum fields. Mappings can add their own in `lookups`.
# Keys are compared after lowercasing and stripping punctuation.
ENUM_SYNONYMS: dict[str, dict[str, str]] = {
    "status": {
        "checked out": "checked_out",
        "checkedout": "checked_out",
        "co": "checked_out",
        "departed": "checked_out",
        "out": "checked_out",
        "in house": "in_house",
        "inhouse": "in_house",
        "checked in": "in_house",
        "ih": "in_house",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "cancel": "cancelled",
        "cxl": "cancelled",
        "no show": "no_show",
        "noshow": "no_show",
        "ns": "no_show",
        "reserved": "reserved",
        "confirmed": "reserved",
        "due in": "reserved",
        "booked": "reserved",
        "new": "reserved",
    },
    "channel": {
        "expedia": "expedia",
        "hotels com": "expedia",
        "exp": "expedia",
        "booking": "booking",
        "booking com": "booking",
        "bdc": "booking",
        "bcom": "booking",
        "direct": "direct",
        "web": "direct",
        "website": "direct",
        "brand com": "direct",
        "phone": "direct",
        "walk in": "direct",
        "voice": "direct",
        "gds": "other",
        "group": "other",
        "wholesale": "other",
    },
    "payment_model": {
        "hotel collect": "hotel_collect",
        "pay at hotel": "hotel_collect",
        "pay at property": "hotel_collect",
        "guest card": "hotel_collect",
        "expedia collect": "ota_collect",
        "ota collect": "ota_collect",
        "channel collect": "ota_collect",
        "payments by booking": "ota_collect",
        "prepaid": "ota_collect",
        "virtual card": "ota_collect",
        "vcc": "ota_collect",
        "agency": "ota_collect",
    },
    "transaction_type": {
        "sale": "sale",
        "purchase": "sale",
        "capture": "sale",
        "settled": "sale",
        "refund": "refund",
        "credit": "refund",
        "return": "refund",
        "void": "void",
        "voided": "void",
        "chargeback": "chargeback",
        "dispute": "chargeback",
        "cb": "chargeback",
    },
    "ota": {
        "expedia": "expedia",
        "booking": "booking",
        "booking com": "booking",
        "bdc": "booking",
    },
}

ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "status": RES_STATUSES,
    "channel": CHANNELS,
    "payment_model": PAYMENT_MODELS,
    "transaction_type": TXN_TYPES,
    "ota": OTAS,
}

# Canonical field registry ---------------------------------------------------

FieldKind = Literal[
    "confirmation",
    "money",
    "date",
    "last4",
    "last_name",
    "full_name",
    "text",
    "enum",
    "currency",
    "int",
    "bool",
]


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: FieldKind
    required: bool = False
    description: str = ""

    @property
    def db_column(self) -> str | None:
        """Column the value lands in. full_name feeds guest_last_name."""
        if self.kind == "money":
            return f"{self.name}_cents"
        if self.kind == "full_name":
            return None
        return self.name


@dataclass(frozen=True)
class TargetSpec:
    name: str
    label: str
    fields: tuple[CanonicalField, ...]
    coverage_date_field: str  # used for the date range a file covers

    def field(self, name: str) -> CanonicalField:
        return next(f for f in self.fields if f.name == name)

    @property
    def required_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.required]


F = CanonicalField

TARGETS: dict[str, TargetSpec] = {
    "reservations": TargetSpec(
        "reservations",
        "PMS reservations",
        (
            F("pms_confirmation_no", "confirmation", True, "PMS confirmation number"),
            F("ota_confirmation_no", "confirmation", False, "OTA confirmation number"),
            F("channel", "enum", False, "Booking channel or source"),
            F("payment_model", "enum", False, "OTA collect or hotel collect"),
            F("guest_last_name", "last_name", False, "Guest last name"),
            F("guest_name", "full_name", False, "Guest full name, last name is kept"),
            F("arrival_date", "date", True, "Arrival date"),
            F("departure_date", "date", True, "Departure date"),
            F("nights", "int", False, "Number of nights"),
            F("status", "enum", True, "Reservation status"),
            F("room_revenue", "money", False, "Room revenue"),
            F("tax", "money", False, "Tax"),
            F("folio_total", "money", False, "Folio total"),
            F("currency", "currency", False, "Currency code"),
        ),
        "arrival_date",
    ),
    "pms_payments": TargetSpec(
        "pms_payments",
        "PMS payment postings",
        (
            F("pms_confirmation_no", "confirmation", True, "PMS confirmation or folio number"),
            F("posting_date", "date", True, "Posting date"),
            F("payment_type", "text", False, "Payment type or method"),
            F("card_last4", "last4", False, "Card last 4 digits"),
            F("amount", "money", True, "Payment amount"),
            F("currency", "currency", False, "Currency code"),
            F("is_reversal", "bool", False, "Reversal or adjustment flag"),
        ),
        "posting_date",
    ),
    "vcc_records": TargetSpec(
        "vcc_records",
        "OTA virtual cards",
        (
            F("ota", "enum", True, "Which OTA issued the card"),
            F("ota_confirmation_no", "confirmation", True, "OTA reservation number"),
            F("card_last4", "last4", False, "Card last 4 digits"),
            F("vcc_amount", "money", True, "Amount loaded on the card"),
            F("currency", "currency", False, "Currency code"),
            F("activation_date", "date", False, "Card activation date"),
            F("expiry_date", "date", True, "Card expiry date"),
            F("ota_status", "text", False, "Charge status shown by the OTA"),
            F("guest_last_name", "last_name", False, "Guest last name"),
            F("guest_name", "full_name", False, "Guest full name, last name is kept"),
            F("arrival_date", "date", False, "Check-in date"),
        ),
        "activation_date",
    ),
    "processor_transactions": TargetSpec(
        "processor_transactions",
        "Processor settlements",
        (
            F("transaction_date", "date", True, "Transaction date"),
            F("settlement_date", "date", False, "Settlement or funding date"),
            F("card_last4", "last4", True, "Card last 4 digits"),
            F("amount", "money", True, "Transaction amount"),
            F("currency", "currency", False, "Currency code"),
            F("transaction_type", "enum", False, "Sale, refund, void or chargeback"),
            F("auth_code", "text", False, "Authorization code"),
            F("reference", "confirmation", False, "Reference, often confirmation or folio"),
        ),
        "transaction_date",
    ),
}

# Which source folders feed which target. Folder names are the contract with
# the people dropping files in, so they are fixed here and documented.
SOURCE_TARGETS: dict[str, str] = {
    "pms_reservations": "reservations",
    "pms_payments": "pms_payments",
    "expedia_vcc": "vcc_records",
    "booking_vcc": "vcc_records",
    "processor_settlement": "processor_transactions",
}

SOURCE_LABELS: dict[str, str] = {
    "pms_reservations": "PMS reservations",
    "pms_payments": "PMS payments",
    "expedia_vcc": "Expedia virtual cards",
    "booking_vcc": "Booking.com virtual cards",
    "processor_settlement": "Processor settlement",
}

REQUIRED_SOURCES = tuple(SOURCE_TARGETS)

# Row schemas ------------------------------------------------------------------


class _Row(BaseModel):
    model_config = {"extra": "ignore"}

    @field_validator("currency", check_fields=False)
    @classmethod
    def _currency(cls, v: str) -> str:
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"currency '{v}' is not a 3 letter code")
        return v.upper()

    @field_validator("card_last4", check_fields=False)
    @classmethod
    def _last4(cls, v: str | None) -> str | None:
        if v is not None and (len(v) != 4 or not v.isdigit()):
            raise ValueError(f"card last 4 '{v}' is not 4 digits")
        return v


class ReservationRow(_Row):
    pms_confirmation_no: str
    ota_confirmation_no: str | None = None
    channel: Literal["expedia", "booking", "direct", "other"] = "other"
    payment_model: Literal["ota_collect", "hotel_collect", "unknown"] = "unknown"
    guest_last_name: str | None = None
    arrival_date: date
    departure_date: date
    nights: int | None = None
    status: Literal["in_house", "checked_out", "cancelled", "no_show", "reserved"]
    room_revenue_cents: int | None = None
    tax_cents: int | None = None
    folio_total_cents: int | None = None
    currency: str = "USD"

    @model_validator(mode="after")
    def _dates(self) -> ReservationRow:
        if self.departure_date < self.arrival_date:
            raise ValueError("departure date is before arrival date")
        if self.nights is None:
            self.nights = (self.departure_date - self.arrival_date).days
        return self


class PmsPaymentRow(_Row):
    pms_confirmation_no: str
    posting_date: date
    payment_type: str | None = None
    card_last4: str | None = None
    amount_cents: int
    currency: str = "USD"
    is_reversal: bool = False

    @model_validator(mode="after")
    def _reversal_sign(self) -> PmsPaymentRow:
        """Reversals are stored negative, whatever sign the export used."""
        if self.amount_cents < 0:
            self.is_reversal = True
        elif self.is_reversal:
            self.amount_cents = -self.amount_cents
        return self


class VccRow(_Row):
    ota: Literal["expedia", "booking"]
    ota_confirmation_no: str
    card_last4: str | None = None
    vcc_amount_cents: int
    currency: str = "USD"
    activation_date: date | None = None
    expiry_date: date
    ota_status: str | None = None
    guest_last_name: str | None = None
    arrival_date: date | None = None

    @field_validator("vcc_amount_cents")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("card amount must be above zero")
        return v


class ProcessorRow(_Row):
    transaction_date: date
    settlement_date: date | None = None
    card_last4: str
    amount_cents: int
    currency: str = "USD"
    transaction_type: Literal["sale", "refund", "void", "chargeback"] = "sale"
    auth_code: str | None = None
    reference: str | None = None

    @model_validator(mode="after")
    def _sign(self) -> ProcessorRow:
        """Sales positive; refunds and chargebacks negative."""
        if self.transaction_type in ("refund", "chargeback"):
            self.amount_cents = -abs(self.amount_cents)
        elif self.transaction_type == "sale":
            self.amount_cents = abs(self.amount_cents)
        return self


ROW_SCHEMAS: dict[str, type[BaseModel]] = {
    "reservations": ReservationRow,
    "pms_payments": PmsPaymentRow,
    "vcc_records": VccRow,
    "processor_transactions": ProcessorRow,
}
