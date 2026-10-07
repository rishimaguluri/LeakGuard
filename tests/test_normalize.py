from datetime import date

import pytest

from leakguard.ingest import normalize as N


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("$1,234.56", 123456),
        ("1234.5", 123450),
        ("(12.00)", -1200),
        ("12.00-", -1200),
        ("-$5.10", -510),
        ("USD 99.99", 9999),
        ("1.234,56", 123456),
        ("0", 0),
        (42.5, 4250),
        ("  $7 ", 700),
    ],
)
def test_money(raw: object, cents: int) -> None:
    assert N.money_to_cents(raw) == cents


@pytest.mark.parametrize("raw", ["", "N/A", None, "-", "nan"])
def test_money_blank(raw: object) -> None:
    assert N.money_to_cents(raw) is None


def test_money_rejects_text() -> None:
    with pytest.raises(ValueError, match="not a money amount"):
        N.money_to_cents("pending")


@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("2026-03-04", "mdy", date(2026, 3, 4)),
        ("2026-03-04 00:00:00", "mdy", date(2026, 3, 4)),
        ("03/04/2026", "mdy", date(2026, 3, 4)),
        ("03/04/2026", "dmy", date(2026, 4, 3)),
        ("25/12/2025", "mdy", date(2025, 12, 25)),  # unambiguous wins over order
        ("3/4/26", "mdy", date(2026, 3, 4)),
        ("05-OCT-25", "mdy", date(2025, 10, 5)),
        ("Oct 5, 2025", "mdy", date(2025, 10, 5)),
        ("20251005", "mdy", date(2025, 10, 5)),
        ("45935", "mdy", date(2025, 10, 5)),  # Excel serial
    ],
)
def test_dates(raw: str, order: str, expected: date) -> None:
    assert N.parse_date(raw, order) == expected


def test_bad_dates() -> None:
    with pytest.raises(ValueError):
        N.parse_date("TBD")
    with pytest.raises(ValueError):
        N.parse_date("2026-02-31")


def test_infer_date_order() -> None:
    assert N.infer_date_order(["01/02/2026", "13/02/2026"]) == ("dmy", False)
    assert N.infer_date_order(["01/02/2026", "02/13/2026"]) == ("mdy", False)
    assert N.infer_date_order(["01/02/2026", "03/04/2026"]) == (None, True)
    assert N.infer_date_order(["2026-01-02"]) == (None, False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EXP-0012345", "12345"),
        ("bdc 4455-66", "445566"),
        ("12345.0", "12345"),
        ("ABC123", "ABC123"),
        ("RES#000987", "987"),
        ("CONFIRMED9", "CONFIRMED9"),  # prefix only stripped before a digit
    ],
)
def test_confirmation(raw: str, expected: str) -> None:
    assert N.confirmation(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("XXXXXXXXXXXX1234", "1234"),
        ("**** **** **** 0042", "0042"),
        ("5555555555554444", "4444"),
        ("1234", "1234"),
        ("42", "0042"),
        ("1234.0", "1234"),
    ],
)
def test_last4(raw: str, expected: str) -> None:
    assert N.last4(raw) == expected


def test_card_number_detection() -> None:
    assert N.looks_like_card_number("5555555555554444")
    assert N.looks_like_card_number("4111 1111 1111 1111")
    assert N.looks_like_card_number("card 4111-1111-1111-1111 exp")
    assert not N.looks_like_card_number("XXXXXXXXXXXX1234")
    assert not N.looks_like_card_number("7123456789")  # 10 digit confirmation
    assert not N.looks_like_card_number("1234567890123")  # 13 digits, fails Luhn/prefix
    assert not N.looks_like_card_number("9999999999999995")  # Luhn ok, no card prefix


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SMITH, JOHN", "Smith"),
        ("John Smith", "Smith"),
        ("Mary O'Brien Jr.", "O'Brien"),
        ("de la Cruz", "Cruz"),
    ],
)
def test_last_name_from_full(raw: str, expected: str) -> None:
    assert N.last_name_from_full(raw) == expected


def test_name_key_ignores_punctuation() -> None:
    assert N.name_key("O'Brien") == N.name_key("OBRIEN") == "obrien"


def test_enum_lookup() -> None:
    assert N.enum_value("CHECKED OUT", "status") == "checked_out"
    assert N.enum_value("No-Show", "status") == "no_show"
    assert N.enum_value("Booking.com", "channel") == "booking"
    assert N.enum_value("EXPEDIA HOTEL COLLECT", "payment_model") == "hotel_collect"
    assert N.enum_value("EXPEDIA COLLECT", "payment_model") == "ota_collect"
    assert N.enum_value("Weird", "status", {"weird": "reserved"}) == "reserved"
    with pytest.raises(ValueError):
        N.enum_value("Nonsense", "status")


def test_bool() -> None:
    assert N.to_bool("Y") is True
    assert N.to_bool("no") is False
    assert N.to_bool("") is None
