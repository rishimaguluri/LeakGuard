"""Audit report: preview the owner-facing summary and download Excel and PDF."""

import streamlit as st

import context
import ui
from leakguard.db import session_scope
from leakguard.reporting import metrics as M
from leakguard.reporting.excel_report import build_workbook
from leakguard.reporting.pdf_report import build_pdf
from leakguard.reporting.report_data import build_report

c = context.page_top(
    "Audit report",
    "The document you hand the owner: a branded PDF summary and an Excel workbook with every line item.",
)
if not context.require_data(c):
    st.stop()

with st.container(border=True):
    a, b, d = st.columns([1.2, 1.4, 1])
    a.markdown(f"**Portfolio**<br>{ui.esc(c.portfolio_name)}", unsafe_allow_html=True)
    period = b.date_input(
        "Period (stay dates)",
        value=(c.start, c.end),
        max_value=c.as_of,
        format="MM/DD/YYYY",
        key="report_period",
    )
    start, end = period if isinstance(period, tuple) and len(period) == 2 else (c.start, c.end)
    d.markdown(
        f"**Covers**<br>All properties and operators in {ui.esc(c.portfolio_name)}",
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="Building report...")
def report_bytes(db_stamp: float, version: int, portfolio_id: int, start, end) -> tuple:  # noqa: ANN001
    with session_scope(c.settings) as ses:
        data = build_report(ses, c.settings, portfolio_id, start, end)
    return data, build_workbook(data), build_pdf(data)


stamp = c.settings.db_path.stat().st_mtime
data, xlsx, pdf = report_bytes(stamp, context.data_version(), c.portfolio_id, start, end)
stem = f"leakguard_audit_{data.slug}_{start:%Y%m%d}_{end:%Y%m%d}"
x1, x2, _ = st.columns([1, 1, 2])
x1.download_button(
    "Download PDF summary",
    pdf,
    f"{stem}.pdf",
    "application/pdf",
    type="primary",
    width="stretch",
)
x2.download_button(
    "Download Excel workbook",
    xlsx,
    f"{stem}.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

s, n = data.summary, data.noi
ui.section("Preview", f"{M.fmt_date(start)} to {M.fmt_date(end)}. Same numbers as the downloads.")
ui.kpis(
    [
        ui.Kpi(
            "Virtual cards reviewed",
            f"{s.vcc_count:,}",
            f"{M.money(s.vcc_volume_cents)} OTA-collect revenue",
        ),
        ui.Kpi(
            "Dollars leaked", M.money(s.leaked_cents), f"{M.pct(s.leak_rate)} leak rate", "loss"
        ),
        ui.Kpi(
            "Recoverable estimate",
            M.money(s.recoverable_estimate_cents),
            "Estimate, not guaranteed",
        ),
        ui.Kpi(
            f"Fee at {data.fee_pct * 100:.0f}% / owner net",
            f"{M.money(s.fee_cents)} / {M.money(s.owner_net_cents)}",
            "On cash recovered so far",
        ),
        ui.Kpi(
            "NOI impact, annualized",
            M.money(n.annualized_owner_net_cents),
            "Owner net after fee",
            "gain",
        ),
    ]
)
if data.takeaway:
    ui.callout(f"<strong>{ui.esc(data.takeaway)}</strong>", "loss")

col_left, col_right = st.columns(2, gap="large")
with col_left:
    ui.section("Dollars leaked by type")
    ui.table(
        data.by_type,
        [
            ui.Col("label", "Exception type"),
            ui.Col("count", "Items", ui.fmt_int, num=True),
            ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True, tone=lambda x: "loss"),
            ui.Col("recovered_cents", "Recovered", ui.fmt_money, num=True, tone=lambda x: "gain"),
        ],
        total={
            "label": "<b>Total</b>",
            "count": ui.fmt_int(s.exception_count),
            "leaked_cents": ui.fmt_money(s.leaked_cents),
            "recovered_cents": ui.fmt_money(s.recovered_cents),
        },
    )
with col_right:
    ui.section("By management company")
    ui.table(
        data.scorecard,
        [
            ui.Col("rank", "Rank", lambda v: f"#{int(v)}"),
            ui.Col("company", "Operator"),
            ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True),
            ui.Col("leak_rate", "Leak rate", ui.fmt_pct, num=True),
            ui.Col("open_cents", "Open", ui.fmt_money, num=True),
        ],
    )

ui.section("By property")
ui.table(
    data.by_property,
    [
        ui.Col("property_code", "Property", lambda v: f"<b>{ui.esc(v)}</b>"),
        ui.Col("property_name", "Name"),
        ui.Col("vcc_volume_cents", "OTA-collect revenue", ui.fmt_money, num=True),
        ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True),
        ui.Col("leak_rate", "Leak rate", ui.fmt_pct, num=True),
        ui.Col("recovered_cents", "Recovered", ui.fmt_money, num=True),
        ui.Col("open_cents", "Open", ui.fmt_money, num=True),
    ],
)

ui.section(
    "Method and limits",
    "Included on the last page of the PDF and the Method sheet of the workbook.",
)
for title, body in data.method:
    with st.expander(title):
        st.markdown(body)
st.markdown(
    f'<p class="lg-footnote">The workbook has {len(data.line_items):,} line items with the evidence for each '
    "flagged card, sorted by priority.</p>",
    unsafe_allow_html=True,
)
