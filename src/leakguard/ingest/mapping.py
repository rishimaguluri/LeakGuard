"""Mapping YAML files: load, choose and apply.

A mapping says which raw headers feed each canonical field. Several aliases
can be listed per field; matching ignores case, spaces and punctuation.
The field's kind (from schemas.py) decides the transform unless the mapping
overrides it under `transforms`.

When several mappings exist for one source folder (for example two PMS
formats), the one whose headers fit the file best is used. Adding a format
therefore means adding a YAML file, never code.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from leakguard.ingest import normalize as N
from leakguard.ingest.readers import RawTable, header_key
from leakguard.ingest.validate import row_has_card_number, scan_for_card_data, validate_row
from leakguard.schemas import ROW_SCHEMAS, SOURCE_TARGETS, TARGETS, TargetSpec

TRANSFORMS = {
    "money",
    "date",
    "confirmation",
    "last4",
    "status",
    "enum",
    "text",
    "last_name",
    "full_name",
    "currency",
    "int",
    "bool",
}
DEFAULT_FALLBACKS = {"channel": "other", "payment_model": "unknown", "transaction_type": "sale"}


class MappingError(ValueError):
    pass


@dataclass
class Mapping:
    name: str
    source_name: str
    target: str
    columns: dict[str, list[str]]
    defaults: dict[str, object] = field(default_factory=dict)
    transforms: dict[str, str] = field(default_factory=dict)
    lookups: dict[str, dict[str, str]] = field(default_factory=dict)
    fallbacks: dict[str, str] = field(default_factory=dict)
    date_order: str | None = None
    sheet: str | None = None
    header_row: int | str = "auto"
    file_types: list[str] = field(default_factory=lambda: ["csv", "xlsx"])
    confirmation_prefixes: list[str] | None = None
    notes: str = ""
    path: Path | None = None

    @property
    def spec(self) -> TargetSpec:
        return TARGETS[self.target]

    def known_headers(self) -> set[str]:
        return {header_key(a) for aliases in self.columns.values() for a in aliases}

    def resolve(self, header: list[str]) -> dict[str, int]:
        """Canonical field -> column index, using the first alias present."""
        keys = [header_key(h) for h in header]
        found: dict[str, int] = {}
        used: set[int] = set()
        for fname, aliases in self.columns.items():
            for alias in aliases:
                k = header_key(alias)
                if k in keys:
                    col = keys.index(k)
                    if col not in used:
                        found[fname] = col
                        used.add(col)
                        break
        return found

    def missing_required(self, header: list[str]) -> list[str]:
        resolved = self.resolve(header)
        missing = []
        for fname in self.spec.required_fields:
            if fname in resolved or fname in self.defaults:
                continue
            if fname == "guest_last_name" and "guest_name" in resolved:
                continue
            missing.append(fname)
        return missing

    def kind_of(self, fname: str) -> str:
        kind = self.transforms.get(fname) or self.spec.field(fname).kind
        return "enum" if kind == "status" else kind

    def to_yaml(self) -> str:
        data: dict[str, object] = {
            "name": self.name,
            "source_name": self.source_name,
            "target": self.target,
            "file_types": self.file_types,
            "header_row": self.header_row,
        }
        if self.sheet:
            data["sheet"] = self.sheet
        if self.date_order:
            data["date_order"] = self.date_order
        data["columns"] = self.columns
        for key in ("defaults", "transforms", "lookups", "fallbacks"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        if self.confirmation_prefixes:
            data["confirmation_prefixes"] = self.confirmation_prefixes
        data["notes"] = self.notes
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# Loading ------------------------------------------------------------------------


def parse_mapping(data: dict, path: Path | None = None) -> Mapping:
    where = path.name if path else "mapping"
    for key in ("name", "source_name", "target", "columns"):
        if key not in data:
            raise MappingError(f"{where}: missing '{key}'")
    target = data["target"]
    if target not in TARGETS:
        raise MappingError(f"{where}: unknown target '{target}'. Use one of {', '.join(TARGETS)}")
    if data["source_name"] in SOURCE_TARGETS and SOURCE_TARGETS[data["source_name"]] != target:
        raise MappingError(
            f"{where}: source '{data['source_name']}' feeds '{SOURCE_TARGETS[data['source_name']]}', not '{target}'"
        )
    spec = TARGETS[target]
    valid = {f.name for f in spec.fields}
    columns = {}
    for fname, aliases in (data.get("columns") or {}).items():
        if fname not in valid:
            raise MappingError(f"{where}: '{fname}' is not a field of {target}")
        columns[fname] = [aliases] if isinstance(aliases, str) else [str(a) for a in aliases]
    for fname, transform in (data.get("transforms") or {}).items():
        if transform not in TRANSFORMS:
            raise MappingError(f"{where}: unknown transform '{transform}' for {fname}")
    date_order = data.get("date_order")
    if date_order not in (None, "mdy", "dmy"):
        raise MappingError(f"{where}: date_order must be mdy or dmy")
    return Mapping(
        name=str(data["name"]),
        source_name=str(data["source_name"]),
        target=target,
        columns=columns,
        defaults=dict(data.get("defaults") or {}),
        transforms=dict(data.get("transforms") or {}),
        lookups={
            k: {str(a): str(b) for a, b in v.items()}
            for k, v in (data.get("lookups") or {}).items()
        },
        fallbacks=dict(data.get("fallbacks") or {}),
        date_order=date_order,
        sheet=data.get("sheet"),
        header_row=data.get("header_row", "auto"),
        file_types=list(data.get("file_types") or ["csv", "xlsx"]),
        confirmation_prefixes=data.get("confirmation_prefixes"),
        notes=str(data.get("notes") or ""),
        path=path,
    )


def load_mapping(path: Path) -> Mapping:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_mapping(data, path)


def load_all_mappings(folder: Path) -> tuple[list[Mapping], list[str]]:
    """Every valid mapping in the folder, plus error messages for broken ones."""
    mappings, errors = [], []
    for path in sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml")):
        try:
            mappings.append(load_mapping(path))
        except (MappingError, yaml.YAMLError) as exc:
            errors.append(str(exc))
    return mappings, errors


def choose_mapping(mappings: list[Mapping], source_name: str, header: list[str]) -> Mapping | None:
    """The mapping for this source whose headers fit best, if any fits at all."""
    best, best_score = None, -1
    for m in mappings:
        if m.source_name != source_name or m.missing_required(header):
            continue
        score = len(m.resolve(header))
        if score > best_score:
            best, best_score = m, score
    return best


# Applying -----------------------------------------------------------------------


@dataclass
class Issue:
    row_number: int | None
    severity: str  # error / warning
    field: str | None
    message: str


@dataclass
class MappedRows:
    rows: list[dict]
    issues: list[Issue]
    rows_read: int
    rows_rejected: int
    warnings: list[str]


def hash_name(value: str) -> str:
    """One-way pseudonym for a guest name. Same name, same hash."""
    return hashlib.sha256(f"leakguard:{N.name_key(value)}".encode()).hexdigest()[:16]


def _transformer(mapping: Mapping, fname: str, date_order: str) -> Callable[[str], object]:
    kind = mapping.kind_of(fname)
    if kind == "money":
        return N.money_to_cents
    if kind == "date":
        return lambda v: N.parse_date(v, date_order)
    if kind == "confirmation":
        prefixes = tuple(mapping.confirmation_prefixes or N.DEFAULT_CONFIRMATION_PREFIXES)
        return lambda v: N.confirmation(v, prefixes)
    if kind == "last4":
        return N.last4
    if kind == "last_name":
        return N.last_name
    if kind == "full_name":
        return N.last_name_from_full
    if kind == "currency":
        return N.currency_code
    if kind == "int":
        return N.to_int
    if kind == "bool":
        return N.to_bool
    if kind == "enum":
        extra = mapping.lookups.get(fname)
        return lambda v: N.enum_value(v, fname, extra)
    return N.clean_text


def apply_mapping(
    mapping: Mapping, table: RawTable, file_name: str, hash_names: bool = False
) -> MappedRows:
    spec = mapping.spec
    schema = ROW_SCHEMAS[mapping.target]
    resolved = mapping.resolve(table.header)
    kinds = {col: mapping.kind_of(f) for f, col in resolved.items()}
    scan = scan_for_card_data(table.header, table.rows, kinds, file_name)
    for fname, col in list(resolved.items()):
        if col in scan.drop_columns:
            del resolved[fname]

    issues: list[Issue] = []
    fallbacks = {**DEFAULT_FALLBACKS, **mapping.fallbacks}
    unknown_enum: Counter[tuple[str, str]] = Counter()

    plans = []
    for fname, col in resolved.items():
        order = mapping.date_order or "mdy"
        if mapping.kind_of(fname) == "date":
            inferred, ambiguous = N.infer_date_order([r[col] for r in table.rows[:5000]])
            if inferred:
                order = inferred
            elif ambiguous and not mapping.date_order:
                issues.append(
                    Issue(
                        None,
                        "warning",
                        fname,
                        (
                            f"Every date in '{table.header[col]}' could be month-first or day-first. "
                            "Read as month/day/year. Set date_order in the mapping if that is wrong."
                        ),
                    )
                )
        db_col = spec.field(fname).db_column or "guest_last_name"
        plans.append(
            (fname, col, db_col, _transformer(mapping, fname, order), mapping.kind_of(fname))
        )

    out: list[dict] = []
    rows_read = rejected = 0
    for offset, row in enumerate(table.rows):
        if not any(c.strip() for c in row):
            continue
        rows_read += 1
        row_number = table.first_row_number + offset
        bad = row_has_card_number(row, scan.reject_row_columns)
        if bad is not None:
            rejected += 1
            issues.append(
                Issue(
                    row_number,
                    "error",
                    None,
                    (
                        f"Column '{table.header[bad]}' holds what looks like a full card number. "
                        "Row rejected and the value was not stored."
                    ),
                )
            )
            continue

        values: dict[str, object] = {}
        errors: list[tuple[str | None, str]] = []
        full_name = None
        for fname, col, db_col, fn, kind in plans:
            raw = row[col]
            try:
                value = fn(raw)
            except ValueError as exc:
                if kind == "enum" and fname in fallbacks:
                    unknown_enum[(fname, raw.strip())] += 1
                    value = fallbacks[fname]
                else:
                    errors.append((fname, str(exc)))
                    continue
            if kind == "full_name":
                full_name = value
            elif value is not None:
                values[db_col] = value
        if full_name and not values.get("guest_last_name"):
            values["guest_last_name"] = full_name
        for fname, default in mapping.defaults.items():
            db_col = spec.field(fname).db_column or fname
            if values.get(db_col) is None:
                values[db_col] = default
        if hash_names and values.get("guest_last_name"):
            values["guest_last_name"] = hash_name(str(values["guest_last_name"]))

        if not errors:
            clean, problems = validate_row(schema, values)
            errors.extend(problems)
        if errors:
            rejected += 1
            for fname, message in errors:
                issues.append(Issue(row_number, "error", fname, message))
            continue
        clean["_row_number"] = row_number
        out.append(clean)

    for (fname, raw), count in unknown_enum.most_common():
        issues.append(
            Issue(
                None,
                "warning",
                fname,
                (
                    f"'{raw}' is not a known {fname.replace('_', ' ')} ({count} rows). "
                    f"Treated as '{fallbacks[fname]}'. Add it under lookups in {mapping.name} if needed."
                ),
            )
        )
    return MappedRows(out, issues, rows_read, rejected, scan.warnings)
