"""Settings: fee, tolerances and windows, saved to config/settings.yaml."""

import streamlit as st

import context
import ui
from leakguard.config import load_settings, save_settings, settings_path
from leakguard.matching.engine import run_matching
from leakguard.reporting import metrics as M

c = context.ctx()
ui.topbar(c.portfolio_name, c.settings.data_mode)
ui.page_header(
    "Settings", "How LeakGuard matches, flags and prices. Saved to config/settings.yaml."
)
s = load_settings()

with st.form("settings"):
    ui.section("Business terms")
    a, b = st.columns(2)
    fee = a.number_input("LeakGuard fee (% of recovered cash)", 0.0, 100.0, s.fee_pct * 100, 1.0)
    cap = b.number_input(
        "Cap rate for implied value (%)",
        1.0,
        25.0,
        s.cap_rate * 100,
        0.25,
        help="Only used for the illustrative implied asset value.",
    )

    ui.section("Matching")
    a, b, d = st.columns(3)
    tol = a.number_input(
        "Charge tolerance ($)",
        0.0,
        100.0,
        s.charge_tolerance_cents / 100,
        0.5,
        help="A charge within this amount of the card counts as correct.",
    )
    window = b.number_input("Upcoming expiry window (days)", 0, 90, s.upcoming_expiry_days)
    settle = d.number_input(
        "Settlement window (days)",
        0,
        60,
        s.settlement_window_days,
        help="How long after the PMS posting a processor settlement can appear.",
    )
    a, b, d = st.columns(3)
    name_score = a.slider("Fuzzy name similarity (minimum)", 50, 100, s.fuzzy_name_min_score)
    arrival = b.number_input("Fuzzy arrival window (days)", 0, 7, s.fuzzy_arrival_window_days)
    amount = d.number_input(
        "Fuzzy amount tolerance (%)", 0.0, 50.0, s.fuzzy_amount_tolerance_pct * 100, 1.0
    )

    ui.section("Privacy")
    hash_names = st.toggle(
        "Hash guest last names on import",
        value=s.hash_guest_names,
        help="Recommended for real data. Fuzzy matching then needs an exact name match. Applies to files imported after the change.",
    )

    ui.section("Data mode")
    mode = st.radio(
        "Which data to show",
        ["demo", "real"],
        index=0 if s.data_mode == "demo" else 1,
        format_func=lambda m: (
            "Demo data (data/demo_raw)" if m == "demo" else "Live data (data/raw)"
        ),
        horizontal=True,
    )
    st.markdown(
        '<p class="lg-footnote">Each mode has its own database, so demo and live data never mix.</p>',
        unsafe_allow_html=True,
    )

    save, rematch = st.columns(2)
    do_save = save.form_submit_button("Save settings", width="stretch")
    do_rematch = rematch.form_submit_button(
        "Save and re-run matching", type="primary", width="stretch"
    )

if do_save or do_rematch:
    new = s.model_copy(
        update={
            "fee_pct": round(fee / 100, 4),
            "cap_rate": round(cap / 100, 4),
            "charge_tolerance_cents": round(tol * 100),
            "upcoming_expiry_days": int(window),
            "settlement_window_days": int(settle),
            "fuzzy_name_min_score": int(name_score),
            "fuzzy_arrival_window_days": int(arrival),
            "fuzzy_amount_tolerance_pct": round(amount / 100, 4),
            "hash_guest_names": hash_names,
            "data_mode": mode,
        }
    )
    save_settings(new)
    msg = f"Saved to {settings_path().name}."
    if do_rematch and new.db_path.exists():
        with st.spinner("Re-running matching..."):
            result = run_matching(new)
        msg += f" Matching re-run: {result.cards:,} cards, {result.type_changes} type changes."
    context.bump()
    st.success(msg)
    if mode != s.data_mode:
        st.rerun()

ui.section("Current values")
st.markdown(
    ui.kv(
        [
            ("Data mode", ui.esc("Demo" if s.data_mode == "demo" else "Live")),
            ("Raw data folder", ui.esc(s.raw_dir)),
            ("Database", ui.esc(s.db_path.name)),
            ("As of", ui.esc(M.fmt_date(s.as_of()))),
            ("Fee", ui.esc(f"{s.fee_pct * 100:.1f}%")),
            ("Charge tolerance", ui.esc(M.money(s.charge_tolerance_cents, True))),
        ]
    ),
    unsafe_allow_html=True,
)
