"""End-to-end writer that turns one run directory into the two
subroutine-flow HTML artifacts.

Reused from both the on-line reporting pipeline
(``orchestration.reporting.FFcDDWReportGenerator``) and the offline
``scripts/build_subroutine_flow_charts.py`` driver. Both call
:func:`write_post_run_subroutine_chart_artifacts`.

Pipeline:

1. Discover scenario names from ``<run_id>_summary.csv`` (skip when missing).
2. Load BKS baseline from ``benchmarks/PRA2017/`` via
   :func:`load_baseline_df` — yields ``instance_id -> ref_obj/job_cnt/stage_cnt``.
3. For each scenario, read every instance's
   ``<instance>_obj_log.json`` + ``<instance>_instance_result.yaml`` into
   :class:`InstanceProgression` objects, build endpoint / progression
   DataFrames, attach ``rpd_f`` from the baseline, then call the per-scenario
   chart writer.
4. Pass all scenarios' frames to the run-level multi-scenario writer.

Failure policy mirrors :func:`post_run_pivot.write_post_run_pivot_artifacts`
— missing baseline CSVs cause a *silent* skip; missing instance manifests
*raise* (a partial chart is worse than no chart).
"""

from __future__ import annotations

import csv
import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd
from routix.io import ArtifactLayout

from ffc_ddw_sum_et._calc import rpd_f

from .instance_cells import cell_dim_values, cell_key_by_instance
from .method_mean_scatter import (
    export_method_mean_scatter_html,
    load_method_mean_metrics,
)
from .multi_scenario_method_chart import (
    export_multi_scenario_method_rpdf_comparison_html,
)
from .obj_log_loader import (
    InstanceProgression,
    build_endpoint_df,
    build_raw_progression_df,
    iter_scenario_instance_progressions,
)
from .rpdf_scatter_chart import export_method_rpdf_scatter_html

logger = logging.getLogger(__name__)


def _build_baseline_map(baseline_df: pd.DataFrame) -> dict[str, float]:
    if baseline_df.empty:
        return {}
    ins_ids = baseline_df["instance_id"].astype(str).tolist()
    refs = baseline_df["ref_obj"].tolist()
    return {
        ins: float(ref)
        for ins, ref in zip(ins_ids, refs)
        if ref is not None and not (isinstance(ref, float) and math.isnan(ref))
    }


def load_baseline_df(
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    instance_table_csv: Path,
) -> pd.DataFrame:
    """Load baseline keyed by instance file-stem (= instance_id).

    Joins three CSVs:

    * ``hybrid_match_csv`` (``ffc_ddw_sum_et_filename`` ↔ ``insIndex``)
    * ``bks_table_csv`` (``insIndex`` → ``BKS_data`` reference objective)
    * ``instance_table_csv`` (``insIndex`` → PRA2017 generator factors
      ``T`` (tardiness), ``R`` (due-date range), ``n``, ``c``)

    Output columns: ``instance_id, t_factor, r_factor, job_cnt, stage_cnt, ref_obj``.
    """
    match = pd.read_csv(hybrid_match_csv, dtype={"insIndex": str})
    match["instance_id"] = match["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    match = match[["instance_id", "insIndex"]]

    bks = pd.read_csv(bks_table_csv, dtype={"insIndex": str})
    bks = bks[["insIndex", "BKS_data"]].rename(columns={"BKS_data": "ref_obj"})

    instance = pd.read_csv(instance_table_csv, dtype={"insIndex": str})
    instance = instance[["insIndex", "T", "R", "n", "c"]].rename(
        columns={"T": "t_factor", "R": "r_factor", "n": "job_cnt", "c": "stage_cnt"}
    )

    merged = match.merge(bks, on="insIndex", how="inner").merge(
        instance, on="insIndex", how="inner"
    )
    merged["ref_obj"] = pd.to_numeric(merged["ref_obj"], errors="coerce")
    merged["t_factor"] = pd.to_numeric(merged["t_factor"], errors="coerce")
    merged["r_factor"] = pd.to_numeric(merged["r_factor"], errors="coerce")
    merged["job_cnt"] = pd.to_numeric(merged["job_cnt"], errors="coerce")
    merged["stage_cnt"] = pd.to_numeric(merged["stage_cnt"], errors="coerce")
    return merged[
        ["instance_id", "t_factor", "r_factor", "job_cnt", "stage_cnt", "ref_obj"]
    ]


def attach_rpdf_columns(
    df: pd.DataFrame,
    baseline_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add ``rpd_f, t_factor, r_factor, job_cnt, stage_cnt`` to ``df`` by joining
    on ``instance_id``.

    ``df`` must already have ``obj_value, instance_id`` populated by
    :func:`build_endpoint_df` / :func:`build_raw_progression_df`.  Rows
    whose ``instance_id`` is missing from baseline are dropped (we'd have
    nothing to plot against).
    """
    if df.empty:
        return df.assign(
            rpd_f=pd.Series(dtype=float),
            t_factor=pd.Series(dtype=float),
            r_factor=pd.Series(dtype=float),
        )

    merged = df.merge(
        baseline_df[
            ["instance_id", "t_factor", "r_factor", "job_cnt", "stage_cnt", "ref_obj"]
        ],
        on="instance_id",
        how="left",
    )
    unmatched = merged["ref_obj"].isna()
    if unmatched.any():
        logger.warning(
            "Dropping %d chart rows missing baseline ref_obj (instances=%s)",
            int(unmatched.sum()),
            sorted(set(merged.loc[unmatched, "instance_id"].astype(str))),
        )
        merged = merged.loc[~unmatched].copy()
    merged["rpd_f"] = [
        rpd_f(float(o), float(r))
        for o, r in zip(merged["obj_value"], merged["ref_obj"], strict=True)
    ]
    return merged.drop(columns=["ref_obj"])


def _read_scenarios_from_summary(summary_csv: Path) -> list[str]:
    if not summary_csv.exists():
        return []
    with open(summary_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    seen: list[str] = []
    for row in rows:
        name = row.get("scenarioName", "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _scenario_metrics_dict(
    label: str,
    progressions: list[InstanceProgression],
    baseline_df: pd.DataFrame,
    *,
    per_instance_norm_timelimit: dict[str, float] | None = None,
) -> dict[str, Any]:
    endpoint_df = build_endpoint_df(
        progressions, per_instance_norm_timelimit=per_instance_norm_timelimit
    )
    raw_progression_df = build_raw_progression_df(
        progressions, per_instance_norm_timelimit=per_instance_norm_timelimit
    )
    return {
        "label": label,
        "endpoint_df": attach_rpdf_columns(endpoint_df, baseline_df),
        "raw_progression_df": attach_rpdf_columns(raw_progression_df, baseline_df),
    }


def write_post_run_subroutine_chart_artifacts(
    *,
    layout: ArtifactLayout,
    hybrid_match_csv: Path,
    bks_table_csv: Path,
    instance_table_csv: Path,
) -> None:
    """Emit per-scenario scatter HTMLs + run-level flow comparison HTML.

    Silently skips when any baseline CSV is missing (matches the existing
    pivot pipeline).
    """
    if not hybrid_match_csv.exists():
        logger.info(
            "Skipping subroutine chart artifacts: %s not found", hybrid_match_csv
        )
        return
    if not bks_table_csv.exists():
        logger.info("Skipping subroutine chart artifacts: %s not found", bks_table_csv)
        return
    if not instance_table_csv.exists():
        logger.info(
            "Skipping subroutine chart artifacts: %s not found", instance_table_csv
        )
        return

    baseline_df = load_baseline_df(hybrid_match_csv, bks_table_csv, instance_table_csv)

    ck_map = cell_key_by_instance(baseline_df)
    dv = cell_dim_values(baseline_df)

    summary_csv = layout.artifact_path("summary_csv")
    scenarios = _read_scenarios_from_summary(summary_csv)
    if not scenarios:
        logger.info(
            "Skipping subroutine chart artifacts: no scenarios in %s", summary_csv
        )
        return

    scenario_metrics: list[dict[str, Any]] = []
    method_mean_scenarios: list[dict[str, Any]] = []
    baseline_obj_map = _build_baseline_map(baseline_df)

    for scenario_name in scenarios:
        progressions = iter_scenario_instance_progressions(layout, scenario_name)
        if not progressions:
            logger.info("Scenario %s has no instances with obj_log_json", scenario_name)
            continue

        metrics = _scenario_metrics_dict(scenario_name, progressions, baseline_df)
        scenario_metrics.append(metrics)

        scatter_path = layout.artifact_path(
            "subroutine_rpdf_scatter_html", scenario_name=scenario_name
        )
        ok = export_method_rpdf_scatter_html(
            endpoint_df=metrics["endpoint_df"],
            raw_progression_df=metrics["raw_progression_df"],
            output_path=scatter_path,
        )
        if not ok:
            logger.info(
                "Per-scenario scatter HTML skipped for %s (no usable rows)",
                scenario_name,
            )

        method_points = load_method_mean_metrics(
            progressions,
            baseline_obj_map,
            cell_by_instance=ck_map,
        )
        if method_points:
            scenario_entry = {"label": scenario_name, "method_points": method_points}
            per_scenario_path = layout.artifact_path(
                "method_mean_scatter_html", scenario_name=scenario_name
            )
            ok = export_method_mean_scatter_html(
                [scenario_entry],
                per_scenario_path,
                title=f"Method mean RPDf vs mean Time% — {scenario_name}",
                dim_values=dv,
            )
            if ok:
                logger.info(
                    "Per-scenario method-mean scatter HTML saved to %s",
                    per_scenario_path,
                )
            method_mean_scenarios.append(scenario_entry)
        else:
            logger.info(
                "No method-mean points for %s (no instances with both obj and BKS)",
                scenario_name,
            )

    if not scenario_metrics:
        logger.info("No scenario yielded usable chart data; skipping flow comparison")
        return

    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")
    ok = export_multi_scenario_method_rpdf_comparison_html(
        scenario_metrics=scenario_metrics,
        output_path=flow_path,
        cell_by_instance=ck_map,
        dim_values=dv,
    )
    if not ok:
        logger.info("Multi-scenario flow comparison HTML skipped (no traces)")

    if method_mean_scenarios:
        run_level_path = layout.artifact_path("multi_scenario_method_mean_scatter_html")
        ok = export_method_mean_scatter_html(
            method_mean_scenarios,
            run_level_path,
            title=f"Method mean RPDf vs mean Time% — {layout._run_id}",
            dim_values=dv,
        )
        if ok:
            logger.info(
                "Run-level method-mean scatter HTML saved to %s", run_level_path
            )

    # --- CSR inner-flow comparison (coarse-scale child-clock trajectories) ---
    _maybe_write_csr_inner_flow_comparison_html(
        layout, scenarios, baseline_df, cell_map=ck_map, dim_values=dv
    )


def _maybe_write_csr_inner_flow_comparison_html(
    layout: ArtifactLayout,
    scenarios: list[str],
    baseline_df: pd.DataFrame,
    *,
    cell_map: dict[str, tuple[str, str, str, str]] | None = None,
    dim_values: dict[str, list[str]] | None = None,
) -> None:
    """Emit a run-level CSR inner-flow comparison HTML reusing the same
    ``export_multi_scenario_method_rpdf_comparison_html`` chart writer
    as the shared flow comparison — the only difference is the artifact
    key (``csr_inner_obj_log_json``), per-instance inner-budget x-axis
    normalization, and distinct labels.
    """
    scenario_metrics: list[dict[str, Any]] = []
    for scenario_name in scenarios:
        progressions = iter_scenario_instance_progressions(
            layout,
            scenario_name,
            obj_log_kind="csr_inner_obj_log_json",
        )
        if not progressions:
            continue
        per_instance_budget = _compute_inner_budget_per_instance(progressions)
        metrics = _scenario_metrics_dict(
            scenario_name,
            progressions,
            baseline_df,
            per_instance_norm_timelimit=per_instance_budget,
        )
        if metrics["endpoint_df"].empty:
            continue
        scenario_metrics.append(metrics)

    if not scenario_metrics:
        logger.info("No CSR inner obj_log data found; skipping inner flow chart")
        return

    out_path = layout.artifact_path("csr_inner_flow_comparison_html")
    ok = export_multi_scenario_method_rpdf_comparison_html(
        scenario_metrics=scenario_metrics,
        output_path=out_path,
        title="CSR inner-solve (coarse) RPDf by κ",
        x_label="Normalized inner-solve time",
        cell_by_instance=cell_map,
        dim_values=dim_values,
    )
    if ok:
        logger.info("CSR inner flow comparison HTML saved to %s", out_path)
    else:
        logger.info("CSR inner flow comparison HTML skipped (no traces)")


def _compute_inner_budget_per_instance(
    progressions: list[InstanceProgression],
) -> dict[str, float]:
    """Compute per-instance inner (child-clock) budget as the max
    ``global_sec`` across all obj_value points — every trajectory then
    starts at 0 and spans [0, 1] on the inner-solve clock.
    """
    result: dict[str, float] = {}
    for prog in progressions:
        max_t = 0.0
        for call in prog.obj_value_calls:
            for point in call.points:
                if point.global_sec > max_t:
                    max_t = point.global_sec
        if max_t > 0:
            result[prog.instance_id] = max_t
    return result
