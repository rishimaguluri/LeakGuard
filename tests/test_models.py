from pathlib import Path

from sqlalchemy import inspect

from leakguard.config import load_settings
from leakguard.db import dispose_engine, get_engine


def test_all_tables_created_with_portfolio_id(isolated_env: Path) -> None:
    settings = load_settings()
    engine = get_engine(settings)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "portfolios",
        "management_companies",
        "properties",
        "source_files",
        "reservations",
        "pms_payments",
        "vcc_records",
        "processor_transactions",
        "exceptions",
        "exception_events",
        "import_issues",
    }
    assert expected <= tables
    for table in expected - {"portfolios"}:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "portfolio_id" in columns, table
    dispose_engine(settings)


def test_money_columns_are_integer_cents(isolated_env: Path) -> None:
    settings = load_settings()
    inspector = inspect(get_engine(settings))
    for table in ("reservations", "pms_payments", "vcc_records", "processor_transactions"):
        for col in inspector.get_columns(table):
            if col["name"].endswith("_cents"):
                assert "INT" in str(col["type"]).upper()
    dispose_engine(settings)
