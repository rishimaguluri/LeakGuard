"""Command line entry point: python -m leakguard <command>."""

from __future__ import annotations

import argparse
import sys

from leakguard.config import load_settings


def _not_built(phase: int) -> int:
    print(f"Not built yet. Arrives in phase {phase}.")
    return 2


def cmd_demo(args: argparse.Namespace) -> int:
    """Generate demo files, rebuild the demo database, import, match, seed history."""
    from leakguard.db import dispose_engine
    from leakguard.demo.generator import generate_demo
    from leakguard.demo.history import seed_history
    from leakguard.ingest.importer import format_summary, import_all
    from leakguard.matching.engine import run_matching

    settings = load_settings().model_copy(update={"data_mode": "demo"})
    print(f"1/4 Generating demo export files in {settings.raw_dir} ...")
    truth = generate_demo(settings)
    cards = sum(p["cards"] for p in truth["properties"].values())
    print(f"    {len(truth['properties'])} properties, {cards:,} virtual cards.")

    dispose_engine(settings)
    if settings.db_path.exists():
        settings.db_path.unlink()
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
    return _not_built(5)


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
    from leakguard.db import dispose_engine

    dispose_engine(settings)
    db_path.unlink()
    print(f"Deleted {db_path.name}.")
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
