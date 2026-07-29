"""coarsen_mode(rounding) robustness: is "K=1 best" invariant to the rounding rule?

Reads the merged POST_PROCESS_ONLY run's ``*_rpdf_comparison.csv`` (39 scenarios:
``csr_k{K}_tl{f}_lastsemi`` bare = cumulative, K in {1,2,4,8}; plus
``csr_k{K}_tl{f}_lastsemi_{ceil,floor,round}``, K in {2,4,8}) and answers the
hypothesis from ``plans/experiment/20260724/lastsemi_rounding_robustness.md``:

  "coarsening hurts, K=1 is best" was measured only under coarsen_mode=cumulative
  (k>1 vs k=1 paired: +27.33 / +31.86 / +34.78 pp for K=2/4/8). Does the verdict
  hold under ceil / floor / round too?

Design note -- the pairing here is NOT mode-vs-mode (as in analyze_recon_*). For
each mode m, every k>1 arm is paired against the SINGLE K=1 baseline
(``csr_k1_tl{f}_lastsemi``), because factor=1 makes all four modes identical
(``coarsen_processing_times`` is the identity at K=1). dRPDf = RPDf(m, k>1) -
RPDf(k=1); dRPDf > 0 => coarsening hurts. The cumulative column reproduces the
plan's known +27/+32/+35 pp values -- a built-in pipeline sanity check.

Robustness verdict:
  * ROBUST (H0 confirmed): all four modes show positive mean dRPDf of similar
    sign/magnitude to cumulative -> "K=1 best" is rounding-invariant.
  * REFUTED: some mode has mean dRPDf <= 0 (or win/tie/loss flips) -> the verdict
    is rounding-dependent; revisit the coarse-instance generation rule.

All RPDf figures are percentage points (RPDf_BKS_data x 100); lower is better.
The win/tie/loss and formatting helpers are imported from analyze_recon_ab.py so
the analyses cannot drift.

Usage:
    uv run python scripts/20260724/analyze_rounding_robust.py <merged_run_dir> [--out-dir DIR]
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
    TIE_TOL,
    _fmt,
    _wtl,
)

# CSR noise floor for mean dObj over the 1440 grid (see CLAUDE.md memory).
NOISE_OBJ = 350.0
MODES = ("cumulative", "ceil", "floor", "round")
COARSE_K = (2, 4, 8)
_SCN = re.compile(r"^csr_k(\d+)_tl(\d+)_lastsemi(?:_(ceil|floor|round))?$")


def _parse(scenario: str) -> tuple[int, int, str]:
    m = _SCN.match(scenario)
    if not m:
        raise ValueError(f"unexpected scenarioName: {scenario!r}")
    mode = m.group(3) or "cumulative"  # bare name => cumulative; K=1 lands here
    return int(m.group(1)), int(m.group(2)), mode


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


def _pair_vs_k1(df: pd.DataFrame, mode: str, k: int) -> pd.DataFrame:
    """Pair a coarsened arm (mode, k>1) against the single K=1 baseline per
    (f, insIndex). Suffix _a = coarsened, _b = K=1. dRPDf > 0 => coarsening hurts."""
    a = df[(df["mode"] == mode) & (df["k"] == k)]
    b = df[df["k"] == 1]  # bare cumulative name, but the universal K=1 baseline
    keys = ["f", "insIndex"]
    m = a.merge(b, on=keys, suffixes=("_a", "_b"))
    m["dRPDf"] = m["RPDf_a"] - m["RPDf_b"]
    m["dObj"] = m["bestObj_a"] - m["bestObj_b"]
    m["k"] = k
    return m


def _cell_table(paired: pd.DataFrame) -> pd.DataFrame:
    """Per (k, f) cell summary of a mode's coarsened-vs-K1 pairing."""
    rows = []
    for (k, f), g in paired.groupby(["k", "f"]):
        win, tie, loss = _wtl(g["dRPDf"])
        rows.append(
            {
                "k": k,
                "f": f,
                "n_paired": len(g),
                "mean_dRPDf": g["dRPDf"].mean(),
                "median_dRPDf": g["dRPDf"].median(),
                "mean_dObj": g["dObj"].mean(),
                "win": win,
                "tie": tie,
                "loss": loss,
            }
        )
    return pd.DataFrame(rows).sort_values(["k", "f"]).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_dir", type=Path)
    ap.add_argument(
        "--out-dir", type=Path, default=Path("analysis/20260724_rounding_robust")
    )
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = load(args.run_dir)

    # --- block 1: coverage ---
    print("=" * 74)
    print(
        "BLOCK 1  coverage (instances per mode x k x f; expect "
        f"{EXPECTED_PER_CELL}; K=1 only under 'cumulative')"
    )
    cov = df.groupby(["mode", "k", "f"]).size().rename("n").reset_index()
    cov_p = cov.pivot_table(index=["mode", "k"], columns="f", values="n", fill_value=0)
    print(cov_p.to_string())
    short = cov[cov["n"] < EXPECTED_PER_CELL]
    if len(short):
        print("\n  WARNING: cells below full grid:")
        print(short.to_string(index=False))

    # --- block 2: mean RPDf by mode x k x f ---
    print("\n" + "=" * 74)
    print("BLOCK 2  mean RPDf (pp; lower better). K=1 row is the shared baseline.")
    mean_rpdf = df.groupby(["k", "f", "mode"])["RPDf"].mean().unstack("mode")
    cols = [c for c in MODES if c in mean_rpdf.columns]
    mean_rpdf = mean_rpdf[cols]
    print(mean_rpdf.to_string(float_format=lambda v: f"{v:.3f}"))
    mean_rpdf.to_csv(args.out_dir / "mean_rpdf_by_cell.csv")

    # --- block 3: PRIMARY -- coarsening penalty per mode (k>1 vs K=1) ---
    print("\n" + "=" * 74)
    print(
        "BLOCK 3  PRIMARY  coarsening penalty per mode  (dRPDf = k>1 - K=1; "
        ">0 => coarsening hurts)"
    )
    per_mode_overall: dict[str, pd.DataFrame] = {}
    for mode in MODES:
        paired = pd.concat(
            [_pair_vs_k1(df, mode, k) for k in COARSE_K], ignore_index=True
        )
        if paired.empty:
            print(f"\n-- mode={mode}: no coarsened arms present, skipping")
            continue
        per_mode_overall[mode] = paired
        cells = _cell_table(paired)
        print(f"\n-- mode={mode} --")
        print(_fmt(cells))
        cells.to_csv(args.out_dir / f"penalty_{mode}_cells.csv", index=False)
        for k in COARSE_K:
            g = paired[paired["k"] == k]
            if g.empty:
                continue
            w, t, ls = _wtl(g["dRPDf"])
            print(
                f"   k={k}: mean dRPDf {g['dRPDf'].mean():+7.3f} pp | "
                f"mean dObj {g['dObj'].mean():+8.2f} | win/tie/loss {w}/{t}/{ls}"
            )

    # --- block 4: headline roll-up -- mean dRPDf by mode x k (over all f) ---
    print("\n" + "=" * 74)
    print(
        "BLOCK 4  HEADLINE  mean dRPDf (pp) by mode x k  (cumulative col must "
        "reproduce +27.33/+31.86/+34.78 for k=2/4/8)"
    )
    roll_rows = []
    for mode, paired in per_mode_overall.items():
        for k in COARSE_K:
            g = paired[paired["k"] == k]
            if g.empty:
                continue
            roll_rows.append(
                {
                    "mode": mode,
                    "k": k,
                    "mean_dRPDf": g["dRPDf"].mean(),
                    "mean_dObj": g["dObj"].mean(),
                }
            )
    roll = pd.DataFrame(roll_rows)
    head = roll.pivot(index="k", columns="mode", values="mean_dRPDf")
    head = head[[m for m in MODES if m in head.columns]]
    print(head.to_string(float_format=lambda v: f"{v:+.3f}"))
    roll.to_csv(args.out_dir / "headline_dRPDf_by_mode_k.csv", index=False)

    # --- block 5: budget parity -- mean elapsedTime by mode x k x f ---
    print("\n" + "=" * 74)
    print(
        "BLOCK 5  budget parity  mean elapsedTime (s) by mode x k x f "
        "(k-invariant within a mode+f => equal-budget comparison valid)"
    )
    et = df.groupby(["mode", "f", "k"])["elapsedTime"].mean().unstack("k")
    print(et.to_string(float_format=lambda v: f"{v:.2f}"))

    # --- block 6: verdict ---
    # "coarsening hurts" for a cell = mean dRPDf > 0 AND it loses more instances
    # than it wins (_wtl(dRPDf) = coarsen_win, tie, coarsen_loss; dRPDf>0 => the
    # coarsened arm is worse => a loss). Robust iff this holds for EVERY (mode, k)
    # cell -- the plan's refutation trigger ("어떤 mode에서 dRPDf <= 0 또는 승패가
    # 뒤집힘") is per cell, so a localized flip must not hide behind a mode mean.
    def _hurts(d: pd.Series) -> bool:
        w, _, ls = _wtl(d)
        return d.mean() > TIE_TOL and ls > w

    print("\n" + "=" * 74)
    print("BLOCK 6  VERDICT  (coarsening penalty per mode, k>1 vs K=1)")
    anomalies: list[tuple[str, int, float, tuple[int, int, int]]] = []
    for mode in MODES:
        paired = per_mode_overall.get(mode)
        if paired is None:
            continue
        mean_d = paired["dRPDf"].mean()
        mean_o = paired["dObj"].mean()
        w, t, ls = _wtl(paired["dRPDf"])
        signal = "signal" if abs(mean_o) > NOISE_OBJ else f"<= noise {NOISE_OBJ:.0f}"
        verdict = "coarsening hurts" if _hurts(paired["dRPDf"]) else "DOES NOT hurt (!)"
        print(
            f"  {mode:11s}: mean dRPDf {mean_d:+7.3f} pp | mean dObj "
            f"{mean_o:+8.2f} ({signal}) | win/tie/loss {w}/{t}/{ls} | {verdict}"
        )
        for k in COARSE_K:
            g = paired[paired["k"] == k]
            if not g.empty and not _hurts(g["dRPDf"]):
                anomalies.append((mode, k, g["dRPDf"].mean(), _wtl(g["dRPDf"])))

    print()
    if not anomalies:
        print(
            "  => ROBUST: coarsening hurts in every (mode, k) cell; "
            "'K=1 best' is rounding-invariant (H0 confirmed)."
        )
    else:
        print(
            "  => REFUTED: coarsening does NOT clearly hurt in these cells "
            "(dRPDf<=0 or win/loss flipped) -- verdict is rounding-dependent:"
        )
        for mode, k, md, (w, t, ls) in anomalies:
            print(
                f"       {mode} k={k}: mean dRPDf {md:+.3f} pp | "
                f"win/tie/loss {w}/{t}/{ls}"
            )

    print(f"\nwrote per-cell / roll-up CSVs to {args.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
