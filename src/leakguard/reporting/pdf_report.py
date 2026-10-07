"""Branded PDF audit summary, 2 to 3 pages, same colors as the dashboard.

Page 1: headline numbers, leakage by type, NOI impact.
Page 2: operator scorecard and properties.
Page 3: method and limits.
"""

from __future__ import annotations

import io

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from leakguard.reporting import metrics as M
from leakguard.reporting.report_data import ReportData

BG = colors.HexColor("#EDF2F8")
CARD = colors.HexColor("#F8FAFD")
LINE = colors.HexColor("#BFD0E6")
TEXT = colors.HexColor("#0E2240")
TEXT_2 = colors.HexColor("#25406A")
ACCENT = colors.HexColor("#1A3766")
LOSS = colors.HexColor("#8C2B45")
GAIN = colors.HexColor("#1F5A3D")
HEAD_FILL = colors.HexColor("#E1E9F4")

SERIF = "Times-Roman"
SANS = "Helvetica"
SANS_BOLD = "Helvetica-Bold"

H1 = ParagraphStyle("h1", fontName=SERIF, fontSize=24, leading=28, textColor=TEXT)
H2 = ParagraphStyle(
    "h2", fontName=SERIF, fontSize=16, leading=20, textColor=TEXT, spaceBefore=10, spaceAfter=6
)
BODY = ParagraphStyle(
    "body", fontName=SANS, fontSize=10, leading=14, textColor=TEXT, alignment=TA_LEFT
)
SMALL = ParagraphStyle("small", fontName=SANS, fontSize=8.5, leading=11.5, textColor=TEXT_2)
CALLOUT = ParagraphStyle("callout", fontName=SANS_BOLD, fontSize=11, leading=15, textColor=TEXT)
KPI_LABEL = ParagraphStyle("kl", fontName=SANS_BOLD, fontSize=8, leading=10, textColor=TEXT_2)
KPI_NOTE = ParagraphStyle("kn", fontName=SANS, fontSize=8, leading=10, textColor=TEXT_2)


def _kpi(label: str, value: str, note: str, color: colors.Color = TEXT) -> list:
    style = ParagraphStyle("kv", fontName=SERIF, fontSize=20, leading=24, textColor=color)
    return [Paragraph(label, KPI_LABEL), Paragraph(value, style), Paragraph(note, KPI_NOTE)]


def _kpi_row(items: list[list]) -> Table:
    width = 7.0 * inch / len(items)
    t = Table([items], colWidths=[width] * len(items))
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 0.8, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.8, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return t


def _table(rows: list[list], widths: list[float], num_cols: set[int], total: bool = False) -> Table:
    data = [
        [
            Paragraph(
                str(c),
                ParagraphStyle("th", fontName=SANS_BOLD, fontSize=8, leading=10, textColor=TEXT_2),
            )
            for c in rows[0]
        ]
    ] + rows[1:]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_FILL),
        ("FONTNAME", (0, 1), (-1, -1), SANS),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("BOX", (0, 0), (-1, -1), 0.8, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 1), (-1, -1), CARD),
    ]
    for c in num_cols:
        style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    if total:
        style += [
            ("FONTNAME", (0, -1), (-1, -1), SANS_BOLD),
            ("BACKGROUND", (0, -1), (-1, -1), HEAD_FILL),
        ]
    t.setStyle(TableStyle(style))
    return t


def _property_chart(r: ReportData) -> Drawing:
    props = r.by_property.head(10).iloc[::-1]
    height = 30 + 22 * max(len(props), 1)
    d = Drawing(7.0 * inch, height)
    if props.empty:
        return d
    chart = HorizontalBarChart()
    chart.x, chart.y = 110, 10
    chart.width, chart.height = 7.0 * inch - 180, height - 20
    chart.data = [[v / 100 for v in props["leaked_cents"]]]
    chart.categoryAxis.categoryNames = [str(c) for c in props["property_code"]]
    chart.categoryAxis.labels.fontName = SANS
    chart.categoryAxis.labels.fontSize = 9
    chart.categoryAxis.labels.fillColor = TEXT_2
    chart.categoryAxis.strokeColor = LINE
    chart.valueAxis.visible = False
    chart.valueAxis.valueMin = 0
    chart.bars[0].fillColor = LOSS
    chart.bars[0].strokeColor = None
    chart.barSpacing = 4
    chart.barLabelFormat = lambda v: M.money_short(v * 100)
    chart.barLabels.fontName = SANS
    chart.barLabels.fontSize = 8.5
    chart.barLabels.fillColor = TEXT_2
    chart.barLabels.nudge = 18
    d.add(chart)
    d.add(
        String(
            0,
            height - 10,
            "Leaked dollars by property",
            fontName=SANS_BOLD,
            fontSize=9,
            fillColor=TEXT_2,
        )
    )
    return d


def _page(canvas, doc, r: ReportData) -> None:  # noqa: ANN001
    canvas.saveState()
    w, h = LETTER
    canvas.setFillColor(BG)
    canvas.rect(0, 0, w, h, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 0.55 * inch, w, 0.55 * inch, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(SERIF, 16)
    canvas.drawString(0.75 * inch, h - 0.36 * inch, "LeakGuard")
    canvas.setFont(SANS, 9)
    label = f"{r.portfolio_name}  |  {M.fmt_date(r.start)} to {M.fmt_date(r.end)}"
    if r.data_mode == "demo":
        label += "  |  Demo data"
    canvas.drawRightString(w - 0.75 * inch, h - 0.34 * inch, label)
    canvas.setFillColor(TEXT_2)
    canvas.setFont(SANS, 8)
    canvas.drawString(
        0.75 * inch,
        0.45 * inch,
        f"Generated {M.fmt_date(r.generated_at.date())}. Estimates are labeled. Recovery is not guaranteed.",
    )
    canvas.drawRightString(w - 0.75 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(r: ReportData) -> bytes:
    s, n = r.summary, r.noi
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.75 * inch,
        title=f"LeakGuard audit, {r.portfolio_name}",
        author="LeakGuard",
    )
    story: list = []

    # Page 1 -----------------------------------------------------------------------
    story.append(Paragraph("Virtual card audit", H1))
    story.append(
        Paragraph(
            f"{r.portfolio_name} ({r.owner_name}). {s.vcc_count:,} Expedia and Booking.com virtual cards "
            f"worth {M.money(s.vcc_volume_cents)} checked against PMS postings and processor settlements "
            f"for stays from {M.fmt_date(r.start)} to {M.fmt_date(r.end)}.",
            BODY,
        )
    )
    story.append(Spacer(1, 12))
    story.append(
        _kpi_row(
            [
                _kpi(
                    "LEAKED",
                    M.money(s.leaked_cents),
                    f"{M.pct(s.leak_rate)} of OTA-collect revenue",
                    LOSS,
                ),
                _kpi(
                    "RECOVERED TO DATE",
                    M.money(s.recovered_cents),
                    f"{M.pct(s.recovery_rate)} of leaked",
                    GAIN,
                ),
                _kpi("OPEN AT RISK", M.money(s.open_at_risk_cents), f"{s.open_count:,} items"),
                _kpi(
                    "RECOVERABLE ESTIMATE",
                    M.money(s.recoverable_estimate_cents),
                    "Estimate, not guaranteed",
                ),
            ]
        )
    )
    if r.takeaway:
        story.append(Spacer(1, 12))
        box = Table([[Paragraph(r.takeaway, CALLOUT)]], colWidths=[7.0 * inch])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), CARD),
                    ("BOX", (0, 0), (-1, -1), 0.8, LINE),
                    ("LINEBEFORE", (0, 0), (0, -1), 4, LOSS),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        story.append(box)

    story.append(Paragraph("Dollars leaked by type", H2))
    rows = [["Exception type", "Items", "Leaked", "Recovered"]]
    for t in r.by_type.itertuples():
        rows.append(
            [t.label, f"{int(t.count):,}", M.money(t.leaked_cents), M.money(t.recovered_cents)]
        )
    rows.append(
        ["Total", f"{s.exception_count:,}", M.money(s.leaked_cents), M.money(s.recovered_cents)]
    )
    story.append(
        _table(rows, [3.4 * inch, 1.0 * inch, 1.3 * inch, 1.3 * inch], {1, 2, 3}, total=True)
    )

    story.append(Paragraph("NOI impact", H2))
    noi_rows = [
        ["Measure", "Amount"],
        ["Recovered cash in period", M.money(n.recovered_cents)],
        ["Recovered cash, annualized", M.money(n.annualized_recovered_cents)],
        [f"LeakGuard fee at {r.fee_pct * 100:.0f}% (annualized)", M.money(n.annualized_fee_cents)],
        ["Owner net gain to NOI (annualized)", M.money(n.annualized_owner_net_cents)],
        [
            f"Implied asset value at {n.cap_rate * 100:.1f}% cap rate (estimate)",
            M.money(n.implied_value_cents),
        ],
    ]
    story.append(KeepTogether(_table(noi_rows, [5.0 * inch, 2.0 * inch], {1})))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "Fee applies only to cash actually recovered. Annualized figures scale the period's recoveries "
            "to 12 months and are estimates.",
            SMALL,
        )
    )

    # Page 2 -----------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Operator scorecard", H2))
    rows = [
        [
            "Rank",
            "Management company",
            "Hotels",
            "OTA-collect revenue",
            "Leaked",
            "Leak rate",
            "Expired share",
            "Open at risk",
        ]
    ]
    for c in r.scorecard.itertuples():
        rows.append(
            [
                f"#{int(c.rank)}",
                c.company,
                str(int(c.properties)),
                M.money(c.vcc_volume_cents),
                M.money(c.leaked_cents),
                M.pct(c.leak_rate),
                M.pct(c.expired_share),
                M.money(c.open_cents),
            ]
        )
    story.append(
        _table(
            rows,
            [
                0.45 * inch,
                1.75 * inch,
                0.55 * inch,
                1.1 * inch,
                0.85 * inch,
                0.7 * inch,
                0.8 * inch,
                0.8 * inch,
            ],
            {2, 3, 4, 5, 6, 7},
        )
    )
    story.append(Spacer(1, 14))
    story.append(_property_chart(r))
    story.append(Paragraph("Properties", H2))
    rows = [
        [
            "Property",
            "Operator",
            "Rooms",
            "OTA-collect revenue",
            "Leaked",
            "Leak rate",
            "Recovered",
            "Open",
        ]
    ]
    for p in r.by_property.itertuples():
        rows.append(
            [
                f"{p.property_code} {p.property_name}",
                p.company,
                str(int(p.room_count or 0)),
                M.money(p.vcc_volume_cents),
                M.money(p.leaked_cents),
                M.pct(p.leak_rate),
                M.money(p.recovered_cents),
                M.money(p.open_cents),
            ]
        )
    body = ParagraphStyle("cell", fontName=SANS, fontSize=8.5, leading=10.5, textColor=TEXT)
    rows = [rows[0]] + [
        [Paragraph(str(x[0]), body), Paragraph(str(x[1]), body), *x[2:]] for x in rows[1:]
    ]
    story.append(
        _table(
            rows,
            [
                1.6 * inch,
                1.1 * inch,
                0.55 * inch,
                1.05 * inch,
                0.75 * inch,
                0.55 * inch,
                0.75 * inch,
                0.65 * inch,
            ],
            {2, 3, 4, 5, 6, 7},
        )
    )

    # Page 3 -----------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Method and limits", H2))
    for title, text in r.method:
        story.append(
            KeepTogether([Paragraph(f"<b>{title}</b>", BODY), Paragraph(text, BODY), Spacer(1, 8)])
        )
    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            "Full line items with evidence for every flagged card are in the Excel workbook that accompanies "
            "this summary.",
            SMALL,
        )
    )

    doc.build(
        story, onFirstPage=lambda c, d: _page(c, d, r), onLaterPages=lambda c, d: _page(c, d, r)
    )
    return buf.getvalue()
