"""Mapping wizard: teach LeakGuard a new export format without code."""

import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import context
import ui
from leakguard.config import load_portfolios, mappings_dir
from leakguard.db import session_scope
from leakguard.ingest.discover import DiscoveredFile
from leakguard.ingest.importer import import_file, sync_portfolios
from leakguard.ingest.mapping import apply_mapping, load_all_mappings
from leakguard.ingest.readers import UnsupportedFile, read_table
from leakguard.ingest.suggest import build_mapping, safe_mapping_name, suggest
from leakguard.matching.engine import run_matching
from leakguard.schemas import ENUM_VALUES, SOURCE_LABELS, SOURCE_TARGETS, TARGETS

c = context.ctx()
ui.topbar(c.portfolio_name, c.settings.data_mode)
ui.page_header(
    "Mapping wizard",
    "Got an export in a format LeakGuard has not seen? Upload it, check the suggested columns, and save a mapping. No code needed.",
)

up_col, kind_col = st.columns([1.4, 1], gap="large")
with up_col:
    upload = st.file_uploader(
        "1. Upload a CSV or Excel export",
        type=["csv", "txt", "tsv", "xlsx", "xlsm"],
        help="Ask for masked card numbers. Full card numbers are dropped automatically.",
    )
with kind_col:
    source = st.selectbox(
        "2. What is this file?", list(SOURCE_TARGETS), format_func=SOURCE_LABELS.get
    )
target = SOURCE_TARGETS[source]
spec = TARGETS[target]

if upload is None:
    ui.empty_state(
        "Upload a file to start",
        "LeakGuard suggests a match for every column, shows you 20 parsed rows, and saves a mapping you can reuse for every future file in this format.",
    )
    st.stop()

workdir = Path(tempfile.gettempdir()) / "leakguard_wizard"
workdir.mkdir(exist_ok=True)
path = workdir / Path(upload.name).name
path.write_bytes(upload.getvalue())

sheet = None
if path.suffix.lower() in (".xlsx", ".xlsm"):
    from leakguard.ingest.readers import list_sheets

    sheets = list_sheets(path)
    sheet = st.selectbox("Sheet", sheets) if len(sheets) > 1 else sheets[0]

mappings, _ = load_all_mappings(mappings_dir())
known = set().union(*(m.known_headers() for m in mappings if m.target == target)) or None
try:
    table = read_table(path, known_headers=known, sheet=sheet)
except UnsupportedFile as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:  # unreadable file
    st.error(f"Could not read this file: {exc}")
    st.stop()

if not table.header or not table.rows:
    st.error(
        "This file has no rows under its header. Check that you exported the detail report, not a summary."
    )
    st.stop()

st.markdown(
    f"Found <b>{len(table.rows):,}</b> rows and <b>{len(table.header)}</b> columns. Header is on row "
    f"<b>{table.first_row_number - 1}</b>"
    + (f", delimiter <code>{ui.esc(table.delimiter)}</code>" if table.delimiter else "")
    + (f", sheet <b>{ui.esc(table.sheet)}</b>" if table.sheet else "")
    + ".",
    unsafe_allow_html=True,
)

# Suggestions -----------------------------------------------------------------------
ui.section(
    "3. Check the column matches",
    "Suggested from header names and sample values. Change any that are wrong.",
)
suggestions = suggest(table, target, mappings)
options = ["Not in this file"] + [h for h in table.header if h.strip()]
chosen: dict[str, str | None] = {}
defaults: dict[str, object] = {}
with st.container(border=True):
    for sug in suggestions:
        f = spec.field(sug.field)
        a, b, d = st.columns([1.3, 1.6, 0.8], vertical_alignment="center")
        req = " <span class='lg-pill loss'>Required</span>" if f.required else ""
        a.markdown(
            f"<b>{ui.esc(f.description)}</b>{req}<br><span class='lg-cov-reason'>{ui.esc(f.name)}</span>",
            unsafe_allow_html=True,
        )
        default_ix = options.index(sug.column) if sug.column in options else 0
        pick = b.selectbox(
            f.description,
            options,
            index=default_ix,
            key=f"map_{target}_{f.name}",
            label_visibility="collapsed",
        )
        chosen[f.name] = None if pick == options[0] else pick
        if sug.column and pick == sug.column:
            tone = "gain" if sug.score >= 80 else "info" if sug.score >= 65 else "plain"
            d.markdown(ui.pill(f"{sug.score}% match", tone), unsafe_allow_html=True)
        elif pick != options[0]:
            d.markdown(ui.pill("Set by you", "info"), unsafe_allow_html=True)
    if "ota" in [f.name for f in spec.fields]:
        guess = "booking" if source == "booking_vcc" else "expedia"
        if not chosen.get("ota"):
            defaults["ota"] = st.selectbox(
                "OTA for every row in this file",
                list(ENUM_VALUES["ota"]),
                index=list(ENUM_VALUES["ota"]).index(guess),
            )
    if not chosen.get("currency"):
        defaults["currency"] = st.text_input(
            "Currency for every row (no currency column)", value="USD", max_chars=3
        ).upper()
    date_order = st.radio(
        "Dates like 03/04/2026 mean",
        ["Let LeakGuard decide", "Month first (US)", "Day first"],
        horizontal=True,
        help="LeakGuard decides from dates like 25/03/2026. Set this if every date is ambiguous.",
    )
order = {"Month first (US)": "mdy", "Day first": "dmy"}.get(date_order)

base = f"{source}_{Path(upload.name).stem}"
mapping = build_mapping(safe_mapping_name(base), source, target, chosen, defaults, sheet, order)
missing = mapping.missing_required(table.header)
if "guest_last_name" in missing:
    missing.remove("guest_last_name")
if missing:
    st.error(
        "Required fields not matched yet: "
        + ", ".join(spec.field(m).description for m in missing)
        + ". Pick the column for each, or check that this is the right kind of file."
    )
    st.stop()

# Preview ----------------------------------------------------------------------------
ui.section(
    "4. Preview 20 parsed rows", "Exactly what will be stored. Card numbers show last 4 only."
)
preview_table = type(table)(table.header, table.rows[:200], table.first_row_number, table.sheet)
result = apply_mapping(mapping, preview_table, path.name, c.settings.hash_guest_names)
rows = pd.DataFrame(result.rows[:20]).drop(columns=["_row_number"], errors="ignore")
for col in rows.columns:
    if col.endswith("_cents"):
        rows[col] = rows[col].map(lambda v: None if v is None or v != v else v / 100)
st.dataframe(
    rows,
    width="stretch",
    hide_index=True,
    column_config={
        col: st.column_config.NumberColumn(col.replace("_cents", ""), format="$%.2f")
        for col in rows.columns
        if col.endswith("_cents")
    },
)
bad = result.rows_rejected
st.markdown(
    f"Of the first {result.rows_read} rows, <b>{len(result.rows)}</b> parse cleanly and <b>{bad}</b> would be rejected.",
    unsafe_allow_html=True,
)
for w in result.warnings:
    st.warning(w)
errs = [i for i in result.issues if i.severity == "error"][:6]
if errs:
    st.markdown("**First problems found**")
    for i in errs:
        st.markdown(f"- Row {i.row_number}: {ui.esc(i.field or '')} {ui.esc(i.message)}")

# Save and import ---------------------------------------------------------------------
ui.section("5. Save the mapping and import")
with st.container(border=True):
    a, b = st.columns(2)
    name = safe_mapping_name(a.text_input("Mapping name", value=mapping.name))
    notes = b.text_input("Notes", value="Created with the mapping wizard from a real export.")
    target_path = mappings_dir() / f"{name}.yaml"
    overwrite = True
    if target_path.exists():
        overwrite = st.checkbox(f"Replace the existing mapping {name}.yaml")
    mapping.name, mapping.notes = name, notes
    with st.expander("See the YAML that will be saved"):
        st.code(mapping.to_yaml(), language="yaml")

    portfolios = load_portfolios(c.settings.data_mode)
    if not portfolios:
        st.warning(
            f"No {c.settings.data_mode} portfolio in config/portfolios.yaml yet. Add one to import this file."
        )
    p1, p2 = st.columns(2)
    pf = p1.selectbox("Portfolio", portfolios, format_func=lambda p: p.name) if portfolios else None
    prop = (
        p2.selectbox(
            "Property", pf.properties if pf else [], format_func=lambda p: f"{p.code}  {p.name}"
        )
        if pf
        else None
    )

    s1, s2 = st.columns(2)
    if s1.button("Save mapping", width="stretch", disabled=not overwrite or not name):
        target_path.write_text(mapping.to_yaml(), encoding="utf-8")
        st.success(
            f"Saved mappings/{name}.yaml. Every future {SOURCE_LABELS[source].lower()} file with these headers will use it."
        )
    if s2.button(
        "Save, import and match",
        type="primary",
        width="stretch",
        disabled=not overwrite or not name or prop is None,
    ):
        target_path.write_text(mapping.to_yaml(), encoding="utf-8")
        dest_dir = c.settings.raw_dir / pf.slug / prop.code / source
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        shutil.copy(path, dest)
        with st.spinner("Importing and matching..."), session_scope(c.settings) as ses:
            synced = sync_portfolios(ses, c.settings)
            portfolio, props = synced[pf.slug]
            found = DiscoveredFile(
                dest, dest.relative_to(c.settings.raw_dir).as_posix(), pf.slug, prop.code, source
            )
            report = import_file(
                ses,
                c.settings,
                found,
                portfolio,
                props[prop.code],
                [mapping],
                force_mapping=mapping,
            )
        run_matching(c.settings)
        context.bump()
        st.success(
            f"Imported {report.rows_imported:,} rows ({report.rows_rejected} rejected) into "
            f"{prop.code}. File saved to {found.relative_path}. Matching refreshed."
        )
