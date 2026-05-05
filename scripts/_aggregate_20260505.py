"""Aggregate analysis/results_index_20260505.csv into per-(RUN, scenario) means.

Writes:
- analysis/results_index_20260505_agg.json (list of dicts)
- analysis/results_index_20260505_agg.csv  (flat table)

Throwaway helper for the 2026-05-05 weekly review; not for production use.
"""

import json
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "analysis" / "results_index_20260505.csv"
OUT_JSON = REPO / "analysis" / "results_index_20260505_agg.json"
OUT_CSV = REPO / "analysis" / "results_index_20260505_agg.csv"


def main() -> None:
    df = pd.read_csv(SRC, low_memory=False)
    valid = df[df["BKS_data"] > 0].copy()

    records = []
    for (r, scen), grp in valid.groupby(["runNumber", "scenarioName"], sort=True):
        bo_n = grp["bestObj"].notna().sum()
        mcf_mean = grp["mcfLb"].mean() if grp["mcfLb"].notna().any() else None
        if bo_n > 0:
            rec = {
                "run": int(r),
                "scen": scen,
                "metric": "bestObj",
                "mean_RPDf": round(grp["RPDf_BKS_data"].mean(), 4),
                "mean_value": round(grp["bestObj"].mean(), 1),
                "n": int(grp["instanceName"].count()),
                "mcfLb_mean": round(mcf_mean, 1) if mcf_mean is not None else None,
            }
        else:
            rec = {
                "run": int(r),
                "scen": scen,
                "metric": "mcfLb (no incumbent)",
                "mean_RPDf": None,
                "mean_value": round(mcf_mean, 1) if mcf_mean is not None else None,
                "n": int(grp["instanceName"].count()),
                "mcfLb_mean": round(mcf_mean, 1) if mcf_mean is not None else None,
            }
        records.append(rec)

    OUT_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    pd.DataFrame(records).to_csv(OUT_CSV, index=False)

    print(f"Wrote {OUT_JSON.relative_to(REPO)} ({len(records)} records)")
    print()

    no_inc = [r for r in records if r["metric"].startswith("mcfLb")]
    print(f"--- No-incumbent RUNs/scenarios ({len(no_inc)}) ---")
    for rec in no_inc:
        print(f"  RUN {rec['run']:>2} {rec['scen']:50s} mcfLb_mean={rec['mean_value']}")
    print()

    ranked = sorted([r for r in records if r["metric"] == "bestObj"], key=lambda x: x["mean_RPDf"])
    print("--- Top 10 by mean RPDf (lowest = best) ---")
    for rec in ranked[:10]:
        print(f"  RPDf={rec['mean_RPDf']:.4f} bestObj={rec['mean_value']:>11.1f} RUN {rec['run']:>2} {rec['scen']}")
    print()
    print("--- Worst 10 ---")
    for rec in ranked[-10:]:
        print(f"  RPDf={rec['mean_RPDf']:.4f} bestObj={rec['mean_value']:>11.1f} RUN {rec['run']:>2} {rec['scen']}")


if __name__ == "__main__":
    main()
