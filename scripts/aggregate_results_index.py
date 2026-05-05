"""Aggregate a long-form results index CSV (per-instance × scenario) into
per-(RUN, scenario) summary rows for the weekly review.

Reads ``analysis/results_index_<date>.csv`` (built by
``scripts/build_results_index_<date>.py``) and writes:
- ``<input>_agg.csv`` — flat one-row-per-(RUN, scenario) table
- ``<input>_agg.json`` — same as JSON for downstream insertion scripts

Each record's ``metric`` field is ``bestObj`` (full-schedule wET available)
or ``mcfLb (no incumbent)`` (algorithm did not register an AlgRecord —
typically ``mcf_lb_only`` or new-step-introduction RUNs where controller
integration is incomplete; in that case ``mean_value`` falls back to
``mcfLb`` and ``mean_RPDf`` is ``None``).

Aggregation is restricted to instances with ``BKS_data > 0`` (RPDf is
unstable when BKS=0).

Usage:
    uv run python scripts/aggregate_results_index.py analysis/results_index_<date>.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def aggregate(df: pd.DataFrame) -> list[dict]:
    """Return one record per (runNumber, scenarioName).

    Includes ALL benchmark instances (no BKS_data > 0 filter). The build
    script handles RPDf for the BKS_data=0 + bestObj=0 corner case
    (defines it as 0.0); other BKS=0 cases produce RPDf=2.0 (max
    symmetric distance) which is included in the average as a real
    signal rather than dropped.
    """
    valid = df
    records = []
    for (r, scen), grp in valid.groupby(["runNumber", "scenarioName"], sort=True):
        bo_n = grp["bestObj"].notna().sum()
        mcf_mean = grp["mcfLb"].mean() if grp["mcfLb"].notna().any() else None
        if bo_n > 0:
            records.append({
                "run": int(r),
                "scen": scen,
                "metric": "bestObj",
                "mean_RPDf": round(grp["RPDf_BKS_data"].mean(), 4),
                "mean_value": round(grp["bestObj"].mean(), 1),
                "n": int(grp["instanceName"].count()),
            })
        else:
            records.append({
                "run": int(r),
                "scen": scen,
                "metric": "mcfLb (no incumbent)",
                "mean_RPDf": None,
                "mean_value": round(mcf_mean, 1) if mcf_mean is not None else None,
                "n": int(grp["instanceName"].count()),
            })
    return records


def fmt_int(x):
    return f"{x:,.0f}" if x is not None else "—"


def print_summary(records: list[dict], top_n: int = 10, bottom_n: int = 5) -> None:
    no_inc = [r for r in records if r["metric"].startswith("mcfLb")]
    if no_inc:
        print(f"\n--- No-incumbent RUNs/scenarios ({len(no_inc)}) ---")
        for rec in no_inc:
            print(f"  RUN {rec['run']:>2} {rec['scen'][:40]:40s} mcfLb_mean={fmt_int(rec['mean_value'])}")

    ranked = sorted([r for r in records if r["metric"] == "bestObj"], key=lambda x: x["mean_RPDf"])
    print(f"\n--- Top {top_n} by mean RPDf (lowest = best) ---")
    for rec in ranked[:top_n]:
        print(f"  RPDf={rec['mean_RPDf']:.4f} bestObj={fmt_int(rec['mean_value']):>11s} RUN {rec['run']:>2} {rec['scen']}")

    print(f"\n--- Bottom {bottom_n} by mean RPDf ---")
    for rec in ranked[-bottom_n:]:
        print(f"  RPDf={rec['mean_RPDf']:.4f} bestObj={fmt_int(rec['mean_value']):>11s} RUN {rec['run']:>2} {rec['scen']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n", 1)[0])
    ap.add_argument("input", type=Path, help="Path to results_index_<date>.csv")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--bottom", type=int, default=5)
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(args.input, low_memory=False)
    records = aggregate(df)

    out_csv = args.input.with_name(args.input.stem + "_agg.csv")
    out_json = args.input.with_name(args.input.stem + "_agg.json")
    pd.DataFrame(records).to_csv(out_csv, index=False)
    out_json.write_text(json.dumps(records, ensure_ascii=False, indent=2))

    print(f"Wrote {out_csv} ({len(records)} records)")
    print(f"Wrote {out_json}")
    print_summary(records, top_n=args.top, bottom_n=args.bottom)


if __name__ == "__main__":
    main()
