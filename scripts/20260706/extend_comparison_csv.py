"""Extend a p25/p50/p75 TL comparison CSV to include p25–p75 (7 scenarios).

Reads the existing ``sw_cp_tl_p25_p50_p75_comparison.csv`` (per c,m,n row)
and adds columns for the missing scenarios: ``p30``, ``p40``, ``p60``, ``p70``.

The κ values are the B2 (per-window unweighted-mean) k from
``k_for_capture.py`` run on the 270-instance u2_pf2 pool with
``P_LEVELS = (25, 30, 40, 50, 60, 70, 75)``.

Usage::

    uv run python scripts/20260706/extend_comparison_csv.py \
        output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/analysis/sw_cp_tl_p25_p50_p75_comparison.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

KAPPA: dict[str, float] = {
    "p25": 0.000311,
    "p30": 0.000388,
    "p40": 0.000773,
    "p50": 0.001811,
    "p60": 0.004570,
    "p70": 0.015762,
    "p75": 0.031593,
}
SCENARIOS = ("p25", "p30", "p40", "p50", "p60", "p70", "p75")


def extend(input_path: Path, output_path: Path | None = None) -> Path:
    if output_path is None:
        output_path = input_path.with_name(
            input_path.stem.replace("p25_p50_p75", "p25_p75_7scenarios") + ".csv"
        )

    with input_path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        median_ntf = float(row["median_ntf"])
        batch_count = int(row["batch_count"])
        existing_tl = float(row["existing_TL_s"])
        for s in SCENARIOS:
            tl_s = KAPPA[s] * median_ntf
            row[f"{s}_TL_s"] = tl_s
            row[f"{s}_over_existing"] = tl_s / existing_tl
            row[f"{s}_pass_total_s"] = tl_s * batch_count

    return _write_csv(rows, output_path)


def _write_csv(rows: list[dict], output_path: Path) -> Path:
    per_scenario_cols: list[str] = []
    for s in SCENARIOS:
        per_scenario_cols += [
            f"{s}_TL_s",
            f"{s}_over_existing",
            f"{s}_pass_total_s",
            f"{s}_over_existing_mean",
            f"{s}_over_existing_median",
        ]

    fieldnames = [
        "c",
        "m",
        "unfixed_u",
        "n",
        "batch_count",
        "median_ntf",
        *per_scenario_cols,
        "existing_TL_s",
        "existing_pass_total_s",
    ]

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            for s in SCENARIOS:
                vals = [float(r[f"{s}_over_existing"]) for r in rows]
                row[f"{s}_over_existing_mean"] = statistics.mean(vals)
                row[f"{s}_over_existing_median"] = statistics.median(vals)
            writer.writerow(row)

    return output_path


def _compute_over_existing(rows: list[dict], scenario: str) -> list[float]:
    """Compute over_existing values, always from kappa to avoid stale rounding."""
    return [
        KAPPA[scenario] * float(r["median_ntf"]) / float(r["existing_TL_s"])
        for r in rows
    ]


def print_summary(input_csv: Path) -> None:
    with input_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))
    header = f"{'':>6} {'mean%':>8} {'median%':>8}"
    print(header)
    print("-" * len(header))
    for s in SCENARIOS:
        vals = _compute_over_existing(rows, s)
        print(
            f"{s:>6} {statistics.mean(vals) * 100:>7.1f}% "
            f"{statistics.median(vals) * 100:>7.1f}%"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_csv", type=Path, help="Path to p25_p50_p75 comparison CSV"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: alongside input)",
    )
    parser.add_argument(
        "-s",
        "--summary-only",
        action="store_true",
        help="Print summary to stdout only (do not write CSV)",
    )
    args = parser.parse_args(argv)

    if args.summary_only:
        print_summary(args.input_csv)
    else:
        out = extend(args.input_csv, args.output)
        print(f"wrote {out}")
        print()
        print_summary(out)


if __name__ == "__main__":
    main()
