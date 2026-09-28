"""Paths and constants shared across the pipeline."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_HOLDINGS_DIR = DATA_DIR / "raw" / "holdings"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "universe.duckdb"
EXCHANGE_MAP_PATH = CONFIG_DIR / "exchange_suffixes.csv"

# Coverage scoring window and thresholds
COVERAGE_LOOKBACK_DAYS = 365 * 3      # score the last ~3 years of history
STALE_AFTER_DAYS = 10                 # last price older than this => stale
GOOD_COVERAGE_MIN = 0.90              # >= 90% of expected days present
