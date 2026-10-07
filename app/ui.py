"""Shared look and feel: CSS, cards, pills, tables and chart styling.

Pages use these helpers so every screen looks the same. Numbers are
computed in leakguard.reporting.metrics; this module only formats them.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from leakguard.reporting.metrics import money, money_short

BG = "#EDF2F8"
CARD = "#F8FAFD"
LINE = "#BFD0E6"
TEXT = "#0E2240"
TEXT_2 = "#25406A"
ACCENT = "#1A3766"
ACCENT_LIGHT = "#6E8FC4"
LOSS = "#8C2B45"
GAIN = "#1F5A3D"
GRID = "#E3EBF5"
FONT = "Lato, Helvetica Neue, Arial, sans-serif"

LOSS_TYPES = {"UNCHARGED", "EXPIRED_UNCHARGED", "UNDERCHARGED", "CHARGED_NOT_SETTLED"}
REVIEW_TYPES = {"OVERCHARGED", "NO_PMS_MATCH", "CANCELLED_REVIEW", "DUPLICATE_VCC"}
STATUS_TONE = {
    "open": "loss",
    "assigned": "info",
    "in_progress": "info",
    "recovered": "gain",
    "written_off": "plain",
    "not_an_issue": "plain",
}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def inject_css() -> None:
    css = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# Building blocks -------------------------------------------------------------------


def topbar(portfolio_name: str | None, mode: str) -> None:
    badge = (
        '<span class="lg-badge demo">Demo data</span>'
        if mode == "demo"
        else f'<span class="lg-badge live">Live data: {esc(portfolio_name)}</span>'
    )
    name = (
        f'<span style="color:{TEXT_2};font-weight:700;">{esc(portfolio_name)}</span>'
        if portfolio_name
        else ""
    )
    st.markdown(
        f'<div class="lg-topbar"><div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;">'
        f'<span class="lg-wordmark">Leak<span>Guard</span></span>{name}</div>{badge}</div>',
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<h1 class="lg-page-title">{esc(title)}</h1>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<p class="lg-subtitle">{esc(subtitle)}</p>', unsafe_allow_html=True)


def section(title: str, caption: str | None = None) -> None:
    cap = f"<p>{esc(caption)}</p>" if caption else ""
    st.markdown(f'<div class="lg-section"><h3>{esc(title)}</h3>{cap}</div>', unsafe_allow_html=True)


@dataclass
class Kpi:
    label: str
    value: str
    note: str = ""
    tone: str = ""  # "", "loss", "gain"


def kpis(items: Iterable[Kpi]) -> None:
    cards = "".join(
        f'<div class="lg-card"><div class="lg-kpi-label">{esc(k.label)}</div>'
        f'<div class="lg-kpi-value {k.tone}">{esc(k.value)}</div>'
        + (f'<div class="lg-kpi-note">{esc(k.note)}</div>' if k.note else "")
        + "</div>"
        for k in items
    )
    st.markdown(f'<div class="lg-kpis">{cards}</div>', unsafe_allow_html=True)


def callout(text: str, tone: str = "") -> None:
    st.markdown(f'<div class="lg-callout {tone}">{text}</div>', unsafe_allow_html=True)


def pill(text: str, tone: str = "info") -> str:
    return f'<span class="lg-pill {tone}">{esc(text)}</span>'


def type_pill(exception_type: str, label: str) -> str:
    tone = (
        "loss"
        if exception_type in LOSS_TYPES
        else "info"
        if exception_type in REVIEW_TYPES
        else "gain"
    )
    return pill(label, tone)


def status_pill(status: str, label: str) -> str:
    return pill(label, STATUS_TONE.get(status, "plain"))


def empty_state(title: str, body: str, command: str | None = None) -> None:
    cmd = f"<p><code>{esc(command)}</code></p>" if command else ""
    st.markdown(
        f'<div class="lg-empty"><h3>{esc(title)}</h3><p>{esc(body)}</p>{cmd}</div>',
        unsafe_allow_html=True,
    )


def no_data_state() -> None:
    empty_state(
        "No data yet",
        "Build the demo portfolio, or add hotel exports to data/raw/ and import them.",
        "python -m leakguard demo",
    )


def kv(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in pairs)
    return f'<dl class="lg-kv">{rows}</dl>'


@dataclass
class Col:
    key: str
    label: str
    fmt: Callable[[object], str] | None = None  # returns escaped html
    num: bool = False
    tone: Callable[[pd.Series], str] | None = None


def table(
    df: pd.DataFrame,
    cols: list[Col],
    total: dict[str, str] | None = None,
    max_rows: int | None = None,
) -> None:
    """Render a styled HTML table. fmt functions must return safe HTML."""
    head = "".join(f'<th class="{"num" if c.num else ""}">{esc(c.label)}</th>' for c in cols)
    body = []
    rows = df if max_rows is None else df.head(max_rows)
    for _, r in rows.iterrows():
        cells = []
        for c in cols:
            value = r.get(c.key)
            text = c.fmt(value) if c.fmt else esc(value)
            classes = " ".join(
                x for x in ["num" if c.num else "", c.tone(r) if c.tone else ""] if x
            )
            cells.append(f'<td class="{classes}">{text}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    if total:
        cells = "".join(
            f'<td class="{"num" if c.num else ""}">{total.get(c.key, "")}</td>' for c in cols
        )
        body.append(f'<tr class="total">{cells}</tr>')
    st.markdown(
        f'<div class="lg-table-wrap"><table class="lg-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def fmt_money(v: object) -> str:
    return esc(money(v))


def fmt_money_cents(v: object) -> str:
    return esc(money(v, with_cents=True))


def fmt_pct(v: object) -> str:
    from leakguard.reporting.metrics import pct

    return esc(pct(v))  # type: ignore[arg-type]


def fmt_int(v: object) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "-"


# Charts ---------------------------------------------------------------------------


def style(
    fig: go.Figure, height: int = 320, legend: bool = True, money_axis: str | None = "y"
) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=16, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=14, color=TEXT),
        hoverlabel=dict(
            bgcolor=CARD, bordercolor=LINE, font=dict(family=FONT, size=14, color=TEXT)
        ),
        showlegend=legend,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=13, color=TEXT_2),
            bgcolor="rgba(0,0,0,0)",
        ),
        bargap=0.35,
    )
    fig.update_xaxes(
        showgrid=False,
        linecolor=LINE,
        tickfont=dict(color=TEXT_2, size=13),
        ticks="",
        zeroline=False,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=GRID,
        linecolor=LINE,
        tickfont=dict(color=TEXT_2, size=13),
        zeroline=False,
        ticks="",
    )
    if money_axis == "y":
        fig.update_yaxes(tickprefix="$", tickformat="~s")
    elif money_axis == "x":
        fig.update_xaxes(tickprefix="$", tickformat="~s", showgrid=True, gridcolor=GRID)
        fig.update_yaxes(showgrid=False)
    fig.update_traces(selector=dict(type="bar"), marker_cornerradius=4, marker_line_width=0)
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "responsive": True})


def trend_chart(monthly: pd.DataFrame, height: int = 300) -> go.Figure:
    """Leaked as bars, recovered as a line with markers (shape, not only color)."""
    fig = go.Figure()
    fig.add_bar(
        x=monthly["month_label"],
        y=monthly["leaked_cents"] / 100,
        name="Leaked (by stay month)",
        marker_color=LOSS,
        customdata=[money(v) for v in monthly["leaked_cents"]],
        hovertemplate="%{x}<br>Leaked %{customdata}<extra></extra>",
    )
    fig.add_scatter(
        x=monthly["month_label"],
        y=monthly["recovered_cents"] / 100,
        name="Recovered (by recovery month)",
        mode="lines+markers",
        line=dict(color=GAIN, width=2),
        marker=dict(size=9, symbol="diamond", color=GAIN, line=dict(color=CARD, width=2)),
        customdata=[money(v) for v in monthly["recovered_cents"]],
        hovertemplate="%{x}<br>Recovered %{customdata}<extra></extra>",
    )
    fig.update_layout(hovermode="x unified")
    return style(fig, height)


def hbar(
    labels: list[str],
    cents: list[int],
    color: str = ACCENT,
    height: int | None = None,
    hover_extra: list[str] | None = None,
    value_text: list[str] | None = None,
) -> go.Figure:
    """Horizontal bars, largest on top, with a short value label at each bar end."""
    order = sorted(range(len(labels)), key=lambda i: cents[i])
    labels = [labels[i] for i in order]
    values = [cents[i] / 100 for i in order]
    texts = [value_text[i] for i in order] if value_text else [money_short(cents[i]) for i in order]
    extra = [hover_extra[i] for i in order] if hover_extra else [""] * len(labels)
    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=color,
            text=texts,
            textposition="outside",
            textfont=dict(color=TEXT_2, size=13),
            cliponaxis=False,
            customdata=[[money(v * 100), e] for v, e in zip(values, extra, strict=True)],
            hovertemplate="%{y}<br>%{customdata[0]}%{customdata[1]}<extra></extra>",
        )
    )
    fig = style(fig, height or max(220, 34 * len(labels) + 60), legend=False, money_axis="x")
    fig.update_xaxes(showgrid=False, showticklabels=False, linecolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=8, r=64, t=8, b=8))
    return fig


def line_chart(monthly: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=monthly["month_label"],
        y=monthly["leaked_cents"] / 100,
        name="Leaked",
        mode="lines+markers",
        line=dict(color=LOSS, width=2),
        marker=dict(size=8, symbol="circle", color=LOSS, line=dict(color=CARD, width=2)),
        customdata=[money(v) for v in monthly["leaked_cents"]],
        hovertemplate="%{x}<br>Leaked %{customdata}<extra></extra>",
    )
    fig.add_scatter(
        x=monthly["month_label"],
        y=monthly["recovered_cents"] / 100,
        name="Recovered",
        mode="lines+markers",
        line=dict(color=GAIN, width=2, dash="dot"),
        marker=dict(size=9, symbol="diamond", color=GAIN, line=dict(color=CARD, width=2)),
        customdata=[money(v) for v in monthly["recovered_cents"]],
        hovertemplate="%{x}<br>Recovered %{customdata}<extra></extra>",
    )
    fig.update_layout(hovermode="x unified")
    return style(fig, height)
