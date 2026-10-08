"""Build the demo portfolio end to end. Used by `python -m leakguard demo`
and by the dashboard on a fresh server (for example a cloud deploy), where
the generated demo files are not in git."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select

from leakguard.config import Settings
from leakguard.db import reset_database, session_scope
from leakguard.demo.generator import generate_demo
from leakguard.demo.history import seed_history
from leakguard.ingest.importer import ImportSummary, import_all
from leakguard.matching.engine import MatchSummary, run_matching
from leakguard.models import Exception_


def demo_ready(settings: Settings) -> bool:
    """True when the demo database exists and has matched cards in it."""
    if not settings.db_path.exists():
        return False
    try:
        with session_scope(settings) as s:
            return bool(s.scalar(select(func.count()).select_from(Exception_)))
    except Exception:  # unreadable or half-built database
        return False


def build_demo(
    settings: Settings, log: Callable[[str], None] = print
) -> tuple[dict, ImportSummary, MatchSummary, dict[str, int]]:
    """Generate files, rebuild the demo database, import, match, seed history."""
    settings = settings.model_copy(update={"data_mode": "demo"})
    log(f"1/4 Generating demo export files in {settings.raw_dir} ...")
    truth = generate_demo(settings)
    cards = sum(p["cards"] for p in truth["properties"].values())
    log(f"    {len(truth['properties'])} properties, {cards:,} virtual cards.")

    reset_database(settings)
    log("2/4 Importing (same pipeline as real data) ...")
    summary = import_all(settings)
    rejected = sum(r.rows_rejected for r in summary.reports)
    log(f"    {len(summary.reports)} files, {summary.rows_imported:,} rows, {rejected} rejected.")
    for r in summary.reports:
        for w in r.warnings:
            if w.startswith("Full card numbers"):
                log(f"    warning: {w}")

    log("3/4 Matching ...")
    match = run_matching(settings)
    log("    " + "\n    ".join(match.lines()))

    log("4/4 Adding six months of simulated recovery work ...")
    counts = seed_history(settings)
    log("    " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()))
    return truth, summary, match, counts
