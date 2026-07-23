"""reconstruct_mode AB analysis: active vs semi_active (+ prior-run baseline).

Reads the merged POST_PROCESS_ONLY run's ``*_rpdf_comparison.csv`` (36 scenarios:
``csr_k{K}_tl{f}_{prior,semi,active}`` over kappa in {1,2,4,8} x f in {5,10,15}%)
and emits the comparison blocks:

  1. coverage -- instances present per (kappa, f, mode); expect 1440 each.
  2. per-cell mean RPDf for each mode (prior / semi / active), pp.
  3. PRIMARY AB -- active vs semi, paired per instance within each cell:
     mean dRPDf (active - semi), win/tie/loss, mean dObj (bestObj active - semi).
     dRPDf < 0 => active is better (lower RPDf).
  4. reproducibility -- semi vs prior, paired: mean dRPDf (semi - prior) and
     mean dObj. Should sit within the CP-SAT wall-clock noise floor
     (per-instance CP is nondeterministic; ~+-350 mean-obj over the 1440 grid
     is noise, not signal). A large systematic gap here means the ``_semi``
     scenarios did NOT reproduce the prior run.
  5. net vs baseline -- active vs prior, paired (the end-to-end question).
  6. aggregate roll-ups by kappa, by f, and overall.

All RPDf figures are percentage points (RPDf_BKS_data x 100); lower is better.

Usage:
    uv run python scripts/20260724/analyze_recon_ab.py <merged_run_dir> [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

import pandas as pd

EXPECTED_PER_CELL = 1440
TIE_TOL = 1e-9  # equal obj => equal symmetric RPDf
_SCN = re.compile(r"^csr_k(\d+)_tl(\d+)_(prior|semi|active)$")


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


def _pair(df: pd.DataFrame, mode_a: str, mode_b: str) -> pd.DataFrame:
    """Join two modes per (k, f, insIndex). Suffix _a = mode_a, _b = mode_b."""
    a = df[df["mode"] == mode_a]
    b = df[df["mode"] == mode_b]
    keys = ["k", "f", "insIndex"]
    m = a.merge(b, on=keys, suffixes=("_a", "_b"))
    m["dRPDf"] = m["RPDf_a"] - m["RPDf_b"]  # a - b; <0 => a better
    m["dObj"] = m["bestObj_a"] - m["bestObj_b"]
    return m


def _wtl(d: pd.Series) -> tuple[int, int, int]:
    """win/tie/loss for 'a' relative to 'b' on dRPDf (a-b). win = a lower."""
    win = int((d < -TIE_TOL).sum())
    loss = int((d > TIE_TOL).sum())
    tie = int(len(d) - win - loss)
    return win, tie, loss


def _cell_table(paired: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for (k, f), g in paired.groupby(["k", "f"]):
        win, tie, loss = _wtl(g["dRPDf"])
        rows.append({
            "k": k, "f": f, "n_paired": len(g),
            "mean_dRPDf": g["dRPDf"].mean(),
            "median_dRPDf": g["dRPDf"].median(),
            "mean_dObj": g["dObj"].mean(),
            "win": win, "tie": tie, "loss": loss,
        })
    out = pd.DataFrame(rows).sort_values(["k", "f"]).reset_index(drop=True)
    out.attrs["label"] = label
    return out


def _fmt(df: pd.DataFrame) -> str:
    disp = df.copy()
    for c in ("mean_dRPDf", "median_dRPDf", "mean_dObj"):
        if c in disp:
            disp[c] = disp[c].map(lambda v: f"{v:+.3f}")
    return disp.to_string(index=False)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out-dir", type=Path,
                    default=Path("analysis/20260724_recon_ab"))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = load(args.run_dir)

    # --- block 1: coverage ---
    print("=" * 70)
    print("BLOCK 1  coverage (instances per k x f x mode; expect "
          f"{EXPECTED_PER_CELL})")
    cov = df.groupby(["k", "f", "mode"]).size().unstack("mode", fill_value=0)
    print(cov.to_string())
    short = cov[cov.lt(EXPECTED_PER_CELL).any(axis=1)]
    if len(short):
        print("\n  WARNING: cells below full grid:")
        print(short.to_string())

    # --- block 2: per-cell mean RPDf by mode ---
    print("\n" + "=" * 70)
    print("BLOCK 2  mean RPDf by mode (pp; lower is better)")
    mean_rpdf = (df.groupby(["k", "f", "mode"])["RPDf"].mean()
                 .unstack("mode"))
    cols = [c for c in ("prior", "semi", "active") if c in mean_rpdf.columns]
    mean_rpdf = mean_rpdf[cols]
    print(mean_rpdf.to_string(float_format=lambda v: f"{v:.3f}"))
    mean_rpdf.to_csv(args.out_dir / "mean_rpdf_by_cell.csv")

    # --- block 3: PRIMARY AB active vs semi ---
    print("\n" + "=" * 70)
    print("BLOCK 3  PRIMARY AB  active vs semi  (dRPDf = active - semi; "
          "<0 => active better)")
    ab = _pair(df, "active", "semi")
    ab_cells = _cell_table(ab, "active_vs_semi")
    print(_fmt(ab_cells))
    print(f"\n  OVERALL  mean dRPDf {ab['dRPDf'].mean():+.4f} pp | "
          f"mean dObj {ab['dObj'].mean():+.2f} | "
          f"win/tie/loss {'/'.join(map(str, _wtl(ab['dRPDf'])))} | "
          f"n={len(ab)}")
    ab_cells.to_csv(args.out_dir / "active_vs_semi_cells.csv", index=False)

    # --- block 4: reproducibility semi vs prior ---
    print("\n" + "=" * 70)
    print("BLOCK 4  reproducibility  semi vs prior  (dRPDf = semi - prior; "
          "expect ~0 within CP noise floor)")
    rep = _pair(df, "semi", "prior")
    rep_cells = _cell_table(rep, "semi_vs_prior")
    print(_fmt(rep_cells))
    print(f"\n  OVERALL  mean dRPDf {rep['dRPDf'].mean():+.4f} pp | "
          f"mean dObj {rep['dObj'].mean():+.2f} | "
          f"win/tie/loss {'/'.join(map(str, _wtl(rep['dRPDf'])))} | "
          f"n={len(rep)}")
    rep_cells.to_csv(args.out_dir / "semi_vs_prior_cells.csv", index=False)

    # --- block 5: net vs baseline active vs prior ---
    print("\n" + "=" * 70)
    print("BLOCK 5  net vs baseline  active vs prior  (dRPDf = active - prior)")
    net = _pair(df, "active", "prior")
    net_cells = _cell_table(net, "active_vs_prior")
    print(_fmt(net_cells))
    print(f"\n  OVERALL  mean dRPDf {net['dRPDf'].mean():+.4f} pp | "
          f"mean dObj {net['dObj'].mean():+.2f} | "
          f"win/tie/loss {'/'.join(map(str, _wtl(net['dRPDf'])))} | "
          f"n={len(net)}")
    net_cells.to_csv(args.out_dir / "active_vs_prior_cells.csv", index=False)

    # --- block 6: aggregate roll-ups ---
    print("\n" + "=" * 70)
    print("BLOCK 6  roll-ups of active-vs-semi mean dRPDf (pp)")
    print("  by kappa:")
    print(ab.groupby("k")["dRPDf"].mean().to_string(
        float_format=lambda v: f"{v:+.4f}"))
    print("  by f (TL %):")
    print(ab.groupby("f")["dRPDf"].mean().to_string(
        float_format=lambda v: f"{v:+.4f}"))

    print(f"\nwrote per-cell CSVs to {args.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
