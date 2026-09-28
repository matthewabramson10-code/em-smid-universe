"""Load ETF holdings files into one normalized table.

iShares exports are CSVs with a few metadata lines on top, the holdings table
in the middle, and a legal disclaimer at the bottom. We locate the header row
instead of hard-coding a line number, because the preamble length changes.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

NORMALIZED_COLUMNS = [
    "snapshot_date", "source_etf", "local_ticker", "name", "sector",
    "country", "exchange", "currency", "weight_pct", "market_value_usd",
]

_ISHARES_RENAME = {
    "Ticker": "local_ticker",
    "Name": "name",
    "Sector": "sector",
    "Location": "country",
    "Exchange": "exchange",
    "Market Currency": "currency",
    "Weight (%)": "weight_pct",
    "Market Value": "market_value_usd",
}


def _to_number(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(",", "", regex=False).str.strip()
    cleaned = cleaned.replace({"-": None, "": None})
    return pd.to_numeric(cleaned, errors="coerce")


def _parse_as_of(lines: list[str]) -> date | None:
    for line in lines[:15]:
        if "holdings as of" in line.lower():
            row = next(csv.reader([line]))
            for cell in row[1:]:
                for fmt in ("%b %d, %Y", "%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(cell.strip(), fmt).date()
                    except ValueError:
                        continue
    return None


def _guess_etf(path: Path, lines: list[str]) -> str:
    m = re.match(r"([A-Za-z]{2,5})_holdings", path.stem)
    if m:
        return m.group(1).upper()
    first = lines[0].lower() if lines else ""
    if "emerging markets small-cap" in first:
        return "EEMS"
    return path.stem.upper()


def load_ishares_holdings(path: str | Path, source_etf: str | None = None,
                          snapshot_date: date | None = None) -> pd.DataFrame:
    """Parse one iShares holdings CSV into the normalized schema (equities only)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    header_idx = next(
        (i for i, ln in enumerate(lines) if ln.lstrip('"').startswith("Ticker")), None
    )
    if header_idx is None:
        raise ValueError(f"{path.name}: no 'Ticker' header row found; is this an iShares export?")

    # The table ends at the first blank-ish line after the header.
    end_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if not lines[i].strip() or lines[i].count(",") < 5:
            end_idx = i
            break

    table = pd.read_csv(io.StringIO("\n".join(lines[header_idx:end_idx])), dtype=str)
    if "Asset Class" in table.columns:
        table = table[table["Asset Class"].str.strip().str.lower() == "equity"]

    df = table.rename(columns=_ISHARES_RENAME)
    for col in NORMALIZED_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["weight_pct"] = _to_number(df["weight_pct"])
    df["market_value_usd"] = _to_number(df["market_value_usd"])
    df["local_ticker"] = df["local_ticker"].astype(str).str.strip()
    df["snapshot_date"] = snapshot_date or _parse_as_of(lines) or date.today()
    df["source_etf"] = source_etf or _guess_etf(path, lines)
    return df[NORMALIZED_COLUMNS].reset_index(drop=True)


def load_all_holdings(folder: str | Path) -> pd.DataFrame:
    """Load every holdings CSV in a folder. Files that fail are reported, not fatal."""
    frames, failures = [], []
    for p in sorted(Path(folder).glob("*.csv")):
        try:
            frames.append(load_ishares_holdings(p))
        except Exception as exc:  # keep going; report at the end
            failures.append((p.name, str(exc)))
    for name, err in failures:
        print(f"[holdings] skipped {name}: {err}")
    if not frames:
        return pd.DataFrame(columns=NORMALIZED_COLUMNS)
    return pd.concat(frames, ignore_index=True)
