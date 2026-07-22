"""Paired ceil-vs-cumulative CSR analysis (plan: plans/analysis/20260721/csr_cumulative_vs_ceil.md).

Reads the merged POST_PROCESS_ONLY run's ``*_rpdf_comparison.csv`` (24 scenarios:
``ceil_k{K}_tl{f}`` + ``cum_k{K}_tl{f}`` over κ ∈ {1,2,4,8} × f ∈ {5,10,15}%),
pairs the two modes per instance within each (κ, f) cell, and emits the six
analysis blocks:

  1. per-cell ΔRPDf (cumulative − ceil); negative = cumulative wins,
  2. per-cell win/tie/loss,
  3. κ=1 calibration — the null floor (CP-SAT time-limit noise + cross-run /
     machine offset), NOT an exact identity: at κ=1 both modes feed identical
     input but each solve runs 8-thread CP-SAT under a wall-clock limit and the
     two sides come from different solve batches,
  4. net effect vs κ — ΔRPDf minus the κ=1 calibration at the same f,
  5. verdict scaffold — κ=1-corrected net edge vs the ~11.20pp κ=1↔κ=2 gap,
  6. coverage — instances paired per cell (expect 1440; anything short is
     logged, never silently dropped).

All RPDf figures are percentage points (RPDf_BKS_data × 100); lower is better,
so ΔRPDf < 0 means cumulative beat ceil.

Usage:
    uv run python scripts/20260721/analyze_csr_cumulative_vs_ceil.py <merged_run_dir>
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

import pandas as pd

ANALYSIS_DIR = Path("analysis/20260721_csr_cumulative_vs_ceil")
EXPECTED_PER_CELL = 1440
# κ=1↔κ=2 gap from the prior experiment (plan block 5 success threshold), %p.
KAPPA_GAP_PP = 11.20
TIE_TOL = 1e-9  # RPDf tie tolerance (equal obj → equal symmetric RPDf)

_SCN = re.compile(r"^(ceil|cum)_k(\d+)_tl(\d+)$")


def _parse(scenario: str) -> tuple[str, int, int]:
    m = _SCN.match(scenario)
    if not m:
        raise ValueError(f"unexpected scenarioName: {scenario!r}")
    return (m.group(1), int(m.group(2)), int(m.group(3)))


def load(run_dir: Path) -> pd.DataFrame:
    csv = glob.glob(str(run_dir / "*_rpdf_comparison.csv"))
    if not csv:
        raise FileNotFoundError(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(csv[0], dtype={"insIndex": str})
    df["RPDf"] = df["RPDf_BKS_data"] * 100.0
    parsed = df["scenarioName"].apply(lambda s: pd.Series(_parse(s)))
    df[["mode", "k", "f"]] = parsed
    return df


def _pair(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (insIndex, k, f) with ceil / cum RPDf and their delta."""
    keep = ["insIndex", "k", "f", "T", "R", "n", "c", "mode", "RPDf"]
    wide = df[keep].pivot_table(
        index=["insIndex", "k", "f", "T", "R", "n", "c"],
        columns="mode",
        values="RPDf",
    )
    # inner-join semantics: only instances present in BOTH modes are paired.
    wide = wide.dropna(subset=["ceil", "cum"]).reset_index()
    wide["delta"] = wide["cum"] - wide["ceil"]  # <0 => cumulative wins
    return wide


def _agg_cell(paired: pd.DataFrame, stratum: str) -> pd.DataFrame:
    """Per-(k, f) mean/median ΔRPDf and win/tie/loss for one T-stratum."""
    g = paired.groupby(["k", "f"])
    out = g["delta"].agg(n_paired="size", mean_delta="mean", median_delta="median")
    out["wins_cum"] = g["delta"].apply(lambda s: int((s < -TIE_TOL).sum()))
    out["ties"] = g["delta"].apply(lambda s: int((s.abs() <= TIE_TOL).sum()))
    out["losses_cum"] = g["delta"].apply(lambda s: int((s > TIE_TOL).sum()))
    out = out.reset_index()
    out.insert(0, "stratum", stratum)
    # κ=1-corrected net effect at the same f and stratum.
    base = out[out["k"] == 1].set_index("f")["mean_delta"]
    out["net_vs_k1"] = out.apply(
        lambda r: r["mean_delta"] - base.get(r["f"], float("nan")), axis=1
    )
    return out


def main(run_dir: Path) -> None:
    df = load(run_dir)
    pd.set_option("display.width", 200)
    paired = _pair(df)

    print(
        f"rows={len(df)} scenarios={df.scenarioName.nunique()} "
        f"instances={df.insIndex.nunique()} paired_rows={len(paired)}"
    )

    # ----- block 6 first: coverage gates every downstream claim -----
    print("\n=== [6] coverage — instances paired per (k, f) cell ===")
    cov = paired.groupby(["k", "f"]).size().rename("n_paired").reset_index()
    cov["short_of_1440"] = EXPECTED_PER_CELL - cov["n_paired"]
    print(cov.to_string(index=False))
    short = cov[cov["n_paired"] < EXPECTED_PER_CELL]
    if len(short):
        print(
            f"\n  ⚠ {len(short)} cell(s) below {EXPECTED_PER_CELL} paired "
            "instances — cumulative run likely unfinished; treat these cells "
            "as provisional (no silent truncation)."
        )
    else:
        print(f"  ✓ all cells at {EXPECTED_PER_CELL} paired instances.")

    # ----- per-stratum aggregate tables (blocks 1, 2, 4) -----
    strata = {"all": paired}
    for t in sorted(paired["T"].unique()):
        strata[f"T={t}"] = paired[paired["T"] == t]
    agg = pd.concat(
        [_agg_cell(sub, name) for name, sub in strata.items()],
        ignore_index=True,
    )

    print("\n=== [1] per-cell mean/median ΔRPDf (cum − ceil), <0 = cum wins ===")
    for name in strata:
        sub = agg[agg["stratum"] == name]
        print(f"\n-- stratum: {name} --")
        print(sub.pivot_table(index="k", columns="f", values="mean_delta").round(2))

    print("\n=== [2] per-cell win/tie/loss (cum vs ceil), overall stratum ===")
    show = agg[agg["stratum"] == "all"][
        ["k", "f", "n_paired", "wins_cum", "ties", "losses_cum", "mean_delta"]
    ]
    print(show.to_string(index=False))

    print("\n=== [3] κ=1 calibration (null floor: CP noise + cross-run offset) ===")
    k1 = agg[(agg["k"] == 1)][
        [
            "stratum",
            "f",
            "n_paired",
            "mean_delta",
            "median_delta",
            "wins_cum",
            "ties",
            "losses_cum",
        ]
    ]
    print(k1.to_string(index=False))
    k1_overall = k1[k1["stratum"] == "all"]["mean_delta"]
    print(
        f"\n  κ=1 mean ΔRPDf (overall, per f): "
        f"{k1_overall.round(3).tolist()} %p — this is the baseline the κ≥2 "
        "effects must clear, not zero."
    )

    print("\n=== [4] net effect vs κ (ΔRPDf − κ=1 calibration at same f) ===")
    for name in strata:
        sub = agg[agg["stratum"] == name]
        print(f"\n-- stratum: {name} --")
        print(sub.pivot_table(index="k", columns="f", values="net_vs_k1").round(2))

    print("\n=== [5] verdict scaffold ===")
    net_k = agg[(agg["stratum"] == "all") & (agg["k"] > 1)]
    best = net_k.loc[net_k["net_vs_k1"].idxmin()] if len(net_k) else None
    if best is not None:
        print(
            f"  best κ=1-corrected net edge: k={int(best.k)} f={int(best.f)}% "
            f"→ net {best.net_vs_k1:.2f} %p (raw ΔRPDf {best.mean_delta:.2f})."
        )
        print(
            f"  success threshold (κ=1↔κ=2 gap): net edge must beat "
            f"−{KAPPA_GAP_PP:.2f} %p to call the rule change causal → "
            f"{'MET' if best.net_vs_k1 <= -KAPPA_GAP_PP else 'NOT met'}."
        )
    print(
        "  (Provisional if block 6 flags short cells; verdict is on the "
        "κ=1-corrected net edge, not raw ΔRPDf.)"
    )

    # ----- artifacts -----
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    paired.sort_values(["k", "f", "insIndex"]).to_csv(
        ANALYSIS_DIR / "cum_vs_ceil_rpdf.csv", index=False
    )
    agg.to_csv(ANALYSIS_DIR / "cum_vs_ceil_summary.csv", index=False)
    agg[["stratum", "k", "f", "n_paired", "wins_cum", "ties", "losses_cum"]].to_csv(
        ANALYSIS_DIR / "cum_vs_ceil_win_tie_loss.csv", index=False
    )
    print(f"\nwrote 3 CSVs to {ANALYSIS_DIR}/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <merged_run_dir>")
    main(Path(sys.argv[1]))
