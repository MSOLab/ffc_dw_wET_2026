"""Run-level multi-scenario subroutine flow comparison chart.

Adapted from
``hybridflowshop/hybridflowshop/report/multi_scenario_method_chart.py``
(``export_multi_scenario_method_rpdf_comparison_html``). The vendored
chart accepts a list of ``{label, endpoint_df, raw_progression_df}`` —
each ``endpoint_df`` already carries ``rpd_f`` (filled by the writer).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Template
from typing import Any

import pandas as pd

from .rpdf_scatter_chart import (
    _build_best_so_far_progression_points,
    _build_step_path,
    _lookup_rpdf_at_or_before,
)

logger = logging.getLogger(__name__)


def _positive_axis_upper(values: list[float]) -> float:
    if not values:
        return 0.01
    max_value = max(values)
    if max_value <= 0:
        return 0.01
    return max_value * 1.05


def _normalize_scenario_input(
    scenario_input: tuple[str, pd.DataFrame] | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(scenario_input, tuple):
        label, endpoint_df = scenario_input
        return {
            "label": str(label),
            "endpoint_df": endpoint_df,
            "raw_progression_df": None,
        }
    if not isinstance(scenario_input, dict):
        raise TypeError("Scenario input must be a tuple or dict.")
    return {
        "label": str(scenario_input["label"]),
        "endpoint_df": scenario_input["endpoint_df"],
        "raw_progression_df": scenario_input.get("raw_progression_df"),
    }


def _prepare_scenario_endpoint_df(endpoint_df: pd.DataFrame) -> pd.DataFrame:
    work_df = endpoint_df.copy()
    order_map = {
        name: idx
        for idx, name in enumerate(pd.unique(work_df["subroutine_name"]), start=1)
    }
    work_df["subroutine_order"] = work_df["subroutine_name"].map(order_map)
    return work_df


def _prepare_scenario_progression_df(
    raw_progression_df: pd.DataFrame | None,
    order_source_df: pd.DataFrame,
) -> pd.DataFrame | None:
    if raw_progression_df is None or raw_progression_df.empty:
        return None
    work_df = raw_progression_df.copy()
    order_map = {
        name: idx
        for idx, name in enumerate(pd.unique(order_source_df["subroutine_name"]), start=1)
    }
    work_df["subroutine_order"] = work_df["subroutine_name"].map(order_map)
    return work_df.dropna(subset=["subroutine_order"]).copy()


def _build_scenario_progression_models(
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    progression_by_instance: dict[str, pd.DataFrame] = {}
    if raw_progression_df is not None and not raw_progression_df.empty:
        sort_cols = [
            c
            for c in ["norm_time", "global_sec", "call_index"]
            if c in raw_progression_df.columns
        ]
        progression_by_instance = {
            str(ins): grp.sort_values(sort_cols)
            for ins, grp in raw_progression_df.groupby("instance_id", sort=True)
        }

    models: list[dict[str, Any]] = []
    for ins, ep_grp in endpoint_df.groupby("instance_id", sort=True):
        ep_grp = ep_grp.sort_values(["norm_time", "subroutine_order", "subroutine_name"])
        prog_grp = progression_by_instance.get(str(ins))
        source_grp = ep_grp if prog_grp is None or prog_grp.empty else prog_grp
        models.append(
            {
                "instance_id": str(ins),
                "progression_points": _build_best_so_far_progression_points(source_grp),
            }
        )
    return models


def _build_guide_marker_customdata(
    scenario_label: str, guide_marker_text: list[str]
) -> list[list[Any]]:
    return [[scenario_label, str(name)] for name in guide_marker_text]


def _build_scenario_mean_series(
    scenario_label: str,
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
) -> dict[str, Any] | None:
    models = _build_scenario_progression_models(endpoint_df, raw_progression_df)
    models = [m for m in models if m["progression_points"]]
    if not models:
        return None

    first_times = [m["progression_points"][0].time for m in models]
    last_times = [m["progression_points"][-1].time for m in models]
    start_time = max(first_times)
    end_time = max(last_times)
    union_times = sorted(
        {
            p.time
            for m in models
            for p in m["progression_points"]
            if start_time <= p.time <= end_time
        }
    )
    if not union_times:
        union_times = [start_time]
        if end_time > start_time:
            union_times.append(end_time)
    elif union_times[-1] < end_time:
        union_times.append(end_time)

    mean_x: list[float] = []
    mean_y: list[float] = []
    for t in union_times:
        values = [
            v
            for m in models
            if (v := _lookup_rpdf_at_or_before(m["progression_points"], t)) is not None
        ]
        if len(values) != len(models):
            continue
        mean_x.append(t)
        mean_y.append(sum(values) / len(values))

    if not mean_x:
        return None

    step_x, step_y = _build_step_path(mean_x, mean_y)
    guide_df = (
        endpoint_df.sort_values(["subroutine_order", "subroutine_name", "norm_time"])
        .groupby("subroutine_name", as_index=False, sort=False)
        .agg(avg_norm_time=("norm_time", "mean"))
    )
    guide_x = guide_df["avg_norm_time"].astype(float).tolist()
    guide_text = guide_df["subroutine_name"].astype(str).tolist()
    return {
        "scenario": scenario_label,
        "step_x": step_x,
        "step_y": step_y,
        "step_customdata": [[scenario_label, len(models)] for _ in step_x],
        "vertical_guides": [
            {"subroutine_name": name, "x": x}
            for name, x in zip(guide_text, guide_x, strict=True)
        ],
        "guide_marker_x": guide_x,
        "guide_marker_text": guide_text,
        "guide_marker_customdata": _build_guide_marker_customdata(
            scenario_label, guide_text
        ),
    }


def _drop_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["norm_time", "rpd_f", "subroutine_name"]).copy()


def _build_payload(
    scenario_metrics: list[tuple[str, pd.DataFrame] | dict[str, Any]],
) -> dict:
    traces: list[dict[str, Any]] = []
    all_x: list[float] = []
    all_y: list[float] = []
    for raw_input in scenario_metrics:
        scenario = _normalize_scenario_input(raw_input)
        endpoint_df = scenario["endpoint_df"]
        raw_progression_df = scenario["raw_progression_df"]
        if endpoint_df is None or endpoint_df.empty:
            continue
        endpoint_clean = _drop_invalid_rows(endpoint_df)
        if endpoint_clean.empty:
            continue
        progression_clean: pd.DataFrame | None = None
        if raw_progression_df is not None and not raw_progression_df.empty:
            progression_clean = _drop_invalid_rows(raw_progression_df)

        endpoint_work = _prepare_scenario_endpoint_df(endpoint_clean)
        progression_work = _prepare_scenario_progression_df(
            progression_clean, endpoint_work
        )
        mean_series = _build_scenario_mean_series(
            str(scenario["label"]), endpoint_work, progression_work
        )
        if mean_series is None:
            continue
        traces.append(mean_series)
        all_x.extend(float(x) for x in mean_series["step_x"])
        all_y.extend(float(y) for y in mean_series["step_y"])
    return {
        "traces": traces,
        "x_max": _positive_axis_upper(all_x),
        "y_max": _positive_axis_upper(all_y),
    }


_HTML_TEMPLATE = Template("""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Subroutine Flow Comparison</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 18px; color: #1b1b1b; }
    h1 { font-size: 20px; margin: 0 0 8px 0; }
    p { margin: 0 0 16px 0; color: #444; }
  </style>
</head>
<body>
  <h1>Subroutine Flow Comparison</h1>
  <p>Mean over-time RPDf progression by scenario.</p>
  <div id="multi-scenario-method-chart" style="width: 100%; height: 760px;"></div>
  <script>
    const payload = $payload_json;
    const SERIES_COLORS = [
      "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
      "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ];
    const SYMBOL_MAP = {
      "calc_mcf_lb_and_derive_full_sch": "diamond",
      "solve_base_model_cpsat": "circle",
      "neh_cp_full_sch_from_mcf_lb": "square",
      "single_pass_full_sch_from_mcf_lb": "triangle-up",
      "neh_cp_last_stage_only_sch_from_mcf_lb": "square-open",
      "single_pass_last_stage_only_sch_from_mcf_lb": "triangle-down",
      "run_last_stage_cp_sat_lb": "x",
      "run_mcf_lb_4": "star"
    };

    function buildVisibleGuideShapes(plotData) {
      return payload.traces.flatMap((trace, idx) => {
        const lineTrace = plotData?.[idx * 2];
        const isVisible = lineTrace && lineTrace.visible !== "legendonly";
        if (!isVisible) return [];
        const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
        return (trace.vertical_guides || []).map((guide) => ({
          type: "line", xref: "x", yref: "paper",
          x0: guide.x, x1: guide.x, y0: 0, y1: 1,
          line: { color: seriesColor, width: 1, dash: "dot" }
        }));
      });
    }

    const traces = payload.traces.flatMap((trace, idx) => {
      const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
      return [
        { type: "scatter", mode: "lines",
          name: trace.scenario, legendgroup: trace.scenario,
          x: trace.step_x, y: trace.step_y,
          customdata: trace.step_customdata,
          line: { width: 2, color: seriesColor },
          hovertemplate:
            "scenario=%{customdata[0]}<br>" +
            "instance_cnt=%{customdata[1]}<br>" +
            "Time%=%{x:.4%}<br>" +
            "Mean RPDf=%{y:.4%}<extra></extra>",
          showlegend: true },
        { type: "scatter", mode: "markers",
          name: trace.scenario, legendgroup: trace.scenario,
          x: trace.guide_marker_x,
          y: trace.guide_marker_x.map(() => 0),
          text: trace.guide_marker_text,
          customdata: trace.guide_marker_customdata,
          marker: {
            size: 8, color: seriesColor,
            symbol: trace.guide_marker_text.map((name) => SYMBOL_MAP[name] || "circle")
          },
          hovertemplate:
            "scenario=%{customdata[0]}<br>" +
            "subroutine=%{customdata[1]}<br>" +
            "avg end Time%=%{x:.4%}<extra></extra>",
          showlegend: false }
      ];
    });

    const layout = {
      title: { text: "Subroutine flow mean over-time RPDf by scenario" },
      xaxis: { title: { text: "Normalized time" }, tickformat: ".$x_percent_decimals%", range: [0, payload.x_max] },
      yaxis: { title: { text: "Mean RPDf" }, tickformat: ".$y_percent_decimals%", range: [0, payload.y_max] },
      template: "plotly_white",
      hovermode: "closest",
      legend: { orientation: "h", groupclick: "togglegroup" },
      margin: { l: 70, r: 20, t: 70, b: 70 },
      shapes: buildVisibleGuideShapes(traces)
    };

    Plotly.newPlot("multi-scenario-method-chart", traces, layout, { responsive: true })
      .then((gd) => {
        const sync = () => Plotly.relayout(gd, { shapes: buildVisibleGuideShapes(gd.data) });
        gd.on("plotly_restyle", sync);
      });
  </script>
</body>
</html>
""")


def _render_html(payload: dict, x_decimals: int, y_decimals: int) -> str:
    return _HTML_TEMPLATE.substitute(
        payload_json=json.dumps(payload, separators=(",", ":")),
        x_percent_decimals=x_decimals,
        y_percent_decimals=y_decimals,
    )


def export_multi_scenario_method_rpdf_comparison_html(
    scenario_metrics: list[tuple[str, pd.DataFrame] | dict[str, Any]],
    output_path: Path,
    *,
    x_percent_decimals: int = 1,
    y_percent_decimals: int = 1,
) -> bool:
    """Render the run-level scenario-comparison chart. Returns ``False`` when
    no scenario yielded usable data.
    """
    payload = _build_payload(scenario_metrics)
    if not payload["traces"]:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(payload, x_percent_decimals, y_percent_decimals),
        encoding="utf-8",
    )
    logger.info("Multi-scenario method comparison HTML saved to %s", output_path)
    return True
