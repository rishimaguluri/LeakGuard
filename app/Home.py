"""LeakGuard dashboard entry point: streamlit run app/Home.py

Sets the theme, checks the password, builds the shared sidebar and data
context, then hands over to the selected page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
SRC_DIR = APP_DIR.parent / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import context  # noqa: E402
import ui  # noqa: E402
from leakguard.config import load_settings  # noqa: E402
from leakguard.demo.build import demo_ready  # noqa: E402

st.set_page_config(
    page_title="LeakGuard",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.inject_css()

if not context.password_gate():
    st.stop()


@st.cache_resource(show_spinner=False)
def _build_demo_once(db_url: str) -> bool:
    """Build the demo on a fresh server (e.g. a cloud deploy). Runs once per
    server process; other visitors wait for it instead of starting another."""
    from leakguard.demo.build import build_demo

    build_demo(load_settings())
    return True


_settings = load_settings()
if _settings.data_mode == "demo" and not demo_ready(_settings):
    ui.topbar(None, "demo")
    with st.status(
        "Setting up the demo portfolio. First visit only, about 2 to 4 minutes.", expanded=True
    ) as status:
        st.write(
            "Generating 12 months of fake hotel exports, importing them through the same "
            "pipeline real files use, matching every virtual card, and adding recovery history."
        )
        _build_demo_once(_settings.db_url)
        status.update(label="Demo portfolio ready", state="complete")
    st.cache_data.clear()
    st.rerun()

PAGES = APP_DIR / "views"
nav = st.navigation(
    {
        "Overview": [
            st.Page(PAGES / "0_Home.py", title="Home", icon=":material/home:", default=True),
            st.Page(PAGES / "1_Portfolio.py", title="Portfolio", icon=":material/apartment:"),
            st.Page(
                PAGES / "2_Operator_Scorecard.py",
                title="Operator scorecard",
                icon=":material/leaderboard:",
            ),
            st.Page(PAGES / "3_Hotel_Detail.py", title="Hotel detail", icon=":material/hotel:"),
        ],
        "Recover": [
            st.Page(
                PAGES / "4_Exception_Queue.py", title="Exception queue", icon=":material/checklist:"
            ),
            st.Page(
                PAGES / "5_Recovery_Tracker.py",
                title="Recovery tracker",
                icon=":material/trending_up:",
            ),
            st.Page(
                PAGES / "6_Audit_Report.py", title="Audit report", icon=":material/description:"
            ),
        ],
        "Data": [
            st.Page(PAGES / "7_Data_Sources.py", title="Data sources", icon=":material/database:"),
            st.Page(
                PAGES / "8_Mapping_Wizard.py",
                title="Mapping wizard",
                icon=":material/table_convert:",
            ),
            st.Page(PAGES / "9_Settings.py", title="Settings", icon=":material/settings:"),
        ],
    }
)
st.session_state["_ctx"] = context.build()
nav.run()
