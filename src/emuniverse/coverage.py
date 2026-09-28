"""Fetch daily prices and score each symbol's data quality.

Price sources are pluggable so the pipeline can be tested offline:
  * YahooSource      - live yfinance downloads, cached to data/cache/*.parquet
  * CacheOnlySource  - reads whatever is already cached (no network)
  * SyntheticSource  - fake prices with realistic gaps, for tests and demos
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from .config import CACHE_DIR, COVERAGE_LOOKBACK_DAYS, GOOD_COVERAGE_MIN, STALE_AFTER_DAYS

PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class PriceSource(Protocol):
    name: str

    def get(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


def _cache_path(symbol: str, cache_dir: Path) -> Path:
    return cache_dir / f"{symbol.replace('/', '_')}.parquet"


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS)


class CacheOnlySource:
    name = "cache"

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = Path(cache_dir)

    def get(self, symbol, start, end):
        p = _cache_path(symbol, self.cache_dir)
        if not p.exists():
            return _empty()
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"])
        return df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]


class YahooSource(CacheOnlySource):
    """yfinance with a per-symbol parquet cache and polite pacing."""
    name = "yahoo"

    def __init__(self, cache_dir: Path = CACHE_DIR, pause_s: float = 0.4, refresh: bool = False):
        super().__init__(cache_dir)
        self.pause_s = pause_s
        self.refresh = refresh

    def get(self, symbol, start, end):
        p = _cache_path(symbol, self.cache_dir)
        if p.exists() and not self.refresh:
            return super().get(symbol, start, end)
        import logging

        import yfinance as yf  # imported lazily so offline runs don't need it

        # yfinance prints a multi-line warning for every symbol it can't find;
        # those misses are recorded as coverage results instead.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        raw = None
        for attempt in range(2):  # one retry covers brief network drops
            try:
                raw = yf.download(symbol, start=start, end=end + timedelta(days=1),
                                  progress=False, auto_adjust=False, threads=False)
            except Exception as exc:
                print(f"[yahoo] {symbol}: {exc}")
                raw = None
            if raw is not None and not raw.empty:
                break
            time.sleep(2 if attempt == 0 else 0)
        time.sleep(self.pause_s)
        if raw is None or raw.empty:
            return _empty()
        if isinstance(raw.columns, pd.MultiIndex):  # newer yfinance returns (field, ticker)
            raw.columns = raw.columns.get_level_values(0)
        df = raw.reset_index().rename(columns=str.lower)[PRICE_COLUMNS]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
        return df


class SyntheticSource:
    """Deterministic fake prices. Coverage quality varies by symbol on purpose."""
    name = "synthetic"

    def __init__(self, seed: int = 7):
        self.seed = seed

    def get(self, symbol, start, end):
        digest = hashlib.sha256(f"{symbol}|{self.seed}".encode()).hexdigest()
        rng = np.random.default_rng(int(digest[:8], 16))
        days = pd.bdate_range(start, end)
        profile = rng.choice(["clean", "gappy", "late_listing", "dead", "missing"],
                             p=[0.45, 0.25, 0.12, 0.10, 0.08])
        if profile == "missing":
            return _empty()
        keep = np.ones(len(days), bool)
        if profile == "gappy":
            keep &= rng.random(len(days)) > 0.25
        if profile == "late_listing":
            keep[: len(days) // 2] = False
        if profile == "dead":
            keep[-len(days) // 5:] = False
        keep &= rng.random(len(days)) > 0.03  # local holidays / random misses
        d = days[keep]
        close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, len(d))))
        vol = rng.integers(0, 500_000, len(d)) * (rng.random(len(d)) > 0.05)
        return pd.DataFrame({"date": d, "open": close, "high": close * 1.01,
                             "low": close * 0.99, "close": close, "volume": vol})


def score_symbol(prices: pd.DataFrame, window_start: date, as_of: date) -> dict:
    """Data-quality metrics for one symbol over [window_start, as_of]."""
    if prices is None or prices.empty:
        return {"n_obs": 0, "first_date": None, "last_date": None, "coverage_ratio": 0.0,
                "max_gap_bdays": None, "zero_volume_pct": None, "days_since_last": None,
                "quality_tier": "none"}
    dts = pd.to_datetime(prices["date"]).sort_values().reset_index(drop=True)
    first, last = dts.iloc[0].date(), dts.iloc[-1].date()
    # Expected days start at the later of window start and first observation, so
    # recent IPOs aren't penalised for history that never existed.
    expected = len(pd.bdate_range(max(window_start, first), as_of))
    n_obs = dts.nunique()
    coverage = min(1.0, n_obs / expected) if expected else 0.0
    gaps = np.busday_count(dts.iloc[:-1].values.astype("datetime64[D]"),
                           dts.iloc[1:].values.astype("datetime64[D]")) if n_obs > 1 else [0]
    days_since = int(np.busday_count(np.datetime64(last), np.datetime64(as_of)))
    zero_vol = float((prices["volume"].fillna(0) == 0).mean()) if "volume" in prices else None

    if days_since > STALE_AFTER_DAYS:
        tier = "stale"
    elif coverage >= GOOD_COVERAGE_MIN:
        tier = "good"
    elif coverage >= 0.6:
        tier = "partial"
    else:
        tier = "poor"
    return {"n_obs": int(n_obs), "first_date": first, "last_date": last,
            "coverage_ratio": round(coverage, 4), "max_gap_bdays": int(max(gaps)),
            "zero_volume_pct": None if zero_vol is None else round(zero_vol, 4),
            "days_since_last": days_since, "quality_tier": tier}


def score_universe(symbols: list[str], source: PriceSource, as_of: date | None = None,
                   lookback_days: int = COVERAGE_LOOKBACK_DAYS) -> pd.DataFrame:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    rows = []
    for i, sym in enumerate(symbols, 1):
        metrics = score_symbol(source.get(sym, start, as_of), start, as_of)
        rows.append({"yahoo_symbol": sym, "source": source.name, "as_of": as_of, **metrics})
        if i % 50 == 0:
            print(f"[coverage] {i}/{len(symbols)} scored")
    return pd.DataFrame(rows)
