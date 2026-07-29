"""reconstruct_mode 3-way analysis: semi_active vs active vs active_but_last_semi.

Reads the merged POST_PROCESS_ONLY run's ``*_rpdf_comparison.csv`` (36 scenarios:
``csr_k{K}_tl{f}_{semi,active,lastsemi}`` over kappa in {1,2,4,8} x f in
{5,10,15}%) and answers the hypothesis test from
``plans/experiment/20260724/active_but_last_semi_reconstruction.md``:

  active is decisively worse than semi (+29 pp) because it reassigns the LAST
  stage's machines and the earliness blows up. ``active_but_last_semi`` keeps the
  last stage semi (coarse assignment preserved) while rebuilding earlier stages
  active. If the earliness loss is recovered -> the last-stage reassignment
  (effect a) dominates -> hypothesis confirmed.

Blocks:
  1. coverage -- instances present per (kappa, f, mode); expect 1440 each.
  2. per-cell mean RPDf for each mode (semi / active / lastsemi), pp.
  3. PRIMARY  -- lastsemi vs semi, paired per instance within each cell:
     mean dRPDf (lastsemi - semi), win/tie/loss, mean dObj. dRPDf < 0 => lastsemi
     is better than the incumbent baseline; ~0 => it merely ties semi.
  4. RECOVERY -- lastsemi vs active, paired: how much of active's regression the
     last-stage-semi rebuild claws back (expect strongly negative = big recovery).
  5. reference -- active vs semi, the known regression this run explains.
  6. roll-ups (by kappa, by f) + the overall recovery fraction of active's loss.

All RPDf figures are percentage points (RPDf_BKS_data x 100); lower is better.
The pairing / cell-table / win-tie-loss helpers are imported from
``analyze_recon_ab.py`` so the two analyses cannot drift.

Usage:
    uv run python scripts/20260724/analyze_recon_lastsemi.py <merged_run_dir> [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helpers
from analyze_recon_ab import (  # noqa: E402  -- shared, drift-proof plumbing
    EXPECTED_PER_CELL,
    _cell_table,
    _fmt,
    _pair,
    _wtl,
)

_SCN = re.compile(r"^csr_k(\d+)_tl(\d+)_(semi|active|lastsemi)$")


def _parse(scenario: str) -> tuple[int, int, str]:
    m = _SCN.match(scenario)
    if not m:
        raise ValueError(f"unexpected scenarioName: {scenario!r}")
    return int(m.group(1)), int(m.group(2)), m.group(3)


def load(run_dir: Path) -> pd.DataFrame:
    csv = glob.glob(str(run_dir / "*_rpdf_comparison.csv"))
    if not csv:
        raise FileNotFoundError(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(csv[0], dtype={"insIndex": str})
    df["RPDf"] = df["RPDf_BKS_data"] * 100.0
    parsed = df["scenarioName"].apply(lambda s: pd.Series(_parse(s)))
    df[["k", "f", "mode"]] = parsed
    df["k"] = df["k"].astype(int)
    df["f"] = df["f"].astype(int)
    return df


def _overall(paired: pd.DataFrame, label: str) -> None:
    print(
        f"\n  OVERALL {label}  mean dRPDf {paired['dRPDf'].mean():+.4f} pp | "
        f"mean dObj {paired['dObj'].mean():+.2f} | "
        f"win/tie/loss {'/'.join(map(str, _wtl(paired['dRPDf'])))} | "
        f"n={len(paired)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_dir", type=Path)
    ap.add_argument(
        "--out-dir", type=Path, default=Path("analysis/20260724_lastsemi_3way")
    )
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = load(args.run_dir)

    # --- block 1: coverage ---
    print("=" * 70)
    print(f"BLOCK 1  coverage (instances per k x f x mode; expect {EXPECTED_PER_CELL})")
    cov = df.groupby(["k", "f", "mode"]).size().unstack("mode", fill_value=0)
    print(cov.to_string())
    short = cov[cov.lt(EXPECTED_PER_CELL).any(axis=1)]
    if len(short):
        print("\n  WARNING: cells below full grid:")
        print(short.to_string())

    # --- block 2: per-cell mean RPDf by mode ---
    print("\n" + "=" * 70)
    print("BLOCK 2  mean RPDf by mode (pp; lower is better)")
    mean_rpdf = df.groupby(["k", "f", "mode"])["RPDf"].mean().unstack("mode")
    cols = [c for c in ("semi", "active", "lastsemi") if c in mean_rpdf.columns]
    mean_rpdf = mean_rpdf[cols]
    print(mean_rpdf.to_string(float_format=lambda v: f"{v:.3f}"))
    mean_rpdf.to_csv(args.out_dir / "mean_rpdf_by_cell.csv")

    # --- block 3: PRIMARY lastsemi vs semi ---
    print("\n" + "=" * 70)
    print(
        "BLOCK 3  PRIMARY  lastsemi vs semi  (dRPDf = lastsemi - semi; "
        "<0 => lastsemi better, ~0 => ties baseline)"
    )
    ls = _pair(df, "lastsemi", "semi")
    ls_cells = _cell_table(ls, "lastsemi_vs_semi")
    print(_fmt(ls_cells))
    _overall(ls, "lastsemi-vs-semi")
    ls_cells.to_csv(args.out_dir / "lastsemi_vs_semi_cells.csv", index=False)

    # --- block 4: RECOVERY lastsemi vs active ---
    print("\n" + "=" * 70)
    print(
        "BLOCK 4  RECOVERY  lastsemi vs active  (dRPDf = lastsemi - active; "
        "<<0 => lastsemi recovers active's loss)"
    )
    rec = _pair(df, "lastsemi", "active")
    rec_cells = _cell_table(rec, "lastsemi_vs_active")
    print(_fmt(rec_cells))
    _overall(rec, "lastsemi-vs-active")
    rec_cells.to_csv(args.out_dir / "lastsemi_vs_active_cells.csv", index=False)

    # --- block 5: reference regression active vs semi ---
    print("\n" + "=" * 70)
    print(
        "BLOCK 5  reference  active vs semi  (dRPDf = active - semi; the known "
        "regression)"
    )
    ab = _pair(df, "active", "semi")
    ab_cells = _cell_table(ab, "active_vs_semi")
    print(_fmt(ab_cells))
    _overall(ab, "active-vs-semi")
    ab_cells.to_csv(args.out_dir / "active_vs_semi_cells.csv", index=False)

    # --- block 6: roll-ups + recovery fraction ---
    print("\n" + "=" * 70)
    print("BLOCK 6  roll-ups of lastsemi-vs-semi mean dRPDf (pp)")
    print("  by kappa:")
    print(ls.groupby("k")["dRPDf"].mean().to_string(float_format=lambda v: f"{v:+.4f}"))
    print("  by f (TL %):")
    print(ls.groupby("f")["dRPDf"].mean().to_string(float_format=lambda v: f"{v:+.4f}"))

    active_loss = ab["dRPDf"].mean()  # active - semi (>0, the regression)
    residual = ls["dRPDf"].mean()  # lastsemi - semi (residual vs baseline)
    print("\n  SUMMARY (mean pp vs semi baseline):")
    print(f"    active   regression : {active_loss:+.4f}")
    print(f"    lastsemi residual   : {residual:+.4f}")
    if abs(active_loss) > 1e-9:
        recovered = (1.0 - residual / active_loss) * 100.0
        print(
            f"    => lastsemi recovers {recovered:.1f}% of active's loss "
            "(100% = back to semi; >100% = beats semi)"
        )

    print(f"\nwrote per-cell CSVs to {args.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
