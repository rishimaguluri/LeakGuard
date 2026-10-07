"""Card data scanner and row validation.

The scanner runs on raw text before any value is mapped, so a full card
number can never reach the database:
  - a column whose header looks like a CVV or security code is dropped
  - an unmapped column holding full card numbers is dropped, with a warning
  - a mapped last-4 column holding full numbers keeps only the last 4,
    with a warning
  - any other mapped column holding a card number rejects that row
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from leakguard.ingest.normalize import looks_like_card_number
from leakguard.ingest.readers import header_key

CVV_HEADERS = re.compile(
    r"^(cvv|cvv2|cvc|cvc2|cid|csc|securitycode|cardsecuritycode|cardverification\w*)$"
)
FULL_CARD_WARNING = (
    "Full card numbers found in {file}. Dropped. Ask the hotel to send masked exports."
)


@dataclass
class ScanResult:
    drop_columns: set[int] = field(default_factory=set)
    reject_row_columns: set[int] = field(default_factory=set)  # mapped, non-last4
    warnings: list[str] = field(default_factory=list)


def _column_has_card_numbers(rows: list[list[str]], col: int) -> bool:
    return any(looks_like_card_number(r[col]) for r in rows if col < len(r))


def scan_for_card_data(
    header: list[str],
    rows: list[list[str]],
    mapped_columns: dict[int, str],
    file_name: str,
) -> ScanResult:
    """Decide what to drop before mapping. mapped_columns: column index -> field kind."""
    result = ScanResult()
    full_numbers_seen = False
    for col, name in enumerate(header):
        kind = mapped_columns.get(col)
        if CVV_HEADERS.match(header_key(name)):
            result.drop_columns.add(col)
            result.warnings.append(
                f"Column '{name}' looks like a card security code. Dropped without reading."
            )
            continue
        if not _column_has_card_numbers(rows, col):
            continue
        full_numbers_seen = True
        if kind is None:
            result.drop_columns.add(col)
        elif kind != "last4":
            result.reject_row_columns.add(col)
    if full_numbers_seen:
        result.warnings.insert(0, FULL_CARD_WARNING.format(file=file_name))
    return result


def row_has_card_number(row: list[str], columns: set[int]) -> int | None:
    """First column in `columns` that holds a card number, or None."""
    for col in columns:
        if col < len(row) and looks_like_card_number(row[col]):
            return col
    return None


def validate_row(
    schema: type[BaseModel], values: dict
) -> tuple[dict | None, list[tuple[str, str]]]:
    """Validate one canonical row. Returns (clean values, [(field, message)])."""
    try:
        model = schema.model_validate(values)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or None
            msg = err["msg"].removeprefix("Value error, ")
            if err["type"] == "missing":
                msg = "required value is missing"
            problems.append((loc, msg))
        return None, problems
    return model.model_dump(), []
