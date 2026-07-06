"""Two-sided RPDf comparison (Alg vs BKS) as T (columns) x R (rows) tables.

For each instance in a run scenario, read the algorithm objective
(``obj_value`` from ``<instance>_instance_result.yaml``) and the reference
``BKS_data`` from the PRA2017 benchmark tables. ``best = min(Alg, BKS)`` per
instance; then

    RPDf_Alg = (Alg - best) / (Alg + best) * 2
    RPDf_BKS = (BKS - best) / (BKS + best) * 2

with the winner side scoring 0 (so the 0/0 case where both are 0 is defined as
0). This matches the convention in ``metrics_ffc_ddw_wET.py`` of the
Juntaek-PhD-Thesis scripts and ``scripts/build_results_index.py``.

Output: a T x R pivot (T as columns, R as rows) of mean RPDf for Alg, for BKS,
and Alg-BKS, each with a ``Total`` column and ``Total`` row, written as
Markdown + CSV under ``analysis/<date>/``.

Usage:
    uv run python scripts/build_rpdf_tr_tables.py \
        --run-dir output/20260704/20260704T164349_114896/s0_c5_base \
        --out-dir analysis/20260704

Defaults point at the 20260704 s0_c5_base run (commit 13736e9).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HYBRID_MATCH_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_hybrid_match.csv"
BKS_TABLE_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_bks_table.csv"


def rpdf(z: pd.Series, best: pd.Series) -> pd.Series:
    """Symmetric RPDf = (z - best) / ((z + best) / 2); 0 when both are 0.

    Equivalent to (z - best) / (z + best) * 2. Winner side (z == best) is 0
    even when the denominator is 0.
    """
    z = z.astype(float)
    best = best.astype(float)
    mask = (z == 0) & (best == 0)
    denom = (z + best) / 2.0
    val = (z - best) / denom.where(~mask, 1.0)
    return val.where(~mask, 0.0)


def load_bks() -> pd.DataFrame:
    """Return insIndex -> (instanceName, T, R, W, BKS_data)."""
    match = pd.read_csv(HYBRID_MATCH_CSV, dtype={"insIndex": str})
    match = match.rename(columns={"ffc_ddw_sum_et_filename": "instance_file"})
    match["instance_name"] = match["instance_file"].str.replace(
        r"\.txt$", "", regex=True
    )

    bks = pd.read_csv(BKS_TABLE_CSV, dtype={"insIndex": str})
    keep = ["insIndex", "n", "c", "totalMcCount", "T", "R", "W", "BKS_data"]
    return match[["insIndex", "instance_name"]].merge(
        bks[keep], on="insIndex", how="inner"
    )


def collect_alg_objectives(run_dir: Path) -> pd.DataFrame:
    """One row per instance dir: instance_name, obj_value."""
    rows = []
    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        yamls = list(d.glob("*_instance_result.yaml"))
        if not yamls:
            continue
        # Instance dir = directory containing an *_instance_result.yaml.
        # All metadata (T, R, W, BKS, ...) is pulled from the BKS merge, not
        # parsed from the directory name.
        with open(yamls[0]) as f:
            yr = yaml.safe_load(f)
        rows.append(
            {
                "instance_name": d.name,
                "obj_value": float(yr["obj_value"]),
            }
        )
    return pd.DataFrame(rows)


def build_long_df(run_dir: Path) -> pd.DataFrame:
    bks = load_bks()
    alg = collect_alg_objectives(run_dir)
    df = alg.merge(bks, on="instance_name", how="inner")
    if len(df) != len(alg):
        missing = set(alg["instance_name"]) - set(df["instance_name"])
        raise RuntimeError(
            f"BKS match failed for {len(missing)} instances, e.g. {sorted(missing)[:3]}"
        )
    df["Alg"] = df["obj_value"]
    df["BKS"] = df["BKS_data"].astype(float)
    df["best"] = df[["Alg", "BKS"]].min(axis=1)
    df["rpdf_alg"] = rpdf(df["Alg"], df["best"])
    df["rpdf_bks"] = rpdf(df["BKS"], df["best"])
    df["rpdf_diff"] = df["rpdf_alg"] - df["rpdf_bks"]
    return df


def pivot_rpdf(df: pd.DataFrame, value: str) -> pd.DataFrame:
    """Mean of `value` as R (rows) x T (cols) with Total row/col."""
    pv = pd.pivot_table(
        df,
        values=value,
        index="R",
        columns="T",
        aggfunc="mean",
        margins=True,
        margins_name="Total",
    )
    # order R ascending then Total last; T ascending then Total last (already by margins)
    row_order = sorted(r for r in pv.index if r != "Total") + ["Total"]
    col_order = sorted(c for c in pv.columns if c != "Total") + ["Total"]
    return pv.loc[row_order, col_order]


def fmt_pct(x: float) -> str:
    if pd.isna(x):
        return "NA"
    return f"{x * 100:.2f}"


def to_markdown(table: pd.DataFrame, title: str) -> str:
    lines = [f"### {title}", ""]
    header = "| R \\ T | " + " | ".join(f"{c}" for c in table.columns) + " |"
    sep = "|---" * (len(table.columns) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for r, row in table.iterrows():
        cells = " | ".join(fmt_pct(v) for v in row)
        lines.append(f"| {r} | {cells} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--run-dir",
        type=Path,
        default=REPO_ROOT / "output/20260704/20260704T164349_114896/s0_c5_base",
        help="Scenario directory containing per-instance subdirectories.",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "analysis/20260704",
        help="Destination directory for the Markdown + CSV outputs.",
    )
    ap.add_argument(
        "--name",
        default="s0_c5_base",
        help="Label used in output filenames and headings.",
    )
    args = ap.parse_args()

    run_dir: Path = args.run_dir.resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"run dir not found: {run_dir}")
    out_dir: Path = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = build_long_df(run_dir)

    alg_tbl = pivot_rpdf(df, "rpdf_alg")
    bks_tbl = pivot_rpdf(df, "rpdf_bks")
    diff_tbl = pivot_rpdf(df, "rpdf_diff")

    # non-degenerate subset (both Alg>0 and BKS>0): no zero-winner caps
    sub = df[(df["Alg"] > 0) & (df["BKS"] > 0)]
    sub_alg_tbl = pivot_rpdf(sub, "rpdf_alg")
    sub_bks_tbl = pivot_rpdf(sub, "rpdf_bks")
    sub_diff_tbl = pivot_rpdf(sub, "rpdf_diff")

    # win/tie/loss over all instances
    wins_alg = int((df["Alg"] < df["BKS"]).sum())
    wins_bks = int((df["BKS"] < df["Alg"]).sum())
    ties = int((df["Alg"] == df["BKS"]).sum())
    n = len(df)

    # instance accounting
    n_both_zero = int(((df["Alg"] == 0) & (df["BKS"] == 0)).sum())
    n_alg_zero_bks_pos = int(((df["Alg"] == 0) & (df["BKS"] > 0)).sum())
    n_bks_zero_alg_pos = int(((df["BKS"] == 0) & (df["Alg"] > 0)).sum())

    md = []
    md.append(f"# Two-sided RPDf vs BKS — {args.name}\n")
    md.append("## Setup\n")
    md.append(f"- Run dir: `{run_dir.relative_to(REPO_ROOT)}`")
    md.append(
        "- Algorithm objective: `obj_value` from each `<instance>_instance_result.yaml`"
    )
    md.append(
        "- BKS reference: `BKS_data` of "
        "`benchmarks/PRA2017/pra2017_bks_table.csv`, joined via "
        "`pra2017_hybrid_match.csv` (insIndex ↔ instance dir)."
    )
    md.append("- `best = min(Alg, BKS)` per instance.")
    md.append("- `RPDf = (Z - best) / ((Z + best) / 2)`; winner ⇒ 0 (0/0 ⇒ 0).")
    md.append("- Cells are mean RPDf (%). `Total` = mean over that whole row/column.\n")

    md.append("## Instance accounting\n")
    md.append(f"- Total instances: **{n}** (288 configs × 5 reps)")
    md.append(
        f"- `Alg == 0`: **{n_alg_zero_bks_pos + n_both_zero}** "
        f"(BKS>0: {n_alg_zero_bks_pos} ⇒ Alg wins 0 / BKS pays +200%; "
        f"BKS==0: {n_both_zero} ⇒ tie at 0)"
    )
    md.append(
        f"- `BKS == 0`: **{n_bks_zero_alg_pos + n_both_zero}** "
        f"(Alg>0: {n_bks_zero_alg_pos} ⇒ BKS wins 0 / Alg pays +200%; "
        f"Alg==0: {n_both_zero})"
    )
    md.append(
        f"- Non-degenerate (both Alg>0 and BKS>0): **{len(sub)}**  ← context subset\n"
    )

    md.append("## Win / tie / loss\n")
    md.append(
        f"- n = {n}: Alg wins **{wins_alg}**, BKS wins **{wins_bks}**, "
        f"ties **{ties}** (Alg {wins_alg / n * 100:.1f}% / BKS {wins_bks / n * 100:.1f}%)."
    )
    md.append(
        f"- Overall mean RPDf — Alg: **{fmt_pct(df['rpdf_alg'].mean())}%**, "
        f"BKS: **{fmt_pct(df['rpdf_bks'].mean())}%**, "
        f"Alg−BKS: **{fmt_pct(df['rpdf_diff'].mean())}%**.\n"
    )

    md.append("## Tables (T columns × R rows, Total margin)\n")
    md.append(to_markdown(alg_tbl, "Alg RPDf (%)"))
    md.append(to_markdown(bks_tbl, "BKS RPDf (%)"))
    md.append(
        to_markdown(diff_tbl, "Alg − BKS RPDf (%)  (negative ⇒ Alg closer to best)")
    )

    md.append("## Context: non-degenerate subset (Alg>0 and BKS>0)\n")
    md.append(
        "Zero-objective solutions on either side make the *opposite* side eat "
        "+200% and dominate that slice's loser mean. Removing those caps "
        f"(n={len(sub)}):\n"
    )
    md.append(
        f"- Overall — Alg: **{fmt_pct(sub['rpdf_alg'].mean())}%**, "
        f"BKS: **{fmt_pct(sub['rpdf_bks'].mean())}%**, "
        f"Alg−BKS: **{fmt_pct(sub['rpdf_diff'].mean())}%**.\n"
    )
    md.append(to_markdown(sub_alg_tbl, "Alg RPDf (%) — non-degenerate"))
    md.append(to_markdown(sub_bks_tbl, "BKS RPDf (%) — non-degenerate"))
    md.append(to_markdown(sub_diff_tbl, "Alg − BKS RPDf (%) — non-degenerate"))

    md.append("## Notes\n")
    md.append(
        "- With `best = min(Alg, BKS)`, the per-instance winner scores 0 and only "
        "the loser carries a positive gap; the side with the lower mean RPDf is "
        "closer to best on average."
    )
    md.append(
        "- Zero-objective cases: when one side is 0 and the other >0, the >0 side "
        "gets RPDf = +200% (caps the loser mean). When both are 0, RPDf = 0 (tie)."
    )

    md_path = out_dir / "rpdf_vs_bks.md"
    md_path.write_text("\n".join(md) + "\n")

    for tag, tbl in (("alg", alg_tbl), ("bks", bks_tbl), ("diff", diff_tbl)):
        csv_path = out_dir / f"{args.name}_rpdf_tr_{tag}.csv"
        tbl.to_csv(csv_path)

    long_path = out_dir / f"{args.name}_rpdf_long.csv"
    df[
        [
            "insIndex",
            "instance_name",
            "n",
            "c",
            "totalMcCount",
            "T",
            "R",
            "W",
            "Alg",
            "BKS",
            "best",
            "rpdf_alg",
            "rpdf_bks",
            "rpdf_diff",
        ]
    ].to_csv(long_path, index=False)

    print(f"wrote {md_path}")
    print(f"wrote {len(df)} long rows to {long_path}")
    print(
        f"\n=== Overall: Alg {fmt_pct(df['rpdf_alg'].mean())}%  BKS {fmt_pct(df['rpdf_bks'].mean())}%  diff {fmt_pct(df['rpdf_diff'].mean())}%"
    )
    print(f"=== win/tie/loss (n={n}): Alg {wins_alg} / BKS {wins_bks} / tie {ties}")
    print("\n=== Alg RPDf (%) T x R ===")
    print(alg_tbl.map(fmt_pct).to_string())
    print("\n=== BKS RPDf (%) T x R ===")
    print(bks_tbl.map(fmt_pct).to_string())
    print("\n=== Alg - BKS (%) T x R ===")
    print(diff_tbl.map(fmt_pct).to_string())


if __name__ == "__main__":
    main()
