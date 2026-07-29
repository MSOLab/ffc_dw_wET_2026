from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from string import Template
from typing import Any

from ffc_ddw_sum_et._calc import rpd_f

from ._cell_filter import CELL_FILTER_JS, cell_filter_toolbar_html
from ._chart_constants import HOVER_PERCENT_DECIMALS, series_colors_json
from .obj_log_loader import InstanceProgression, build_endpoint_df

logger = logging.getLogger(__name__)

_EMPTY_POSITIVE_AXIS_UPPER = 0.01
_POSITIVE_AXIS_PADDING = 1.05
_MIN_NORMALIZED_TIME_X_UPPER = 1.0


def _is_compound_method_inner(full_name: str) -> bool:
    """Detect CSR inner progress points in the post-contract format
    ``coarsen_solve_reconstruct-<digit>-<child_step>``.
    """
    return full_name.startswith("coarsen_solve_reconstruct-")


def _is_top_level_method(full_name: str) -> bool:
    """Whether ``full_name`` is a bare top-level controller step.

    Top level means the label is exactly a step name as the controller calls it
    (``neh_cp``, ``coarsen_solve_reconstruct``, …). Anything registered *below*
    one such call is not: CSR inner steps (``coarsen_solve_reconstruct-…`` /
    ``….inner-…``) and per-batch endpoints of a single call
    (``incremental_sw_cp.<n>-batch_<id>``, which carry a ``.`` suffix).

    The chart draws the two levels with different marker shapes.
    """
    return "." not in full_name and not _is_compound_method_inner(full_name)


def _order_parents_after_children(
    step_labels: dict[int, tuple[str, str]],
) -> list[int]:
    """Emission order for the step ids in ``step_labels``.

    Base order is first appearance across the endpoint rows (controller
    order). That is only self-consistent when every instance ran the same
    steps: a compound step (``coarsen_solve_reconstruct``) registers its own
    endpoint *after* its inner points, so an instance with few inner points
    contributes the parent label before a longer instance contributes the
    remaining inner ones — leaving the parent, which sits at the largest
    time, stranded mid-sequence with the connecting line doubling back to it.

    A label ``P`` is the parent of ``P-<child>`` / ``P.<child>``, so pull each
    such ``P`` to just after its last child.
    """
    first_seen = {name: idx for idx, (_, name) in step_labels.items()}

    def sort_key(idx: int) -> tuple[float, int]:
        name = step_labels[idx][1]
        child_ids = [
            other_idx
            for other_name, other_idx in first_seen.items()
            if other_name != name
            and (other_name.startswith(f"{name}-") or other_name.startswith(f"{name}."))
        ]
        return (max(child_ids) + 0.5, idx) if child_ids else (float(idx), idx)

    return sorted(step_labels, key=sort_key)


def _build_timelimit_map(
    progressions: list[InstanceProgression],
) -> dict[str, float]:
    return {p.instance_id: p.timelimit_sec for p in progressions}


def load_method_mean_metrics(
    progressions: list[InstanceProgression],
    baseline_obj_by_instance: dict[str, float],
    *,
    drop_non_improving_methods: bool = False,
    cell_by_instance: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Per-method mean ``(Time%, RPDf)`` points for the method-mean scatter chart.

    Builds the data behind
    ``<run_id>_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html``:
    each controller step becomes one averaged point so the chart can trace the
    quality/time trade-off along the controller flow. Steps are keyed by the
    *full* ``subroutine_name``, so a step that emits several endpoints under one
    call_index (e.g. ``incremental_sw_cp`` registering per batch as
    ``incremental_sw_cp.<n>-batch_<id>``) contributes one point *per batch*
    rather than a single collapsed marker. Called
    by :func:`~.post_run_chart_writer.write_post_run_subroutine_chart_artifacts`
    during post-run reporting — after the run has finished and every instance's
    ``<instance>_obj_log.json`` is on disk — and by
    ``scripts/build_subroutine_flow_charts.py`` to regenerate charts from an
    existing run without re-running experiments.

    Averages use **carry-forward (intent-to-treat)**: an instance that did not
    reach a later method (e.g. the final ``solve_base_model_cpsat`` cut off by
    tight TL) keeps its last observed ``(time, rpdf, obj)`` in that method's
    average instead of being dropped. The endpoint therefore equals the
    full-sample mean rather than the mean over reachers only — matching the
    ``analysis_wide`` / ``analysis_long`` sheets of ``*_report.xlsx``.

    Each step plots the **best-so-far incumbent** (running-min obj), not its
    own raw ``obj_value``: a step that registers a solution worse than the
    incumbent it received (e.g. ``neh_cp`` after ``run_flip_makespan_cp_...``)
    plots the incumbent, so the trajectory never degrades in RPDf.

    Instances with no baseline ref, non-positive timelimit, or all-NaN rpdf are
    excluded from every point; a method that no instance reached is skipped
    (its carry-forward point would duplicate the previous one).

    Args:
        progressions (list[InstanceProgression]): one
            :class:`~.obj_log_loader.InstanceProgression` per instance (from
            :func:`~.obj_log_loader.iter_scenario_instance_progressions`); each
            carries the decoded ``obj_log`` trajectory and the manifest's
            ``timelimit_sec``.
        baseline_obj_by_instance (dict[str, float]): ``instance_id -> ref_obj``
            map; the reference objective for RPDf (typically ``BKS_data`` from
            ``benchmarks/PRA2017/pra2017_bks_table.csv``). Instances missing
            from this map are excluded from every point. Built upstream by
            :func:`~.post_run_chart_writer._build_baseline_map`.
        drop_non_improving_methods (bool, optional): opt-in noise filter.
            ``False`` (default) keeps non-improving methods as flat horizontal
            segments — a useful "time wasted" signal. ``True`` drops them,
            keeping the first method and the flow's last method regardless.
            Defaults to False.

    Returns:
        list[dict[str, Any]]: ``{method, label, mean_time_pct, mean_rpdf,
        instance_count, is_top_level}`` dicts in controller (first-appearance)
        order. ``method`` is the base name (pre-``.``); ``label`` is the
        ``subroutine_name`` shown in the hover (already collapsed for
        ``incremental_job_contrib_cp`` — one point per jd level).
        ``is_top_level`` is ``True`` only for a bare controller step name and
        drives the marker shape: top-level steps get an open circle, everything
        registered below one call (CSR inner steps, per-batch endpoints) gets an
        open star-diamond.
        ``mean_time_pct`` is
        ``global_end_sec / timelimit_sec`` in ``[0, 1]``; ``mean_rpdf`` is the
        mean of ``rpd_f(obj, ref) = 2*(obj-ref)/(obj+ref)``;
        ``instance_count`` is the carry-forward total (active instances), not
        the reacher count. Empty list when ``progressions`` is empty or the
        decoded endpoint DataFrame has no rows.
    """
    if not progressions:
        return []

    timelimit_map = _build_timelimit_map(progressions)
    endpoint_df = build_endpoint_df(progressions)
    if endpoint_df.empty:
        return []

    # Fine-grained step key: the *full* subroutine_name keeps each
    # incremental_sw_cp batch (``incremental_sw_cp.<n>-batch_<id>``) as its
    # own point instead of collapsing the whole call_index into a single
    # marker. ``incremental_job_contrib_cp`` is an exception — its
    # per-rep contexts are already collapsed at load time, yielding one
    # point per jd level. Order = first appearance across the endpoint
    # frame (controller order), matching the flow-comparison guide markers,
    # then adjusted by ``_order_parents_after_children``. The display
    # ``method`` is the base name (pre-``.``) so all batches share one
    # symbol/colour; ``label`` carries the full name for the hover.
    step_order = {
        name: idx
        for idx, name in enumerate(
            dict.fromkeys(endpoint_df["subroutine_name"].tolist())
        )
    }

    instance_data: dict[str, list[tuple[int, str, str, float, float, float]]] = {}
    for ins_id, ins_grp in endpoint_df.groupby("instance_id", sort=True):
        ins_id_str = str(ins_id)
        timelimit = timelimit_map.get(ins_id_str)
        if not timelimit or timelimit <= 0:
            continue
        ref = baseline_obj_by_instance.get(ins_id_str)
        if ref is None:
            continue
        steps: list[tuple[int, str, str, float, float, float]] = []
        for name, name_grp in ins_grp.groupby("subroutine_name", sort=False):
            best_idx = name_grp["global_end_sec"].idxmax()
            best_row = name_grp.loc[best_idx]
            full_name = str(name)
            base_name = full_name.split(".", 1)[0]
            time_pct = float(best_row["global_end_sec"]) / timelimit
            obj = float(best_row["obj_value"])
            rp = rpd_f(obj, ref)
            if math.isnan(rp):
                continue
            steps.append(
                (step_order[full_name], base_name, full_name, time_pct, rp, obj)
            )
        steps.sort(key=lambda x: x[0])
        # Best-so-far (incumbent) carry-forward: a step whose raw obj_value is
        # worse than the running-best incumbent — e.g. neh_cp registering a
        # solution worse than the flip-makespan incumbent it received — must
        # plot the incumbent, not its own worse output. The incumbent never
        # degrades, so obj is a running minimum and rpdf is recomputed from it.
        best_obj = math.inf
        carried: list[tuple[int, str, str, float, float, float]] = []
        for s_order, base_name, full_name, time_pct, _rp, obj in steps:
            best_obj = min(best_obj, obj)
            carried.append(
                (
                    s_order,
                    base_name,
                    full_name,
                    time_pct,
                    rpd_f(best_obj, ref),
                    best_obj,
                )
            )
        instance_data[ins_id_str] = carried

    step_labels: dict[int, tuple[str, str]] = {}
    for steps in instance_data.values():
        for order_idx, base_name, full_name, _, _, _ in steps:
            if order_idx not in step_labels:
                step_labels[order_idx] = (base_name, full_name)
    sorted_order = _order_parents_after_children(step_labels)

    # Per-instance last observed (time_pct, rpdf, obj) — carry-forward source.
    prev_state_by_instance: dict[str, tuple[float, float, float]] = {}
    # Instances that entered the flow at least once (carry-forward eligible).
    active_instances: set[str] = set()

    # Cell-level carry-forward state (when cell_by_instance is provided).
    ins_cell_key: dict[str, str | None] = {}
    cell_instance_ids: dict[str, set[str]] = {}
    cell_prev_state: dict[str, dict[str, tuple[float, float, float]]] = {}
    cell_active: dict[str, set[str]] = {}
    if cell_by_instance is not None:
        for ins_id in instance_data:
            raw = cell_by_instance.get(ins_id)
            if raw is not None:
                ck = "|".join(raw)
                ins_cell_key[ins_id] = ck
                cell_instance_ids.setdefault(ck, set()).add(ins_id)
        for ck in cell_instance_ids:
            cell_prev_state[ck] = {}
            cell_active[ck] = set()

    candidates: list[dict[str, Any]] = []
    for order_idx in sorted_order:
        base_name, full_name = step_labels[order_idx]
        is_top_level = _is_top_level_method(full_name)
        reached: list[tuple[str, float, float, float]] = []
        improves = False
        for ins_id, steps in instance_data.items():
            found: tuple[float, float, float] | None = None
            for s_order, _, _, t_pct, r, obj in steps:
                if s_order == order_idx:
                    found = (t_pct, r, obj)
                    break
            if found is not None:
                t_pct, r, obj = found
                prior_state = prev_state_by_instance.get(ins_id)
                prior_obj = prior_state[2] if prior_state is not None else None
                if prior_obj is None or obj < prior_obj:
                    improves = True
                reached.append((ins_id, t_pct, r, obj))
                active_instances.add(ins_id)
        if not reached:
            # No instance reached this method → a carry-forward point would
            # duplicate the previous point exactly (same obj/time/rpdf for
            # every active instance). Skip to avoid duplicate dots. This is
            # distinct from a "non-improving but reached" method, which does
            # produce a new (later time, same rpdf) horizontal segment.
            continue
        # Carry-forward average: reached values + prev_state for unreached active
        # instances. Update prev_state for reached instances first, then carry
        # forward the rest — no double-count, no read-after-write hazard.
        time_pcts: list[float] = []
        rpdfs: list[float] = []
        reached_ids = {ins_id for ins_id, _, _, _ in reached}
        for ins_id, t_pct, r, obj in reached:
            time_pcts.append(t_pct)
            rpdfs.append(r)
            prev_state_by_instance[ins_id] = (t_pct, r, obj)
        for ins_id in active_instances:
            if ins_id not in reached_ids:
                ps = prev_state_by_instance.get(ins_id)
                if ps is not None:
                    time_pcts.append(ps[0])
                    rpdfs.append(ps[1])

        # Cell-level aggregation (when cell_by_instance is provided).
        cells_dict: dict[str, dict[str, Any]] = {}
        if cell_instance_ids:
            for ck, c_ins_ids in cell_instance_ids.items():
                c_reached: list[tuple[str, float, float, float]] = []
                for ins_id in c_ins_ids:
                    c_found: tuple[float, float, float] | None = None
                    for s_order, _, _, t_pct, r, obj in instance_data.get(ins_id, []):
                        if s_order == order_idx:
                            c_found = (t_pct, r, obj)
                            break
                    if c_found is not None:
                        c_reached.append((ins_id,) + c_found)
                        cell_active[ck].add(ins_id)
                c_time_pcts: list[float] = []
                c_rpdfs: list[float] = []
                c_reached_ids = {ins_id for ins_id, _, _, _ in c_reached}
                for ins_id, t_pct, r, obj in c_reached:
                    c_time_pcts.append(t_pct)
                    c_rpdfs.append(r)
                    cell_prev_state[ck][ins_id] = (t_pct, r, obj)
                for ins_id in cell_active[ck]:
                    if ins_id not in c_reached_ids:
                        c_ps = cell_prev_state[ck].get(ins_id)
                        if c_ps is not None:
                            c_time_pcts.append(c_ps[0])
                            c_rpdfs.append(c_ps[1])
                if c_time_pcts:
                    cells_dict[ck] = {
                        "x": [sum(c_time_pcts) / len(c_time_pcts)],
                        "y": [sum(c_rpdfs) / len(c_rpdfs)],
                        "n": len(c_time_pcts),
                        "reached": len(c_reached),
                    }

        entry: dict[str, Any] = {
            "method": base_name,
            "label": full_name,
            "improves": improves,
            "mean_time_pct": sum(time_pcts) / len(time_pcts),
            "mean_rpdf": sum(rpdfs) / len(rpdfs),
            "instance_count": len(time_pcts),
            "is_top_level": is_top_level,
        }
        if cells_dict:
            entry["cells"] = cells_dict
        candidates.append(entry)

    if drop_non_improving_methods and candidates:
        last_idx = len(candidates) - 1
        kept: list[dict[str, Any]] = []
        for idx, cand in enumerate(candidates):
            if cand["improves"] or idx == last_idx:
                kept.append(cand)
            else:
                logger.info(
                    "Dropping non-improving method %r",
                    cand["label"],
                )
        candidates = kept

    return [{k: v for k, v in c.items() if k != "improves"} for c in candidates]


def _positive_axis_upper(values: list[float]) -> float:
    if not values:
        return _EMPTY_POSITIVE_AXIS_UPPER
    max_value = max(values)
    if max_value <= 0:
        return _EMPTY_POSITIVE_AXIS_UPPER
    return max_value * _POSITIVE_AXIS_PADDING


def _x_axis_upper(values: list[float]) -> float:
    if not values:
        return _MIN_NORMALIZED_TIME_X_UPPER
    return max(_MIN_NORMALIZED_TIME_X_UPPER, max(values))


def _y_axis_lower(values: list[float]) -> float:
    if not values:
        return 0.0
    return min(0.0, min(values))


def _build_payload(
    scenarios: list[dict[str, Any]],
    dim_values: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    all_x: list[float] = []
    all_y: list[float] = []
    for scenario in scenarios:
        method_points = scenario.get("method_points") or []
        if not method_points:
            continue
        xs = [float(p["mean_time_pct"]) for p in method_points]
        ys = [float(p["mean_rpdf"]) for p in method_points]
        names = [str(p["method"]) for p in method_points]
        labels = [str(p.get("label", p["method"])) for p in method_points]
        counts = [int(p["instance_count"]) for p in method_points]
        top_levels = [bool(p.get("is_top_level", True)) for p in method_points]
        cells_list: list[dict[str, Any]] = []
        for p in method_points:
            cells_list.append(p.get("cells") or {})
        traces.append(
            {
                "scenario": str(scenario["label"]),
                "x": xs,
                "y": ys,
                "method": names,
                "label": labels,
                "instance_count": counts,
                "is_top_level": top_levels,
                "cells": cells_list,
            }
        )
        all_x.extend(xs)
        all_y.extend(ys)
        # Include cell values in axis range computation.
        for p in method_points:
            cells = p.get("cells")
            if cells:
                for c in cells.values():
                    all_x.extend(c.get("x", []))
                    all_y.extend(c.get("y", []))
    result: dict[str, Any] = {
        "traces": traces,
        "x_max": _x_axis_upper(all_x),
        "y_min": _y_axis_lower(all_y),
        "y_max": _positive_axis_upper(all_y),
    }
    if dim_values:
        result["dim_values"] = dim_values
    return result


_HTML_TEMPLATE = Template("""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>$title</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 18px; color: #1b1b1b; }
    h1 { font-size: 20px; margin: 0 0 8px 0; }
    p { margin: 0 0 16px 0; color: #444; }
  </style>
</head>
<body>
  <h1>$title</h1>
  <p>Per-method mean (Time%, RPDf) across instances. Methods without recorded obj_value are omitted.
  Marker shape marks the call level: open circle = top-level subroutine, open star-diamond = sub-step
  (CSR inner step, or one batch of a single call).</p>
  $cell_filter_toolbar
  <div id="method-mean-scatter" style="width: 100%; height: 760px;"></div>
  <script>
    const payload = $payload_json;
    const SERIES_COLORS = $series_colors_json;
    const TOP_LEVEL_SYMBOL = "circle-open";
    const SUB_LEVEL_SYMBOL = "star-diamond-open";

    $cell_filter_js

    function applyFilters() {
      const cellKey = getSelectedCellKeys();
      const traces = payload.traces.map((trace, idx) => {
        const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
        let xs, ys, counts, labels, methods, topLevels;

        const useAll = (cellKey === null || !trace.cells || trace.cells.length === 0
                        || trace.cells.every(c => Object.keys(c).length === 0));

        if (useAll) {
          xs = trace.x; ys = trace.y; counts = trace.instance_count;
          labels = trace.label; methods = trace.method; topLevels = trace.is_top_level;
        } else {
          const allCells = trace.cells;
          const selectedKeys = cellKey.split("|");
          const dims = ["t_factor","r_factor","job_cnt","stage_cnt"];
          xs = []; ys = []; counts = []; labels = []; methods = []; topLevels = [];

          for (let m = 0; m < allCells.length; m++) {
            const cells = allCells[m] || {};
            let matchedCells = [];
            Object.entries(cells).forEach(([ck, c]) => {
              const parts = ck.split("|");
              const match = dims.every((d, i) => selectedKeys[i] === "All" || selectedKeys[i] === parts[i]);
              if (match) matchedCells.push(c);
            });
            if (matchedCells.length === 0) continue;
            // Each cell is a single (mean Time%, mean RPDf) point, so both
            // axes recombine as plain weighted means — see mergePointCells.
            const merged = mergePointCells(matchedCells);
            if (!merged) continue;
            const reachedSum = matchedCells.reduce((s, c) => s + (c.reached || 0), 0);
            if (reachedSum === 0) continue;
            xs.push(merged.x[0]);
            ys.push(merged.y[0]);
            counts.push(merged.n);
            labels.push(trace.label[m]);
            methods.push(trace.method[m]);
            topLevels.push(trace.is_top_level[m]);
          }
        }

        const customdata = methods.map((name, i) => [trace.scenario, labels[i], counts[i]]);
        return {
          type: "scatter",
          mode: "lines+markers",
          name: trace.scenario,
          x: xs, y: ys,
          customdata: customdata,
          line: { width: 2, color: seriesColor },
          marker: {
            size: 11, color: seriesColor,
            symbol: topLevels.map((top) => top ? TOP_LEVEL_SYMBOL : SUB_LEVEL_SYMBOL),
            line: { width: 2 }
          },
          hovertemplate:
            "scenario=%{customdata[0]}<br>" +
            "method=%{customdata[1]}<br>" +
            "instance_cnt=%{customdata[2]}<br>" +
            "mean Time%=%{x:.$x_hover_decimals%}<br>" +
            "mean RPDf=%{y:.$y_hover_decimals%}<extra></extra>"
        };
      });

      const layout = {
        title: { text: "$title" },
        xaxis: { title: { text: "Mean normalized time" }, tickformat: ".$x_percent_decimals%", range: [0, payload.x_max] },
        yaxis: { title: { text: "Mean RPDf" }, tickformat: ".$y_percent_decimals%", range: [payload.y_min, payload.y_max] },
        template: "plotly_white",
        hovermode: "closest",
        legend: { orientation: "h" },
        margin: { l: 70, r: 20, t: 70, b: 70 }
      };

      Plotly.react("method-mean-scatter", traces, layout, { responsive: true });
    }

    applyFilters();

    document.querySelectorAll("#cell-filter-toolbar select").forEach(el => {
      el.addEventListener("change", applyFilters);
    });
  </script>
</body>
</html>
""")


def _render_html(
    payload: dict[str, Any],
    title: str,
    x_decimals: int,
    y_decimals: int,
    *,
    dim_values: dict[str, list[str]] | None = None,
) -> str:
    # The JS helpers are always emitted: the render path calls them
    # unconditionally, and ``getCellSelection`` already returns "All" when no
    # toolbar exists. Emitting them only alongside the toolbar left callers
    # that pass no ``dim_values`` (e.g. scripts/build_cross_run_flow_chart.py)
    # with a ReferenceError and a blank chart.
    toolbar = cell_filter_toolbar_html(dim_values) if dim_values else ""
    return _HTML_TEMPLATE.substitute(
        payload_json=json.dumps(payload, separators=(",", ":")),
        title=title,
        x_percent_decimals=x_decimals,
        y_percent_decimals=y_decimals,
        x_hover_decimals=HOVER_PERCENT_DECIMALS,
        y_hover_decimals=HOVER_PERCENT_DECIMALS,
        series_colors_json=series_colors_json(),
        cell_filter_toolbar=toolbar,
        cell_filter_js=CELL_FILTER_JS,
    )


def export_method_mean_scatter_html(
    scenarios: list[dict[str, Any]],
    output_path: Path,
    *,
    title: str = "Method mean RPDf vs mean Time%",
    x_percent_decimals: int = 1,
    y_percent_decimals: int = 1,
    dim_values: dict[str, list[str]] | None = None,
) -> bool:
    payload = _build_payload(scenarios, dim_values=dim_values)
    if not payload["traces"]:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(
            payload,
            title,
            x_percent_decimals,
            y_percent_decimals,
            dim_values=dim_values,
        ),
        encoding="utf-8",
    )
    logger.info("Method-mean scatter HTML saved to %s", output_path)
    return True
