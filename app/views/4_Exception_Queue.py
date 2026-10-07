"""Exception queue: filter, review evidence, and work items to recovery."""

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import context
import ui
from leakguard.db import session_scope
from leakguard.recovery import workflow as wf
from leakguard.reporting import metrics as M
from leakguard.reporting.evidence import load_evidence
from leakguard.schemas import EXCEPTION_TYPES, STATUS_LABELS, STATUSES, TYPE_LABELS

c = context.page_top(
    "Exception queue",
    "Every flagged card, highest priority first. Select a row to see the evidence and act on it.",
)
if not context.require_data(c):
    st.stop()

q_all = M.queue(c.df_all)
if q_all.empty:
    ui.empty_state("Nothing flagged", "Every virtual card was charged correctly. Nothing to work.")
    st.stop()

# Filters ---------------------------------------------------------------------------
with st.container(border=True):
    f1, f2, f3, f4 = st.columns([1.3, 1.3, 1, 1])
    prop_ids = sorted(q_all["property_id"].unique(), key=lambda p: c.properties[p][0])
    default_prop = [p for p in [st.session_state.pop("queue_property", None)] if p in prop_ids]
    if default_prop:
        st.session_state["q_props"] = default_prop
    props = f1.multiselect(
        "Property",
        prop_ids,
        key="q_props",
        placeholder="All properties",
        format_func=lambda p: c.properties[p][0],
    )
    types = f2.multiselect(
        "Type",
        list(EXCEPTION_TYPES),
        format_func=TYPE_LABELS.get,
        key="q_types",
        placeholder="All types",
    )
    statuses = f3.multiselect(
        "Status",
        list(STATUSES),
        format_func=STATUS_LABELS.get,
        key="q_status",
        default=["open", "assigned", "in_progress"],
        placeholder="Any status",
    )
    otas = f4.multiselect(
        "OTA",
        ["expedia", "booking"],
        format_func=lambda o: "Booking.com" if o == "booking" else "Expedia",
        key="q_ota",
        placeholder="Both OTAs",
    )
    g1, g2, g3, g4 = st.columns([1, 1, 1.6, 1.3])
    methods = g1.multiselect(
        "Match method",
        ["exact", "fuzzy", "none"],
        key="q_match",
        format_func=lambda m: {"exact": "Exact", "fuzzy": "Fuzzy", "none": "No match"}[m],
        placeholder="Any",
    )
    within = g2.number_input(
        "Expiring within days",
        min_value=0,
        max_value=365,
        value=0,
        step=1,
        key="q_within",
        help="0 shows all",
    )
    top_dollars = max(1, int(q_all["amount_at_risk_cents"].max() / 100) + 1)
    amount = g3.slider("Amount at risk ($)", 0, top_dollars, (0, top_dollars), key="q_amount")
    search = g4.text_input("Search", key="q_search", placeholder="Confirmation or guest")

q = q_all
if props:
    q = q[q["property_id"].isin(props)]
if types:
    q = q[q["exception_type"].isin(types)]
if statuses:
    q = q[q["status"].isin(statuses)]
if otas:
    q = q[q["ota"].isin(otas)]
if methods:
    q = q[q["match_method"].isin(methods)]
if within:
    d = q["days_to_expiry"]
    q = q[d.notna() & (d >= 0) & (d <= within)]
q = q[
    (q["amount_at_risk_cents"] >= amount[0] * 100) & (q["amount_at_risk_cents"] <= amount[1] * 100)
]
if search.strip():
    term = search.strip().upper()
    q = q[
        q["ota_confirmation_no"].fillna("").str.upper().str.contains(term, regex=False)
        | q["pms_confirmation_no"].fillna("").str.upper().str.contains(term, regex=False)
        | q["guest_last_name"].fillna("").str.upper().str.contains(term, regex=False)
    ]
q = q.reset_index(drop=True)


def export_frame(rows: pd.DataFrame) -> bytes:
    out = pd.DataFrame(
        {
            "Property": rows["property_code"],
            "OTA": rows["ota"].map({"expedia": "Expedia", "booking": "Booking.com"}),
            "OTA confirmation": rows["ota_confirmation_no"],
            "PMS confirmation": rows["pms_confirmation_no"],
            "Guest last name": rows["guest_last_name"],
            "Arrival": rows["arrival_date"],
            "Departure": rows["departure_date"],
            "Card last 4": rows["card_last4"],
            "Card amount": rows["expected_cents"] / 100,
            "Charged": rows["charged_cents"] / 100,
            "Amount at risk": rows["amount_at_risk_cents"] / 100,
            "Card expiry": rows["expiry_date"],
            "Days to expiry": rows["days_to_expiry"],
            "Type": rows["type_label"],
            "Why flagged": rows["rule"],
            "Status": rows["status_label"],
            "Assigned to": rows["assigned_to"],
        }
    )
    return out.to_csv(index=False).encode("utf-8")


h1, h2 = st.columns([3, 1], vertical_alignment="center")
h1.markdown(
    f"<b>{len(q):,}</b> items · {M.money(int(q['amount_at_risk_cents'].sum()))} at risk"
    f" · sorted by priority",
    unsafe_allow_html=True,
)
h2.download_button(
    "Export filtered list (CSV)",
    export_frame(q),
    f"leakguard_queue_{c.as_of}.csv",
    "text/csv",
    width="stretch",
)

if q.empty:
    ui.empty_state("No items match these filters", "Clear a filter above to see more.")
    st.stop()

display = pd.DataFrame(
    {
        "Priority": q["priority_score"].round(0).astype(int),
        "Property": q["property_code"],
        "Type": q["type_label"],
        "Status": q["status_label"],
        "OTA": q["ota"].map({"expedia": "Expedia", "booking": "Booking.com"}),
        "OTA confirmation": q["ota_confirmation_no"],
        "Guest": q["guest_last_name"],
        "At risk": q["amount_at_risk_cents"] / 100,
        "Days to expiry": q["days_to_expiry"],
        "Match": q["match_method"].map({"exact": "Exact", "fuzzy": "Fuzzy", "none": "None"}),
        "Assigned to": q["assigned_to"].fillna(""),
    }
)

LOSS_LABELS = {TYPE_LABELS[t] for t in ui.LOSS_TYPES}
STATUS_COLORS = {"Open": ui.LOSS, "Recovered": ui.GAIN}


def type_style(v: str) -> str:
    color = ui.LOSS if v in LOSS_LABELS else ui.ACCENT
    return f"color: {color}; font-weight: 700"


def status_style(v: str) -> str:
    return f"color: {STATUS_COLORS.get(v, ui.TEXT_2)}; font-weight: 700"


styled = display.style.map(type_style, subset=["Type"]).map(status_style, subset=["Status"])
event = st.dataframe(
    styled,
    width="stretch",
    hide_index=True,
    height=min(42 + 35 * len(display), 460),
    on_select="rerun",
    selection_mode="multi-row",
    key="queue_table",
    column_config={
        "Priority": st.column_config.NumberColumn(width="small"),
        "At risk": st.column_config.NumberColumn(format="dollar"),
        "Days to expiry": st.column_config.NumberColumn(format="%d"),
    },
)
selected = [int(q.loc[i, "id"]) for i in event.selection.rows] if event and event.selection else []

# Bulk actions ------------------------------------------------------------------------
user = st.session_state.get("user_name", "")
if len(selected) > 1:
    ui.section(f"{len(selected)} items selected")
    with st.container(border=True):
        b1, b2, b3 = st.columns([1, 1, 1])
        with b1, st.form("bulk_status"):
            new_status = st.selectbox(
                "Change status to",
                [s for s in STATUSES if s != "recovered"],
                format_func=STATUS_LABELS.get,
            )
            note = st.text_input("Note (required for written off or not an issue)")
            name = st.text_input("Your name", value=user)
            if st.form_submit_button("Apply to selected", type="primary"):
                try:
                    with session_scope(c.settings) as ses:
                        n = wf.bulk_change_status(ses, selected, new_status, name, note)
                    st.session_state["user_name"] = name
                    context.bump()
                    st.toast(f"Updated {n} items.")
                    st.rerun()
                except wf.WorkflowError as exc:
                    st.error(str(exc))
        with b2, st.form("bulk_assign"):
            assignee = st.text_input("Assign to", placeholder="For example CMH01 controller")
            name = st.text_input("Your name", value=user, key="bulk_assign_name")
            if st.form_submit_button("Assign selected", type="primary"):
                try:
                    with session_scope(c.settings) as ses:
                        n = wf.bulk_assign(ses, selected, assignee, name)
                    st.session_state["user_name"] = name
                    context.bump()
                    st.toast(f"Assigned {n} items.")
                    st.rerun()
                except wf.WorkflowError as exc:
                    st.error(str(exc))
        with b3:
            st.markdown("Send the selected items to property staff as a spreadsheet.")
            st.download_button(
                "Export selected (CSV)",
                export_frame(q[q["id"].isin(selected)]),
                f"leakguard_selected_{c.as_of}.csv",
                "text/csv",
                width="stretch",
            )
    st.stop()

focus = selected[0] if selected else st.session_state.get("queue_focus")
if focus is None or focus not in set(q_all["id"]):
    st.markdown(
        '<p class="lg-footnote">Select a row to open its evidence panel. Select several rows for bulk actions and export.</p>',
        unsafe_allow_html=True,
    )
    st.stop()
st.session_state["queue_focus"] = focus

# Evidence panel ---------------------------------------------------------------------
with session_scope(c.settings) as ses:
    ev = load_evidence(ses, focus, c.settings.settlement_window_days)
e = ev.exception
row = q_all[q_all["id"] == focus].iloc[0]


CHANNEL_LABELS = {
    "expedia": "Expedia",
    "booking": "Booking.com",
    "direct": "Direct",
    "other": "Other",
}
PAYMENT_LABELS = {
    "ota_collect": "OTA collect",
    "hotel_collect": "Hotel collect",
    "unknown": "Unknown",
}


def describe_event(evn) -> str:  # noqa: ANN001
    """Plain-language line for one audit event."""
    kind, old, new = evn.event_type, evn.old_value or "", evn.new_value or ""
    if kind == "detected":
        return f"Flagged as {TYPE_LABELS.get(new, new)}"
    if kind == "type_changed":
        return f"Type changed from {TYPE_LABELS.get(old, old)} to {TYPE_LABELS.get(new, new)}"
    if kind == "assigned":
        return f"Assigned to {new}"
    if kind == "status_changed":
        return f"Status {STATUS_LABELS.get(old, old)} to {STATUS_LABELS.get(new, new)}"
    if kind == "recovered":
        return f"Recovery recorded, total {M.money(int(new or 0), True)}"
    return {
        "recovery_cleared": "Recovery cleared",
        "match_confirmed": "Fuzzy match confirmed",
        "auto_resolved": "Charge detected in new data, marked recovered",
        "note": "Note added",
    }.get(kind, kind)


def d(v: date | None) -> str:
    return ui.esc(M.fmt_date(v))


ui.section("Evidence")
with st.container(border=True):
    st.markdown(
        f'<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">'
        f'<span class="lg-kpi-value loss" style="font-size:1.8rem;">{M.money(e.amount_at_risk_cents, True)}</span>'
        f"{ui.type_pill(e.exception_type, TYPE_LABELS[e.exception_type])}"
        f"{ui.status_pill(e.status, STATUS_LABELS[e.status])}"
        f"<span><b>{ui.esc(row['property_code'])}</b> {ui.esc(row['property_name'])}</span>"
        f'<span style="color:{ui.TEXT_2};">Priority {e.priority_score:,.0f}</span></div>',
        unsafe_allow_html=True,
    )
    match_text = {
        "exact": "Exact match on OTA confirmation number.",
        "fuzzy": "Fuzzy match on guest name, arrival date and amount. "
        + (
            "Confirmed by a person."
            if e.match_confirmed
            else "Needs a person to confirm before recovery."
        ),
        "none": "No PMS reservation matched.",
    }[e.match_method]
    ui.callout(
        f"<strong>Why it was flagged.</strong> {ui.esc(e.rule)}<br>"
        f'<span style="color:{ui.TEXT_2};font-size:14px;">{ui.esc(match_text)} '
        f"Confidence: {ui.esc(e.confidence)}.</span>",
        "loss",
    )

    v1, v2 = st.columns(2, gap="large")
    with v1:
        st.markdown("**Virtual card**")
        vcc = ev.vcc
        st.markdown(
            ui.kv(
                [
                    ("OTA", ui.esc("Booking.com" if e.ota == "booking" else "Expedia")),
                    ("OTA confirmation", ui.esc(e.ota_confirmation_no)),
                    ("Card last 4", ui.esc(e.card_last4 or "Not given")),
                    ("Card amount", ui.esc(M.money(e.expected_cents, True))),
                    ("Activation", d(vcc.activation_date if vcc else None)),
                    (
                        "Expiry",
                        d(e.expiry_date)
                        + (
                            f" ({int(row['days_to_expiry'])} days)"
                            if row["days_to_expiry"] == row["days_to_expiry"]
                            and row["days_to_expiry"] is not None
                            else ""
                        ),
                    ),
                    ("OTA status", ui.esc(vcc.ota_status if vcc and vcc.ota_status else "-")),
                    ("Source file", ui.esc(Path(ev.vcc_file).name if ev.vcc_file else "-")),
                ]
            ),
            unsafe_allow_html=True,
        )
    with v2:
        st.markdown("**Matched reservation**")
        res = ev.reservation
        if res is None:
            st.markdown(
                "No reservation in the PMS export matched this card. Check whether the booking was "
                "moved to another property or entered under a different confirmation number."
            )
        else:
            st.markdown(
                ui.kv(
                    [
                        ("PMS confirmation", ui.esc(res.pms_confirmation_no)),
                        ("OTA confirmation in PMS", ui.esc(res.ota_confirmation_no or "Blank")),
                        ("Guest", ui.esc(res.guest_last_name or "-")),
                        ("Stay", f"{d(res.arrival_date)} to {d(res.departure_date)}"),
                        ("Status", ui.esc(res.status.replace("_", " ").capitalize())),
                        (
                            "Channel / payment",
                            ui.esc(
                                f"{CHANNEL_LABELS.get(res.channel, res.channel)} / {PAYMENT_LABELS.get(res.payment_model, res.payment_model)}"
                            ),
                        ),
                        (
                            "Room + tax",
                            ui.esc(
                                M.money((res.room_revenue_cents or 0) + (res.tax_cents or 0), True)
                            ),
                        ),
                        (
                            "Source file",
                            ui.esc(Path(ev.reservation_file).name if ev.reservation_file else "-"),
                        ),
                    ]
                ),
                unsafe_allow_html=True,
            )

    p1, p2 = st.columns(2, gap="large")
    with p1:
        st.markdown(f"**PMS postings** · charged {M.money(e.charged_cents, True)}")
        if ev.postings:
            ui.table(
                pd.DataFrame(
                    [
                        {
                            "date": p.posting_date,
                            "type": p.payment_type,
                            "last4": p.card_last4 or "-",
                            "amount": p.amount_cents,
                            "match": "Counted"
                            if (p.card_last4 == e.card_last4 or p.card_last4 is None)
                            and p.currency == e.currency
                            else "Other card",
                        }
                        for p in ev.postings
                    ]
                ),
                [
                    ui.Col("date", "Date", d),
                    ui.Col("type", "Payment type"),
                    ui.Col("last4", "Last 4"),
                    ui.Col("amount", "Amount", ui.fmt_money_cents, num=True),
                    ui.Col("match", "Used"),
                ],
            )
        else:
            st.markdown("No payment postings on this reservation.")
    with p2:
        settled = "not checked" if e.settled_cents is None else M.money(e.settled_cents, True)
        st.markdown(f"**Processor transactions** · settled {settled}")
        if ev.transactions:
            ui.table(
                pd.DataFrame(
                    [
                        {
                            "date": t.transaction_date,
                            "settle": t.settlement_date,
                            "type": t.transaction_type,
                            "last4": t.card_last4,
                            "amount": t.amount_cents,
                            "ref": t.reference or "-",
                        }
                        for t in ev.transactions
                    ]
                ),
                [
                    ui.Col("date", "Date", d),
                    ui.Col("settle", "Settled", d),
                    ui.Col("type", "Type"),
                    ui.Col("last4", "Last 4"),
                    ui.Col("amount", "Amount", ui.fmt_money_cents, num=True),
                    ui.Col("ref", "Reference"),
                ],
            )
        else:
            st.markdown("No processor transactions on this card around the stay dates.")

t1, t2 = st.columns([1, 1.3], gap="large")
with t1:
    ui.section("Audit trail")
    items = []
    for evn in ev.events:
        tone = (
            "gain"
            if evn.event_type in ("recovered", "auto_resolved")
            else "loss"
            if evn.event_type == "detected"
            else ""
        )
        what = describe_event(evn)
        note = f"<br>{ui.esc(evn.note)}" if evn.note and evn.event_type != "detected" else ""
        items.append(
            f'<li class="{tone}"><b>{ui.esc(what)}</b>{note}<br>'
            f'<span class="when">{ui.esc(evn.user)} · {evn.timestamp:%b %d, %Y %H:%M}</span></li>'
        )
    st.markdown(f'<ul class="lg-timeline">{"".join(items)}</ul>', unsafe_allow_html=True)

with t2:
    ui.section("Actions", "Every action is written to the audit trail.")
    name = st.text_input(
        "Your name", value=user, key="actor_name", placeholder="Shown in the audit trail"
    )

    def act(fn, *args, ok: str) -> None:  # noqa: ANN001
        try:
            with session_scope(c.settings) as ses:
                fn(ses, focus, *args)
            st.session_state["user_name"] = name
            context.bump()
            st.toast(ok)
            st.rerun()
        except wf.WorkflowError as exc:
            st.error(str(exc))

    tabs = ["Assign", "Status", "Record recovery", "Note"]
    needs_confirm = e.match_method == "fuzzy" and not e.match_confirmed
    if needs_confirm:
        tabs.insert(0, "Confirm match")
    if not e.is_exception:
        st.markdown("This card is not an exception. Nothing to work.")
    else:
        panes = dict(zip(tabs, st.tabs(tabs), strict=True))
        if needs_confirm:
            with panes["Confirm match"], st.form("f_confirm"):
                st.markdown(
                    "Check the guest name, dates and amount against the folio before confirming."
                )
                note = st.text_input("Note", placeholder="What you checked")
                if st.form_submit_button("Confirm this match", type="primary"):
                    act(lambda s, i: wf.confirm_match(s, i, name, note), ok="Match confirmed.")
        with panes["Assign"], st.form("f_assign"):
            assignee = st.text_input(
                "Assign to", value=e.assigned_to or "", placeholder="For example CMH01 controller"
            )
            note = st.text_input("Note", key="assign_note")
            if st.form_submit_button("Assign", type="primary"):
                act(lambda s, i: wf.assign(s, i, assignee, name, note), ok="Assigned.")
        with panes["Status"], st.form("f_status"):
            options = [s for s in STATUSES if s != "recovered"]
            new_status = st.selectbox(
                "New status",
                options,
                format_func=STATUS_LABELS.get,
                index=options.index(e.status) if e.status in options else 0,
            )
            note = st.text_input(
                "Note", key="status_note", placeholder="Required for written off or not an issue"
            )
            if st.form_submit_button("Update status", type="primary"):
                act(
                    lambda s, i: wf.change_status(s, i, new_status, name, note),
                    ok="Status updated.",
                )
        with panes["Record recovery"], st.form("f_recover"):
            remaining = max(e.amount_at_risk_cents - e.recovered_cents, 0)
            r1, r2 = st.columns(2)
            dollars = r1.number_input(
                "Amount collected ($)", min_value=0.0, value=round(remaining / 100, 2), step=10.0
            )
            when = r2.date_input(
                "Date collected",
                value=c.as_of,
                max_value=max(c.as_of, date.today()),
                format="MM/DD/YYYY",
            )
            note = st.text_input(
                "Note", key="recover_note", placeholder="For example charged in PMS, OTA paid claim"
            )
            if e.recovered_cents:
                st.markdown(f"Already recorded: {M.money(e.recovered_cents, True)}.")
            if st.form_submit_button("Record recovery", type="primary"):
                act(
                    lambda s, i: wf.record_recovery(s, i, round(dollars * 100), when, name, note),
                    ok="Recovery recorded.",
                )
        with panes["Note"], st.form("f_note"):
            text = st.text_area("Note", key="note_text", height=90)
            if st.form_submit_button("Add note", type="primary"):
                act(lambda s, i: wf.add_note(s, i, text, name), ok="Note added.")
    if e.notes:
        st.markdown("**Notes**")
        st.markdown(ui.esc(e.notes).replace("\n", "<br>"), unsafe_allow_html=True)
