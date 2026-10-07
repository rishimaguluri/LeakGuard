"""Suggest column mappings for an unfamiliar export (the mapping wizard).

Each canonical field is scored against each file column on two things:
  name   how close the header is to any alias used by existing mappings for
         this target, or to the field's own name (rapidfuzz, 0 to 100)
  values share of sample values that parse as the field's kind (a date
         column should parse as dates, an amount as money, and so on)
score = 0.7 x name + 0.3 x values. Fields are assigned greedily, highest
score first, one column per field, if the score is at least MIN_SCORE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from leakguard.ingest import normalize as N
from leakguard.ingest.mapping import Mapping
from leakguard.ingest.readers import RawTable, header_key
from leakguard.schemas import TARGETS, CanonicalField

MIN_SCORE = 55
SAMPLE_ROWS = 50


@dataclass
class Suggestion:
    field: str
    column: str | None
    score: int
    name_score: int
    value_score: int


def _aliases(field: CanonicalField, target: str, mappings: list[Mapping]) -> list[str]:
    names = {field.name.replace("_", " "), field.description}
    if field.kind == "money":
        names.add(field.name.replace("_", " ") + " amount")
    for m in mappings:
        if m.target == target:
            names.update(m.columns.get(field.name, []))
    return [n for n in names if n]


def _parses(kind: str, field: str, value: str) -> bool:
    try:
        if kind == "money":
            return N.money_to_cents(value) is not None and bool(re.search(r"\d", value))
        if kind == "date":
            return N.parse_date(value) is not None
        if kind == "last4":
            digits = N.digits_only(value)
            return 4 <= len(digits) <= 19 and (
                len(digits) == 4
                or bool(re.search(r"[X*x•]", value))
                or N.looks_like_card_number(value)
            )
        if kind == "confirmation":
            return bool(re.fullmatch(r"[A-Za-z0-9\-# ]{4,30}", value.strip())) and bool(
                re.search(r"\d", value)
            )
        if kind in ("last_name", "full_name"):
            return bool(re.fullmatch(r"[A-Za-z'\-., ]{2,60}", value.strip()))
        if kind == "currency":
            return bool(re.fullmatch(r"[A-Za-z]{3}|[$€£]", value.strip()))
        if kind == "int":
            return value.strip().isdigit()
        if kind == "bool":
            return N.to_bool(value) is not None
        if kind == "enum":
            return N.enum_value(value, field) is not None
        return bool(value.strip())
    except ValueError:
        return False


def value_score(kind: str, field: str, values: list[str]) -> int:
    sample = [v for v in values if not N.is_blank(v)][:SAMPLE_ROWS]
    if not sample:
        return 0
    return round(100 * sum(_parses(kind, field, v) for v in sample) / len(sample))


def name_score(header: str, aliases: list[str]) -> int:
    h = header_key(header)
    if not h:
        return 0
    best = 0
    for alias in aliases:
        a = header_key(alias)
        if a == h:
            return 100
        best = max(
            best,
            int(fuzz.ratio(h, a)),
            int(fuzz.token_set_ratio(header.lower(), alias.lower())) - 10,
        )
    return best


def suggest(table: RawTable, target: str, mappings: list[Mapping]) -> list[Suggestion]:
    spec = TARGETS[target]
    columns = [(i, h) for i, h in enumerate(table.header) if h.strip()]
    candidates: list[tuple[int, str, int, int, int]] = []
    for f in spec.fields:
        aliases = _aliases(f, target, mappings)
        for col, header in columns:
            ns = name_score(header, aliases)
            vs = value_score(f.kind, f.name, [r[col] for r in table.rows[:SAMPLE_ROWS]])
            candidates.append((round(0.7 * ns + 0.3 * vs), f.name, col, ns, vs))
    candidates.sort(reverse=True)
    taken_fields: dict[str, Suggestion] = {}
    taken_cols: set[int] = set()
    for score, fname, col, ns, vs in candidates:
        if score < MIN_SCORE or fname in taken_fields or col in taken_cols:
            continue
        taken_fields[fname] = Suggestion(fname, table.header[col], score, ns, vs)
        taken_cols.add(col)
    return [taken_fields.get(f.name) or Suggestion(f.name, None, 0, 0, 0) for f in spec.fields]


def build_mapping(
    name: str,
    source_name: str,
    target: str,
    chosen: dict[str, str | None],
    defaults: dict[str, object] | None = None,
    sheet: str | None = None,
    date_order: str | None = None,
    notes: str = "",
) -> Mapping:
    """A Mapping from the wizard's choices (field -> column header)."""
    columns = {f: [col] for f, col in chosen.items() if col}
    return Mapping(
        name=name,
        source_name=source_name,
        target=target,
        columns=columns,
        defaults=dict(defaults or {}),
        sheet=sheet,
        date_order=date_order,
        notes=notes
        or "Created with the mapping wizard. Check against a second export before relying on it.",
    )


def safe_mapping_name(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")[:60]
