"""Shared page context: password gate, sidebar filters and cached data.

Home.py builds the context once per run and pages read it with ctx().
Data is cached on the database file's modification time plus a version
counter that pages bump after every write, so numbers refresh at once.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

import ui
from leakguard.config import Settings, load_settings
from leakguard.db import session_scope
from leakguard.reporting import metrics as M


@dataclass
class Ctx:
    settings: Settings
    as_of: date
    has_data: bool
    portfolio_id: int | None = None
    portfolio_name: str | None = None
    portfolio_slug: str | None = None
    start: date | None = None
    end: date | None = None
    filters: M.Filters | None = None
    companies: dict[int, str] | None = None
    properties: dict[int, tuple[str, str, int]] | None = None  # id -> (code, name, company_id)
    df_all: pd.DataFrame | None = None
    df: pd.DataFrame | None = None
    summary: M.Summary | None = None


# Password gate ---------------------------------------------------------------------


def _configured_password() -> str | None:
    try:
        return st.secrets.get("app_password") or None
    except Exception:  # no secrets.toml
        return None


def password_gate() -> bool:
    """True when the visitor may see the app."""
    password = _configured_password()
    if password is None:
        st.session_state["gate_off"] = True
        return True
    if st.session_state.get("authed"):
        return True
    st.markdown('<div class="lg-login">', unsafe_allow_html=True)
    st.markdown(
        '<div class="lg-wordmark" style="font-size:2rem;">Leak<span>Guard</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="lg-subtitle">Virtual card revenue recovery for hotel owners.</p>',
        unsafe_allow_html=True,
    )
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", type="primary", width="stretch")
    if ok:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("That password is not right.")
    st.markdown("</div>", unsafe_allow_html=True)
    return False


# Cached loading -------------------------------------------------------------------


def data_version() -> int:
    return st.session_state.get("data_version", 0)


def bump() -> None:
    """Call after any write so every page reloads its numbers."""
    st.session_state["data_version"] = data_version() + 1
    st.cache_data.clear()


def _db_stamp(settings: Settings) -> float:
    return settings.db_path.stat().st_mtime if settings.db_path.exists() else 0.0


@st.cache_data(show_spinner=False)
def _load_lists(db_url: str, stamp: float, version: int) -> dict:
    settings = load_settings()
    with session_scope(settings) as s:
        pfs = [(p.id, p.name, p.slug) for p in M.portfolios(s)]
        out = {"portfolios": pfs, "companies": {}, "properties": {}}
        for pid, _, _ in pfs:
            out["companies"][pid] = {c.id: c.name for c in M.companies(s, pid)}
            out["properties"][pid] = {
                p.id: (p.code, p.name, p.management_company_id) for p in M.properties(s, pid)
            }
        return out


@st.cache_data(show_spinner="Loading portfolio data...")
def _load_frame(
    db_url: str, stamp: float, version: int, filters: M.Filters, as_of: date
) -> pd.DataFrame:
    settings = load_settings()
    with session_scope(settings) as s:
        return M.load_frame(s, filters, as_of)


def build(settings: Settings | None = None) -> Ctx:
    settings = settings or load_settings()
    as_of = settings.as_of()
    if not settings.db_path.exists():
        return Ctx(settings, as_of, False)
    stamp = _db_stamp(settings)
    lists = _load_lists(settings.db_url, stamp, data_version())
    if not lists["portfolios"]:
        return Ctx(settings, as_of, False)

    sb = st.sidebar
    names = {pid: name for pid, name, _ in lists["portfolios"]}
    slugs = {pid: slug for pid, _, slug in lists["portfolios"]}
    pid = sb.selectbox("Portfolio", list(names), format_func=names.get, key="f_portfolio")
    companies = lists["companies"][pid]
    props = lists["properties"][pid]

    default_start, default_end = M.default_period(as_of)
    period = sb.date_input(
        "Stay dates",
        value=(default_start, default_end),
        max_value=as_of,
        key="f_period",
        format="MM/DD/YYYY",
    )
    start, end = (
        period if isinstance(period, tuple) and len(period) == 2 else (default_start, default_end)
    )

    chosen_companies = sb.multiselect(
        "Management company",
        list(companies),
        format_func=companies.get,
        key="f_companies",
        placeholder="All management companies",
    )
    prop_options = [p for p, v in props.items() if not chosen_companies or v[2] in chosen_companies]
    chosen_props = sb.multiselect(
        "Property",
        prop_options,
        format_func=lambda p: f"{props[p][0]}  {props[p][1]}",
        key="f_properties",
        placeholder="All properties",
    )
    sb.markdown(
        f'<p class="lg-footnote">Figures as of {M.fmt_date(as_of)}. '
        f"Trends use the stay checkout date.</p>",
        unsafe_allow_html=True,
    )
    if st.session_state.get("gate_off"):
        sb.markdown(
            '<p class="lg-footnote">Password gate is off. Set app_password in '
            ".streamlit/secrets.toml before sharing this screen.</p>",
            unsafe_allow_html=True,
        )

    filters = M.Filters(pid, None, None, tuple(chosen_companies), tuple(chosen_props))
    df_all = _load_frame(settings.db_url, stamp, data_version(), filters, as_of)
    df = M.in_period(df_all, start, end)
    summary = M.summary(df, df_all, settings)
    return Ctx(
        settings=settings,
        as_of=as_of,
        has_data=not df_all.empty,
        portfolio_id=pid,
        portfolio_name=names[pid],
        portfolio_slug=slugs[pid],
        start=start,
        end=end,
        filters=M.Filters(pid, start, end, tuple(chosen_companies), tuple(chosen_props)),
        companies=companies,
        properties=props,
        df_all=df_all,
        df=df,
        summary=summary,
    )


def ctx() -> Ctx:
    return st.session_state["_ctx"]


def page_top(title: str, subtitle: str | None = None) -> Ctx:
    """Top bar plus heading. Returns the context."""
    c = ctx()
    ui.topbar(c.portfolio_name, c.settings.data_mode)
    ui.page_header(title, subtitle)
    return c


def require_data(c: Ctx) -> bool:
    if c.has_data:
        return True
    ui.no_data_state()
    return False
