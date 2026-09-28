"""Map (local ticker, exchange) to a Yahoo Finance symbol.

The mapping lives in config/exchange_suffixes.csv so you can fix it without
touching code. Rows are matched in file order by case-insensitive substring
of the exchange name; the first match wins, so put specific rows (KOSDAQ)
above generic ones (Korea Exchange).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .config import EXCHANGE_MAP_PATH


def load_exchange_map(path: str | Path = EXCHANGE_MAP_PATH) -> pd.DataFrame:
    m = pd.read_csv(path, dtype=str, keep_default_na=False)
    m["match"] = m["match"].str.lower().str.strip()
    return m


def _apply_rule(ticker: str, rule: str) -> str:
    t = ticker.strip().upper()
    if rule == "pad6":
        return t.zfill(6) if t.isdigit() else t
    if rule == "pad4":
        return t.zfill(4) if t.isdigit() else t
    if rule == "strip_star":
        return t.rstrip("*")
    if rule == "strip_thai":
        return re.sub(r"(-R|-F|/F|-NVDR)$", "", t)
    return t


def _china_a_suffix(ticker: str) -> str:
    # 6xxxxx trades in Shanghai; 0xxxxx / 3xxxxx in Shenzhen.
    return ".SS" if ticker.startswith("6") else ".SZ"


def to_yahoo(local_ticker: str, exchange: str, emap: pd.DataFrame) -> tuple[str | None, str]:
    """Return (yahoo_symbol, status). status is 'mapped' or 'unmapped_exchange'."""
    ex = (exchange or "").lower()
    for row in emap.itertuples(index=False):
        if row.match and row.match in ex:
            sym = _apply_rule(local_ticker, row.ticker_rule)
            suffix = row.yahoo_suffix
            if suffix in (".SS", ".SZ"):
                suffix = _china_a_suffix(sym)
            return f"{sym}{suffix}", "mapped"
    return None, "unmapped_exchange"


def map_symbols(holdings: pd.DataFrame, emap: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add yahoo_symbol and map_status columns to a holdings table."""
    emap = load_exchange_map() if emap is None else emap
    out = holdings.copy()
    results = [to_yahoo(t, e, emap) for t, e in zip(out["local_ticker"], out["exchange"])]
    out["yahoo_symbol"] = [r[0] for r in results]
    out["map_status"] = [r[1] for r in results]
    return out


def unmapped_exchanges(mapped: pd.DataFrame) -> pd.DataFrame:
    """Exchanges with no rule yet, with a count; add these to the config CSV."""
    miss = mapped[mapped["map_status"] == "unmapped_exchange"]
    return (miss.groupby(["exchange", "country"]).size()
                .rename("n_names").reset_index().sort_values("n_names", ascending=False))
