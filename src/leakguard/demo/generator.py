"""Writes fake raw export files for a fictional portfolio.

The files look like real PMS, OTA and processor exports: different column
names per source, several date formats, a title block above one header,
a few malformed rows, and one file with full card numbers. They go through
the normal import pipeline. Nothing here touches the database.

Leaks are injected at per-management-company rates so the operator
scorecard tells a story. Everything injected is recorded in a ground-truth
file (_demo_truth.json) that tests compare the matcher's output against.

Deterministic: the same seed always produces byte-identical files.
"""

from __future__ import annotations

import csv
import json
import math
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

from leakguard.config import PortfolioConfig, PropertyConfig, Settings, load_portfolios
from leakguard.ingest.normalize import confirmation
from leakguard.matching.rules import (
    ReservationFacts,
    VccFacts,
    fuzzy_criteria_met,
    fuzzy_pool_eligible,
)

DEFAULT_SEED = 20260930
TRUTH_FILE = "_demo_truth.json"

FIRST_NAMES = (
    "James Mary Robert Patricia John Jennifer Michael Linda David Elizabeth William Barbara "
    "Richard Susan Joseph Jessica Thomas Sarah Charles Karen Daniel Lisa Matthew Nancy "
    "Anthony Betty Mark Sandra Steven Ashley Andrew Emily Kevin Donna Brian Michelle "
    "Priya Wei Carlos Sofia Ahmed Mei Diego Aisha"
).split()

SURNAMES = (
    "Anderson Baker Barnes Bell Bennett Brooks Brown Bryant Butler Campbell Carter Clark "
    "Coleman Collins Cook Cooper Cox Cruz Davis Diaz Edwards Evans Fisher Flores Foster "
    "Garcia Gonzales Gray Green Griffin Hall Harris Hayes Henderson Hernandez Hill Howard "
    "Hughes Jackson James Jenkins Johnson Jones Kelly Kim King Lee Lewis Long Lopez Martin "
    "Martinez Miller Mitchell Moore Morgan Morris Murphy Myers Nelson Nguyen Ortiz Parker "
    "Patel Perez Perry Peterson Phillips Powell Price Ramirez Reed Reyes Richardson Rivera "
    "Roberts Robinson Rodriguez Rogers Ross Russell Sanchez Sanders Scott Simmons Smith "
    "Stewart Sullivan Taylor Thomas Thompson Torres Turner Walker Ward Washington Watson "
    "White Williams Wilson Wood Wright Young Alvarez Castillo Chavez Delgado Dominguez "
    "Fernandez Gutierrez Herrera Jimenez Mendoza Morales Ramos Romero Ruiz Silva Soto "
    "Vargas Vasquez Abbott Acosta Adkins Aguilar Albright Aldridge Alston Ashby Atwood "
    "Ballard Barlow Bartlett Beckett Blackwell Blevins Bowen Bradshaw Brennan Briggs "
    "Buchanan Calloway Carmichael Chandler Chatham Conley Corbin Crawford Dalton Danvers "
    "Davenport Dempsey Donovan Draper Dunbar Eastman Ellison Emerson Fairbanks Farrow "
    "Fitzgerald Fleming Fontaine Garrison Gallagher Goodwin Hadley Halvorsen Hargrove "
    "Hawthorne Holloway Ingram Kendrick Kingsley Lancaster Langford Lockhart Macintyre "
    "Mansfield Merriweather Montgomery Northcott Okafor Pemberton Prescott Quinlan "
    "Radcliffe Rutherford Sheffield Stanton Thornton Underwood Vandermeer Whitfield "
    "Winslow Yamamoto Zielinski Okonkwo Lindqvist Castellano Nakamura Abernathy"
).split() + ["O'Brien", "O'Connor", "Smith-Jones", "Muñoz", "Peña", "McAllister"]

DOW_FACTOR = {
    "select_service": (1.05, 1.15, 1.15, 1.10, 0.95, 0.80, 0.80),
    "full_service": (0.90, 0.95, 1.00, 1.00, 1.10, 1.20, 0.85),
}
BASE_OCC = {"select_service": 0.72, "full_service": 0.70}
ADR_RANGE = {"select_service": (135, 175), "full_service": (225, 305)}
LOS_WEIGHTS = {
    "select_service": ((1, 40), (2, 30), (3, 15), (4, 8), (5, 4), (7, 3)),
    "full_service": ((1, 28), (2, 32), (3, 20), (4, 10), (5, 5), (7, 5)),
}
OTA_COMMISSION = 0.18


@dataclass(frozen=True)
class LeakRates:
    uncharged: float
    under: float
    over: float
    unsettled: float


LEAK_PROFILES: dict[str, LeakRates] = {
    "Lakeview Hospitality": LeakRates(uncharged=0.017, under=0.006, over=0.002, unsettled=0.003),
    "Northpoint Hotel Group": LeakRates(uncharged=0.040, under=0.014, over=0.004, unsettled=0.007),
}
DEFAULT_LEAK = LeakRates(uncharged=0.03, under=0.01, over=0.003, unsettled=0.005)

# Rare cases, as a share of virtual cards.
CANCELLED_REVIEW_RATE = 0.004
CANCELLED_CHARGED_RATE = 0.002
DUPLICATE_RATE = 0.0008
NO_MATCH_RATE = 0.001
FUZZY_RATE = 0.003
SPLIT_POSTING_RATE = 0.03
REVERSAL_RATE = 0.01
MISSING_LAST4_RATE = 0.02  # generic PMS only

# Story beats, per property code.
BIG_EXPIRED = {"AUS01": 2, "SAV01": 2, "NSH01": 1}
EXPIRING_THIS_WEEK = {"AUS01": 2, "DEN01": 2, "SAV01": 2, "RAL01": 1, "CMH01": 1, "NSH01": 1}
# One coverage gap so the Data sources page has something to flag.
SOURCE_CUTOFF = {("DEN01", "booking_vcc"): date(2026, 7, 31)}
MALFORMED = {
    ("CMH01", "pms_reservations"): 2,
    ("AUS01", "pms_reservations"): 2,
    ("DEN01", "pms_reservations"): 1,
    ("IND01", "pms_payments"): 1,
    ("SAV01", "pms_payments"): 2,
    ("RAL01", "expedia_vcc"): 1,
    ("NSH01", "booking_vcc"): 1,
    ("CLT01", "processor_settlement"): 1,
}
FULL_CARD_NUMBER_PROPERTY = "NSH01"
SEMICOLON_PROPERTY = "CLT01"
SPLIT_EXPEDIA_PROPERTY = "CMH01"


@dataclass
class GenConfig:
    seed: int = DEFAULT_SEED
    as_of: date = date(2026, 9, 30)
    months: int = 12
    scale: float = 1.0
    future_days: int = 21
    non_ota_sample: float = 0.05

    @property
    def start(self) -> date:
        first_of_month = self.as_of.replace(day=1)
        year, month = first_of_month.year, first_of_month.month - (self.months - 1)
        while month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)


@dataclass
class Res:
    pms_conf: str
    ota_conf: str | None
    pms_ota_conf: str | None  # what the PMS shows, may be blank or mistyped
    channel: str
    payment_model: str
    first: str
    last: str
    arrival: date
    departure: date
    status: str
    room_cents: int
    tax_cents: int
    incidentals_cents: int = 0
    exported: bool = True
    rate_label: str = ""

    @property
    def nights(self) -> int:
        return (self.departure - self.arrival).days

    @property
    def stay_cents(self) -> int:
        return self.room_cents + self.tax_cents

    @property
    def folio_cents(self) -> int:
        return self.stay_cents + self.incidentals_cents


@dataclass
class Card:
    ota: str
    conf: str
    last4: str
    amount: int
    activation: date
    expiry: date
    res: Res
    guest_last: str
    outcome: str = "normal"  # normal/uncharged/under/over/unsettled/none
    truth: str = ""
    match: str = "exact"
    full_number: str = ""


@dataclass
class Posting:
    pms_conf: str
    posting_date: date
    method: str  # vcc_expedia / vcc_booking / visa / mastercard / amex / cash
    last4: str | None
    amount: int
    reversal: bool = False


@dataclass
class Txn:
    txn_date: date
    settle_date: date
    brand: str
    last4: str
    amount: int  # positive in the file, type gives the sign
    txn_type: str
    auth: str
    reference: str


@dataclass
class PropertyData:
    prop: PropertyConfig
    reservations: list[Res] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    postings: list[Posting] = field(default_factory=list)
    txns: list[Txn] = field(default_factory=list)


# Helpers ----------------------------------------------------------------------


def _weighted(rng: random.Random, pairs: tuple[tuple[int, int], ...]) -> int:
    values, weights = zip(*pairs, strict=True)
    return rng.choices(values, weights=weights)[0]


def _poisson_like(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))


def _count(rate_or_n: float, base: int, scale: float, minimum: int = 1) -> int:
    return max(minimum, round(rate_or_n * base * scale)) if base else 0


def _season(d: date) -> float:
    """Peaks in July, low in January."""
    return 1 + 0.16 * math.sin(2 * math.pi * (d.month - 4) / 12)


def _variant(rng: random.Random, name: str) -> str:
    """A small spelling difference a fuzzy matcher should still accept."""
    if "'" in name or "-" in name:
        return name.replace("'", "").replace("-", " ")
    if len(name) >= 7:
        i = rng.randint(1, len(name) - 2)
        return name[:i] + name[i + 1 :]
    return name.upper()


def _transpose(rng: random.Random, conf: str) -> str:
    i = rng.randint(0, len(conf) - 2)
    swapped = conf[:i] + conf[i + 1] + conf[i] + conf[i + 2 :]
    return swapped if swapped != conf else conf[:-1] + str((int(conf[-1]) + 1) % 10)


# Reservations -------------------------------------------------------------------


class _PropertyGenerator:
    def __init__(self, prop: PropertyConfig, index: int, cfg: GenConfig, rates: LeakRates):
        self.prop = prop
        self.cfg = cfg
        self.rates = rates
        self.rng = random.Random(f"{cfg.seed}-{prop.code}")
        self.segment = prop.segment or "select_service"
        self.data = PropertyData(prop)
        self.ota_share = self.rng.uniform(0.25, 0.45)
        self.adr_base = self.rng.uniform(*ADR_RANGE[self.segment])
        self.tax_rate = self.rng.uniform(0.12, 0.17)
        self.pms_counter = (index + 1) * 10_000_000 + self.rng.randint(100_000, 900_000)
        self.used_ota_confs: set[str] = set()
        self.last4_seen: dict[str, date] = {}
        self.operator_jitter = self.rng.uniform(0.85, 1.15)

    # ids
    def next_pms_conf(self) -> str:
        self.pms_counter += self.rng.randint(1, 3)
        return str(self.pms_counter)

    def new_ota_conf(self, ota: str) -> str:
        first = "7" if ota == "expedia" else "4"
        while True:
            conf = first + "".join(str(self.rng.randint(0, 9)) for _ in range(9))
            if conf not in self.used_ota_confs:
                self.used_ota_confs.add(conf)
                return conf

    def new_last4(self, when: date) -> str:
        while True:
            last4 = f"{self.rng.randint(0, 9999):04d}"
            seen = self.last4_seen.get(last4)
            if seen is None or abs((when - seen).days) > 45:
                self.last4_seen[last4] = when
                return last4

    def status_for(self, arrival: date, departure: date, cancelled: bool) -> str:
        as_of = self.cfg.as_of
        if cancelled:
            return "cancelled"
        if arrival <= as_of and self.rng.random() < 0.015:
            return "no_show"
        if departure <= as_of:
            return "checked_out"
        if arrival <= as_of:
            return "in_house"
        return "reserved"

    def make_reservation(
        self,
        arrival: date,
        channel: str,
        payment_model: str,
        nights: int | None = None,
        rate_dollars: float | None = None,
        status: str | None = None,
    ) -> Res:
        rng = self.rng
        nights = nights or _weighted(rng, LOS_WEIGHTS[self.segment])
        departure = arrival + timedelta(days=nights)
        weekend = 1.08 if arrival.weekday() in (4, 5) else 1.0
        rate = rate_dollars or self.adr_base * _season(arrival) * weekend * rng.uniform(0.85, 1.2)
        if payment_model == "ota_collect":
            rate *= 1 - OTA_COMMISSION
        room = round(rate * nights * 100)
        tax = round(room * self.tax_rate)
        cancel_rate = 0.08 if channel in ("expedia", "booking") else 0.05
        if status is None:
            status = self.status_for(arrival, departure, rng.random() < cancel_rate)
        ota_conf = self.new_ota_conf(channel) if channel in ("expedia", "booking") else None
        incidentals = round(rng.uniform(15, 140) * 100) if rng.random() < 0.3 else 0
        return Res(
            pms_conf=self.next_pms_conf(),
            ota_conf=ota_conf,
            pms_ota_conf=ota_conf,
            channel=channel,
            payment_model=payment_model,
            first=rng.choice(FIRST_NAMES),
            last=rng.choice(SURNAMES),
            arrival=arrival,
            departure=departure,
            status=status,
            room_cents=room,
            tax_cents=tax,
            incidentals_cents=incidentals,
        )

    def generate_reservations(self) -> None:
        cfg, rng = self.cfg, self.rng
        rooms = self.prop.room_count or 120
        avg_los = 1.9 if self.segment == "select_service" else 2.3
        day = cfg.start
        end = cfg.as_of + timedelta(days=cfg.future_days)
        while day <= end:
            occ = BASE_OCC[self.segment] * _season(day) * DOW_FACTOR[self.segment][day.weekday()]
            lam = rooms * min(occ, 0.97) / avg_los * cfg.scale
            if day > cfg.as_of:
                lam *= 0.55  # bookings still to come
            for _ in range(_poisson_like(rng, lam)):
                self._one_booking(day)
            day += timedelta(days=1)

    def _one_booking(self, arrival: date) -> None:
        rng = self.rng
        if rng.random() < self.ota_share:
            channel = "expedia" if rng.random() < 0.55 else "booking"
            collect_share = 0.62 if channel == "expedia" else 0.50
            model = "ota_collect" if rng.random() < collect_share else "hotel_collect"
        else:
            if rng.random() > self.cfg.non_ota_sample:
                return
            channel = "direct" if rng.random() < 0.8 else "other"
            model = "hotel_collect" if channel == "direct" else "unknown"
        self.data.reservations.append(self.make_reservation(arrival, channel, model))

    # Cards ------------------------------------------------------------------

    def make_card(
        self, res: Res, expiry_days: int | None = None, amount: int | None = None
    ) -> Card:
        activation = res.arrival
        expiry = activation + timedelta(days=expiry_days or self.rng.randint(30, 180))
        return Card(
            ota=res.channel,
            conf=res.ota_conf or "",
            last4=self.new_last4(activation),
            amount=amount if amount is not None else res.stay_cents,
            activation=activation,
            expiry=expiry,
            res=res,
            guest_last=res.last,
        )

    def generate_cards(self) -> None:
        rng, rates = self.rng, self.rates
        horizon = self.cfg.as_of + timedelta(days=self.cfg.future_days)
        for res in self.data.reservations:
            if res.payment_model != "ota_collect" or res.arrival > horizon:
                continue
            if res.status in ("cancelled", "no_show"):
                continue  # most cancellations carry no card; injected separately
            card = self.make_card(res)
            if res.status in ("checked_out", "in_house"):
                roll = rng.random() / self.operator_jitter
                cut = 0.0
                for outcome, rate in (
                    ("uncharged", rates.uncharged),
                    ("under", rates.under),
                    ("over", rates.over),
                    ("unsettled", rates.unsettled),
                ):
                    cut += rate
                    if roll < cut:
                        card.outcome = outcome
                        break
            else:
                card.outcome = "none"
            self.data.cards.append(card)

    def inject_story_cases(self) -> None:
        """Rare cases and story beats, added on top of the base leak rates."""
        code, cfg, rng = self.prop.code, self.cfg, self.rng
        base = len(self.data.cards)
        stayed = [
            c for c in self.data.cards if c.res.status == "checked_out" and c.outcome == "normal"
        ]

        for _ in range(BIG_EXPIRED.get(code, 0)):
            arrival = cfg.as_of - timedelta(days=rng.randint(180, 300))
            res = self.make_reservation(
                arrival,
                rng.choice(["expedia", "booking"]),
                "ota_collect",
                nights=rng.randint(8, 14),
                rate_dollars=rng.uniform(480, 650),
                status="checked_out",
            )
            self.data.reservations.append(res)
            card = self.make_card(res, expiry_days=rng.randint(30, 60))
            card.outcome = "uncharged"
            self.data.cards.append(card)

        for i in range(EXPIRING_THIS_WEEK.get(code, 0)):
            arrival = cfg.as_of - timedelta(days=rng.randint(25, 60))
            res = self.make_reservation(
                arrival,
                rng.choice(["expedia", "booking"]),
                "ota_collect",
                nights=rng.randint(2, 5),
                status="checked_out",
            )
            self.data.reservations.append(res)
            days_left = 0 if (code == "AUS01" and i == 0) else rng.randint(1, 7)
            card = self.make_card(res)
            card.expiry = cfg.as_of + timedelta(days=days_left)
            card.outcome = "uncharged"
            self.data.cards.append(card)

        for _ in range(_count(CANCELLED_REVIEW_RATE, base, 1.0)):
            self._cancelled_card(charged=False)
        for _ in range(_count(CANCELLED_CHARGED_RATE, base, 1.0)):
            self._cancelled_card(charged=True)

        picks = rng.sample(stayed, min(len(stayed), _count(DUPLICATE_RATE, base, 1.0)))
        for first in picks:
            dup = self.make_card(first.res)
            dup.activation = first.activation + timedelta(days=rng.randint(1, 3))
            dup.expiry = dup.activation + timedelta(days=rng.randint(30, 180))
            dup.outcome = "uncharged"
            dup.truth = "DUPLICATE_VCC"
            self.data.cards.append(dup)

        remaining = [c for c in stayed if c not in picks]
        no_match = rng.sample(remaining, min(len(remaining), _count(NO_MATCH_RATE, base, 1.0)))
        for card in no_match:
            card.res.exported = False
            card.outcome = "uncharged"
            card.truth = "NO_PMS_MATCH"
            card.match = "none"

        remaining = [c for c in remaining if c not in no_match]
        fuzzy = rng.sample(remaining, min(len(remaining), _count(FUZZY_RATE, base, 1.0)))
        for card in fuzzy:
            card.match = "fuzzy"
            card.res.pms_ota_conf = None if rng.random() < 0.5 else _transpose(rng, card.conf)
            card.guest_last = _variant(rng, card.res.last)

    def _cancelled_card(self, charged: bool) -> None:
        rng, cfg = self.rng, self.cfg
        arrival = cfg.as_of - timedelta(days=rng.randint(10, 300))
        status = "cancelled" if rng.random() < 0.6 else "no_show"
        res = self.make_reservation(
            arrival, rng.choice(["expedia", "booking"]), "ota_collect", status=status
        )
        self.data.reservations.append(res)
        penalty = round(res.stay_cents / max(res.nights, 1))
        card = self.make_card(res, amount=penalty)
        card.outcome = "normal" if charged else "uncharged"
        self.data.cards.append(card)

    # Payments and settlements ---------------------------------------------------

    def _txn_for(self, posting: Posting, brand: str, last4: str, reference: str) -> None:
        if posting.posting_date > self.cfg.as_of:
            return
        self.data.txns.append(
            Txn(
                txn_date=posting.posting_date,
                settle_date=posting.posting_date + timedelta(days=self.rng.randint(1, 2)),
                brand=brand,
                last4=last4,
                amount=abs(posting.amount),
                txn_type="REFUND" if posting.amount < 0 else "SALE",
                auth=f"{self.rng.randint(0, 999999):06d}",
                reference=reference,
            )
        )

    def generate_payments(self) -> None:
        rng = self.rng
        is_opera = (self.prop.pms_name or "").lower() == "opera"
        for card in self.data.cards:
            if not card.res.exported or card.outcome in ("uncharged", "none"):
                continue
            amount = card.amount
            if card.outcome == "under":
                amount -= max(1000, round(card.amount * rng.uniform(0.15, 0.5)))
            elif card.outcome == "over":
                amount += max(1000, round(card.amount * rng.uniform(0.05, 0.3)))
            method = f"vcc_{card.ota}"
            missing = not is_opera and rng.random() < MISSING_LAST4_RATE
            posting_last4 = None if missing else card.last4
            when = card.activation
            parts = [amount]
            if card.outcome == "normal" and rng.random() < SPLIT_POSTING_RATE and amount > 2000:
                first = round(amount * rng.uniform(0.3, 0.7))
                parts = [first, amount - first]
            postings = [Posting(card.res.pms_conf, when, method, posting_last4, p) for p in parts]
            if card.outcome == "normal" and rng.random() < REVERSAL_RATE:
                postings = [
                    Posting(card.res.pms_conf, when, method, posting_last4, amount),
                    Posting(card.res.pms_conf, when, method, posting_last4, -amount, True),
                    Posting(
                        card.res.pms_conf, when + timedelta(days=1), method, posting_last4, amount
                    ),
                ]
            reference = card.res.pms_conf if is_opera else ""
            for posting in postings:
                self.data.postings.append(posting)
                if card.outcome != "unsettled":
                    self._txn_for(posting, "MASTERCARD", card.last4, reference)

        for res in self.data.reservations:
            if not res.exported or res.payment_model == "ota_collect":
                continue
            if res.status != "checked_out":
                continue
            method = rng.choice(["visa", "visa", "mastercard", "amex", "cash"])
            last4 = None if method == "cash" else f"{rng.randint(0, 9999):04d}"
            posting = Posting(res.pms_conf, res.departure, method, last4, res.folio_cents)
            self.data.postings.append(posting)
            if last4:
                reference = res.pms_conf if is_opera else ""
                self._txn_for(posting, method.upper(), last4, reference)

    # Truth -------------------------------------------------------------------

    def label_truth(self) -> None:
        as_of = self.cfg.as_of
        for card in self.data.cards:
            if card.truth:
                continue
            res = card.res
            if res.status in ("cancelled", "no_show"):
                card.truth = "CANCELLED_REVIEW" if card.outcome == "uncharged" else "CHARGED_OK"
            elif card.outcome == "none":
                card.truth = "NOT_YET_DUE"
            elif card.outcome == "uncharged":
                card.truth = "EXPIRED_UNCHARGED" if card.expiry < as_of else "UNCHARGED"
            else:
                card.truth = {
                    "normal": "CHARGED_OK",
                    "under": "UNDERCHARGED",
                    "over": "OVERCHARGED",
                    "unsettled": "CHARGED_NOT_SETTLED",
                }[card.outcome]

    def make_injected_cases_unambiguous(self, settings: Settings) -> None:
        """Re-draw names until each fuzzy case has exactly one candidate and
        each no-match case has none, using the matcher's own criteria."""
        rng = self.rng
        exported = [r for r in self.data.reservations if r.exported]
        card_confs = {(c.ota, confirmation(c.conf)) for c in self.data.cards}

        def facts(r: Res) -> ReservationFacts:
            return ReservationFacts(
                id=id(r),
                pms_confirmation_no=r.pms_conf,
                ota_confirmation_no=r.pms_ota_conf,
                channel=r.channel,
                payment_model=r.payment_model,
                status=r.status,
                arrival_date=r.arrival,
                departure_date=r.departure,
                expected_cents=r.stay_cents,
                guest_last_name=r.last,
            )

        # Pool membership does not depend on names, so build it once.
        pools = {
            ota: [
                r
                for r in exported
                if fuzzy_pool_eligible(facts(r), ota)
                and (ota, confirmation(r.pms_ota_conf)) not in card_confs
            ]
            for ota in ("expedia", "booking")
        }
        window = settings.fuzzy_arrival_window_days

        def candidates(card: Card) -> list[Res]:
            vcc = VccFacts(
                ota=card.ota,
                ota_confirmation_no=card.conf,
                card_last4=card.last4,
                vcc_amount_cents=card.amount,
                currency="USD",
                activation_date=card.activation,
                expiry_date=card.expiry,
                guest_last_name=card.guest_last,
                arrival_date=card.res.arrival,
            )
            return [
                r
                for r in pools[card.ota]
                if abs((r.arrival - card.res.arrival).days) <= window
                and fuzzy_criteria_met(
                    vcc,
                    facts(r),
                    settings.fuzzy_name_min_score,
                    window,
                    settings.fuzzy_amount_tolerance_pct,
                )
            ]

        tricky = [c for c in self.data.cards if c.match in ("fuzzy", "none")]
        for _ in range(50):
            changed = False
            for card in tricky:
                found = candidates(card)
                ok = found == [card.res] if card.match == "fuzzy" else not found
                if ok:
                    continue
                changed = True
                card.res.last = rng.choice(SURNAMES)
                card.guest_last = (
                    _variant(rng, card.res.last) if card.match == "fuzzy" else rng.choice(SURNAMES)
                )
            if not changed:
                return
        raise RuntimeError(f"Could not make injected cases unambiguous for {self.prop.code}")


# Writers ------------------------------------------------------------------------


def _write_csv(
    path: Path,
    header: list[str],
    rows: list[list[object]],
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=encoding) as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(rows)


def _write_xlsx(
    path: Path,
    header: list[str],
    rows: list[list[object]],
    title_rows: list[str] | None = None,
    sheet: str = "Sheet1",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet)
    for line in title_rows or []:
        ws.append([line])
    if title_rows:
        ws.append([])
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)
    # openpyxl stamps the current time into docProps; pin it so output is reproducible.
    _pin_xlsx_timestamp(path)


def _pin_xlsx_timestamp(path: Path) -> None:
    import zipfile

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "docProps/core.xml":
                import re

                data = re.sub(
                    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", b"2026-01-01T00:00:00Z", data
                )
            info = zipfile.ZipInfo(item.filename, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, data)
    tmp.replace(path)


def _opera_date(d: date) -> str:
    return d.strftime("%d-%b-%y").upper()


def _us_date(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _eu_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _dollars(cents: int, symbol: bool = False) -> str:
    text = f"{abs(cents) / 100:,.2f}" if symbol else f"{abs(cents) / 100:.2f}"
    if symbol:
        text = "$" + text
    return ("-" + text) if cents < 0 else text


OPERA_STATUS = {
    "checked_out": "CHECKED OUT",
    "in_house": "IN HOUSE",
    "cancelled": "CANCELLED",
    "no_show": "NO SHOW",
    "reserved": "RESERVED",
}
GENERIC_STATUS = {
    "checked_out": "Checked Out",
    "in_house": "In House",
    "cancelled": "Cancelled",
    "no_show": "No Show",
    "reserved": "Confirmed",
}
OPERA_METHOD = {
    "vcc_expedia": "VIRTUAL CARD EXPEDIA",
    "vcc_booking": "VIRTUAL CARD BDC",
    "visa": "VISA",
    "mastercard": "MASTERCARD",
    "amex": "AMEX",
    "cash": "CASH",
}
GENERIC_METHOD = {
    "vcc_expedia": "Virtual Card - Expedia",
    "vcc_booking": "Virtual Card - Booking.com",
    "visa": "Visa",
    "mastercard": "Mastercard",
    "amex": "American Express",
    "cash": "Cash",
}


class _Writer:
    def __init__(self, data: PropertyData, folder: Path, cfg: GenConfig):
        self.data = data
        self.prop = data.prop
        self.folder = folder
        self.cfg = cfg
        self.rng = random.Random(f"{cfg.seed}-{self.prop.code}-writer")
        self.rejects: dict[str, int] = {}
        self.is_opera = (self.prop.pms_name or "").lower() == "opera"
        self.period = f"{cfg.start:%Y-%m}_to_{cfg.as_of:%Y-%m}"

    def _malformed(self, source: str) -> int:
        return MALFORMED.get((self.prop.code, source), 0)

    def _record(self, path: Path, count: int) -> None:
        if count:
            self.rejects[path.relative_to(self.folder.parent.parent).as_posix()] = count

    def write_all(self) -> None:
        self.write_reservations()
        self.write_payments()
        self.write_expedia()
        self.write_booking()
        self.write_processor()

    def write_reservations(self) -> None:
        rows: list[list[object]] = []
        reservations = sorted(
            (r for r in self.data.reservations if r.exported), key=lambda r: (r.arrival, r.pms_conf)
        )
        if self.is_opera:
            header = [
                "Conf No",
                "External Ref",
                "Source Code",
                "Payment Method",
                "Rate Code",
                "Guest Name",
                "Arrival",
                "Departure",
                "Nights",
                "Res Status",
                "Room Type",
                "Room Rev",
                "Tax Amt",
                "Total Rev",
                "Currency",
            ]
            for r in reservations:
                ext = ""
                if r.pms_ota_conf:
                    ext = ("EXP-" if r.channel == "expedia" else "BDC-") + r.pms_ota_conf
                source = {
                    "expedia": "EXPEDIA",
                    "booking": "BOOKING.COM",
                    "direct": "DIRECT",
                    "other": "GDS",
                }[r.channel]
                method = {
                    "ota_collect": "OTA COLLECT VCC",
                    "hotel_collect": "HOTEL COLLECT",
                    "unknown": "CREDIT CARD",
                }[r.payment_model]
                if r.channel == "direct":
                    method = "CREDIT CARD"
                rows.append(
                    [
                        r.pms_conf,
                        ext,
                        source,
                        method,
                        self.rng.choice(["BAR", "RACK", "OTA1", "PKG"]),
                        f"{r.last.upper()}, {r.first.upper()}",
                        _opera_date(r.arrival),
                        _opera_date(r.departure),
                        r.nights,
                        OPERA_STATUS[r.status],
                        self.rng.choice(["KING", "QQ", "STE", "DBL"]),
                        _dollars(r.room_cents),
                        _dollars(r.tax_cents),
                        _dollars(r.folio_cents),
                        "USD",
                    ]
                )
            for i in range(self._malformed("pms_reservations")):
                rows.append(
                    [
                        "" if i == 0 else "99999",
                        "",
                        "DIRECT",
                        "CREDIT CARD",
                        "BAR",
                        "TEST, ROW",
                        "TBD",
                        "TBD",
                        "",
                        "RESERVED",
                        "KING",
                        "0",
                        "0",
                        "0",
                        "USD",
                    ]
                )
        else:
            header = [
                "Confirmation #",
                "OTA Confirmation",
                "Channel",
                "Rate Plan",
                "Guest Last Name",
                "Guest First Name",
                "Arrival Date",
                "Departure Date",
                "Status",
                "Room Revenue",
                "Tax",
                "Total",
                "Currency",
                "Market Segment",
                "Room Type",
            ]
            for r in reservations:
                channel = {
                    "expedia": "Expedia",
                    "booking": "Booking.com",
                    "direct": "Direct",
                    "other": "GDS",
                }[r.channel]
                if r.channel == "expedia":
                    plan = (
                        "EXPEDIA COLLECT"
                        if r.payment_model == "ota_collect"
                        else "EXPEDIA HOTEL COLLECT"
                    )
                elif r.channel == "booking":
                    plan = (
                        "PAYMENTS BY BOOKING"
                        if r.payment_model == "ota_collect"
                        else "BOOKING PAY AT HOTEL"
                    )
                else:
                    plan = self.rng.choice(["BAR", "AAA", "CORP", "GOV"])
                iso = self.rng.random() < 0.05
                fmt = (lambda d: d.isoformat()) if iso else _us_date
                rows.append(
                    [
                        r.pms_conf,
                        r.pms_ota_conf or "",
                        channel,
                        plan,
                        r.last,
                        r.first,
                        fmt(r.arrival),
                        fmt(r.departure),
                        GENERIC_STATUS[r.status],
                        _dollars(r.room_cents, True),
                        _dollars(r.tax_cents, True),
                        _dollars(r.folio_cents, True),
                        "USD",
                        self.rng.choice(["Transient", "Leisure", "Corporate"]),
                        self.rng.choice(["King", "Two Queens", "Suite"]),
                    ]
                )
            for i in range(self._malformed("pms_reservations")):
                rows.append(
                    [
                        "" if i == 0 else "88888",
                        "",
                        "Direct",
                        "BAR",
                        "Test",
                        "Row",
                        "TBD",
                        "TBD",
                        "Confirmed",
                        "$0.00",
                        "$0.00",
                        "$0.00",
                        "USD",
                        "",
                        "",
                    ]
                )
        delimiter = ";" if self.prop.code == SEMICOLON_PROPERTY else ","
        encoding = "cp1252" if self.prop.code == SEMICOLON_PROPERTY else "utf-8"
        path = self.folder / "pms_reservations" / f"reservations_{self.period}.csv"
        _write_csv(path, header, rows, delimiter, encoding)
        self._record(path, self._malformed("pms_reservations"))

    def write_payments(self) -> None:
        rows: list[list[object]] = []
        postings = sorted(self.data.postings, key=lambda p: (p.posting_date, p.pms_conf))
        if self.is_opera:
            header = [
                "Conf No",
                "Trx Date",
                "Trx Code",
                "Description",
                "Card No",
                "Amount",
                "Currency",
                "Cashier",
            ]
            for p in postings:
                desc = OPERA_METHOD[p.method]
                if p.amount < 0:
                    desc = "REVERSAL " + desc
                card = f"XXXXXXXXXXXX{p.last4}" if p.last4 else ""
                rows.append(
                    [
                        p.pms_conf,
                        _opera_date(p.posting_date),
                        9000 + len(desc),
                        desc,
                        card,
                        _dollars(p.amount),
                        "USD",
                        self.rng.choice(["NA1", "FD2", "AUD"]),
                    ]
                )
            for _ in range(self._malformed("pms_payments")):
                rows.append(
                    ["12345", _opera_date(self.cfg.as_of), 9001, "VISA", "", "N/A", "USD", "NA1"]
                )
        else:
            header = [
                "Confirmation #",
                "Date",
                "Payment Method",
                "Card Last Four",
                "Amount",
                "Reversal",
                "User",
            ]
            for p in postings:
                rows.append(
                    [
                        p.pms_conf,
                        _us_date(p.posting_date),
                        GENERIC_METHOD[p.method],
                        p.last4 or "",
                        _dollars(abs(p.amount), True),
                        "Y" if p.amount < 0 else "N",
                        self.rng.choice(["fd01", "na02", "acct"]),
                    ]
                )
            for _ in range(self._malformed("pms_payments")):
                rows.append(["12345", _us_date(self.cfg.as_of), "Visa", "1111", "N/A", "N", "fd01"])
        delimiter = ";" if self.prop.code == SEMICOLON_PROPERTY else ","
        encoding = "cp1252" if self.prop.code == SEMICOLON_PROPERTY else "utf-8"
        path = self.folder / "pms_payments" / f"payments_{self.period}.csv"
        _write_csv(path, header, rows, delimiter, encoding)
        self._record(path, self._malformed("pms_payments"))

    def _cards(self, ota: str) -> list[Card]:
        cutoff = SOURCE_CUTOFF.get((self.prop.code, f"{ota}_vcc"))
        cards = [c for c in self.data.cards if c.ota == ota]
        if cutoff:
            cards = [c for c in cards if c.activation <= cutoff]
        return sorted(cards, key=lambda c: (c.activation, c.conf, c.last4))

    def write_expedia(self) -> None:
        full_numbers = self.prop.code == FULL_CARD_NUMBER_PROPERTY
        card_col = "Card Number" if full_numbers else "Card Last 4"
        header = [
            "Reservation ID",
            "Guest Name",
            "Check-In",
            "Check-Out",
            card_col,
            "Card Amount",
            "Currency",
            "Card Active From",
            "Card Expiry",
            "Card Status",
            "Commission",
        ]
        rows: list[list[object]] = []
        for c in self._cards("expedia"):
            # Fake Luhn-valid number ending in the card's last 4.
            number = self._full_number(c.last4) if full_numbers else ""
            status = "Expired" if c.expiry < self.cfg.as_of else "Active"
            rows.append(
                [
                    c.conf,
                    f"{c.res.first} {c.guest_last}",
                    c.res.arrival.isoformat(),
                    c.res.departure.isoformat(),
                    number or c.last4,
                    _dollars(c.amount, True),
                    "USD",
                    c.activation.isoformat(),
                    c.expiry.isoformat(),
                    status,
                    _dollars(round(c.amount * OTA_COMMISSION / (1 - OTA_COMMISSION)), True),
                ]
            )
        malformed = self._malformed("expedia_vcc")
        for _ in range(malformed):
            rows.append(
                [
                    "7000000001",
                    "Test Row",
                    "2026-01-05",
                    "2026-01-06",
                    "0000",
                    "$100.00",
                    "USD",
                    "2026-01-05",
                    "2026-02-31",
                    "Active",
                    "$18.00",
                ]
            )
        base = self.folder / "expedia_vcc"
        if self.prop.code == SPLIT_EXPEDIA_PROPERTY:
            mid = self.cfg.start + timedelta(days=182)
            mid = mid.replace(day=1)
            last_of_first = mid - timedelta(days=1)
            first = [r for r in rows if r[7] < mid.isoformat()]
            second = [r for r in rows if r[7] >= mid.isoformat()]
            _write_csv(
                base / f"expedia_cards_{self.cfg.start:%Y-%m}_to_{last_of_first:%Y-%m}.csv",
                header,
                first,
            )
            _write_xlsx(
                base / f"expedia_cards_{mid:%Y-%m}_to_{self.cfg.as_of:%Y-%m}.xlsx",
                header,
                second,
                sheet="Virtual Cards",
            )
        else:
            path = base / f"expedia_virtual_cards_{self.period}.csv"
            _write_csv(path, header, rows)
            self._record(path, malformed)

    def _full_number(self, last4: str) -> str:
        """Fake 16 digit Mastercard-style number, Luhn-valid, ending in last4."""
        while True:
            body = (
                "5"
                + str(self.rng.randint(1, 5))
                + "".join(str(self.rng.randint(0, 9)) for _ in range(10))
            )
            candidate = body + last4
            total = 0
            for i, ch in enumerate(reversed(candidate)):
                d = int(ch)
                if i % 2 == 1:
                    d = d * 2 - 9 if d > 4 else d * 2
                total += d
            if total % 10 == 0:
                return candidate

    def write_booking(self) -> None:
        header = [
            "Reservation number",
            "Booker name",
            "Arrival",
            "Departure",
            "Virtual card number",
            "Amount",
            "Currency",
            "Activation date",
            "Expiration date",
            "Payment status",
            "Property ID",
        ]
        rows: list[list[object]] = []
        hotel_id = 1_000_000 + int(self.prop.code[-2:]) * 7919
        for c in self._cards("booking"):
            rows.append(
                [
                    c.conf,
                    f"{c.res.first} {c.guest_last}",
                    _eu_date(c.res.arrival),
                    _eu_date(c.res.departure),
                    f"**** **** **** {c.last4}",
                    f"{c.amount / 100:,.2f}",
                    "USD",
                    _eu_date(c.activation),
                    _eu_date(c.expiry),
                    "Expired" if c.expiry < self.cfg.as_of else "Available",
                    hotel_id,
                ]
            )
        malformed = self._malformed("booking_vcc")
        for _ in range(malformed):
            rows.append(
                [
                    "4000000001",
                    "Test Row",
                    "05/01/2026",
                    "06/01/2026",
                    "**** **** **** 0000",
                    "pending",
                    "USD",
                    "05/01/2026",
                    "05/03/2026",
                    "Available",
                    hotel_id,
                ]
            )
        path = self.folder / "booking_vcc" / f"booking_virtual_cards_{self.period}.csv"
        _write_csv(path, header, rows)
        self._record(path, malformed)

    def write_processor(self) -> None:
        header = [
            "Transaction Date",
            "Settlement Date",
            "Card Brand",
            "Card Number",
            "Amount",
            "Transaction Type",
            "Auth Code",
            "Reference",
            "Batch #",
            "Terminal",
        ]
        rows: list[list[object]] = []
        for t in sorted(self.data.txns, key=lambda t: (t.txn_date, t.last4, t.amount)):
            rows.append(
                [
                    datetime(t.txn_date.year, t.txn_date.month, t.txn_date.day),
                    datetime(t.settle_date.year, t.settle_date.month, t.settle_date.day),
                    t.brand,
                    f"************{t.last4}",
                    t.amount / 100,
                    t.txn_type,
                    t.auth,
                    t.reference,
                    f"B{t.settle_date:%y%m%d}",
                    "T01",
                ]
            )
        malformed = self._malformed("processor_settlement")
        for _ in range(malformed):
            rows.append(
                [
                    "not a date",
                    None,
                    "VISA",
                    "************1111",
                    10.0,
                    "SALE",
                    "000000",
                    "",
                    "B0",
                    "T01",
                ]
            )
        title = [
            "Merchant settlement report",
            f"Merchant: {self.prop.name}",
            f"Period: {self.cfg.start:%m/%d/%Y} to {self.cfg.as_of:%m/%d/%Y}",
        ]
        path = self.folder / "processor_settlement" / f"settlement_{self.period}.xlsx"
        _write_xlsx(path, header, rows, title_rows=title, sheet="Transactions")
        self._record(path, malformed)


# Entry points ---------------------------------------------------------------------


def _truth_key(card: Card) -> str:
    return f"{card.ota}|{confirmation(card.conf)}|{card.last4}"


def generate_portfolio(
    portfolio: PortfolioConfig,
    raw_root: Path,
    cfg: GenConfig,
    settings: Settings,
    leak_profiles: dict[str, LeakRates] | None = None,
) -> dict:
    """Write raw files for every property and return the ground truth."""
    profiles = leak_profiles or LEAK_PROFILES
    portfolio_dir = raw_root / portfolio.slug
    if portfolio_dir.exists():
        shutil.rmtree(portfolio_dir)
    truth: dict = {
        "portfolio": portfolio.slug,
        "as_of": cfg.as_of.isoformat(),
        "seed": cfg.seed,
        "scale": cfg.scale,
        "properties": {},
        "rejects": {},
        "full_card_number_files": [],
    }
    for index, prop in enumerate(portfolio.properties):
        rates = profiles.get(prop.management_company, DEFAULT_LEAK)
        gen = _PropertyGenerator(prop, index, cfg, rates)
        gen.generate_reservations()
        gen.generate_cards()
        gen.inject_story_cases()
        gen.make_injected_cases_unambiguous(settings)
        gen.generate_payments()
        gen.label_truth()

        writer = _Writer(gen.data, portfolio_dir / prop.code, cfg)
        writer.write_all()
        truth["rejects"].update(writer.rejects)
        if prop.code == FULL_CARD_NUMBER_PROPERTY:
            truth["full_card_number_files"].append(
                f"{portfolio.slug}/{prop.code}/expedia_vcc/expedia_virtual_cards_{writer.period}.csv"
            )

        visible = _visible_cards(gen.data.cards, prop.code)
        truth["properties"][prop.code] = {
            "management_company": prop.management_company,
            "cards": len(visible),
            "results": {_truth_key(c): [c.truth, c.match] for c in visible},
        }
    (raw_root / TRUTH_FILE).write_text(
        json.dumps(truth, indent=1, sort_keys=True), encoding="utf-8"
    )
    return truth


def _visible_cards(cards: list[Card], code: str) -> list[Card]:
    """Cards that actually appear in the OTA files (respects coverage cutoffs)."""
    out = []
    for c in cards:
        cutoff = SOURCE_CUTOFF.get((code, f"{c.ota}_vcc"))
        if cutoff and c.activation > cutoff:
            continue
        out.append(c)
    return out


def generate_demo(settings: Settings, cfg: GenConfig | None = None) -> dict:
    """Generate the Summit Ridge demo portfolio into data/demo_raw/."""
    cfg = cfg or GenConfig(as_of=settings.demo_as_of_date)
    portfolios = load_portfolios("demo")
    if not portfolios:
        raise RuntimeError("No demo portfolio in config/portfolios.yaml")
    demo_settings = settings.model_copy(update={"data_mode": "demo"})
    return generate_portfolio(portfolios[0], demo_settings.raw_dir, cfg, demo_settings)


FAKE_REAL_PORTFOLIO = PortfolioConfig(
    slug="fixture_hotels",
    mode="real",
    name="Fixture Hotels LLC",
    owner_name="Fixture Hotels LLC",
    owner_type="independent",
    management_companies=["Fixture Management Co"],
    properties=[
        PropertyConfig(
            code="FX01",
            name="Fixture Inn Midtown",
            brand="Fixture Inn",
            segment="select_service",
            city="Springfield",
            state="OH",
            room_count=100,
            management_company="Fixture Management Co",
            pms_name="GenericPMS",
            timezone="America/New_York",
        )
    ],
)


def write_fake_real_hotel(raw_root: Path, as_of: date, settings: Settings, seed: int = 7) -> dict:
    """A small 'real looking' hotel for the end-to-end test of real mode."""
    cfg = GenConfig(seed=seed, as_of=as_of, months=3, scale=1.0)
    return generate_portfolio(FAKE_REAL_PORTFOLIO, raw_root, cfg, settings)
