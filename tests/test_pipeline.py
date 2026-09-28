import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from emuniverse import coverage, holdings, symbology, warehouse  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ishares_sample.csv"


@pytest.fixture(scope="module")
def h():
    return holdings.load_ishares_holdings(FIXTURE, source_etf="EEMS")


def test_loader_keeps_only_equities(h):
    assert len(h) == 17
    assert "USD" not in set(h["local_ticker"])
    assert h["snapshot_date"].iloc[0] == date(2026, 9, 25)
    assert h["weight_pct"].dtype.kind == "f"
    assert h.loc[h.local_ticker == "2345", "market_value_usd"].iloc[0] == pytest.approx(4512300.10)


@pytest.mark.parametrize("ticker,exchange,expected", [
    ("47810", "Korea Exchange (Stock Market)", "047810.KS"),
    ("196170", "Korea Exchange (Kosdaq)", "196170.KQ"),
    ("241", "Hong Kong Exchanges And Clearing Ltd", "0241.HK"),
    ("300750", "Shenzhen Stock Exchange", "300750.SZ"),
    ("600519", "Shanghai Stock Exchange", "600519.SS"),
    ("ALSN*", "Bolsa Mexicana De Valores", "ALSN.MX"),
    ("CPALL-R", "Stock Exchange Of Thailand", "CPALL.BK"),
    ("6446", "Gretai Securities Market", "6446.TWO"),
    ("KRU", "Warsaw Stock Exchange/Equities/Main Market", "KRU.WA"),
    ("TOTS3", "XBSP - B3 S.A.", "TOTS3.SA"),
    ("TRMET.E", "Istanbul Stock Exchange", "TRMET.IS"),
    ("TTW.R", "Stock Exchange Of Thailand", "TTW.BK"),
    ("AGUAS.A", "Santiago Stock Exchange", "AGUAS-A.SN"),
])
def test_to_yahoo(ticker, exchange, expected):
    sym, status = symbology.to_yahoo(ticker, exchange, symbology.load_exchange_map())
    assert (sym, status) == (expected, "mapped")


@pytest.mark.parametrize("ticker", ["-", "", "  ", None])
def test_missing_ticker(ticker):
    assert symbology.to_yahoo(ticker, "Hong Kong Exchanges And Clearing Ltd",
                              symbology.load_exchange_map()) == (None, "missing_ticker")


def test_unmapped_exchange_is_reported(h):
    mapped = symbology.map_symbols(h)
    un = symbology.unmapped_exchanges(mapped)
    assert list(un["exchange"]) == ["Unlisted Test Exchange"]


def test_score_symbol_tiers():
    start, as_of = date(2025, 1, 1), date(2025, 12, 31)
    days = pd.bdate_range(start, as_of)
    full = pd.DataFrame({"date": days, "close": 1.0, "volume": 100})
    assert coverage.score_symbol(full, start, as_of)["quality_tier"] == "good"
    assert coverage.score_symbol(full.iloc[::3], start, as_of)["quality_tier"] == "poor"
    assert coverage.score_symbol(full.iloc[:-30], start, as_of)["quality_tier"] == "stale"
    assert coverage.score_symbol(pd.DataFrame(), start, as_of)["quality_tier"] == "none"
    # A late listing is scored only from its first trade, so it stays 'good'.
    assert coverage.score_symbol(full.iloc[200:], start, as_of)["quality_tier"] == "good"


def test_synthetic_source_is_deterministic():
    a = coverage.SyntheticSource().get("X.KS", date(2025, 1, 1), date(2025, 6, 30))
    b = coverage.SyntheticSource().get("X.KS", date(2025, 1, 1), date(2025, 6, 30))
    pd.testing.assert_frame_equal(a, b)


def test_warehouse_point_in_time_changes(tmp_path, h):
    con = warehouse.connect(tmp_path / "t.duckdb")
    first = symbology.map_symbols(h)
    warehouse.upsert_holdings(con, first)
    # Second snapshot a month later: drop one name, add one.
    second = first[first.local_ticker != "MGROS"].copy()
    new = second.iloc[[0]].copy()
    new["local_ticker"], new["name"] = "9999", "NEW LISTING CO"
    second = pd.concat([second, new])
    second["snapshot_date"] = date(2026, 10, 25)
    warehouse.upsert_holdings(con, second)
    # Re-loading the same file must not duplicate rows.
    warehouse.upsert_holdings(con, second)

    n = con.execute("SELECT snapshot_date, COUNT(*) FROM universe_snapshots GROUP BY 1 ORDER BY 1").fetchall()
    assert [c for _, c in n] == [17, 17]
    changes = con.execute("SELECT change, security_id FROM universe_changes ORDER BY change").fetchall()
    assert changes == [("add", "Taiwan|Taiwan Stock Exchange|9999"),
                       ("drop", "Turkey|Istanbul Stock Exchange|MGROS")]
    first_seen = con.execute(
        "SELECT first_seen FROM securities WHERE local_ticker='2345'").fetchone()[0]
    assert first_seen == date(2026, 9, 25)
