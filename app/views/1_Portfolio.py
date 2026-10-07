"""Portfolio: leakage by property, by type, over time, and NOI impact."""

import streamlit as st

import context
import ui
from leakguard.reporting import metrics as M

c = context.page_top(
    "Portfolio", "Where the money leaks, how much has come back, and what it means for NOI."
)
if not context.require_data(c):
    st.stop()

s, df = c.summary, c.df
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
    ]
)

props = M.by_property(df)
types = M.by_type(df)

a, b = st.columns(2, gap="large")
with a:
    ui.section("Leakage by property")
    if props.empty:
        ui.empty_state("No leakage found", "No exceptions in this period.")
    else:
        ui.show(
            ui.hbar(
                [f"{r.property_code} {r.property_name}" for r in props.itertuples()],
                list(props["leaked_cents"]),
                ui.LOSS,
                hover_extra=[f"<br>Leak rate {M.pct(r.leak_rate)}" for r in props.itertuples()],
            )
        )
with b:
    ui.section("Leakage by exception type")
    if not types.empty:
        ui.show(
            ui.hbar(
                list(types["label"]),
                list(types["leaked_cents"]),
                ui.ACCENT,
                hover_extra=[f"<br>{n:,} items" for n in types["count"]],
            )
        )

ui.section("Monthly trend", "Leaked by stay month, recovered by the month the money came back.")
ui.show(ui.line_chart(M.monthly(c.df_all, c.start, c.end)))

ui.section("Properties")
if not props.empty:
    total = {
        "property_code": "<b>Portfolio</b>",
        "room_count": ui.fmt_int(props["room_count"].sum()),
        "vcc_volume_cents": ui.fmt_money(s.vcc_volume_cents),
        "leaked_cents": ui.fmt_money(s.leaked_cents),
        "leak_rate": ui.fmt_pct(s.leak_rate),
        "recovered_cents": ui.fmt_money(s.recovered_cents),
        "open_cents": ui.fmt_money(s.open_at_risk_cents),
        "recovery_rate": ui.fmt_pct(s.recovery_rate),
    }
    ui.table(
        props,
        [
            ui.Col("property_code", "Property", lambda v: f"<b>{ui.esc(v)}</b>"),
            ui.Col("property_name", "Name"),
            ui.Col("company", "Operator"),
            ui.Col("room_count", "Rooms", ui.fmt_int, num=True),
            ui.Col("vcc_volume_cents", "OTA-collect revenue", ui.fmt_money, num=True),
            ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True, tone=lambda r: "loss"),
            ui.Col("leak_rate", "Leak rate", ui.fmt_pct, num=True),
            ui.Col("recovered_cents", "Recovered", ui.fmt_money, num=True, tone=lambda r: "gain"),
            ui.Col("open_cents", "Open", ui.fmt_money, num=True),
            ui.Col("recovery_rate", "Recovery rate", ui.fmt_pct, num=True),
        ],
        total=total,
    )

ui.section("NOI impact", "Recovered cash expressed the way lenders and asset managers read it.")
noi = M.noi_impact(s, c.start, c.end, c.settings)
ui.kpis(
    [
        ui.Kpi(
            "Recovered cash, annualized",
            M.money(noi.annualized_recovered_cents),
            f"From {M.money(noi.recovered_cents)} over {noi.period_days} days",
            "gain",
        ),
        ui.Kpi(
            f"LeakGuard fee at {noi.fee_pct * 100:.0f}%",
            M.money(noi.annualized_fee_cents),
            "Annualized",
        ),
        ui.Kpi(
            "Owner net gain to NOI",
            M.money(noi.annualized_owner_net_cents),
            "Annualized, after fee",
            "gain",
        ),
        ui.Kpi(
            f"Implied value at {noi.cap_rate * 100:.1f}% cap rate",
            M.money(noi.implied_value_cents),
            "Estimate, for illustration",
        ),
    ]
)
st.markdown(
    f'<p class="lg-footnote">If every leaked dollar in this period were collected, annualized leakage is '
    f"{M.money(noi.annualized_leak_cents)}. The current recoverable estimate is "
    f"{M.money(s.recoverable_estimate_cents)}, based on typical recovery odds by exception type. "
    "Estimates are not guarantees.</p>",
    unsafe_allow_html=True,
)
