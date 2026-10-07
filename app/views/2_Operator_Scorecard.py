"""Operator scorecard: management companies ranked by leak rate."""

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import context
import ui
from leakguard.reporting import metrics as M

c = context.page_top(
    "Operator scorecard",
    "Management companies ranked by leak rate: leaked dollars per dollar of OTA-collect revenue.",
)
if not context.require_data(c):
    st.stop()

card = M.scorecard(c.df)
if card.empty:
    ui.empty_state(
        "No operators to compare", "No virtual cards in this period for the selected filters."
    )
    st.stop()

ui.callout(f"<strong>{ui.esc(M.operator_takeaway(card))}</strong>", "loss" if len(card) > 1 else "")


def days(v: float) -> str:
    return "-" if v != v else f"{v:.0f} days"


cols = (
    st.columns(len(card), gap="medium")
    if len(card) <= 3
    else [st.container() for _ in range(len(card))]
)
for col, (_, r) in zip(cols, card.iterrows(), strict=True):
    with col:
        worst = r["rank"] == 1 and len(card) > 1
        st.markdown(
            f'<div class="lg-card"><div class="lg-rank"><div class="lg-rank-num">#{int(r["rank"])}</div>'
            f'<div><div class="lg-rank-name">{ui.esc(r["company"])}</div>'
            f'<div style="color:{ui.TEXT_2};font-size:14px;">{int(r["properties"])} properties · '
            f"{M.money(r['vcc_volume_cents'])} OTA-collect revenue</div>"
            f'<div class="lg-rank-metrics">'
            f'<div>Leak rate<b class="{"loss" if worst else ""}">{M.pct(r["leak_rate"])}</b></div>'
            f"<div>Leaked<b>{M.money(r['leaked_cents'])}</b></div>"
            f"<div>Open at risk<b>{M.money(r['open_cents'])}</b></div>"
            f"<div>Expired-card share<b>{M.pct(r['expired_share'])}</b></div>"
            f"<div>Days to resolve<b>{days(r['avg_days_to_resolve'])}</b></div>"
            f"<div>Recovery rate<b>{M.pct(r['recovery_rate'])}</b></div>"
            "</div></div></div></div>",
            unsafe_allow_html=True,
        )

ui.section("Side by side", "Each panel has its own scale. Lower is better on every measure.")
names = list(card["company"])
# Short names fit under each mini chart; first words are unique for typical operator lists.
short = [n.split()[0] for n in names]
if len(set(short)) < len(short):
    short = names
colors = [ui.ACCENT if i == 0 else ui.ACCENT_LIGHT for i in range(len(names))]
panels = [
    ("Leak rate", [v * 100 for v in card["leak_rate"]], [M.pct(v) for v in card["leak_rate"]]),
    (
        "Expired-card share",
        [v * 100 for v in card["expired_share"]],
        [M.pct(v) for v in card["expired_share"]],
    ),
    (
        "Avg days to resolve",
        [0 if v != v else v for v in card["avg_days_to_resolve"]],
        [days(v) for v in card["avg_days_to_resolve"]],
    ),
    (
        "Open at risk",
        [v / 100 for v in card["open_cents"]],
        [M.money_short(v) for v in card["open_cents"]],
    ),
]
fig = make_subplots(rows=1, cols=4, subplot_titles=[p[0] for p in panels], horizontal_spacing=0.06)
for i, (title, values, texts) in enumerate(panels, start=1):
    fig.add_trace(
        go.Bar(
            x=short,
            y=values,
            marker_color=colors,
            text=texts,
            textposition="outside",
            textfont=dict(color=ui.TEXT_2, size=13),
            cliponaxis=False,
            showlegend=False,
            hovertemplate="%{x}<br>" + title + ": %{text}<extra></extra>",
        ),
        row=1,
        col=i,
    )
ui.style(fig, height=330, legend=False, money_axis=None)
fig.update_yaxes(showticklabels=False, showgrid=False)
fig.update_xaxes(tickfont=dict(size=13), tickangle=0)
fig.update_annotations(font=dict(size=14, color=ui.TEXT, family=ui.FONT))
fig.update_layout(margin=dict(l=8, r=8, t=40, b=8))
ui.show(fig)

ui.section("Scorecard")
ui.table(
    card,
    [
        ui.Col("rank", "Rank", lambda v: f"<b>#{int(v)}</b>"),
        ui.Col("company", "Management company", lambda v: f"<b>{ui.esc(v)}</b>"),
        ui.Col("properties", "Properties", ui.fmt_int, num=True),
        ui.Col("vcc_volume_cents", "OTA-collect revenue", ui.fmt_money, num=True),
        ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True, tone=lambda r: "loss"),
        ui.Col("leak_rate", "Leak rate", ui.fmt_pct, num=True),
        ui.Col("expired_share", "Expired-card share", ui.fmt_pct, num=True),
        ui.Col("avg_days_to_resolve", "Avg days to resolve", lambda v: ui.esc(days(v)), num=True),
        ui.Col("open_cents", "Open at risk", ui.fmt_money, num=True),
    ],
)
st.markdown(
    '<p class="lg-footnote">Days to resolve counts from guest checkout to the date the money was '
    "recovered. Expired-card share is the part of leaked dollars sitting on cards that expired "
    "uncharged, the hardest money to get back.</p>",
    unsafe_allow_html=True,
)

ui.section("Drill down", "Pick an operator to see its properties.")
choice = st.selectbox("Management company", names, label_visibility="collapsed")
company_df = c.df[c.df["company"] == choice]
props = M.by_property(company_df)
a, b = st.columns([1, 1.2], gap="large")
with a:
    ui.show(
        ui.hbar(
            [f"{r.property_code} {r.property_name}" for r in props.itertuples()],
            [round(r.leak_rate * 1_000_000) for r in props.itertuples()],
            ui.LOSS,
            value_text=[M.pct(r.leak_rate) for r in props.itertuples()],
            hover_extra=[f"<br>Leaked {M.money(r.leaked_cents)}" for r in props.itertuples()],
        ).update_traces(hovertemplate="%{y}<br>Leak rate %{text}%{customdata[1]}<extra></extra>")
    )
with b:
    ui.table(
        props.sort_values("leak_rate", ascending=False),
        [
            ui.Col("property_code", "Property", lambda v: f"<b>{ui.esc(v)}</b>"),
            ui.Col("room_count", "Rooms", ui.fmt_int, num=True),
            ui.Col("leaked_cents", "Leaked", ui.fmt_money, num=True, tone=lambda r: "loss"),
            ui.Col("leak_rate", "Leak rate", ui.fmt_pct, num=True),
            ui.Col("expired_share", "Expired share", ui.fmt_pct, num=True),
            ui.Col("open_cents", "Open", ui.fmt_money, num=True),
        ],
    )
