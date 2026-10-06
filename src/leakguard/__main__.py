"""Command line entry point: python -m leakguard <command>."""

from __future__ import annotations

import argparse
import sys

from leakguard.config import load_settings


def _not_built(phase: int) -> int:
    print(f"Not built yet. Arrives in phase {phase}.")
    return 2


def cmd_demo(args: argparse.Namespace) -> int:
    from leakguard.demo.generator import generate_demo

    settings = load_settings().model_copy(update={"data_mode": "demo"})
    print(f"Generating demo files in {settings.raw_dir} ...")
    truth = generate_demo(settings)
    cards = sum(p["cards"] for p in truth["properties"].values())
    print(f"Wrote {len(truth['properties'])} properties, {cards:,} virtual cards.")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    return _not_built(2)


def cmd_match(args: argparse.Namespace) -> int:
    return _not_built(3)


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
    db_path.unlink()
    print(f"Deleted {db_path.name}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leakguard")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Generate demo files, import and match").set_defaults(func=cmd_demo)
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
