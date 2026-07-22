"""Analyze the 20260721 CSR coarsen_mode experiment.

Reads a run's ``*_rpdf_comparison.csv`` (already carries T/R/n/c and
``RPDf_BKS_data`` per instance × scenario) and prints, for the 17-scenario
grid ``csr_k1`` + factor {2,4,8,16} × mode {ceil, round, floor, cumulative}:

  1. per-scenario mean/median RPDf and mean wall time,
  2. the factor × mode RPDf pivot (vs the csr_k1 baseline),
  3. the RPDf breakdown by job count ``n`` and by ``(n, c)``,
  4. per-instance win/tie/loss of each coarsen scenario against baseline, and
  5. the oracle (best-coarsen-per-instance) contrast against baseline.

Usage:
    uv run python scripts/20260721/analyze_csr_coarsen_mode.py <run_dir>

All RPDf figures are in percentage points (RPDf_BKS_data × 100); lower is
better. Because the coarsen budget (0.0225nc) binds well before the 0.09nc
scenario limit (time% ≈ 0.25 uniformly), every scenario spends the same wall
time, so this is a fixed-compute comparison that isolates the coarsening effect.
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

import pandas as pd

MODE_ORDER = ["ceil", "round", "floor", "cumulative"]


def _parse(scenario: str) -> tuple[int, str]:
    if scenario == "csr_k1":
        return (1, "baseline")
    m = re.match(r"csr_k(\d+)_(\w+)", scenario)
    if not m:
        raise ValueError(f"unexpected scenarioName: {scenario}")
    return (int(m.group(1)), m.group(2))


def load(run_dir: Path) -> pd.DataFrame:
    csv = glob.glob(str(run_dir / "*_rpdf_comparison.csv"))
    if not csv:
        raise FileNotFoundError(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(csv[0], dtype={"insIndex": str})
    df["RPDf"] = df["RPDf_BKS_data"] * 100.0
    df[["factor", "mode"]] = df["scenarioName"].apply(lambda s: pd.Series(_parse(s)))
    return df


def main(run_dir: Path) -> None:
    df = load(run_dir)
    pd.set_option("display.width", 200)
    print(
        f"rows={len(df)} scenarios={df.scenarioName.nunique()} "
        f"instances={df.insIndex.nunique()}"
    )

    base = df[df.scenarioName == "csr_k1"]
    base_rpdf = base.RPDf.mean()

    print("\n=== [1] per-scenario mean RPDf%% (lower=better) ===")
    g = (
        df.groupby("scenarioName")
        .agg(
            rpdf=("RPDf", "mean"),
            median=("RPDf", "median"),
            time_s=("elapsedTime", "mean"),
            time_pct=("time%", "mean"),
        )
        .sort_values("rpdf")
    )
    print(g.round(3))

    print("\n=== [2] factor x mode mean RPDf%% ===")
    print(f"baseline csr_k1 = {base_rpdf:.3f}")
    piv = df[df["factor"] > 1].pivot_table(
        index="factor", columns="mode", values="RPDf", aggfunc="mean"
    )[MODE_ORDER]
    print(piv.round(3))

    print("\n=== [3a] mean RPDf%% by n (baseline vs ceil arms) ===")
    cols = ["csr_k1", "csr_k2_ceil", "csr_k4_ceil", "csr_k8_ceil", "csr_k16_ceil"]
    print(
        df.pivot_table(
            index="n", columns="scenarioName", values="RPDf", aggfunc="mean"
        )[cols].round(2)
    )

    print("\n=== [3b] mean RPDf%% by (n,c): baseline vs ceil arms ===")
    print(
        df.pivot_table(
            index=["n", "c"], columns="scenarioName", values="RPDf", aggfunc="mean"
        )[cols].round(2)
    )

    print("\n=== [4] per-instance win/tie/loss vs baseline csr_k1 ===")
    b = base.set_index("insIndex").RPDf
    rows = []
    for scn, sub in df[df["factor"] > 1].groupby("scenarioName"):
        s = sub.set_index("insIndex").RPDf.reindex(b.index)
        win = int((s < b - 1e-9).sum())
        tie = int((abs(s - b) <= 1e-9).sum())
        loss = int((s > b + 1e-9).sum())
        rows.append((scn, win, tie, loss, round((s - b).mean(), 2)))
    r = pd.DataFrame(
        rows, columns=["scenario", "win", "tie", "loss", "mean_delta"]
    ).sort_values("mean_delta")
    print(r.to_string(index=False))

    print("\n=== [5] oracle: min RPDf over all 16 coarsen scenarios vs baseline ===")
    cmin = df[df["factor"] > 1].groupby("insIndex").RPDf.min().reindex(b.index)
    print(
        f"instances where best-coarsen < baseline: {int((cmin < b - 1e-9).sum())}/160"
    )
    print(f"mean(best-coarsen - baseline): {(cmin - b).mean():.2f} %p")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <run_dir>")
    main(Path(sys.argv[1]))
