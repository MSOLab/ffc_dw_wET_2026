"""Flatten MCF-LB seed-candidate objectives into an analysis CSV.

Post-processes the per-instance ``mcf_lb_diagnostic.yaml`` files written by a
run and emits one CSV row per (instance, scenario) containing:

* instance generation parameters (n, c, m, T, R, W, rep) parsed from the name;
* the registered-result summary (``final_obj``, ``best_sched_source``,
  ``combined_lb``, ``argmax_stage_id``, ``r1_full_sch_obj``, ``r2_full_sch_obj``);
* the last-stage candidate split (``last_stage_{r1|r2}_{midpoint|simple}``);
* every non-last stage's candidate objectives flattened to
  ``stage_<q>_{anchor}_{bn2d|mixed_fw|mixed_rv}`` columns.

The *before* baseline is the same instance's row from the last-stage,
``seed_compare``-off scenario (``--baseline-scenario``); after collecting all
rows the script joins each non-baseline row to its baseline by ``instance_name``
and adds ``before_obj`` / ``improvement`` / ``improvement_pct``.

Usage:
    uv run python scripts/analyze_seed_candidates.py <run_output_dir> \
        [--baseline-scenario mcf_lb_last_stage_baseline] [--out <path.csv>]
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
from typing import Any

import pandas as pd
from routix.io import load_yaml

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

_DIAG_KEY = "calc_mcf_lb_and_derive_full_sch_diagnostic"
_SUMMARY_FIELDS = (
    "final_obj",
    "best_sched_source",
    "combined_lb",
    "argmax_stage_id",
    "r1_full_sch_obj",
    "r2_full_sch_obj",
)
_DEFAULT_BASELINE = "mcf_lb_last_stage_baseline"


def _parse_instance_name(part: str):
    """Try a path component as a PRA2017 instance name (with/without .txt)."""
    parsed = FFcDDWParameters._parse_instance_name(part)
    if parsed is None and not part.endswith(".txt"):
        parsed = FFcDDWParameters._parse_instance_name(f"{part}.txt")
    return parsed


def _resolve_scenario_and_instance(
    diag_path: Path, run_dir: Path
) -> tuple[str, str, Any]:
    """Derive (scenario, instance_name, InstanceParams|None) from the path.

    Scenario is the first path component under ``run_dir``; the instance is the
    component that parses as a PRA2017 instance name.
    """
    rel_parts = diag_path.relative_to(run_dir).parts
    scenario = rel_parts[0] if rel_parts else ""
    instance_name = ""
    gen = None
    for part in rel_parts:
        parsed = _parse_instance_name(part)
        if parsed is not None:
            instance_name = part
            gen = parsed
            break
    if not instance_name:
        # Parse miss (e.g. a non-PRA2017 name). The layout is
        # ``<scenario>/<instance>/progress/<file>``, so prefer the instance
        # dir (parent of ``progress``) over the leaf dir, which would
        # collapse every instance to "progress".
        parents = diag_path.parts
        if "progress" in parents:
            instance_name = parents[parents.index("progress") - 1]
        else:
            instance_name = diag_path.parent.name
    return scenario, instance_name, gen


def _row_from_diag(diag: dict[str, Any]) -> dict[str, Any]:
    """Flatten one diagnostic's summary + per-stage candidate objectives."""
    row: dict[str, Any] = {f: diag.get(f) for f in _SUMMARY_FIELDS}
    for record in diag.get("per_stage_records") or []:
        candidate_objs = record.get("candidate_objs") or {}
        if record.get("is_last_stage"):
            prefix = "last_stage"
        else:
            prefix = f"stage_{record.get('stage_id')}"
        for key, obj in candidate_objs.items():
            row[f"{prefix}_{key}"] = obj
    return row


def collect_rows(run_dir: Path) -> list[dict[str, Any]]:
    """One row per (instance, scenario) across all diagnostic YAMLs."""
    rows: list[dict[str, Any]] = []
    # The runner writes ``<instance>_mcf_lb_diagnostic.yaml`` under each
    # instance's ``progress/`` dir, so match the suffix (not a prefix).
    for diag_path in sorted(run_dir.rglob("*mcf_lb_diagnostic*.yaml")):
        data = load_yaml(diag_path) or {}
        diag = data.get(_DIAG_KEY)
        if not diag:
            continue
        scenario, instance_name, gen = _resolve_scenario_and_instance(
            diag_path, run_dir
        )
        row: dict[str, Any] = {
            "instance_name": instance_name,
            "scenario": scenario,
            "n": gen.n if gen else None,
            "c": gen.c if gen else None,
            "m": gen.m if gen else None,
            "T_factor": gen.T_factor if gen else None,
            "R_factor": gen.R_factor if gen else None,
            "W_factor": gen.W_factor if gen else None,
            "rep": gen.rep if gen else None,
        }
        row.update(_row_from_diag(diag))
        rows.append(row)
    return rows


def add_improvement(df: pd.DataFrame, baseline_scenario: str) -> pd.DataFrame:
    """Add ``before_obj`` / ``improvement`` / ``improvement_pct`` by joining
    each row to its baseline-scenario row on ``instance_name``.
    """
    baseline = (
        df[df["scenario"] == baseline_scenario]
        .set_index("instance_name")["final_obj"]
        .to_dict()
    )
    df["before_obj"] = df["instance_name"].map(baseline)
    df["improvement"] = df["before_obj"] - df["final_obj"]
    df["improvement_pct"] = df["improvement"] / df["before_obj"]
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_output_dir", type=Path, help="Run output directory")
    parser.add_argument(
        "--baseline-scenario",
        default=_DEFAULT_BASELINE,
        help=f"Scenario whose final_obj is the before baseline (default: {_DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path (default: analysis/seed_candidate_analysis_<date>.csv)",
    )
    args = parser.parse_args()

    run_dir: Path = args.run_output_dir
    if not run_dir.is_dir():
        raise SystemExit(f"Not a directory: {run_dir}")

    rows = collect_rows(run_dir)
    if not rows:
        raise SystemExit(
            f"No *mcf_lb_diagnostic*.yaml with '{_DIAG_KEY}' under {run_dir}"
        )

    df = (
        pd.DataFrame(rows)
        .sort_values(["instance_name", "scenario"])
        .reset_index(drop=True)
    )
    df = add_improvement(df, args.baseline_scenario)

    out_path: Path = args.out or (
        Path("analysis") / f"seed_candidate_analysis_{_dt.date.today():%Y%m%d}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows × {len(df.columns)} cols → {out_path}")


if __name__ == "__main__":
    main()
