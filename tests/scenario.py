"""Build tiny hand-made hotels as raw CSV files, then import and match them.

Tests describe reservations, postings, cards and processor rows; this writes
them in the generic PMS, Expedia and processor formats so every test goes
through the real import pipeline.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from leakguard.config import load_settings
from leakguard.db import session_scope
from leakguard.ingest.importer import import_all
from leakguard.matching.engine import run_matching

PORTFOLIO = "test_pf"
PROPERTY = "T01"

PORTFOLIOS_YAML = {
    "portfolios": [
        {
            "slug": PORTFOLIO,
            "mode": "demo",
            "name": "Test Portfolio",
            "owner_name": "Test Owner",
            "owner_type": "independent",
            "management_companies": ["Alpha Mgmt", "Beta Mgmt"],
            "properties": [
                {
                    "code": PROPERTY,
                    "name": "Test Hotel One",
                    "management_company": "Alpha Mgmt",
                    "room_count": 100,
                    "segment": "select_service",
                },
                {
                    "code": "T02",
                    "name": "Test Hotel Two",
                    "management_company": "Beta Mgmt",
                    "room_count": 200,
                    "segment": "full_service",
                },
            ],
        }
    ]
}


@dataclass
class Scenario:
    root: Path
    property_code: str = PROPERTY
    reservations: list[list[str]] = field(default_factory=list)
    payments: list[list[str]] = field(default_factory=list)
    cards: list[list[str]] = field(default_factory=list)
    txns: list[list[str]] = field(default_factory=list)

    def res(
        self,
        conf: str,
        ota_conf: str | None,
        last: str,
        arrival: date,
        departure: date,
        status: str = "Checked Out",
        amount: float = 300.0,
        channel: str = "Expedia",
        plan: str = "EXPEDIA COLLECT",
    ) -> None:
        room = round(amount / 1.1, 2)
        tax = round(amount - room, 2)
        self.reservations.append(
            [
                conf,
                ota_conf or "",
                channel,
                plan,
                last,
                arrival.isoformat(),
                departure.isoformat(),
                status,
                f"{room:.2f}",
                f"{tax:.2f}",
                f"{amount:.2f}",
                "USD",
            ]
        )

    def pay(
        self,
        conf: str,
        when: date,
        amount: float,
        last4: str | None = "1111",
        method: str = "Virtual Card - Expedia",
        reversal: bool = False,
        currency: str = "USD",
    ) -> None:
        self.payments.append(
            [
                conf,
                when.isoformat(),
                method,
                last4 or "",
                f"{amount:.2f}",
                "Y" if reversal else "N",
                currency,
            ]
        )

    def card(
        self,
        ota_conf: str,
        last4: str | None,
        amount: float,
        active: date,
        expiry: date,
        guest: str = "Pat Guest",
        arrival: date | None = None,
    ) -> None:
        self.cards.append(
            [
                ota_conf,
                guest,
                (arrival or active).isoformat(),
                last4 or "",
                f"{amount:.2f}",
                "USD",
                active.isoformat(),
                expiry.isoformat(),
            ]
        )

    def txn(
        self, when: date, last4: str, amount: float, kind: str = "SALE", reference: str = ""
    ) -> None:
        self.txns.append(
            [when.isoformat(), f"************{last4}", f"{amount:.2f}", kind, reference, "USD"]
        )

    def write(self) -> None:
        base = self.root / "data" / "demo_raw" / PORTFOLIO / self.property_code
        _csv(
            base / "pms_reservations" / "res.csv",
            [
                "Confirmation #",
                "OTA Confirmation",
                "Channel",
                "Rate Plan",
                "Guest Last Name",
                "Arrival Date",
                "Departure Date",
                "Status",
                "Room Revenue",
                "Tax",
                "Total",
                "Currency",
            ],
            self.reservations,
        )
        _csv(
            base / "pms_payments" / "pay.csv",
            [
                "Confirmation #",
                "Date",
                "Payment Method",
                "Card Last Four",
                "Amount",
                "Reversal",
                "Currency",
            ],
            self.payments,
        )
        _csv(
            base / "expedia_vcc" / "cards.csv",
            [
                "Reservation ID",
                "Guest Name",
                "Check-In",
                "Card Last 4",
                "Card Amount",
                "Currency",
                "Card Active From",
                "Card Expiry",
            ],
            self.cards,
        )
        if self.txns:
            _csv(
                base / "processor_settlement" / "settle.csv",
                [
                    "Transaction Date",
                    "Card Number",
                    "Amount",
                    "Transaction Type",
                    "Reference",
                    "Currency",
                ],
                self.txns,
            )


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def use_test_portfolio(root: Path) -> None:
    (root / "config" / "portfolios.yaml").write_text(yaml.safe_dump(PORTFOLIOS_YAML))


def run(scenario: Scenario, **overrides) -> dict[str, object]:
    """Write, import and match. Returns exceptions keyed by OTA confirmation."""
    from leakguard.models import Exception_

    scenario.write()
    settings = load_settings().model_copy(update=overrides)
    summary = import_all(settings)
    failed = [r for r in summary.reports if r.status == "failed"]
    assert not failed, failed
    run_matching(settings)
    with session_scope(settings) as s:
        rows = s.query(Exception_).all()
        out: dict[str, object] = {}
        for row in rows:
            key = row.ota_confirmation_no if row.ota_confirmation_no not in out else row.vcc_key
            out[key] = row
        return out
