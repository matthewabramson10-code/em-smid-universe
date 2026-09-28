"""Run the whole pipeline: holdings -> symbols -> coverage -> warehouse -> report.

    python refresh.py                   # live Yahoo prices (cached after first run)
    python refresh.py --source cache    # no network; score only what's cached
    python refresh.py --source synthetic --holdings tests/fixtures   # offline demo
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from emuniverse import coverage, holdings, report, symbology, warehouse  # noqa: E402
from emuniverse.config import DB_PATH, RAW_HOLDINGS_DIR, REPORTS_DIR  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdings", type=Path, default=RAW_HOLDINGS_DIR,
                    help="folder of ETF holdings CSVs")
    ap.add_argument("--source", choices=["yahoo", "cache", "synthetic"], default="yahoo")
    ap.add_argument("--refresh-prices", action="store_true",
                    help="re-download prices even if cached")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--reports", type=Path, default=REPORTS_DIR)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = ap.parse_args(argv)

    h = holdings.load_all_holdings(args.holdings)
    if h.empty:
        print(f"No holdings CSVs found in {args.holdings}. Download one (see README) and rerun.")
        return 1
    print(f"[holdings] {len(h)} equity rows from {h['source_etf'].nunique()} ETF file(s)")

    mapped = symbology.map_symbols(h)
    unmapped = symbology.unmapped_exchanges(mapped)
    print(f"[symbols] {(mapped.map_status == 'mapped').sum()} mapped, "
          f"{(mapped.map_status != 'mapped').sum()} unmapped")

    src = {"yahoo": lambda: coverage.YahooSource(refresh=args.refresh_prices),
           "cache": coverage.CacheOnlySource,
           "synthetic": coverage.SyntheticSource}[args.source]()
    symbols = sorted(mapped.loc[mapped.map_status == "mapped", "yahoo_symbol"].unique())
    cov = coverage.score_universe(symbols, src, as_of=args.as_of)
    print(f"[coverage] tiers: {cov['quality_tier'].value_counts().to_dict()}")

    con = warehouse.connect(args.db)
    warehouse.upsert_holdings(con, mapped)
    warehouse.upsert_coverage(con, cov)
    cov_by_sec = warehouse.latest_coverage_by_security(con)
    cov_by_sec = cov_by_sec[(cov_by_sec["source"] == src.name) | cov_by_sec["source"].isna()]

    outputs = report.build_report(con, cov_by_sec, unmapped, src.name, args.reports)
    con.close()
    print(f"[done] warehouse: {args.db}")
    for p in outputs:
        print(f"[done] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
