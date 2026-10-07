"""Mapping wizard suggestions and the processor refund sign."""

from pathlib import Path

from leakguard.config import PROJECT_ROOT
from leakguard.ingest.mapping import apply_mapping, load_all_mappings, parse_mapping
from leakguard.ingest.readers import read_table
from leakguard.ingest.suggest import build_mapping, safe_mapping_name, suggest

MAPPINGS, _ = load_all_mappings(PROJECT_ROOT / "mappings")

GATEWAY = (
    "Gateway Settlement Detail\n"
    "Run date,2026-09-30\n"
    "\n"
    "Txn Posted,Funded On,Masked PAN,Gross Amt,Kind,Approval,Folio Ref\n"
    "09/02/2026,09/03/2026,XXXX-XXXX-XXXX-4242,$512.40,Sale,A1B2C3,F10023\n"
    '09/03/2026,09/04/2026,XXXX-XXXX-XXXX-1881,"$1,204.10",Sale,Z9Y8X7,F10024\n'
    "09/04/2026,09/05/2026,XXXX-XXXX-XXXX-4242,(45.00),Refund,Q1W2E3,F10023\n"
)


def _table(tmp_path: Path):
    path = tmp_path / "gateway.csv"
    path.write_text(GATEWAY, encoding="utf-8")
    return read_table(path)


def test_suggestions_for_unseen_processor_format(tmp_path: Path) -> None:
    table = _table(tmp_path)
    assert table.header[0] == "Txn Posted"  # found the header under the title rows
    got = {s.field: s.column for s in suggest(table, "processor_transactions", MAPPINGS)}
    assert got["transaction_date"] == "Txn Posted"
    assert got["settlement_date"] == "Funded On"
    assert got["card_last4"] == "Masked PAN"
    assert got["amount"] == "Gross Amt"
    assert got["transaction_type"] == "Kind"
    assert got["auth_code"] == "Approval"
    assert got["reference"] == "Folio Ref"
    assert got["currency"] is None


def test_wizard_mapping_round_trips_and_parses(tmp_path: Path) -> None:
    table = _table(tmp_path)
    chosen = {s.field: s.column for s in suggest(table, "processor_transactions", MAPPINGS)}
    mapping = build_mapping(
        "gw", "processor_settlement", "processor_transactions", chosen, {"currency": "USD"}
    )
    import yaml

    reloaded = parse_mapping(yaml.safe_load(mapping.to_yaml()))
    result = apply_mapping(reloaded, table, "gateway.csv")
    assert result.rows_rejected == 0
    assert [r["amount_cents"] for r in result.rows] == [51240, 120410, -4500]
    assert [r["transaction_type"] for r in result.rows] == ["sale", "sale", "refund"]
    assert {r["card_last4"] for r in result.rows} == {"4242", "1881"}


def test_negative_amount_without_type_is_a_refund(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text(
        "Transaction Date,Card Number,Amount\n2026-01-05,****1234,-25.00\n", encoding="utf-8"
    )
    mapping = next(m for m in MAPPINGS if m.name == "processor_settlement_demo")
    row = apply_mapping(mapping, read_table(path), "p.csv").rows[0]
    assert row["amount_cents"] == -2500 and row["transaction_type"] == "refund"


def test_safe_mapping_name() -> None:
    assert safe_mapping_name("Acme PMS: Reservations (v2)") == "acme_pms_reservations_v2"
