"""Analyze bestObj randomness across N repeated `uv run main.py` runs.

Reads the list of timestamp dirs from a file (one per line, relative to
output/20260423/) and aggregates bestObj per (instanceName, scenarioName) pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def load_runs(new_dirs_file: Path, base_dir: Path) -> pd.DataFrame:
    dirs = [d.strip() for d in new_dirs_file.read_text().splitlines() if d.strip()]
    frames: list[pd.DataFrame] = []
    for d in dirs:
        csv_path = base_dir / d / f"{d}_summary.csv"
        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)
        df = pd.read_csv(csv_path)
        df["runId"] = d
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    base_dir = Path("output/20260423")
    new_dirs_file = Path("/tmp/new_dirs.txt")

    df = load_runs(new_dirs_file, base_dir)
    n_runs = df["runId"].nunique()
    n_instances = df["instanceName"].nunique()
    n_scenarios = df["scenarioName"].nunique()
    print(f"runs={n_runs}  instances={n_instances}  scenarios={n_scenarios}  rows={len(df)}")
    print(f"expected rows = {n_runs * n_instances * n_scenarios}")

    group_cols = ["instanceName", "scenarioName"]
    agg = (
        df.groupby(group_cols)["bestObj"]
        .agg(
            n="count",
            min="min",
            max="max",
            mean="mean",
            std="std",
            unique="nunique",
        )
        .reset_index()
    )
    agg["range"] = agg["max"] - agg["min"]
    agg["cv_pct"] = (agg["std"] / agg["mean"]) * 100.0

    agg = agg.sort_values(["instanceName", "scenarioName"]).reset_index(drop=True)

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:.3f}")

    print("\n=== bestObj randomness per (instance, scenario) ===")
    print(agg.to_string(index=False))

    print("\n=== deterministic scenarios (unique == 1) ===")
    deterministic = agg[agg["unique"] == 1]
    print(f"count = {len(deterministic)} / {len(agg)}")

    print("\n=== top-5 most random (by cv_pct) ===")
    print(agg.nlargest(5, "cv_pct").to_string(index=False))

    print("\n=== top-5 widest range (abs) ===")
    print(agg.nlargest(5, "range").to_string(index=False))

    print("\n=== raw bestObj values per (instance, scenario) ===")
    pivot = df.pivot_table(
        index=group_cols,
        columns="runId",
        values="bestObj",
        aggfunc="first",
    )
    pivot.columns = [f"r{i + 1}" for i in range(pivot.shape[1])]
    print(pivot.to_string())

    out_csv = Path("output") / "20260424" / "bestobj_randomness_summary.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    agg.to_csv(out_csv, index=False)
    print(f"\nwrote {out_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
