"""Tabulate the sub-5 % budget crossover ladder (20260725_crossover_ladder).

Answers "is there a budget f at which coarsening (K>1) beats K=1?" by pairing
every coarsened scenario against its own arm's K=1 baseline, instance by
instance.

The run sweeps four arms that isolate the two channels through which coarsening
changes the outcome -- resolution loss and depth gain::

    a_{k}              dispatch-only (v4, solve=False)  -- resolution channel only
    b_{k}              mcf_lb only                      -- resolution channel only
    c_{k}_f{NN}        mcf_lb + flip CP(f)              -- resolution + equal-budget CP
    m1_{k}_f{NN}       full inner solve_flow, budget f  -- both channels combined

Scenario names encode ``{arm}_k{K}[_{mode}][_f{NN}]``; ``K=1`` carries no mode
(there is nothing to round). dRPDf is ``RPDf(coarse) - RPDf(K=1)`` in percentage
points, paired by ``insIndex`` against the *same arm and same f*, so a positive
value always means coarsening hurt.

Mode matters for cross-analysis comparisons: a "best over modes" number and a
fixed-mode number are different statistics, and the 20260724 rounding-robustness
ladder is quoted in ``cumulative``. This script emits both so a document never
has to splice the two.

Usage:
    uv run python scripts/20260726/analyze_crossover_ladder.py <run_dir> \
        [--outdir analysis/<run_id>_crossover_ladder]

Outputs (under --outdir):
    drpdf_by_mode_k.csv    one row per (arm, f, k, mode) -- dRPDf + win/tie/loss
    arm_summary.csv        per (arm, f, k) best mode, plus the K=1 RPDf
    m1_ladder.csv          k=2 dRPDf vs f, per mode (the crossover ladder)
    elapsed_by_scenario.csv  mean elapsed on the (n=200, c=10) slice
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# scripts/20260726/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# "m1_k8_cumulative_f04" / "a_k1" / "b_k32_ceil" / "c_k1_f02"
SCENARIO_RE = re.compile(
    r"^(?P<arm>a|b|c|m1)_k(?P<k>\d+)(?:_(?P<mode>cumulative|ceil|floor|round))?"
    r"(?:_f(?P<f>\d+))?$"
)

MODES = ["ceil", "cumulative", "round", "floor"]

# The depth diagnostic and the elapsed table are quoted on the hardest size cell.
DEPTH_SLICE = {"n": 200, "c": 10}


def parse_scenario(name: str) -> dict | None:
    """``"m1_k8_ceil_f04"`` -> ``{arm: m1, k: 8, mode: ceil, f: 4}``."""
    m = SCENARIO_RE.match(name)
    if not m:
        return None
    return {
        "arm": m["arm"],
        "k": int(m["k"]),
        "mode": m["mode"],  # None for k=1
        "f": int(m["f"]) if m["f"] else None,
    }


def load_run(run_dir: Path) -> pd.DataFrame:
    """Read the run's ``<ts>_rpdf_comparison.csv`` and attach parsed scenarios."""
    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if not matches:
        raise SystemExit(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(matches[0])

    parsed = df["scenarioName"].map(parse_scenario)
    unparsed = sorted(df.loc[parsed.isna(), "scenarioName"].unique())
    if unparsed:
        raise SystemExit(f"unparseable scenario names: {unparsed}")

    for field in ("arm", "k", "mode", "f"):
        df[field] = [p[field] for p in parsed]
    # RPDf is stored as a fraction; every table in the write-up is in percent.
    df["rpdf_pct"] = 100.0 * df["RPDf_BKS_data"]
    return df


def paired_drpdf(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (arm, f, k, mode): dRPDf vs the arm's own K=1, in pp."""
    wide = df.pivot_table(index="insIndex", columns="scenarioName", values="rpdf_pct")
    meta = df.drop_duplicates("scenarioName").set_index("scenarioName")

    rows: list[dict] = []
    for scenario in wide.columns:
        arm, k, mode, f = (meta.loc[scenario, c] for c in ("arm", "k", "mode", "f"))
        if k == 1:
            continue
        base = f"{arm}_k1" + (f"_f{int(f):02d}" if pd.notna(f) else "")
        if base not in wide.columns:
            raise SystemExit(f"missing K=1 baseline {base!r} for {scenario!r}")
        delta = (wide[scenario] - wide[base]).dropna()
        # A NaN RPDf means the scenario registered no incumbent at all. Those
        # instances drop out of the paired comparison, so the objective tables
        # are silent on exactly the cases where one side has nothing to compare.
        # Count them explicitly -- "coarse solved it, K=1 did not" is a win for
        # coarsening that no dRPDf can express.
        k1_only = int((wide[base].notna() & wide[scenario].isna()).sum())
        coarse_only = int((wide[base].isna() & wide[scenario].notna()).sum())
        rows.append(
            {
                "arm": arm,
                "f": int(f) if pd.notna(f) else None,
                "k": int(k),
                "mode": mode,
                "n_paired": len(delta),
                "k1_only_feasible": k1_only,
                "coarse_only_feasible": coarse_only,
                "k1_rpdf_pct": wide[base].mean(),
                "coarse_rpdf_pct": wide[scenario].mean(),
                "mean_drpdf_pp": delta.mean(),
                "win": int((delta < 0).sum()),
                "tie": int((delta == 0).sum()),
                "loss": int((delta > 0).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(["arm", "f", "k", "mode"])


def best_by_mode(drpdf: pd.DataFrame) -> pd.DataFrame:
    """Per (arm, f, k), the mode with the *smallest* penalty (best for coarsening)."""
    idx = drpdf.groupby(["arm", "f", "k"], dropna=False)["mean_drpdf_pp"].idxmin()
    return (
        drpdf.loc[idx]
        .rename(columns={"mode": "best_mode"})
        .sort_values(["arm", "f", "k"])
    )


def elapsed_table(df: pd.DataFrame) -> pd.DataFrame:
    """Mean elapsed on the (n=200, c=10) slice the write-up quotes."""
    big = df[(df["n"] == DEPTH_SLICE["n"]) & (df["c"] == DEPTH_SLICE["c"])]
    out = (
        big.groupby(["arm", "f", "k", "mode"], dropna=False)["elapsedTime"]
        .agg(["mean", "count"])
        .reset_index()
        .rename(columns={"mean": "elapsed_s", "count": "n_instances"})
    )
    return out.sort_values(["arm", "f", "k", "mode"])


def print_report(
    drpdf: pd.DataFrame, best: pd.DataFrame, elapsed: pd.DataFrame
) -> None:
    """Print the tables the analysis document quotes, in document order."""
    fmt_f = lambda f: "-" if pd.isna(f) else f"f={int(f)}%"  # noqa: E731

    print("=== arm-level summary: best-over-mode dRPDf (pp) vs own K=1 ===")
    print(
        f"{'arm':4s} {'f':6s} {'K1 RPDf':>8s}  "
        + "  ".join(f"k={k:<15d}" for k in (2, 8, 32))
    )
    for (arm, f), grp in best.groupby(["arm", "f"], dropna=False):
        cells = []
        for k in (2, 8, 32):
            row = grp[grp["k"] == k]
            if row.empty:
                cells.append(" " * 17)
                continue
            r = row.iloc[0]
            cells.append(f"{r.mean_drpdf_pp:+6.2f} {r.best_mode:<10s}")
        k1 = grp["k1_rpdf_pct"].iloc[0]
        print(f"{arm:4s} {fmt_f(f):6s} {k1:7.2f}%  " + "  ".join(cells))

    print("\n=== W/L at k=8 (best mode) ===")
    for _, r in best[best["k"] == 8].iterrows():
        print(f"  {r.arm:3s} {fmt_f(r.f):6s} {r.best_mode:<10s} W/L {r.win}/{r.loss}")

    unpaired = drpdf[drpdf["n_paired"] < drpdf["n_paired"].max()]
    print("\n=== feasibility asymmetry (instances the paired dRPDf drops) ===")
    if unpaired.empty:
        print("  none -- every scenario registered an incumbent on every instance")
    else:
        for _, r in unpaired.iterrows():
            print(
                f"  {r.arm:3s} {fmt_f(r.f):6s} k={int(r.k):<3d} {str(r['mode']):<10s} "
                f"paired {int(r.n_paired):3d}  "
                f"coarse-only {int(r.coarse_only_feasible):2d}  "
                f"K1-only {int(r.k1_only_feasible):2d}"
            )

    print("\n=== mode severity at k=8 (dRPDf pp, ascending = least harmful first) ===")
    k8 = drpdf[drpdf["k"] == 8]
    for (arm, f), grp in k8.groupby(["arm", "f"], dropna=False):
        ordered = grp.sort_values("mean_drpdf_pp")
        cells = "  ".join(
            f"{r['mode']}:{r.mean_drpdf_pp:+.2f}" for _, r in ordered.iterrows()
        )
        print(f"  {arm:3s} {fmt_f(f):6s} {cells}")

    print("\n=== m1 crossover ladder: k=2 dRPDf (pp) vs f, per mode ===")
    ladder = drpdf[(drpdf["arm"] == "m1") & (drpdf["k"] == 2)]
    print("  mode        " + "  ".join(f"f={f}%" for f in (1, 2, 3, 4)))
    for mode in MODES:
        vals = ladder[ladder["mode"] == mode].sort_values("f")["mean_drpdf_pp"]
        print(f"  {mode:<10s}" + "  ".join(f"{v:+6.2f}" for v in vals))

    print(
        f"\n=== mean elapsed on the (n={DEPTH_SLICE['n']}, c={DEPTH_SLICE['c']}) slice ==="
    )
    for _, r in elapsed[elapsed["k"].isin([1, 8])].iterrows():
        mode = r["mode"] if isinstance(r["mode"], str) else "-"
        print(
            f"  {r.arm:3s} {fmt_f(r.f):6s} k={int(r.k):<3d} {mode:<10s} {r.elapsed_s:6.2f}s"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--outdir", type=Path, default=None)
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    outdir = args.outdir or REPO_ROOT / "analysis" / f"{run_dir.name}_crossover_ladder"
    outdir.mkdir(parents=True, exist_ok=True)

    df = load_run(run_dir)
    drpdf = paired_drpdf(df)
    best = best_by_mode(drpdf)
    elapsed = elapsed_table(df)
    ladder = drpdf[(drpdf["arm"] == "m1") & (drpdf["k"] == 2)].pivot(
        index="f", columns="mode", values="mean_drpdf_pp"
    )

    drpdf.to_csv(outdir / "drpdf_by_mode_k.csv", index=False)
    best.to_csv(outdir / "arm_summary.csv", index=False)
    ladder.to_csv(outdir / "m1_ladder.csv")
    elapsed.to_csv(outdir / "elapsed_by_scenario.csv", index=False)

    print_report(drpdf, best, elapsed)
    print(f"\nwrote 4 CSVs to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
