"""Analyze the NEH-CP sequence-source pilot: mode effect vs CP noise.

Doc: ``plans/analysis/20260731/neh_cp_seq_source_pilot.md``.
Config under test: ``metadata/20260731/neh_cp_seq_source_compare.yaml``.

Answers three questions about a ``neh_cp_seq_source_compare`` run:

1. ~~Is ``bottleneck`` a distinct sequence mode?~~ **Deleted 2026-08-04**
   (proven degenerate — always selects the first stage).
2. How large is the mode effect relative to CP-SAT noise?
3. How large is the seeding-prefix effect?

Blocks 1 and 2 need the ``bottleneck`` scenarios, which were dropped from the
config after this pilot; the script skips whichever blocks the run lacks, so it
still runs against later ``*_seq_source_compare`` runs.

Usage:
    uv run python scripts/20260731/analyze_neh_cp_seq_pilot.py <run_dir>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

MODES = ("midpoint", "first_stage", "completion")
PREFIXES = {
    "neh_cp": "mcf_lb->fmm",
    "dv4_neh_cp": "dispatch_v4",
    "dv4_mcf_fmm_neh_cp": "dv4->mcf_lb->fmm",
}
# The two ``.*?`` gaps make this match both log formats: the pre-2026-08-04
# lines that carry ``dist_to_bottleneck=`` between first_stage and completion,
# and today's lines that carry ``tiebreak=`` / ``end_stage=`` after the source.
DIAG_RE = re.compile(
    r"seq source=(?P<mode>\w+) .*?"
    r"dist_to_midpoint=(?P<midpoint>[\d.]+) "
    r"dist_to_first_stage=(?P<first_stage>[\d.]+) .*?"
    r"dist_to_completion=(?P<completion>[\d.]+) "
    r"dist_to_job_priority=(?P<job_priority>[\d.]+)"
)


def load_rpdf(run_dir: Path) -> pd.DataFrame:
    """Load ``*_rpdf_comparison.csv`` with RPDf in percent."""
    (path,) = run_dir.glob("*_rpdf_comparison.csv")
    df = pd.read_csv(path, dtype={"insIndex": str})
    df["rpdf"] = df["RPDf_BKS_data"] * 100.0
    return df


def block1_bottleneck_stage(run_dir: Path) -> None:
    """Which stage does the minimum-idle rule actually select?

    As of 2026-08-04 the bottleneck mode has been deleted — it was proven
    degenerate (always selects the first stage on left-shifted schedules).
    """
    print("\n=== Block 1: bottleneck stage (deleted 2026-08-04) ===")
    print("  bottleneck mode deleted — always selected the first stage.")


def block2_sequence_distances(run_dir: Path) -> None:
    """How far apart are the four derived orders, per the controller's log?"""
    print("\n=== Block 2: pairwise sequence distance (controller diagnostics) ===")
    rows = []
    for log in run_dir.glob("*/*/*SubroutineController.log"):
        for m in DIAG_RE.finditer(log.read_text()):
            rows.append({k: v for k, v in m.groupdict().items()})
    if not rows:
        print("  (no seq diagnostics found — skipped)")
        return
    df = pd.DataFrame(rows)
    for col in df.columns[1:]:
        df[col] = df[col].astype(float)
    print(f"  {len(df)} derived orders")
    print(df.groupby("mode")[list(df.columns[1:])].agg(["min", "max"]).to_string())


def block3_mode_vs_noise(rpdf: pd.DataFrame) -> None:
    """Mode spread vs CP-SAT noise."""
    print("\n=== Block 3: mode effect vs CP noise ===")
    piv = rpdf.pivot(index="insIndex", columns="scenarioName", values="rpdf")
    for prefix, label in PREFIXES.items():
        cols = [f"{prefix}_{m}_seq" for m in MODES if f"{prefix}_{m}_seq" in piv]
        if len(cols) < 2:
            continue
        means = piv[cols].mean()
        spread = means.max() - means.min()
        print(f"  {label:18s} mode spread = {spread:5.2f} pp")


def block4_prefix_effect(rpdf: pd.DataFrame) -> None:
    """Seeding-prefix effect, averaged over the sequence modes."""
    print("\n=== Block 4: seeding prefix effect (mean RPDf %) ===")
    piv = rpdf.pivot(index="insIndex", columns="scenarioName", values="rpdf")
    if "neh_cp_baseline" in piv:
        base = piv["neh_cp_baseline"]
        print(
            f"  {'none (baseline)':18s} mean={base.mean():5.2f}  "
            f"per-instance={[round(x, 2) for x in base]}"
        )
    for prefix, label in PREFIXES.items():
        cols = [f"{prefix}_{m}_seq" for m in MODES if f"{prefix}_{m}_seq" in piv]
        if not cols:
            continue
        print(
            f"  {label:18s} mean={piv[cols].values.mean():5.2f}  "
            f"per-instance={[round(x, 2) for x in piv[cols].mean(axis=1)]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    rpdf = load_rpdf(args.run_dir)
    cells = rpdf[["n", "c", "T", "R", "W"]].drop_duplicates()
    print(
        f"run: {args.run_dir}\n"
        f"  {rpdf['scenarioName'].nunique()} scenarios x "
        f"{rpdf['insIndex'].nunique()} instances, {len(cells)} parameter cell(s)"
    )
    print("\n=== mean RPDf (%) by mode x prefix ===")
    print(
        rpdf.pivot_table(index="scenarioName", values="rpdf", aggfunc="mean")
        .sort_values("rpdf")
        .round(2)
        .to_string()
    )
    block1_bottleneck_stage(args.run_dir)
    block2_sequence_distances(args.run_dir)
    block3_mode_vs_noise(rpdf)
    block4_prefix_effect(rpdf)


if __name__ == "__main__":
    main()
