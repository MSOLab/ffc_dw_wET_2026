"""Analyze the CSR idle-mode dump: pivots + per-instance domination, on BOTH
objective layers (coarse = pre-uncoarsening, recon = reconstructed).

Reads the tidy CSV from ``dump_csr_coarse_obj.py`` and writes pivot tables.
Cross-checks the reconstructed obj against the run summary CSV when given.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MODE_ORDER = ["flooring", "ceiling", "lookahead"]


def _pivot(df: pd.DataFrame, value: str) -> pd.DataFrame:
    piv = df.pivot_table(index="factor", columns="mode", values=value, aggfunc="mean")
    return piv[MODE_ORDER]


def _diffs(piv: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ceil-floor": piv["ceiling"] - piv["flooring"],
            "look-floor": piv["lookahead"] - piv["flooring"],
            "ceil-look": piv["ceiling"] - piv["lookahead"],
        }
    )


def _domination(df: pd.DataFrame, value: str, label: str) -> pd.DataFrame:
    """Per (instance, factor): is lookahead <= ceiling and <= flooring?"""
    wide = df.pivot_table(
        index=["instanceName", "factor"], columns="mode", values=value
    ).reset_index()
    sub = wide[wide.factor > 1].copy()  # factor=1 is the byte-identical control
    sub["look_vs_ceil"] = sub["lookahead"] - sub["ceiling"]
    sub["look_vs_floor"] = sub["lookahead"] - sub["flooring"]
    n = len(sub)
    print(f"\n=== [{label}] per-instance domination (factor>1, n={n}) ===")
    print(
        f"  lookahead <= ceiling : {(sub.look_vs_ceil <= 0).sum()}/{n}"
        f"  (violations look>ceil: {(sub.look_vs_ceil > 0).sum()})"
    )
    print(
        f"  lookahead <= flooring: {(sub.look_vs_floor <= 0).sum()}/{n}"
        f"  (violations look>floor: {(sub.look_vs_floor > 0).sum()})"
    )
    viol = sub[sub.look_vs_ceil > 0]
    if len(viol):
        print("  worst lookahead>ceiling rows:")
        print(
            viol.sort_values("look_vs_ceil", ascending=False)
            .head(6)[["instanceName", "factor", "flooring", "ceiling", "lookahead"]]
            .to_string(index=False)
        )
    return sub


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dump", type=Path, default=Path("analysis/csr_idle_modes_v4_20260702.csv")
    )
    ap.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Run summary CSV to cross-check recon obj.",
    )
    ap.add_argument(
        "--out-coarse",
        type=Path,
        default=Path("analysis/csr_idle_modes_v4_coarse_obj_table_20260702.csv"),
    )
    args = ap.parse_args()

    df = pd.read_csv(args.dump)

    # factor=1 control: three modes byte-identical (both layers).
    f1 = df[df.factor == 1].pivot_table(
        index="instanceName", columns="mode", values="coarse_obj"
    )
    ctrl = (f1["flooring"] == f1["ceiling"]).all() and (
        f1["flooring"] == f1["lookahead"]
    ).all()
    print(f"factor=1 control (coarse three modes byte-identical): {ctrl}")

    coarse_piv = _pivot(df, "coarse_obj")
    recon_piv = _pivot(df, "recon_obj")
    print("\n=== COARSE (pre-uncoarsening) obj — mean by factor x mode ===")
    print(coarse_piv.round(1).to_string())
    print(_diffs(coarse_piv).round(1).to_string())
    print("\n=== RECON (reconstructed) obj — mean by factor x mode ===")
    print(recon_piv.round(1).to_string())
    print(_diffs(recon_piv).round(1).to_string())

    _domination(df, "coarse_obj", "COARSE")
    _domination(df, "recon_obj", "RECON")

    # Weighted E/T split (coarse) to show where the gap sits.
    et = df.groupby(["factor", "mode"])[["coarse_wE", "coarse_wT"]].mean().round(1)
    print("\n=== COARSE weighted E/T split (mean) ===")
    print(et.reindex(MODE_ORDER, level=1).to_string())

    # Save a wide coarse-obj table: factor rows, mode means + diffs.
    out = coarse_piv.copy()
    out.columns = [f"coarse_{c}" for c in out.columns]
    d = _diffs(coarse_piv)
    for c in d.columns:
        out[f"coarse_{c}"] = d[c]
    args.out_coarse.parent.mkdir(parents=True, exist_ok=True)
    out.round(2).to_csv(args.out_coarse)
    print(f"\nWrote coarse-obj table -> {args.out_coarse}")

    # Cross-check recon obj against the run summary (bestObj), if provided.
    if args.summary is not None:
        import re

        s = pd.read_csv(args.summary)
        s[["mode", "factor"]] = s["scenarioName"].apply(
            lambda x: pd.Series(re.match(r"(\w+?)_f(\d+)", x).groups())
        )
        s["factor"] = s["factor"].astype(int)
        merged = df.merge(
            s[["instanceName", "mode", "factor", "bestObj"]],
            on=["instanceName", "mode", "factor"],
            how="inner",
        )
        max_abs = (merged["recon_obj"] - merged["bestObj"]).abs().max()
        print(
            f"\nrecon_obj vs summary bestObj — max |diff| = {max_abs} "
            f"(0 => dump reproduces the run exactly)"
        )


if __name__ == "__main__":
    main()
