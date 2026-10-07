"""Data sources: what was imported, coverage gaps, import issues, re-run."""

import streamlit as st

import context
import ui
from leakguard.db import session_scope
from leakguard.ingest.importer import import_all
from leakguard.matching.engine import run_matching
from leakguard.reporting import metrics as M
from leakguard.schemas import REQUIRED_SOURCES, SOURCE_LABELS

c = context.ctx()
ui.topbar(c.portfolio_name, c.settings.data_mode)
ui.page_header(
    "Data sources",
    "Every export LeakGuard has read, how complete each property's data is, and any rows it could not use.",
)

folder = c.settings.raw_dir
st.markdown(
    f'<p>Reading <span class="lg-code">{ui.esc(folder)}</span>. Files go in '
    '<span class="lg-code">&lt;portfolio&gt;/&lt;property code&gt;/&lt;source&gt;/</span>.</p>',
    unsafe_allow_html=True,
)

if st.button("Re-run import and matching", type="primary"):
    with st.status("Importing and matching...", expanded=True) as status:
        st.write("Importing files. Unchanged files are skipped.")
        summary = import_all(c.settings)
        changed = [r for r in summary.reports if r.status in ("imported", "replaced")]
        failed = [r for r in summary.reports if r.status == "failed"]
        st.write(
            f"{len(changed)} new or changed files, {summary.rows_imported:,} rows. {len(failed)} failed."
        )
        for p in summary.problems:
            st.write(p)
        st.write("Matching virtual cards.")
        match = run_matching(c.settings)
        st.write(
            f"{match.cards:,} cards checked. {match.new_exceptions} new exceptions, "
            f"{match.auto_resolved} auto-resolved."
        )
        status.update(label="Import and matching finished", state="complete")
    context.bump()
    st.button("Refresh the page", on_click=lambda: None)

if not c.has_data:
    ui.no_data_state()
    st.stop()

with session_scope(c.settings) as ses:
    files = M.source_files_frame(ses, c.portfolio_id)
    props = M.properties(ses, c.portfolio_id)
    issues = M.import_issues_frame(ses, c.portfolio_id)
cov = M.coverage(files, props, c.as_of)
gaps = M.coverage_gap_count(cov)

imported = files[files["status"] == "imported"] if not files.empty else files
ui.kpis(
    [
        ui.Kpi(
            "Files imported",
            f"{len(imported):,}",
            f"{len(files) - len(imported)} skipped or failed",
        ),
        ui.Kpi(
            "Rows imported",
            f"{int(imported['rows_imported'].sum()):,}" if not imported.empty else "0",
        ),
        ui.Kpi(
            "Rows rejected",
            f"{int(imported['rows_rejected'].sum()):,}" if not imported.empty else "0",
            "Listed under import issues below",
        ),
        ui.Kpi(
            "Coverage gaps",
            f"{gaps}",
            "Missing or short exports" if gaps else "All sources present",
            "loss" if gaps else "gain",
        ),
    ]
)

ui.section("Coverage by property", "Each property needs all five exports for the same dates.")
head = "".join(f"<th>{ui.esc(SOURCE_LABELS[s])}</th>" for s in REQUIRED_SOURCES)
rows = []
for p in props:
    cells = []
    for s in REQUIRED_SOURCES:
        cell = cov[p.code][s]
        if cell.status == "ok":
            mark = '<span class="lg-cov ok">&#10003; Covered</span>'
        elif cell.status == "gap":
            mark = '<span class="lg-cov gap">Gap</span>'
        else:
            mark = '<span class="lg-cov gap">Missing</span>'
        cells.append(f'<td>{mark}<span class="lg-cov-reason">{ui.esc(cell.reason)}</span></td>')
    rows.append(
        f"<tr><td><b>{ui.esc(p.code)}</b><br><span class='lg-cov-reason'>{ui.esc(p.name)}</span></td>{''.join(cells)}</tr>"
    )
st.markdown(
    f'<div class="lg-table-wrap"><table class="lg-table"><thead><tr><th>Property</th>{head}</tr></thead>'
    f"<tbody>{''.join(rows)}</tbody></table></div>",
    unsafe_allow_html=True,
)
if gaps:
    st.markdown(
        '<p class="lg-footnote">Cards in a gap were not checked. Ask the property for the missing '
        "export or a longer date range. docs/DATA_REQUEST.md lists exactly what to ask for.</p>",
        unsafe_allow_html=True,
    )

pdfs = (
    files[(files["status"] == "skipped") & files["file_name"].str.lower().str.endswith(".pdf")]
    if not files.empty
    else files
)
if not pdfs.empty:
    ui.callout(
        f"<strong>{len(pdfs)} PDF file(s) found.</strong> PDFs cannot be read in this version. "
        "Ask the hotel for the same reports exported as CSV or Excel.",
        "loss",
    )

ui.section("Imported files")
view = files.copy()
view["status_pill"] = [
    ui.pill(s.title(), "gain" if s == "imported" else "loss" if s == "failed" else "plain")
    for s in view["status"]
]
view["dates"] = [
    f"{M.fmt_date(a)} to {M.fmt_date(b)}" if a else "-"
    for a, b in zip(view["date_min"], view["date_max"], strict=True)
]
view["note"] = view["message"].fillna("")
ui.table(
    view,
    [
        ui.Col("property_code", "Property", lambda v: f"<b>{ui.esc(v)}</b>"),
        ui.Col("source", "Source"),
        ui.Col("file_name", "File"),
        ui.Col("status_pill", "Status", lambda v: v),
        ui.Col("mapping_name", "Mapping", lambda v: ui.esc(v or "-")),
        ui.Col("rows_read", "Read", ui.fmt_int, num=True),
        ui.Col("rows_imported", "Imported", ui.fmt_int, num=True),
        ui.Col(
            "rows_rejected",
            "Rejected",
            ui.fmt_int,
            num=True,
            tone=lambda r: "loss" if r["rows_rejected"] else "",
        ),
        ui.Col("dates", "Dates covered"),
        ui.Col("note", "Notes", lambda v: ui.esc(v)),
    ],
)

ui.section("Import issues", "Rows that were rejected and warnings worth knowing about.")
if issues.empty:
    ui.empty_state("No import issues", "Every row in every file was read cleanly.")
else:
    grouped = (
        issues.groupby(["severity", "file_name", "field", "message"], dropna=False)
        .agg(
            rows=(
                "row_number",
                lambda r: (
                    ", ".join(str(int(x)) for x in list(r.dropna())[:8])
                    + (" ..." if r.notna().sum() > 8 else "")
                ),
            ),
            count=("message", "size"),
        )
        .reset_index()
        .sort_values(["severity", "count"], ascending=[True, False])
    )
    grouped["sev"] = [
        ui.pill("Rejected" if s == "error" else "Warning", "loss" if s == "error" else "info")
        for s in grouped["severity"]
    ]
    ui.table(
        grouped,
        [
            ui.Col("sev", "Type", lambda v: v),
            ui.Col("file_name", "File"),
            ui.Col("field", "Field", lambda v: ui.esc(v if isinstance(v, str) else "-")),
            ui.Col("message", "Message"),
            ui.Col("count", "Rows", ui.fmt_int, num=True),
            ui.Col("rows", "Row numbers", lambda v: ui.esc(v or "-")),
        ],
    )
