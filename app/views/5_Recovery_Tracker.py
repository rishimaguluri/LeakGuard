"""Recovery tracker: what has come back, when, and what is still in the pipeline."""

import plotly.graph_objects as go
import streamlit as st

import context
import ui
from leakguard.reporting import metrics as M
from leakguard.schemas import TYPE_LABELS

c = context.page_top("Recovery tracker", "Cash recovered so far, our fee, and the open pipeline.")
if not context.require_data(c):
    st.stop()

s, df = c.summary, c.df
r = M.recovery_stats(s, df)
ui.kpis(
    [
        ui.Kpi(
            "Recovered",
            M.money(r.recovered_cents),
            f"{r.recovered_count:,} items closed as recovered",
            "gain",
        ),
        ui.Kpi(
            f"LeakGuard fee at {c.settings.fee_pct * 100:.0f}%",
            M.money(r.fee_cents),
            f"Owner keeps {M.money(r.owner_net_cents)}",
        ),
        ui.Kpi(
            "Open pipeline",
            M.money(r.open_pipeline_cents),
            f"About {M.money(r.pipeline_estimate_cents)} likely recoverable (estimate)",
        ),
        ui.Kpi(
            "Recovery rate",
            M.pct(r.recovery_rate),
            f"Median {r.median_days:.0f} days from checkout"
            if r.median_days is not None
            else "No recoveries yet",
        ),
    ]
)

if r.recovered_cents == 0:
    ui.empty_state(
        "Nothing recovered yet",
        "Work items in the Exception queue and record each recovery. Progress shows up here.",
    )
    st.stop()

ui.section("Recovered by month", "By the month the money came back.")
monthly = M.monthly(c.df_all, c.start, c.end)
fig = go.Figure(
    go.Bar(
        x=monthly["month_label"],
        y=monthly["recovered_cents"] / 100,
        marker_color=ui.GAIN,
        customdata=[M.money(v) for v in monthly["recovered_cents"]],
        hovertemplate="Recovered %{customdata}<extra></extra>",
    )
)
cumulative = monthly["recovered_cents"].cumsum()
fig.add_scatter(
    x=monthly["month_label"],
    y=cumulative / 100,
    name="Cumulative",
    mode="lines+markers",
    line=dict(color=ui.ACCENT, width=2),
    marker=dict(size=8, symbol="circle", line=dict(color=ui.CARD, width=2)),
    customdata=[M.money(v) for v in cumulative],
    hovertemplate="To date %{customdata}<extra></extra>",
)
fig.data[0].name = "Recovered in month"
ui.show(ui.month_axis(ui.style(fig, 300), monthly))

a, b = st.columns(2, gap="large")
with a:
    ui.section("By exception type")
    by_t = M.recovered_breakdown(df, "exception_type")
    ui.show(
        ui.hbar(
            [TYPE_LABELS[t] for t in by_t["exception_type"]],
            list(by_t["recovered_cents"]),
            ui.GAIN,
            hover_extra=[f"<br>{n:,} items" for n in by_t["count"]],
        )
    )
with b:
    ui.section("By property")
    by_p = M.recovered_breakdown(df, "property_code")
    ui.show(
        ui.hbar(
            list(by_p["property_code"]),
            list(by_p["recovered_cents"]),
            ui.GAIN,
            hover_extra=[f"<br>{n:,} items" for n in by_p["count"]],
        )
    )

ui.section("Latest recoveries")
recent = df[df["recovered_cents"] > 0].sort_values("recovered_date", ascending=False).head(15)
recent = recent.assign(
    type_pill=[ui.type_pill(t, TYPE_LABELS[t]) for t in recent["exception_type"]]
)
ui.table(
    recent,
    [
        ui.Col("recovered_date", "Recovered", lambda v: ui.esc(M.fmt_date(v))),
        ui.Col("property_code", "Property", lambda v: f"<b>{ui.esc(v)}</b>"),
        ui.Col("type_pill", "Type", lambda v: v),
        ui.Col("ota_confirmation_no", "OTA confirmation"),
        ui.Col("assigned_to", "Assigned to", lambda v: ui.esc(v if isinstance(v, str) else "-")),
        ui.Col("recovered_cents", "Amount", ui.fmt_money_cents, num=True, tone=lambda row: "gain"),
    ],
)

ui.section("Written off")
wo = df[df["status"] == "written_off"]
st.markdown(
    f"{len(wo):,} items worth {M.money(int(wo['amount_at_risk_cents'].sum()) if not wo.empty else 0)} "
    "were written off, mostly expired cards the OTA would not pay. These are not counted as recovered."
)
