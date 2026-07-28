"""Initialization-budget curve analysis (dv4_c5init_f10/f20/f40 vs full C5 init).

Reads a merged POST_PROCESS_ONLY run dir (built by
``scripts/build_merged_run_dir.py``) and emits the tables that
``plans/analysis/20260729/init_budget_curve.md`` quotes:

* per-scenario final RPDf (mean / median / paired delta vs the baseline)
* the (T, R) and size-cell decomposition
* the step trajectory (mean Time%, mean RPDf per controller step), parsed from
  the run's ``*_method_mean_rpdf_and_mean_norm_time_scatter.html`` payload —
  the same numbers the chart plots, so the doc and the chart cannot drift
* where each shortened-init trajectory crosses the baseline trajectory

Usage::

    uv run python scripts/20260729/analyze_init_budget_curve.py \\
        output/20260728_init_budget_merge/20260729T041116_435991
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASELINE = "a_v2_kappa005_max8"
"""Direct baseline: same ISW-CP tail, full (100 %) C5 initialization."""

NEW_SCENARIOS = ["dv4_c5init_f10", "dv4_c5init_f20", "dv4_c5init_f40"]

OUT_DIR = Path("analysis/20260729_init_budget_curve")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merged_run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args()


def load_wide(run_dir: Path) -> pd.DataFrame:
    """Per-instance RPDf/obj, one column pair per scenario (report.xlsx)."""
    xlsx = next(run_dir.glob("*_report.xlsx"))
    return pd.read_excel(xlsx, sheet_name="analysis_wide")


def load_trajectory(run_dir: Path) -> pd.DataFrame:
    """Step trajectory from the scatter chart's embedded payload."""
    html = next(run_dir.glob("*_method_mean_rpdf_and_mean_norm_time_scatter.html"))
    text = html.read_text()
    match = re.search(r"const payload = (\{.*?\});\n", text, re.DOTALL)
    if match is None:
        raise ValueError(f"no payload found in {html}")
    payload = json.loads(match.group(1))
    rows = []
    for trace in payload["traces"]:
        for i, label in enumerate(trace["label"]):
            rows.append(
                {
                    "scenario": trace["scenario"],
                    "step_idx": i,
                    "label": label,
                    "method": trace["method"][i],
                    "time_frac": trace["x"][i],
                    "mean_rpdf": trace["y"][i],
                    "instance_count": trace["instance_count"][i],
                }
            )
    return pd.DataFrame(rows)


def scenario_summary(wide: pd.DataFrame) -> pd.DataFrame:
    """Final-incumbent RPDf per scenario, plus the paired delta vs BASELINE."""
    scenarios = [c[len("RPDf_") :] for c in wide.columns if c.startswith("RPDf_")]
    base = wide[f"RPDf_{BASELINE}"]
    rows = []
    for s in scenarios:
        rpdf = wide[f"RPDf_{s}"]
        delta = rpdf - base
        rows.append(
            {
                "scenario": s,
                "mean_rpdf_pct": rpdf.mean() * 100,
                "median_rpdf_pct": rpdf.median() * 100,
                "se_pct": rpdf.std(ddof=1) / len(rpdf) ** 0.5 * 100,
                "mean_obj": wide[f"obj_{s}"].mean(),
                "d_mean_obj_vs_base": wide[f"obj_{s}"].mean()
                - wide[f"obj_{BASELINE}"].mean(),
                "d_mean_rpdf_pp": delta.mean() * 100,
                "d_paired_se_pp": delta.std(ddof=1) / len(delta) ** 0.5 * 100,
                "win": int((rpdf < base).sum()),
                "tie": int((rpdf == base).sum()),
                "loss": int((rpdf > base).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("mean_rpdf_pct").reset_index(drop=True)


def cell_table(wide: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Mean RPDf (%) per cell, one column per scenario of interest."""
    scenarios = NEW_SCENARIOS + [BASELINE]
    frame = wide[keys].copy()
    for s in scenarios:
        frame[s] = wide[f"RPDf_{s}"] * 100
    out = frame.groupby(keys)[scenarios].mean().round(3)
    for s in NEW_SCENARIOS:
        out[f"d_{s}"] = (out[s] - out[BASELINE]).round(3)
    out["n_ins"] = frame.groupby(keys).size()
    return out.reset_index()


def within_family(wide: pd.DataFrame) -> pd.DataFrame:
    """Paired f-vs-f comparisons inside the new run.

    These are the only unconfounded budget comparisons available: same code,
    same machine, same run, same dispatch prefix, same tail — the
    initialization TL is the single difference. The baseline comparisons in
    ``scenario_summary`` additionally carry the prefix and the code drift.
    """
    rows = []
    for i, a in enumerate(NEW_SCENARIOS):
        for b in NEW_SCENARIOS[i + 1 :]:
            delta = wide[f"RPDf_{b}"] - wide[f"RPDf_{a}"]
            rows.append(
                {
                    "pair": f"{b} - {a}",
                    "d_mean_rpdf_pp": delta.mean() * 100,
                    "paired_se_pp": delta.std(ddof=1) / len(delta) ** 0.5 * 100,
                    "b_better": int((delta < 0).sum()),
                    "tie": int((delta == 0).sum()),
                    "b_worse": int((delta > 0).sum()),
                }
            )
    return pd.DataFrame(rows)


def prefix_value(run_dir: Path, wide: pd.DataFrame) -> pd.DataFrame:
    """How much the v4 dispatch prefix is worth, and a code-drift probe.

    ``summary.csv`` carries two initialization values per instance:
    ``initObj`` (the flow's first reported incumbent — the dispatch schedule in
    the new scenarios, the MCF-derived schedule in the baseline) and
    ``dispatchedObj`` (the MCF-derived full schedule's own objective,
    independent of best-so-far). Comparing them answers §7-4; comparing the
    baseline's against the new run's answers whether code drift moved the
    deterministic MCF-LB step at all.
    """
    # Not a glob: the per-step summaries (e.g.
    # `*_calc_mcf_lb_and_derive_full_sch_summary.csv`) share the suffix.
    summary = pd.read_csv(run_dir / f"{run_dir.name}_summary.csv")
    piv = summary.pivot_table(
        index="instanceName",
        columns="scenarioName",
        values=["initObj", "dispatchedObj"],
        aggfunc="first",
    )
    init, mcf = piv["initObj"], piv["dispatchedObj"]
    bks = (
        summary[summary.scenarioName == BASELINE]
        .set_index("instanceName")["bks"]
        .reindex(init.index)
    )
    dispatch = init[NEW_SCENARIOS[0]]
    mcf_new = mcf[NEW_SCENARIOS[0]]

    def rpdf(obj: pd.Series) -> float:
        return (2 * (obj - bks) / (obj + bks)).mean() * 100

    return pd.DataFrame(
        [
            {
                "metric": "mean RPDf %, v4 dispatch schedule",
                "value": round(rpdf(dispatch), 3),
            },
            {
                "metric": "mean RPDf %, MCF-derived schedule",
                "value": round(rpdf(mcf_new), 3),
            },
            {
                "metric": "mean RPDf %, best of the two (post-MCF incumbent)",
                "value": round(rpdf(pd.concat([dispatch, mcf_new], axis=1).min(1)), 3),
            },
            {
                "metric": "instances where dispatch beats MCF-derived",
                "value": int((dispatch < mcf_new).sum()),
            },
            {
                "metric": "instances where the two tie",
                "value": int((dispatch == mcf_new).sum()),
            },
            {
                "metric": f"instances where new MCF obj == {BASELINE} MCF obj",
                "value": int((mcf_new == init[BASELINE]).sum()),
            },
            {"metric": "instances", "value": len(wide)},
        ]
    )


def crossing_points(traj: pd.DataFrame) -> pd.DataFrame:
    """Time% from which a scenario's curve stays at or below the baseline's.

    Both curves are best-so-far means, so each is monotone non-increasing in
    Time%. Reported is the *last* sign change, not the first: a shortened-init
    curve can dip below early (the dispatch prefix) and come back up while the
    baseline is still inside its longer initialization. Compared on a common
    Time% grid by linear interpolation.
    """
    base = traj[traj.scenario == BASELINE].sort_values("time_frac")
    rows = []
    for s in NEW_SCENARIOS:
        cur = traj[traj.scenario == s].sort_values("time_frac")
        grid = np.array(sorted(set(cur.time_frac) | set(base.time_frac)))
        cur_y = np.interp(grid, cur.time_frac.to_numpy(), cur.mean_rpdf.to_numpy())
        base_y = np.interp(grid, base.time_frac.to_numpy(), base.mean_rpdf.to_numpy())
        diff = cur_y - base_y
        # Each curve is only defined from its own first point onwards; restrict
        # to the overlap so np.interp's flat left extrapolation is not read as
        # a crossing.
        lo = max(cur.time_frac.min(), base.time_frac.min())
        overlap = grid >= lo
        above = grid[overlap & (diff > 0)]
        below_after = grid[
            overlap & (diff <= 0) & (grid > (above[-1] if len(above) else -1))
        ]
        rows.append(
            {
                "scenario": s,
                "last_time_frac_above": float(above[-1]) if len(above) else None,
                "stays_below_from": float(below_after[0]) if len(below_after) else None,
                "max_gap_pp": float(diff[overlap].max()) * 100,
                "final_gap_pp": float(diff[-1]) * 100,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    run_dir = args.merged_run_dir
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    wide = load_wide(run_dir)
    traj = load_trajectory(run_dir)

    summary = scenario_summary(wide)
    tr = cell_table(wide, ["T", "R"])
    size = cell_table(wide, ["n", "c", "totalMcCount"])
    cross = crossing_points(traj)
    prefix = prefix_value(run_dir, wide)
    family = within_family(wide)

    family.to_csv(out_dir / "within_family.csv", index=False)
    prefix.to_csv(out_dir / "prefix_value.csv", index=False)
    summary.to_csv(out_dir / "scenario_summary.csv", index=False)
    tr.to_csv(out_dir / "tr_cells.csv", index=False)
    size.to_csv(out_dir / "size_cells.csv", index=False)
    traj.to_csv(out_dir / "trajectory.csv", index=False)
    cross.to_csv(out_dir / "crossing.csv", index=False)

    pd.set_option("display.width", 200)
    print(f"instances: {len(wide)}\n")
    print("== final RPDf per scenario (delta vs %s) ==" % BASELINE)
    print(summary.round(3).to_string(index=False))
    print("\n== step trajectory (top-level steps) ==")
    print(
        traj[traj.method != "incremental_sw_cp"]
        .pivot_table(index="method", columns="scenario", values="mean_rpdf", sort=False)
        .round(4)
        .to_string()
    )
    print("\n== within-family paired budget comparisons (unconfounded) ==")
    print(family.round(3).to_string(index=False))
    print("\n== v4 dispatch prefix value / MCF-LB drift probe ==")
    print(prefix.to_string(index=False))
    print("\n== crossing vs baseline ==")
    print(cross.round(4).to_string(index=False))
    print("\n== (T, R) cells: mean RPDf %% ==")
    print(tr.to_string(index=False))
    print(f"\nwrote CSVs to {out_dir}/")


if __name__ == "__main__":
    main()
