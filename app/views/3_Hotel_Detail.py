"""Hotel detail: one property's numbers, exceptions and data coverage."""

import streamlit as st

import context
import ui
from leakguard.db import session_scope
from leakguard.reporting import metrics as M
from leakguard.schemas import REQUIRED_SOURCES, SOURCE_LABELS

c = context.page_top(
    "Hotel detail",
    "One property at a time: what leaked, what is open, and how complete the data is.",
)
if not context.require_data(c):
    st.stop()

prop_ids = sorted(c.df_all["property_id"].unique(), key=lambda p: c.properties[p][0])
pid = st.selectbox(
    "Property",
    prop_ids,
    format_func=lambda p: f"{c.properties[p][0]}  {c.properties[p][1]}",
    key="hotel_pick",
)
code, name, company_id = c.properties[pid]
df_all = c.df_all[c.df_all["property_id"] == pid]
df = c.df[c.df["property_id"] == pid]
s = M.summary(df, df_all, c.settings)
st.markdown(
    f'<p class="lg-subtitle">Run by {ui.esc(c.companies[company_id])}</p>', unsafe_allow_html=True
)

ui.kpis(
    [
        ui.Kpi(
            "OTA-collect revenue", M.money(s.vcc_volume_cents), f"{s.vcc_count:,} virtual cards"
        ),
        ui.Kpi("Leaked", M.money(s.leaked_cents), f"{M.pct(s.leak_rate)} leak rate", "loss"),
        ui.Kpi(
            "Recovered",
            M.money(s.recovered_cents),
            f"{M.pct(s.recovery_rate)} recovery rate",
            "gain",
        ),
        ui.Kpi("Open at risk", M.money(s.open_at_risk_cents), f"{s.open_count:,} items"),
        ui.Kpi(
            f"Expiring in {c.settings.upcoming_expiry_days} days",
            f"{s.expiring_count}",
            M.money(s.expiring_cents),
            "loss" if s.expiring_count else "",
        ),
    ]
)

a, b = st.columns([1.4, 1], gap="large")
with a:
    ui.section("Monthly trend")
    ui.show(ui.trend_chart(M.monthly(df_all, c.start, c.end), height=290))
with b:
    ui.section("Data coverage", "Each required export and the dates it covers.")
    with session_scope(c.settings) as ses:
        files = M.source_files_frame(ses, c.portfolio_id)
        prop_objs = [p for p in M.properties(ses, c.portfolio_id) if p.id == pid]
    cov = M.coverage(files, prop_objs, c.as_of).get(code, {})
    rows = []
    for source in REQUIRED_SOURCES:
        cell = cov.get(source)
        mark = (
            '<span class="lg-cov ok">Covered</span>'
            if cell and cell.status == "ok"
            else '<span class="lg-cov gap">Gap</span>'
            if cell and cell.status == "gap"
            else '<span class="lg-cov gap">Missing</span>'
        )
        rows.append(
            f"<tr><td>{ui.esc(SOURCE_LABELS[source])}</td><td>{mark}"
            f'<span class="lg-cov-reason">{ui.esc(cell.reason if cell else "")}</span></td></tr>'
        )
    st.markdown(
        '<div class="lg-table-wrap"><table class="lg-table"><thead><tr><th>Source</th>'
        f"<th>Status</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )

ui.section(
    "Exceptions", "Open items first, by priority. Open one in the Exception queue to work it."
)
q = M.queue(df_all)
q = q.assign(_open=q["is_open"].astype(int)).sort_values(
    ["_open", "priority_score"], ascending=False
)
if q.empty:
    ui.empty_state("No exceptions", "Every virtual card at this property was charged correctly.")
else:
    view = q.head(25).copy()
    view["type_pill"] = [
        ui.type_pill(t, lbl)
        for t, lbl in zip(view["exception_type"], view["type_label"], strict=True)
    ]
    view["status_pill"] = [
        ui.status_pill(st_, lbl)
        for st_, lbl in zip(view["status"], view["status_label"], strict=True)
    ]
    ui.table(
        view,
        [
            ui.Col("type_pill", "Type", lambda v: v),
            ui.Col("status_pill", "Status", lambda v: v),
            ui.Col("ota_confirmation_no", "OTA confirmation"),
            ui.Col("guest_last_name", "Guest"),
            ui.Col("due_date", "Checkout", lambda v: ui.esc(M.fmt_date(v))),
            ui.Col(
                "days_to_expiry",
                "Days to expiry",
                lambda v: "-" if v is None or v != v else f"{int(v)}",
                num=True,
            ),
            ui.Col(
                "amount_at_risk_cents",
                "At risk",
                ui.fmt_money_cents,
                num=True,
                tone=lambda r: "loss",
            ),
            ui.Col("recovered_cents", "Recovered", ui.fmt_money_cents, num=True),
        ],
    )
    if len(q) > 25:
        st.markdown(
            f'<p class="lg-footnote">Showing 25 of {len(q):,}. The Exception queue has the full list.</p>',
            unsafe_allow_html=True,
        )
    if st.button("Open this property in the Exception queue", type="primary"):
        st.session_state["queue_property"] = int(pid)
        st.switch_page("views/4_Exception_Queue.py")
