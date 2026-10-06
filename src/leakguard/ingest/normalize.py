"""Normalizers for raw export values.

Each function takes one raw string and returns a clean value or raises
ValueError with a message a person can act on. None means "blank".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from leakguard.schemas import ENUM_SYNONYMS

BLANKS = {"", "nan", "none", "null", "n/a", "na", "-", "--", "nat"}

DEFAULT_CONFIRMATION_PREFIXES = ("EXPEDIA", "EXP", "BOOKING", "BDC", "BKG", "CONF", "RES")


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return str(value).strip().lower() in BLANKS


def clean_text(value: object) -> str | None:
    if is_blank(value):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


# Money --------------------------------------------------------------------

_EU_MONEY = re.compile(r"^-?\d{1,3}(\.\d{3})+,\d{1,2}$|^-?\d+,\d{2}$")


def money_to_cents(value: object) -> int | None:
    """Parse money into integer cents.

    Handles "$1,234.56", "(123.45)" and "123.45-" as negatives, currency codes
    and symbols, and European "1.234,56".
    """
    if is_blank(value):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))
    text = str(value).strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    text = re.sub(r"[A-Za-z$€£¥\s]", "", text)
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    if _EU_MONEY.match(text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a money amount") from None
    cents = int((amount * 100).quantize(Decimal("1"), ROUND_HALF_UP))
    return -cents if negative else cents


# Dates --------------------------------------------------------------------

_NUMERIC_DATE = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_TEXT_FORMATS = (
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %b %y",
    "%b %d, %Y",
    "%b %d %Y",
    "%B %d, %Y",
    "%d %B %Y",
    "%Y%m%d",
    "%d-%B-%Y",
)
EXCEL_EPOCH = date(1899, 12, 30)


def infer_date_order(values: list[str]) -> tuple[str | None, bool]:
    """Look at a column of slash dates and decide month-first or day-first.

    Returns (order, ambiguous). order is "mdy", "dmy" or None when the
    column has no slash dates. ambiguous is True when every value could be
    read either way, so the caller should warn and use its default.
    """
    first_over_12 = second_over_12 = seen = False
    for raw in values:
        m = _NUMERIC_DATE.match(str(raw).strip().split(" ")[0])
        if not m or len(m.group(1)) == 4:
            continue
        seen = True
        if int(m.group(1)) > 12:
            first_over_12 = True
        if int(m.group(2)) > 12:
            second_over_12 = True
    if not seen:
        return None, False
    if first_over_12 and not second_over_12:
        return "dmy", False
    if second_over_12 and not first_over_12:
        return "mdy", False
    return None, True


def parse_date(value: object, order: str = "mdy") -> date | None:
    """Parse a date in any common format. order breaks ties for 03/04/2026."""
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", text):
        return date.fromisoformat(text[:10])
    m = _NUMERIC_DATE.match(text.split(" ")[0])
    if m:
        a, b, c = m.groups()
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
        else:
            year = int(c) + (2000 if len(c) == 2 else 0)
            first, second = int(a), int(b)
            if first > 12:
                day, month = first, second
            elif second > 12:
                month, day = first, second
            elif order == "dmy":
                day, month = first, second
            else:
                month, day = first, second
        try:
            return date(year, month, day)
        except ValueError:
            raise ValueError(f"'{value}' is not a valid date") from None
    if re.fullmatch(r"\d{5}(\.0+)?", text):
        serial = int(float(text))
        if 30000 < serial < 80000:
            return EXCEL_EPOCH + timedelta(days=serial)
    for fmt in _TEXT_FORMATS:
        try:
            return datetime.strptime(text.title() if "%b" in fmt else text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a recognised date")


# Confirmation numbers -------------------------------------------------------


def confirmation(
    value: object, prefixes: tuple[str, ...] = DEFAULT_CONFIRMATION_PREFIXES
) -> str | None:
    """Uppercase, drop spaces and punctuation, strip known prefixes and
    leading zeros. Applied the same way to both sides of every join."""
    if is_blank(value):
        return None
    text = str(value).strip().upper()
    if re.fullmatch(r"\d+\.0+", text):  # Excel turned it into a float
        text = text.split(".")[0]
    text = re.sub(r"[\s\-./#_:]", "", text)
    for prefix in sorted(prefixes, key=len, reverse=True):
        rest = text[len(prefix) :]
        if text.startswith(prefix) and rest and rest[0].isdigit():
            text = rest
            break
    text = text.lstrip("0") or "0"
    return text


# Cards ----------------------------------------------------------------------


def digits_only(value: object) -> str:
    return re.sub(r"\D", "", str(value))


def last4(value: object) -> str | None:
    """Last 4 digits from a masked or full card value. Never returns more."""
    if is_blank(value):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    digits = digits_only(text)
    if len(digits) < 4:
        if digits:
            return digits.zfill(4)
        raise ValueError(f"'{value}' has no card digits")
    return digits[-4:]


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_CARD_PREFIX = re.compile(r"^(3[47]|4|5[1-5]|2[2-7]|6(011|5|4[4-9]))")


def looks_like_card_number(value: object) -> bool:
    """True when a value contains a 13 to 19 digit card number.

    To avoid flagging long confirmation numbers, a match must also pass
    the Luhn check and start with a card network prefix.
    """
    if is_blank(value):
        return False
    for match in _CARD_CANDIDATE.finditer(str(value)):
        digits = digits_only(match.group())
        if 13 <= len(digits) <= 19 and _CARD_PREFIX.match(digits) and luhn_valid(digits):
            return True
    return False


# Names ----------------------------------------------------------------------


def last_name(value: object) -> str | None:
    if is_blank(value):
        return None
    text = clean_text(value) or ""
    return text.title() if text.isupper() or text.islower() else text


def last_name_from_full(value: object) -> str | None:
    """'SMITH, JOHN' -> 'Smith'. 'John Smith' -> 'Smith'."""
    if is_blank(value):
        return None
    text = clean_text(value) or ""
    if "," in text:
        part = text.split(",")[0]
    else:
        tokens = [
            t for t in text.split(" ") if t.lower().rstrip(".") not in {"jr", "sr", "ii", "iii"}
        ]
        part = tokens[-1] if tokens else text
    return last_name(part)


def name_key(value: str | None) -> str:
    """Letters only, lowercase. Used for fuzzy name comparison."""
    if not value:
        return ""
    return re.sub(r"[^a-z]", "", value.lower())


# Enums and small types ------------------------------------------------------


def _lookup_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def enum_value(value: object, field: str, extra: dict[str, str] | None = None) -> str | None:
    """Map a raw word to our enum using mapping lookups, then built-in synonyms.

    Tries an exact match first, then the longest synonym contained in the value.
    """
    if is_blank(value):
        return None
    key = _lookup_key(str(value))
    table = {_lookup_key(k): v for k, v in (extra or {}).items()}
    for k, v in ENUM_SYNONYMS.get(field, {}).items():
        table.setdefault(_lookup_key(k), v)
    if key in table:
        return table[key]
    padded = f" {key} "
    for k in sorted(table, key=len, reverse=True):
        if len(k) >= 3 and f" {k} " in padded:
            return table[k]
    raise ValueError(f"'{value}' is not a known {field.replace('_', ' ')}")


def currency_code(value: object) -> str | None:
    if is_blank(value):
        return None
    text = str(value).strip().upper()
    symbols = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "C$": "CAD"}
    return symbols.get(text, text)


def to_int(value: object) -> int | None:
    if is_blank(value):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        raise ValueError(f"'{value}' is not a whole number") from None


TRUE_WORDS = {"y", "yes", "true", "1", "t", "x", "reversal", "reversed", "void"}
FALSE_WORDS = {"n", "no", "false", "0", "f"}


def to_bool(value: object) -> bool | None:
    if is_blank(value):
        return None
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise ValueError(f"'{value}' is not yes or no")
