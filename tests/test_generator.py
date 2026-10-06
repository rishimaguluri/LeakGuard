import hashlib
from collections import Counter
from pathlib import Path

from leakguard.config import load_portfolios, load_settings
from leakguard.demo.generator import TRUTH_FILE, GenConfig, generate_portfolio
from leakguard.ingest.normalize import looks_like_card_number

from .conftest import AS_OF


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    settings = load_settings()
    portfolio = load_portfolios("demo")[0]
    cfg = GenConfig(as_of=AS_OF, scale=0.03)
    generate_portfolio(portfolio, tmp_path / "a", cfg, settings)
    generate_portfolio(portfolio, tmp_path / "b", cfg, settings)
    assert _hashes(tmp_path / "a") == _hashes(tmp_path / "b")


def test_files_follow_folder_convention(small_demo: tuple[Path, dict]) -> None:
    root, _ = small_demo
    raw = root / "data" / "demo_raw" / "summit_ridge"
    for prop in load_portfolios("demo")[0].properties:
        sources = {p.name for p in (raw / prop.code).iterdir()}
        assert sources == {
            "pms_reservations",
            "pms_payments",
            "expedia_vcc",
            "booking_vcc",
            "processor_settlement",
        }
    assert (root / "data" / "demo_raw" / TRUTH_FILE).exists()


def test_northpoint_leaks_more_than_lakeview(small_demo: tuple[Path, dict]) -> None:
    _, truth = small_demo
    leaks: Counter[str] = Counter()
    cards: Counter[str] = Counter()
    for prop in truth["properties"].values():
        company = prop["management_company"]
        cards[company] += prop["cards"]
        leaks[company] += sum(
            1
            for t, _ in prop["results"].values()
            if t in ("UNCHARGED", "EXPIRED_UNCHARGED", "UNDERCHARGED")
        )
    lakeview = leaks["Lakeview Hospitality"] / cards["Lakeview Hospitality"]
    northpoint = leaks["Northpoint Hotel Group"] / cards["Northpoint Hotel Group"]
    assert northpoint > 1.8 * lakeview


def test_overall_leak_rates_match_plan(small_demo: tuple[Path, dict]) -> None:
    _, truth = small_demo
    counts: Counter[str] = Counter()
    total = 0
    for prop in truth["properties"].values():
        total += prop["cards"]
        counts.update(t for t, _ in prop["results"].values())
    uncharged = (counts["UNCHARGED"] + counts["EXPIRED_UNCHARGED"]) / total
    assert 0.02 <= uncharged <= 0.06  # base 2 to 4% plus injected story cases
    assert 0.005 <= counts["UNDERCHARGED"] / total <= 0.02
    assert counts["OVERCHARGED"] > 0
    assert counts["CHARGED_NOT_SETTLED"] > 0
    assert counts["NO_PMS_MATCH"] > 0
    assert counts["DUPLICATE_VCC"] > 0
    assert counts["CANCELLED_REVIEW"] > 0
    assert any(m == "fuzzy" for p in truth["properties"].values() for _, m in p["results"].values())


def test_one_file_has_full_card_numbers(small_demo: tuple[Path, dict]) -> None:
    root, truth = small_demo
    [rel] = truth["full_card_number_files"]
    text = (root / "data" / "demo_raw" / rel).read_text(encoding="utf-8")
    assert "Card Number" in text.splitlines()[0]
    assert looks_like_card_number(text.splitlines()[1])


def test_malformed_rows_are_recorded(small_demo: tuple[Path, dict]) -> None:
    _, truth = small_demo
    assert sum(truth["rejects"].values()) == 11
