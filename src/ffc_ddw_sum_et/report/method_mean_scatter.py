from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from string import Template
from typing import Any

from ffc_ddw_sum_et._calc import rpd_f

from ._chart_constants import series_colors_json, symbol_map_json
from .obj_log_loader import InstanceProgression, build_endpoint_df

logger = logging.getLogger(__name__)

_EMPTY_POSITIVE_AXIS_UPPER = 0.01
_POSITIVE_AXIS_PADDING = 1.05
_MIN_NORMALIZED_TIME_X_UPPER = 1.0


def _build_timelimit_map(
    progressions: list[InstanceProgression],
) -> dict[str, float]:
    return {p.instance_id: p.timelimit_sec for p in progressions}


def load_method_mean_metrics(
    progressions: list[InstanceProgression],
    baseline_obj_by_instance: dict[str, float],
    *,
    drop_non_improving_methods: bool = False,
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
        instance_count}`` dicts in controller (first-appearance) order.
        ``method`` is the base name (pre-``.``) used for the marker
        symbol/colour; ``label`` is the full ``subroutine_name`` (carrying the
        batch suffix) shown in the hover. ``mean_time_pct`` is
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
    # marker. Order = first appearance across the endpoint frame (controller
    # order), matching the flow-comparison guide markers. The display
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
        instance_data[ins_id_str] = steps

    step_labels: dict[int, tuple[str, str]] = {}
    for steps in instance_data.values():
        for order_idx, base_name, full_name, _, _, _ in steps:
            if order_idx not in step_labels:
                step_labels[order_idx] = (base_name, full_name)
    sorted_order = sorted(step_labels)

    # Per-instance last observed (time_pct, rpdf, obj) — carry-forward source.
    prev_state_by_instance: dict[str, tuple[float, float, float]] = {}
    # Instances that entered the flow at least once (carry-forward eligible).
    active_instances: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for order_idx in sorted_order:
        base_name, full_name = step_labels[order_idx]
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
        candidates.append(
            {
                "method": base_name,
                "label": full_name,
                "improves": improves,
                "mean_time_pct": sum(time_pcts) / len(time_pcts),
                "mean_rpdf": sum(rpdfs) / len(rpdfs),
                "instance_count": len(time_pcts),
            }
        )

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


def _build_payload(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
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
        traces.append(
            {
                "scenario": str(scenario["label"]),
                "x": xs,
                "y": ys,
                "method": names,
                "label": labels,
                "instance_count": counts,
            }
        )
        all_x.extend(xs)
        all_y.extend(ys)
    return {
        "traces": traces,
        "x_max": _x_axis_upper(all_x),
        "y_min": _y_axis_lower(all_y),
        "y_max": _positive_axis_upper(all_y),
    }


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
  <p>Per-method mean (Time%, RPDf) across instances. Methods without recorded obj_value are omitted.</p>
  <div id="method-mean-scatter" style="width: 100%; height: 760px;"></div>
  <script>
    const payload = $payload_json;
    const SERIES_COLORS = $series_colors_json;
    const SYMBOL_MAP = $symbol_map_json;

    const traces = payload.traces.map((trace, idx) => {
      const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
      const customdata = trace.method.map((name, i) => [trace.scenario, trace.label[i], trace.instance_count[i]]);
      return {
        type: "scatter",
        mode: "lines+markers",
        name: trace.scenario,
        x: trace.x,
        y: trace.y,
        customdata: customdata,
        line: { width: 2, color: seriesColor },
        marker: {
          size: 11,
          color: seriesColor,
          symbol: trace.method.map((name) => SYMBOL_MAP[name] || "circle"),
          line: { width: 1, color: "#1b1b1b" }
        },
        hovertemplate:
          "scenario=%{customdata[0]}<br>" +
          "method=%{customdata[1]}<br>" +
          "instance_cnt=%{customdata[2]}<br>" +
          "mean Time%=%{x:.$x_percent_decimals%}<br>" +
          "mean RPDf=%{y:.$y_percent_decimals%}<extra></extra>"
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

    Plotly.newPlot("method-mean-scatter", traces, layout, { responsive: true });
  </script>
</body>
</html>
""")


def _render_html(
    payload: dict[str, Any], title: str, x_decimals: int, y_decimals: int
) -> str:
    return _HTML_TEMPLATE.substitute(
        payload_json=json.dumps(payload, separators=(",", ":")),
        title=title,
        x_percent_decimals=x_decimals,
        y_percent_decimals=y_decimals,
        series_colors_json=series_colors_json(),
        symbol_map_json=symbol_map_json(),
    )


def export_method_mean_scatter_html(
    scenarios: list[dict[str, Any]],
    output_path: Path,
    *,
    title: str = "Method mean RPDf vs mean Time%",
    x_percent_decimals: int = 1,
    y_percent_decimals: int = 1,
) -> bool:
    payload = _build_payload(scenarios)
    if not payload["traces"]:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(payload, title, x_percent_decimals, y_percent_decimals),
        encoding="utf-8",
    )
    logger.info("Method-mean scatter HTML saved to %s", output_path)
    return True
