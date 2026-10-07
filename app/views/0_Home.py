"""Home: what LeakGuard found, on one screen."""

import streamlit as st

import context
import ui
from leakguard.db import session_scope
from leakguard.reporting import metrics as M

c = context.page_top("What LeakGuard found", None)
if not context.require_data(c):
    st.stop()

s, df, df_all = c.summary, c.df, c.df_all
st.markdown(
    f'<p class="lg-subtitle">{M.fmt_date(c.start)} to {M.fmt_date(c.end)} · '
    f"{s.vcc_count:,} virtual cards worth {M.money(s.vcc_volume_cents)} checked against PMS "
    "and processor records.</p>",
    unsafe_allow_html=True,
)

ui.kpis(
    [
        ui.Kpi(
            "Leaked, last 12 months" if (c.end - c.start).days > 330 else "Leaked in period",
            M.money(s.leaked_cents),
            f"{M.pct(s.leak_rate)} of OTA-collect revenue",
            "loss",
        ),
        ui.Kpi(
            "Recovered to date",
            M.money(s.recovered_cents),
            f"{M.pct(s.recovery_rate)} of leaked dollars",
            "gain",
        ),
        ui.Kpi(
            "Open dollars at risk", M.money(s.open_at_risk_cents), f"{s.open_count:,} open items"
        ),
        ui.Kpi(
            f"Cards expiring in {c.settings.upcoming_expiry_days} days",
            f"{s.expiring_count:,}",
            f"{M.money(s.expiring_cents)} not yet charged"
            if s.expiring_count
            else "Nothing expiring soon",
            "loss" if s.expiring_count else "",
        ),
    ]
)

card = M.scorecard(df)
takeaway = M.operator_takeaway(card)
if takeaway:
    ui.callout(f"<strong>{ui.esc(takeaway)}</strong>", "loss" if len(card) > 1 else "")

left, right = st.columns([1.35, 1], gap="large")

with left:
    ui.section(
        "Top 5 actions this week",
        "Highest priority open items: biggest dollars, closest to expiry.",
    )
    top = M.top_actions(df_all)
    if top.empty:
        ui.empty_state(
            "Nothing open",
            "Every flagged card has been worked. New items appear after the next import.",
        )
    for _, r in top.iterrows():
        with st.container(border=True):
            a, b = st.columns([4, 1.2], vertical_alignment="center")
            days = r["days_to_expiry"]
            if days is None or days != days:
                expiry = "No expiry date"
            elif days < 0:
                expiry = f"Expired {abs(int(days))} days ago"
            elif days == 0:
                expiry = "Expires today"
            else:
                expiry = f"Expires in {int(days)} day{'s' if days != 1 else ''}"
            a.markdown(
                f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
                f'<b style="font-size:1.2rem;">{M.money(r["amount_at_risk_cents"])}</b>'
                f"{ui.type_pill(r['exception_type'], r['type_label'])}"
                f"<span>{ui.esc(r['property_code'])} · {ui.esc(r['property_name'])}</span></div>"
                f'<div style="color:{ui.TEXT_2};font-size:14px;margin-top:4px;">'
                f"{ui.esc(r['ota'].title())} {ui.esc(r['ota_confirmation_no'])} · {expiry} · "
                f"{ui.esc(r['status_label'])}</div>",
                unsafe_allow_html=True,
            )
            if b.button("Open", key=f"open_{r['id']}", width="stretch"):
                st.session_state["queue_focus"] = int(r["id"])
                st.switch_page("views/4_Exception_Queue.py")

with right:
    ui.section("Leaked vs recovered by month")
    ui.show(ui.trend_chart(M.monthly(df_all, c.start, c.end), height=280))

    ui.section("What to do next")
    with session_scope(c.settings) as ses:
        files = M.source_files_frame(ses, c.portfolio_id)
        props = M.properties(ses, c.portfolio_id)
    gaps = M.coverage_gap_count(M.coverage(files, props, c.as_of))
    steps = M.next_steps(s, df_all, card, gaps, c.settings.upcoming_expiry_days)
    if steps:
        st.markdown(
            '<ol class="lg-steps">' + "".join(f"<li>{ui.esc(x)}</li>" for x in steps) + "</ol>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("Nothing urgent. Keep importing new exports each week.")

st.markdown(
    '<p class="lg-footnote">Leaked means money on virtual cards that was not collected, collected '
    "wrong, or needs review. Every item needs a person to confirm it before money moves. Recovery "
    "is not guaranteed, especially on expired cards.</p>",
    unsafe_allow_html=True,
)
