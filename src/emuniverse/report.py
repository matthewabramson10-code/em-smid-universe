"""Charts and a short markdown summary of universe coverage."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .config import REPORTS_DIR

TIER_ORDER = ["good", "partial", "poor", "stale", "none", "unmapped"]
TIER_COLORS = {"good": "#2a9d8f", "partial": "#8ab17d", "poor": "#e9c46a",
               "stale": "#f4a261", "none": "#e76f51", "unmapped": "#9e9e9e"}


def _tiers(cov: pd.DataFrame) -> pd.DataFrame:
    df = cov.copy()
    df["tier"] = df["quality_tier"].fillna("none")
    df.loc[df["map_status"] != "mapped", "tier"] = "unmapped"
    return df


def coverage_by_country_chart(cov: pd.DataFrame, out: Path) -> Path:
    df = _tiers(cov)
    tab = (df.groupby(["country", "tier"]).size().unstack(fill_value=0)
             .reindex(columns=TIER_ORDER, fill_value=0))
    share = tab.div(tab.sum(axis=1), axis=0)
    share = share.assign(_u=share["good"] + share["partial"]).sort_values(["_u", "good"]).drop(columns="_u")
    fig, ax = plt.subplots(figsize=(9, max(3, 0.38 * len(share) + 1.2)))
    left = pd.Series(0.0, index=share.index)
    for tier in TIER_ORDER:
        ax.barh(share.index, share[tier], left=left, color=TIER_COLORS[tier], label=tier,
                edgecolor="white", linewidth=0.6)
        left += share[tier]
    for y, (c, n) in enumerate(tab.sum(axis=1).reindex(share.index).items()):
        ax.text(1.01, y, f"n={n}", va="center", fontsize=8, color="#555")
    ax.set_xlim(0, 1.1)
    ax.set_xlabel("Share of names in country")
    ax.set_title("Yahoo Finance data quality by country")
    ax.legend(ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def coverage_heatmap(cov: pd.DataFrame, out: Path) -> Path:
    df = _tiers(cov)
    df["usable"] = df["tier"].isin(["good", "partial"])
    piv = df.pivot_table(index="country", columns="sector", values="usable", aggfunc="mean")
    fig, ax = plt.subplots(figsize=(max(6, 0.6 * piv.shape[1] + 3), max(3, 0.4 * piv.shape[0] + 2)))
    im = ax.imshow(piv.values, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(piv.shape[1]), piv.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(piv.shape[0]), piv.index, fontsize=8)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7,
                        color="white" if v < 0.6 else "black")
    fig.colorbar(im, ax=ax, label="Share usable (good or partial)")
    ax.set_title("Usable price history by country and sector")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def universe_size_chart(con, out: Path) -> Path:
    df = con.execute("""
        SELECT snapshot_date, source_etf, COUNT(*) AS n
        FROM universe_snapshots GROUP BY 1, 2 ORDER BY 1
    """).df()
    fig, ax = plt.subplots(figsize=(8, 3.5))
    for etf, g in df.groupby("source_etf"):
        ax.plot(g["snapshot_date"], g["n"], marker="o", label=etf)
    ax.set_ylabel("Names")
    ax.set_title("Universe size by snapshot")
    ax.legend(frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def write_summary(cov: pd.DataFrame, unmapped: pd.DataFrame, out: Path, source_name: str) -> Path:
    df = _tiers(cov)
    tier_counts = df["tier"].value_counts().reindex(TIER_ORDER, fill_value=0)
    by_country = (df.assign(usable=df["tier"].isin(["good", "partial"]))
                    .groupby("country").agg(names=("tier", "size"), usable=("usable", "mean"))
                    .sort_values("usable"))
    lines = [f"# Universe coverage summary", "",
             f"Price source: **{source_name}**. Names in universe: **{len(df)}**.", "",
             "## Quality tiers", "", "| Tier | Names |", "|---|---|"]
    lines += [f"| {t} | {n} |" for t, n in tier_counts.items()]
    lines += ["", "## Usable share by country (worst first)", "",
              "| Country | Names | Usable |", "|---|---|---|"]
    lines += [f"| {c} | {int(r.names)} | {r.usable:.0%} |" for c, r in by_country.iterrows()]
    if len(unmapped):
        lines += ["", "## Exchanges with no Yahoo mapping yet",
                  "Add a row for each to `config/exchange_suffixes.csv`.", "",
                  "| Exchange | Country | Names |", "|---|---|---|"]
        lines += [f"| {r.exchange} | {r.country} | {r.n_names} |" for r in unmapped.itertuples()]
    out.write_text("\n".join(lines) + "\n")
    return out


def build_report(con, cov_by_sec: pd.DataFrame, unmapped: pd.DataFrame, source_name: str,
                 out_dir: Path = REPORTS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        coverage_by_country_chart(cov_by_sec, out_dir / "coverage_by_country.png"),
        coverage_heatmap(cov_by_sec, out_dir / "coverage_heatmap.png"),
        universe_size_chart(con, out_dir / "universe_size.png"),
        write_summary(cov_by_sec, unmapped, out_dir / "coverage_summary.md", source_name),
    ]
