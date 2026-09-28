# EM SMID-Cap Universe Builder & Data Quality Pipeline

Project 1 of the Shikhara EM SMID research series. It builds a versioned universe
of emerging-markets small- and mid-cap equities from ETF holdings files, maps each
name to a Yahoo Finance symbol, scores how usable each name's price history is,
and stores everything in a local DuckDB warehouse that later projects read from.

## Why this exists

Every downstream backtest depends on which stocks were in the universe *on that
date* and whether their data can be trusted. Free data coverage of true local-market
EM small caps is patchy, so data quality is treated here as a research result,
not an afterthought.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m pytest -q                                     # 22 offline tests

# Offline demo on the bundled synthetic fixture (no network needed)
python3 refresh.py --source synthetic --holdings tests/fixtures --reports reports/demo
```

Then run it on real data:

1. Open the iShares **EEMS** product page, choose *Holdings → Detailed Holdings and
   Analytics*, and download the CSV.
2. Save it as `data/raw/holdings/EEMS_holdings_YYYY-MM-DD.csv`. The `EEMS_holdings`
   prefix sets the ETF name; the snapshot date is read from inside the file.
3. Run `python3 refresh.py`. The first run downloads prices for every name (a few
   minutes for ~1,500 names) and caches them in `data/cache/`. Later runs reuse the
   cache; add `--refresh-prices` to re-download.
4. Read `reports/coverage_summary.md`. Any exchange listed under *Exchanges with no
   Yahoo mapping yet* needs a row in `config/exchange_suffixes.csv`.

Re-download the holdings file every month or so and drop it in the same folder.
Each file becomes a dated snapshot, which is how the point-in-time history grows.

## Pipeline

```
data/raw/holdings/*.csv
   │  holdings.py     parse iShares CSVs (finds the header row, drops cash/futures)
   ▼
symbology.py          (local ticker, exchange) → Yahoo symbol via config/exchange_suffixes.csv
   ▼
coverage.py           fetch daily prices (Yahoo, cache-only, or synthetic) and score each name
   ▼
warehouse.py          DuckDB: securities, universe_snapshots, coverage, universe_changes view
   ▼
report.py             charts + coverage_summary.md in reports/
```

### Warehouse tables

| Table | One row per | Purpose |
|---|---|---|
| `securities` | security | Static info, Yahoo symbol, first/last seen in any snapshot |
| `universe_snapshots` | snapshot date × ETF × security | Point-in-time membership and weights |
| `coverage` | scoring date × source × symbol | Data-quality metrics |
| `universe_changes` (view) | add or drop event | Derived by diffing consecutive snapshots |

`security_id` is `country|exchange|local_ticker`, which stays stable even when the
Yahoo symbol mapping is corrected later.

### Quality tiers

Scored over the last three years, starting from a name's first observed trade so
recent listings aren't penalised for history that didn't exist.

| Tier | Rule |
|---|---|
| good | ≥ 90% of business days present |
| partial | 60–90% |
| poor | < 60% |
| stale | last price more than 10 business days before the scoring date |
| none | Yahoo returned nothing |
| unmapped | no exchange rule yet |

Expected days are weekday counts, so local holidays show up as roughly 3–5% missing.
That is why "good" starts at 90% rather than 100%.

## Results (first live run, 28 Sep 2026)

Universe: **1,694 equities** from the iShares EEMS holdings file, across 26 countries.

| Outcome | Names | Share |
|---|---|---|
| Usable Yahoo history (good or partial) | 1,558 | 92% |
| No data returned by Yahoo | 122 | 7% |
| Not mapped (Russia, sanctioned) or no ticker | 14 | 1% |

**Where free data works:** Korea, Turkey, Thailand, Brazil, Saudi Arabia, South
Africa, Mexico, Poland and the Gulf markets all reach 100%; India (97%), Taiwan
(99%) and China (96%) are close behind. Those three countries alone are 55% of
the universe.

**Where it doesn't:**

| Country | Names | Usable | Why |
|---|---|---|---|
| Malaysia | 61 | 0% | iShares gives name codes (`UNISEM`); Yahoo only accepts numeric Bursa codes (`5005.KL`). Fixable with a lookup table. |
| Philippines | 25 | 0% | Yahoo has almost no PSE coverage. Needs another source. |
| UAE | 25 | 48% | Suffix unclear for part of the market; needs verification. |
| Russia | 8 | – | Sanctioned and untradeable since 2022; correctly excluded. |

**What the first runs taught:** the first pass looked like 73% coverage, but most
of the gap was symbology, not missing data. iShares writes Turkish tickers as
`XXXX.E`, Thai NVDRs as `XXX.R`, and Brazil's exchange as bare `XBSP`; fixing those
three rules recovered about 170 names. Another 135 failures were a network drop
mid-run. Separating "the data doesn't exist" from "we asked for it wrong" is the
main lesson for any EM data pipeline.

**Implication for later projects:** research on this universe should treat
Malaysia and the Philippines as out of scope until a second data source is added,
and report results with and without them.

## Known limitations

- **Survivorship bias.** The first snapshot only contains today's constituents.
  History before your first download is survivorship-biased; say so in any
  backtest that uses it. Archived holdings files, if you can find them, can be
  loaded the same way to extend history backwards.
- **Symbology.** Suffix rules marked `verify` in the config CSV (UAE, Egypt,
  Vietnam, Pakistan, Colombia, Peru) are best guesses; the coverage step will
  show whether they work.
- **Yahoo is one source.** Next steps are cross-checking against FMP / Alpha
  Vantage and adding ISIN-based reconciliation.
- **Corporate actions** (ticker changes, delistings) are not handled yet. A ticker
  change currently looks like one drop plus one add.

## Next steps

- Malaysia: build a name-code → numeric Bursa code lookup (61 names).
- Philippines: find a second price source for PSE names.
- Add a second ETF (SPDR EWX or WisdomTree DGS) with its own loader and reconcile
  overlapping names.
- Score FMP and Alpha Vantage coverage alongside Yahoo for a source-comparison heatmap.
- Map ADRs to SEC EDGAR CIKs.
- Schedule `refresh.py` weekly.
