"""Scenario means and paired contrasts over a merged run's rpdf_comparison.csv.

The run-level reporter emits per-scenario dashboards but no paired contrast
between two named scenarios, which is what a sequence-mode A/B needs: the arms
share the instance grid, so the per-instance difference has a far smaller SE
than the difference of two independent means.

Reads ``<run_dir>/<ts>_rpdf_comparison.csv`` (one row per instance x scenario,
``RPDf_BKS_data`` is the symmetric RPDf as a fraction) and prints:

1. per-scenario mean RPDf / elapsed / time%, pooled and per T slice;
2. paired contrasts ``a - b`` with SE, sigma and win/tie/loss, pooled and per
   slice -- negative means ``a`` is better;
3. the (n, c) cell table.

T slices are mandatory, not optional: pooled means have cancelled real effects
before (``plans/analysis/20260802/neh_cp_budget_allocation.md`` action 4).

Scenario labels may be abbreviated -- any unique suffix of the full label works,
so ``completion3_seq`` matches ``dv4_mcf_fmm_neh_cp_completion3_seq``.

Usage::

    uv run python scripts/20260804/summarize_seq_merge.py \\
        output/20260804_merge_neh_cp_last1_stage_seq/20260804T233244_618881 \\
        --contrast completion3_seq completion_seq \\
        --contrast midpoint3_seq midpoint2_seq

Every number is flow-level ``bestObj``, i.e. ``min(seed, NEH)``. Arms whose NEH
step failed to beat the seed report identical numbers, which is why the tie
counts run to about a third of the grid; a verdict on the NEH step itself needs
the step-level measurement plane (``docs/artifacts/obj_log.md``).
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

T_SLICES = (0.2, 0.4, 0.6)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("run_dir", type=Path, help="merged run directory")
    parser.add_argument(
        "--contrast",
        nargs=2,
        action="append",
        default=[],
        metavar=("A", "B"),
        help="paired contrast a - b; repeatable (negative = a better)",
    )
    return parser.parse_args()


def _load(run_dir: Path) -> pd.DataFrame:
    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if len(matches) != 1:
        sys.exit(f"expected exactly one *_rpdf_comparison.csv in {run_dir}")
    df = pd.read_csv(matches[0], dtype={"insIndex": str})
    df["rpdf"] = df["RPDf_BKS_data"] * 100.0
    return df


def _resolve(label: str, scenarios: list[str]) -> str:
    hits = [s for s in scenarios if s == label or s.endswith(label)]
    if len(hits) != 1:
        sys.exit(f"scenario {label!r} matched {len(hits)} labels: {hits or scenarios}")
    return hits[0]


def _contrast(piv: pd.DataFrame, a: str, b: str, mask: pd.Series | None) -> str:
    p = piv if mask is None else piv[mask]
    d = p[a] - p[b]
    mean = d.mean()
    se = d.std(ddof=1) / np.sqrt(len(d))
    # exact float equality is the right test here: a tie means both arms
    # returned the same incumbent objective, not merely a close one.
    win = int((d < 0).sum())
    loss = int((d > 0).sum())
    return (
        f"d={mean:+7.3f}  SE={se:5.3f}  {mean / se:+6.2f} sigma  "
        f"w/t/l={win}/{len(d) - win - loss}/{loss}  (N={len(d)})"
    )


def main() -> None:
    args = _parse_args()
    df = _load(args.run_dir)
    scenarios = list(dict.fromkeys(df["scenarioName"]))
    short = {s: s.split("neh_cp_")[-1] for s in scenarios}

    piv = df.pivot(index="insIndex", columns="scenarioName", values="rpdf").join(
        df.drop_duplicates("insIndex").set_index("insIndex")[["n", "c", "T"]]
    )
    if piv[scenarios].isna().any().any():
        sys.exit("scenarios do not share one instance grid (NaN after pivot)")

    print(f"# {args.run_dir}  ({len(scenarios)} scenarios x {len(piv)} instances)\n")
    print("## per-scenario means")
    print(
        f"{'scenario':28s}{'RPDf':>9s}{'elapsed':>10s}{'time%':>9s}"
        + "".join(f"{f'T={t}':>10s}" for t in T_SLICES)
    )
    for scenario, group in df.groupby("scenarioName", sort=False):
        row = f"{short[scenario]:28s}{group.rpdf.mean():9.3f}{group.elapsedTime.mean():10.3f}{group['time%'].mean():9.4f}"
        row += "".join(f"{group[group['T'] == t].rpdf.mean():10.3f}" for t in T_SLICES)
        print(row)

    for a_label, b_label in args.contrast:
        a, b = _resolve(a_label, scenarios), _resolve(b_label, scenarios)
        print(f"\n## {short[a]} - {short[b]}")
        print(f"  pooled  {_contrast(piv, a, b, None)}")
        for t in T_SLICES:
            print(f"  T={t}   {_contrast(piv, a, b, piv['T'] == t)}")
        for c in sorted(piv["c"].unique()):
            print(f"  c={c:<5d} {_contrast(piv, a, b, piv['c'] == c)}")

    print("\n## mean RPDf by (n, c)")
    print("  n    c |" + "".join(f"{short[s][:11]:>12s}" for s in scenarios))
    for (n, c), cell in piv.groupby(["n", "c"]):
        print(
            f"{n:3d} {c:4d} |" + "".join(f"{cell[s].mean():12.2f}" for s in scenarios)
        )


if __name__ == "__main__":
    main()
