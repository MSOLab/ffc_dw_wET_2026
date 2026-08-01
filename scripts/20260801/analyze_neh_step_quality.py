"""Isolate the NEH-CP step's own output from the flow's best incumbent.

Doc: ``plans/analysis/20260801/neh_cp_seq_source_full.md``.
Companion to ``analyze_neh_cp_seq_full.py``, which reads the run's
``*_rpdf_comparison.csv``. That CSV carries ``bestObj``, the best incumbent over
the **whole** subroutine flow — so on any instance where NEH-CP fails to beat
its seed, the reported objective is the *seed's*, identical across sequence
modes. That inflates the seeding-prefix effect and mutes the mode effect (it is
also why the flow-based paired mode comparisons show 453-479 exact ties out of
1440, against 99-119 once the NEH step is scored on its own output).

This script parses every instance's ``*_obj_log.json`` and splits the flow into
the objective **at the end of each step**, so the NEH-CP step can be scored on
its own output:

- ``seed_obj``  - incumbent handed to NEH-CP (last pre-NEH step; NaN for the
  unseeded baseline)
- ``neh_obj``   - incumbent NEH-CP itself ended with
- ``flow_best`` - min over the whole log, i.e. what ``bestObj`` reports

RPDf against ``BKS_data`` is computed with the report pipeline's own
``ffc_ddw_sum_et._calc.rpd_f`` rather than re-derived: a hand-written
``2*(obj-ref)/(obj+ref)`` is 0/0 on the zero-cost instances and drops ~57 of
them per scenario, which biases every mean upward.

Usage:
    uv run python scripts/20260801/analyze_neh_step_quality.py <run_dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from ffc_ddw_sum_et._calc import rpd_f

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helper
from analyze_neh_cp_seq_full import oracle_table  # noqa: E402  -- shared oracle

MODES = ("midpoint", "first_stage", "completion")
PREFIXES = {
    "neh_cp": "mcf_lb->fmm",
    "dv4_neh_cp": "dispatch_v4",
    "dv4_mcf_fmm_neh_cp": "dv4->mcf_lb->fmm",
}
BASELINE = "neh_cp_baseline"
TIE_TOL = 1e-9
MATCH_CSV = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")
BKS_CSV = Path("benchmarks/PRA2017/pra2017_bks_table.csv")
OUT_DIR = Path("analysis/20260801_neh_cp_seq_full")


def scenario_name(prefix: str, mode: str) -> str:
    return f"{prefix}_{mode}_seq"


def parse_obj_log(path: Path) -> dict[str, float]:
    """Objective at the end of each step, plus the flow-wide best."""
    payload = json.loads(path.read_text())
    data = {float(t): v for t, v in payload["obj_value"]["data"].items()}
    notes = {float(t): n for t, n in payload["obj_value"].get("notes", {}).items()}
    steps = sorted(notes.items())
    neh_obj = seed_obj = neh_seconds = np.nan
    for idx, (t, label) in enumerate(steps):
        if "neh_cp" in label:
            neh_obj = data[t]
            neh_seconds = t - steps[idx - 1][0] if idx else t
            if idx:
                seed_obj = data[steps[idx - 1][0]]
            break
    return {
        "seed_obj": seed_obj,
        "neh_obj": neh_obj,
        "neh_seconds": neh_seconds,
        "flow_best": min(data.values()) if data else np.nan,
    }


def collect(run_dir: Path) -> pd.DataFrame:
    rows = []
    for scen_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        for ins_dir in sorted(p for p in scen_dir.iterdir() if p.is_dir()):
            (log,) = ins_dir.glob("*_obj_log.json")
            row = parse_obj_log(log)
            row["scenarioName"] = scen_dir.name
            row["instanceName"] = ins_dir.name
            rows.append(row)
    return pd.DataFrame(rows)


def attach_bks(df: pd.DataFrame) -> pd.DataFrame:
    match = pd.read_csv(MATCH_CSV, dtype={"insIndex": str})
    match["instanceName"] = match["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    bks = pd.read_csv(BKS_CSV, dtype={"insIndex": str})
    out = df.merge(match[["insIndex", "instanceName"]], on="instanceName", how="left")
    out = out.merge(bks[["insIndex", "n", "c", "T", "R", "BKS_data"]], on="insIndex")
    for col in ("seed_obj", "neh_obj", "flow_best"):
        out[f"rpdf_{col.split('_')[0]}"] = [
            np.nan if pd.isna(obj) else 100.0 * rpd_f(obj, ref)
            for obj, ref in zip(out[col], out["BKS_data"], strict=True)
        ]
    return out


def paired(wide: pd.DataFrame, a: str, b: str) -> dict[str, object]:
    d = (wide[a] - wide[b]).dropna()
    n = len(d)
    se = d.std(ddof=1) / np.sqrt(n) if n else np.nan
    return {
        "pair": f"{a} - {b}",
        "n_paired": n,
        "mean_diff": d.mean(),
        "ci95": 1.96 * se,
        "sigma": d.mean() / se if se else np.nan,
        "win": int((d < -TIE_TOL).sum()),
        "tie": int((d.abs() <= TIE_TOL).sum()),
        "loss": int((d > TIE_TOL).sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = attach_bks(collect(args.run_dir))
    df.to_csv(args.out_dir / "step_objectives.csv", index=False)

    print("\n=== Block 7a - does NEH-CP improve on its seed? ===")
    seeded = df[df["seed_obj"].notna()].copy()
    seeded["improved"] = seeded["neh_obj"] < seeded["seed_obj"] - TIE_TOL
    seeded["equal"] = (seeded["neh_obj"] - seeded["seed_obj"]).abs() <= TIE_TOL
    imp = seeded.groupby("scenarioName").agg(
        n=("improved", "size"),
        improved=("improved", "sum"),
        equal=("equal", "sum"),
        mean_seed_rpdf=("rpdf_seed", "mean"),
        mean_neh_rpdf=("rpdf_neh", "mean"),
        mean_flow_rpdf=("rpdf_flow", "mean"),
        mean_neh_seconds=("neh_seconds", "mean"),
    )
    imp["improved_pct"] = 100.0 * imp["improved"] / imp["n"]
    print(imp.round(2).to_string())

    secs = df.groupby("scenarioName")["neh_seconds"].agg(["mean", "min", "max"])
    print("\nNEH-CP step seconds (budget-homogeneity check, baseline included):")
    print(secs.round(2).to_string())

    print("\n=== Block 7b - scenario ranking on the NEH step's own output ===")
    rank = (
        df.groupby("scenarioName")["rpdf_neh"]
        .agg(["mean", "median", "std"])
        .sort_values("mean")
    )
    print(rank.round(2).to_string())

    print("\n=== Block 7c - mode effect on the NEH step's own output ===")
    wide = df.pivot(index="instanceName", columns="scenarioName", values="rpdf_neh")
    rows = []
    for prefix, label in PREFIXES.items():
        for m_a, m_b in combinations(MODES, 2):
            row = paired(wide, scenario_name(prefix, m_a), scenario_name(prefix, m_b))
            row["prefix"] = label
            row["pair"] = f"{m_a} - {m_b}"
            rows.append(row)
    for mode in MODES:
        rows.append(
            {
                **paired(wide, scenario_name("neh_cp", mode), BASELINE),
                "prefix": "vs baseline",
            }
        )
    modes = pd.DataFrame(rows)
    modes["verdict"] = np.where(
        modes["ci95"].abs() >= modes["mean_diff"].abs(),
        "indistinguishable",
        np.where(modes["mean_diff"] < 0, "a better", "b better"),
    )
    print(
        modes[
            [
                "prefix",
                "pair",
                "mean_diff",
                "ci95",
                "sigma",
                "win",
                "tie",
                "loss",
                "verdict",
            ]
        ]
        .round(3)
        .to_string(index=False)
    )

    print("\n=== Block 7d - oracle mode portfolios on the NEH step's own output ===")
    frames = []
    for prefix, label in PREFIXES.items():
        mat = wide[[scenario_name(prefix, m) for m in MODES]].dropna()
        mat.columns = list(MODES)
        frames.append(oracle_table(mat, label))
    oracle = pd.concat(frames, ignore_index=True).sort_values(["family", "oracle_mean"])
    print(oracle.round(3).to_string(index=False))

    imp.to_csv(args.out_dir / "neh_vs_seed.csv")
    rank.to_csv(args.out_dir / "neh_step_ranking.csv")
    modes.to_csv(args.out_dir / "neh_step_mode_effect.csv", index=False)
    oracle.to_csv(args.out_dir / "neh_step_oracle_portfolios.csv", index=False)
    print(f"\nwrote 5 CSVs to {args.out_dir}")


if __name__ == "__main__":
    main()
