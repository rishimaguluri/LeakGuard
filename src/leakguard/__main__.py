"""Command line entry point: python -m leakguard <command>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from leakguard.config import load_settings


def cmd_demo(args: argparse.Namespace) -> int:
    """Generate demo files, rebuild the demo database, import, match, seed history."""
    from leakguard.db import reset_database
    from leakguard.demo.generator import generate_demo
    from leakguard.demo.history import seed_history
    from leakguard.ingest.importer import format_summary, import_all
    from leakguard.matching.engine import run_matching

    settings = load_settings().model_copy(update={"data_mode": "demo"})
    print(f"1/4 Generating demo export files in {settings.raw_dir} ...")
    truth = generate_demo(settings)
    cards = sum(p["cards"] for p in truth["properties"].values())
    print(f"    {len(truth['properties'])} properties, {cards:,} virtual cards.")

    reset_database(settings)
    print("2/4 Importing (same pipeline as real data) ...")
    summary = import_all(settings)
    if args.verbose:
        print(format_summary(summary))
    else:
        rejected = sum(r.rows_rejected for r in summary.reports)
        print(
            f"    {len(summary.reports)} files, {summary.rows_imported:,} rows, {rejected} rejected."
        )
        for r in summary.reports:
            for w in r.warnings:
                if w.startswith("Full card numbers"):
                    print(f"    warning: {w}")

    print("3/4 Matching ...")
    match = run_matching(settings)
    print("    " + "\n    ".join(match.lines()))

    print("4/4 Adding six months of simulated recovery work ...")
    counts = seed_history(settings)
    print("    " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()))
    print("Ready. Open the dashboard with: streamlit run app/Home.py")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from leakguard.ingest.importer import format_summary, import_all

    settings = load_settings()
    print(f"Importing {settings.data_mode} data from {settings.raw_dir} ...")
    summary = import_all(settings)
    print(format_summary(summary))
    return 1 if any(r.status == "failed" for r in summary.reports) else 0


def cmd_match(args: argparse.Namespace) -> int:
    from leakguard.matching.engine import run_matching

    settings = load_settings()
    print(f"Matching {settings.data_mode} data as of {settings.as_of()} ...")
    summary = run_matching(settings)
    print("\n".join(summary.lines()))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Write the Excel workbook and PDF summary to exports/."""
    from datetime import date

    from leakguard.config import exports_dir
    from leakguard.db import session_scope
    from leakguard.reporting.excel_report import build_workbook
    from leakguard.reporting.metrics import default_period
    from leakguard.reporting.pdf_report import build_pdf
    from leakguard.reporting.report_data import build_report, find_portfolio

    settings = load_settings()
    if not settings.db_path.exists():
        print(f"No {settings.data_mode} database yet. Run python -m leakguard import first.")
        return 1
    start, end = default_period(settings.as_of())
    if args.start:
        start = date.fromisoformat(args.start)
    if args.end:
        end = date.fromisoformat(args.end)
    with session_scope(settings) as session:
        pf = find_portfolio(session, args.portfolio)
        if pf is None:
            print(f"No portfolio '{args.portfolio}' in the {settings.data_mode} database.")
            return 1
        data = build_report(session, settings, pf.id, start, end)
    out = Path(args.out) if args.out else exports_dir()
    out.mkdir(parents=True, exist_ok=True)
    stem = f"leakguard_audit_{data.slug}_{start:%Y%m%d}_{end:%Y%m%d}"
    xlsx, pdf = out / f"{stem}.xlsx", out / f"{stem}.pdf"
    xlsx.write_bytes(build_workbook(data))
    pdf.write_bytes(build_pdf(data))
    print(f"Wrote {xlsx}\nWrote {pdf}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Delete the database file for one mode. Asks for confirmation unless --yes."""
    settings = load_settings().model_copy(update={"data_mode": args.mode})
    db_path = settings.db_path
    if not db_path.exists():
        print(f"Nothing to reset. {db_path.name} does not exist.")
        return 0
    if not args.yes:
        answer = input(f"Delete {db_path.name}? Type '{args.mode}' to confirm: ")
        if answer.strip() != args.mode:
            print("Cancelled.")
            return 1
    from leakguard.db import reset_database

    reset_database(settings)
    print(f"Emptied {db_path.name}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leakguard")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Generate demo files, import and match")
    demo.add_argument("--verbose", action="store_true", help="Print the full import report")
    demo.set_defaults(func=cmd_demo)
    sub.add_parser(
        "import", help="Import every file in the current mode's raw folder"
    ).set_defaults(func=cmd_import)
    sub.add_parser("match", help="Run matching and refresh exceptions").set_defaults(func=cmd_match)

    report = sub.add_parser("report", help="Export the audit report")
    report.add_argument("--portfolio", required=True, help="Portfolio slug")
    report.add_argument("--start", help="Period start, YYYY-MM-DD (default: 12 months to as-of)")
    report.add_argument("--end", help="Period end, YYYY-MM-DD")
    report.add_argument("--out", help="Output folder (default: exports/)")
    report.set_defaults(func=cmd_report)

    reset = sub.add_parser("reset", help="Wipe one mode's database")
    reset.add_argument("--mode", required=True, choices=["demo", "real"])
    reset.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    reset.set_defaults(func=cmd_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
