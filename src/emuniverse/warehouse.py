"""DuckDB warehouse: securities, point-in-time universe snapshots, coverage.

universe_snapshots is append-only by (snapshot_date, source_etf). Each new
holdings download adds one dated snapshot; add/drop dates are derived from
consecutive snapshots rather than overwritten, which is what makes the
universe point-in-time going forward.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS securities (
    security_id   VARCHAR PRIMARY KEY,   -- country|exchange|local_ticker
    local_ticker  VARCHAR,
    name          VARCHAR,
    country       VARCHAR,
    exchange      VARCHAR,
    currency      VARCHAR,
    sector        VARCHAR,
    yahoo_symbol  VARCHAR,
    map_status    VARCHAR,
    first_seen    DATE,
    last_seen     DATE
);
CREATE TABLE IF NOT EXISTS universe_snapshots (
    snapshot_date    DATE,
    source_etf       VARCHAR,
    security_id      VARCHAR,
    weight_pct       DOUBLE,
    market_value_usd DOUBLE,
    PRIMARY KEY (snapshot_date, source_etf, security_id)
);
CREATE TABLE IF NOT EXISTS coverage (
    as_of           DATE,
    source          VARCHAR,
    yahoo_symbol    VARCHAR,
    n_obs           INTEGER,
    first_date      DATE,
    last_date       DATE,
    coverage_ratio  DOUBLE,
    max_gap_bdays   INTEGER,
    zero_volume_pct DOUBLE,
    days_since_last INTEGER,
    quality_tier    VARCHAR,
    PRIMARY KEY (as_of, source, yahoo_symbol)
);
CREATE OR REPLACE VIEW universe_changes AS
WITH s AS (
    SELECT DISTINCT snapshot_date, security_id FROM universe_snapshots
), d AS (
    SELECT snapshot_date,
           LAG(snapshot_date) OVER (ORDER BY snapshot_date) AS prev_date
    FROM (SELECT DISTINCT snapshot_date FROM s)
)
SELECT d.snapshot_date AS change_date, cur.security_id, 'add' AS change
FROM d JOIN s cur ON cur.snapshot_date = d.snapshot_date
WHERE d.prev_date IS NOT NULL
  AND cur.security_id NOT IN (SELECT security_id FROM s WHERE snapshot_date = d.prev_date)
UNION ALL
SELECT d.snapshot_date, prev.security_id, 'drop'
FROM d JOIN s prev ON prev.snapshot_date = d.prev_date
WHERE prev.security_id NOT IN (SELECT security_id FROM s WHERE snapshot_date = d.snapshot_date);
"""


def connect(path: str | Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def make_security_id(df: pd.DataFrame) -> pd.Series:
    return (df["country"].fillna("?").str.strip() + "|" + df["exchange"].fillna("?").str.strip()
            + "|" + df["local_ticker"].fillna("?").str.strip().str.upper())


def upsert_holdings(con, mapped: pd.DataFrame) -> None:
    df = mapped.copy()
    df["security_id"] = make_security_id(df)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date

    sec = (df.sort_values("snapshot_date")
             .groupby("security_id")
             .agg(local_ticker=("local_ticker", "last"), name=("name", "last"),
                  country=("country", "last"), exchange=("exchange", "last"),
                  currency=("currency", "last"), sector=("sector", "last"),
                  yahoo_symbol=("yahoo_symbol", "last"), map_status=("map_status", "last"),
                  first_seen=("snapshot_date", "min"), last_seen=("snapshot_date", "max"))
             .reset_index())
    con.register("new_sec", sec)
    con.execute("""
        INSERT INTO securities SELECT * FROM new_sec
        ON CONFLICT (security_id) DO UPDATE SET
            name = excluded.name, sector = excluded.sector, currency = excluded.currency,
            yahoo_symbol = excluded.yahoo_symbol, map_status = excluded.map_status,
            first_seen = LEAST(securities.first_seen, excluded.first_seen),
            last_seen  = GREATEST(securities.last_seen, excluded.last_seen)
    """)
    snap = (df.groupby(["snapshot_date", "source_etf", "security_id"], as_index=False)
              .agg(weight_pct=("weight_pct", "sum"), market_value_usd=("market_value_usd", "sum")))
    con.register("new_snap", snap)
    con.execute("INSERT OR REPLACE INTO universe_snapshots SELECT * FROM new_snap")
    con.unregister("new_sec")
    con.unregister("new_snap")


def upsert_coverage(con, cov: pd.DataFrame) -> None:
    cols = ["as_of", "source", "yahoo_symbol", "n_obs", "first_date", "last_date",
            "coverage_ratio", "max_gap_bdays", "zero_volume_pct", "days_since_last",
            "quality_tier"]
    con.register("new_cov", cov[cols])
    con.execute("INSERT OR REPLACE INTO coverage SELECT * FROM new_cov")
    con.unregister("new_cov")


def latest_coverage_by_security(con) -> pd.DataFrame:
    """One row per security with its most recent coverage score."""
    return con.execute("""
        WITH c AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY yahoo_symbol, source
                                         ORDER BY as_of DESC) AS rn
            FROM coverage
        )
        SELECT s.security_id, s.name, s.country, s.sector, s.exchange, s.yahoo_symbol,
               s.map_status, c.source, c.coverage_ratio, c.quality_tier, c.n_obs,
               c.max_gap_bdays, c.days_since_last
        FROM securities s LEFT JOIN c ON c.yahoo_symbol = s.yahoo_symbol AND c.rn = 1
    """).df()
