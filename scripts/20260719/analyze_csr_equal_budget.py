"""Equal-budget setting comparison: at each fixed f, which setting wins?

Plan: ``plans/analysis/20260719/csr_triple_analysis_plan.md`` §Phase 3.

This is the *meaningful* read of the budget sweep. "Which f is best?" is a
trivial question -- more budget is monotonically better, so the answer is always
the largest f measured, and it says nothing about the algorithm. The question
that discriminates between settings is the transpose:

    at a FIXED budget f, which (flow, K) setting produces the best RPDf?

i.e. read *down* each f-column, not across each setting-row. Every cell in a
column cost the same wall-clock, so a column is a fair head-to-head.

``analyze_csr_tl_scaling_sweep.py`` already prints this table (its block 1.5).
This script adds what is needed to judge how *decisive* each column is:

- the gap from the column winner to the runner-up, in %p;
- a per-instance paired win/tie/loss of winner vs runner-up, since a mean gap
  alone cannot distinguish "wins everywhere by a little" from "wins on average
  while losing on half the instances";
- whether the winner is stable across all f and all slices.

Scope, as everywhere in this analysis: single-step init flows, so these are
**init-quality** numbers under a fixed init budget, not final solution quality.

Sources (f=25 comes from two separate runs, one per K-group):

    20260714T234921_531156   f=5,10,15,20,30  K=1,2,4,8   1440
    20260714T184236_642971   f=25             K=2,4,8     1440
    20260715T183418_361919   f=25             K=1         1440

Usage:
    uv run python scripts/20260719/analyze_csr_equal_budget.py \
        [--outdir analysis/20260719_csr_budget_sweep]

Outputs (under --outdir):
    csr_equal_budget.csv        one row per (slice, f, setting): mean, rank, gap
    csr_equal_budget_gaps.csv   one row per (slice, f): winner, runner-up, paired w/t/l
    csr_equal_budget.png        mean RPDf% vs f, one line per setting, per slice
"""

from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# scripts/20260719/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relpath: str):
    """scripts/ is not an importable package; load a sibling module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ads = _load_module("analyze_dispatch_sweep", "scripts/analyze_dispatch_sweep.py")

METRIC = "RPDf_BKS_data"
INSTANCE_COL = "insIndex"
METHOD_COL = "scenarioName"

SWEEP_RUN = REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260714T234921_531156"
K248_RUN = REPO_ROOT / "output/20260714_csr_full_grid_k248/20260714T184236_642971"
K1_RUN = REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260715T183418_361919"

# csr_full_d2wp_k1_tl05 -> (full, 1, 5). A missing _tl suffix means the scenario
# comes from the fixed-budget f=25 run, where 25 % is implicit in the config.
_RE = re.compile(r"^csr_(?P<flow>full|neh)_d2wp_k(?P<k>\d+)(?:_tl(?P<f>\d+))?$")

DEFAULT_SLICES: tuple[tuple[str, dict[str, float]], ...] = (
    ("overall", {}),
    ("T=0.6", {"T": 0.6}),
    ("(T,R)=(0.6,0.2)", {"T": 0.6, "R": 0.2}),
)

RULE = "=" * 78


def load(run_dir: Path, implicit_f: int | None = None) -> pd.DataFrame:
    """Load a run and parse (flow, K, f) out of every scenario name."""
    df = _ads.load_rpdf(run_dir)
    if df[METRIC].isna().any():
        raise ValueError(f"{run_dir}: null {METRIC}; refusing a partial set")
    parsed = df[METHOD_COL].str.extract(_RE)
    if parsed["flow"].isna().any():
        bad = sorted(df.loc[parsed["flow"].isna(), METHOD_COL].unique())
        raise ValueError(f"{run_dir}: unparseable scenario(s): {bad}")
    df["flow"] = parsed["flow"]
    df["K"] = parsed["k"].astype(int)
    f = parsed["f"]
    if implicit_f is not None:
        f = f.fillna(str(implicit_f))
    if f.isna().any():
        raise ValueError(f"{run_dir}: scenario without a budget and no implicit_f")
    df["f"] = f.astype(int)
    # Short label so a column of 8 settings stays readable: F/N = full/neh.
    df["setting"] = df["flow"].str[0].str.upper() + "_k" + df["K"].astype(str)
    return df


def _slice(df: pd.DataFrame, spec: dict[str, float], label: str) -> pd.DataFrame:
    for col, val in spec.items():
        df = df[df[col] == val]
    if spec and df.empty:
        raise ValueError(f"slice {label} matched no instances")
    return df


def column_table(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Mean RPDf% per (f, setting) with the within-column rank and gap.

    ``gap_to_winner_pp`` is what makes a column readable: it converts an
    absolute level (which moves with f) into a same-budget distance.
    """
    out = (
        df.groupby(["f", "setting"])
        .agg(
            instances=(INSTANCE_COL, "nunique"),
            mean_RPDf_pct=(METRIC, lambda s: float(s.mean() * 100.0)),
        )
        .reset_index()
    )
    out["rank_in_column"] = out.groupby("f")["mean_RPDf_pct"].rank(method="min")
    winner = out.groupby("f")["mean_RPDf_pct"].transform("min")
    out["gap_to_winner_pp"] = out["mean_RPDf_pct"] - winner
    out.insert(0, "slice", label)
    return out.sort_values(["f", "mean_RPDf_pct"]).reset_index(drop=True)


def paired_wtl(df: pd.DataFrame, f: int, a: str, b: str) -> dict[str, int]:
    """Per-instance paired win/tie/loss between two settings at the same f."""
    sub = df[df["f"] == f]
    mat = sub.pivot(index=INSTANCE_COL, columns="setting", values=METRIC)
    pair = mat[[a, b]].dropna()
    diff = pair[a] - pair[b]
    return {
        "n": int(len(pair)),
        "a_wins": int((diff < 0).sum()),
        "ties": int((diff == 0).sum()),
        "b_wins": int((diff > 0).sum()),
    }


def gap_table(df: pd.DataFrame, table: pd.DataFrame, label: str) -> pd.DataFrame:
    """Per f: the winner, the runner-up, their gap, and the paired w/t/l."""
    rows = []
    for f in sorted(table["f"].unique()):
        col = table[table["f"] == f].sort_values("mean_RPDf_pct")
        win = col.iloc[0]
        run = col.iloc[1]
        wtl = paired_wtl(df, f, str(win["setting"]), str(run["setting"]))
        rows.append(
            {
                "slice": label,
                "f": f,
                "winner": win["setting"],
                "winner_RPDf_pct": win["mean_RPDf_pct"],
                "runner_up": run["setting"],
                "runner_up_RPDf_pct": run["mean_RPDf_pct"],
                "gap_pp": run["mean_RPDf_pct"] - win["mean_RPDf_pct"],
                "winner_wins": wtl["a_wins"],
                "ties": wtl["ties"],
                "runner_up_wins": wtl["b_wins"],
                "n": wtl["n"],
            }
        )
    return pd.DataFrame(rows)


def plot(table: pd.DataFrame, out_png: Path) -> None:
    """Mean RPDf% vs f, one line per setting, one panel per slice.

    Reading a *vertical* cut of a panel is the point of the figure, so the
    x-axis ticks sit exactly on the measured f values.
    """
    slices = list(table["slice"].unique())
    fig, axes = plt.subplots(1, len(slices), figsize=(5.0 * len(slices), 4.4))
    axes = [axes] if len(slices) == 1 else list(axes)
    # Okabe-Ito: K by color, flow by linestyle -- so a reader can separate the
    # two axes without a legend lookup. Every line is also directly labelled.
    kcolor = {1: "#0072B2", 2: "#009E73", 4: "#E69F00", 8: "#D55E00"}
    for ax, label in zip(axes, slices):
        sub = table[table["slice"] == label]
        for setting, grp in sub.groupby("setting"):
            grp = grp.sort_values("f")
            k = int(str(setting).split("_k")[1])
            solid = str(setting).startswith("F")
            ax.plot(
                grp["f"],
                grp["mean_RPDf_pct"],
                marker="o" if solid else "s",
                markersize=4,
                color=kcolor[k],
                linestyle="-" if solid else "--",
                label=setting,
            )
            last = grp.iloc[-1]
            ax.annotate(
                str(setting),
                (last["f"], last["mean_RPDf_pct"]),
                textcoords="offset points",
                xytext=(4, -2),
                fontsize=7,
                color=kcolor[k],
            )
        ax.set_xticks(sorted(sub["f"].unique()))
        ax.set_xlabel("CSR budget fraction f (%)")
        ax.set_ylabel("mean RPDf (%)")
        ax.set_title(label, fontsize=10)
        ax.grid(alpha=0.3)
        ax.axhline(0, color="#666666", linewidth=0.8)
    axes[0].legend(fontsize=7, frameon=False, ncol=2)
    fig.suptitle(
        "Equal-budget setting comparison -- read each f-column VERTICALLY\n"
        "(same f = same cost; 'best f' is trivially the largest and is not the question)",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_csr_equal_budget")
    p.add_argument(
        "--outdir",
        type=Path,
        default=REPO_ROOT / "analysis" / "20260719_csr_budget_sweep",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    df = pd.concat(
        [load(SWEEP_RUN), load(K248_RUN, implicit_f=25), load(K1_RUN)],
        ignore_index=True,
    )
    # Each (setting, f) cell must come from exactly one run, or the mean would be
    # taken over a doubled frame.
    dupes = df.groupby(["setting", "f"]).size()
    expected = df[INSTANCE_COL].nunique()
    if (dupes != expected).any():
        bad = dupes[dupes != expected]
        raise ValueError(f"cells with wrong row count (expected {expected}):\n{bad}")

    print(RULE)
    print("EQUAL-BUDGET SETTING COMPARISON")
    print(RULE)
    print("Question: at a FIXED budget f, which (flow, K) setting is best?")
    print("Read each f-column DOWN. 'Which f is best' is trivial (more budget is")
    print("monotonically better) and is deliberately NOT the question here.")
    print()
    print(
        f"{df[INSTANCE_COL].nunique()} instances x {df['setting'].nunique()} "
        f"settings x {df['f'].nunique()} budget points"
    )

    tables, gaps = [], []
    for label, spec in DEFAULT_SLICES:
        sliced = _slice(df, spec, label)
        table = column_table(sliced, label)
        gap = gap_table(sliced, table, label)
        tables.append(table)
        gaps.append(gap)

        print()
        print(RULE)
        print(f"slice: {label}  (n={int(table['instances'].max())} per cell)")
        print(RULE)
        pivot = table.pivot(index="setting", columns="f", values="mean_RPDf_pct")
        print(pivot.round(2).to_string())
        print()
        print("  column winner -> runner-up gap, and the paired check:")
        for _, row in gap.iterrows():
            print(
                f"    f={row['f']:>2}%: {row['winner']:>5} "
                f"({row['winner_RPDf_pct']:7.2f} %)  "
                f"beats {row['runner_up']:>5} "
                f"({row['runner_up_RPDf_pct']:7.2f} %)  "
                f"by {row['gap_pp']:5.2f} %p   "
                f"w/t/l = {row['winner_wins']}/{row['ties']}/{row['runner_up_wins']}"
            )

        winners = set(gap["winner"])
        if len(winners) == 1:
            print(f"  -> {winners.pop()} is the winner at EVERY budget in this slice.")
        else:
            print(f"  -> winner CHANGES with budget: {sorted(winners)}")

    table_all = pd.concat(tables, ignore_index=True)
    gap_all = pd.concat(gaps, ignore_index=True)

    print()
    print(RULE)
    print("Verdict")
    print(RULE)
    winners = set(gap_all["winner"])
    n_cols = len(gap_all)
    if len(winners) == 1:
        w = winners.pop()
        print(f"{w} wins all {n_cols} (slice x f) columns.")
        print("The setting ranking is therefore budget-independent over the")
        print("measured range: no crossover, so the choice of f does not change")
        print("which setting to pick.")
        worst = gap_all.loc[gap_all["gap_pp"].idxmin()]
        print(
            f"Narrowest column: {worst['slice']} @ f={worst['f']}% -- "
            f"{worst['gap_pp']:.2f} %p over {worst['runner_up']} "
            f"(w/t/l {worst['winner_wins']}/{worst['ties']}/{worst['runner_up_wins']})"
        )
    else:
        print(f"winner is NOT stable across columns: {sorted(winners)}")
        print(gap_all[["slice", "f", "winner", "gap_pp"]].to_string(index=False))

    table_all.to_csv(args.outdir / "csr_equal_budget.csv", index=False)
    gap_all.to_csv(args.outdir / "csr_equal_budget_gaps.csv", index=False)
    plot(table_all, args.outdir / "csr_equal_budget.png")
    print()
    print(f"wrote csr_equal_budget{{,_gaps}}.csv + .png to {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
