"""Run-level multi-scenario subroutine flow comparison chart.

Adapted from
``hybridflowshop/hybridflowshop/report/multi_scenario_method_chart.py``
(``export_multi_scenario_method_rpdf_comparison_html``). The vendored
chart accepts a list of ``{label, endpoint_df, raw_progression_df}`` —
each ``endpoint_df`` already carries ``rpd_f`` (filled by the writer).
"""

import json
import logging
from pathlib import Path
from string import Template
from typing import Any

import pandas as pd

from ._cell_filter import CELL_FILTER_JS, cell_filter_toolbar_html
from ._chart_constants import (
    HOVER_PERCENT_DECIMALS,
    series_colors_json,
    symbol_map_json,
)
from .np_utils import (
    decimate_step_series,
    progression_points_to_arrays,
    round_step_series,
    step_function_mean_over_union,
)
from .trajectory_utils import (
    build_best_so_far_progression_points,
    keep_strict_global_improvements_or_endpoints,
)

logger = logging.getLogger(__name__)

# Fallback axis-upper used when no positive RPDf values are present
# (mean series collapsed to 0). Kept small so the chart still renders a
# readable y-axis instead of degenerating to a single tick.
_EMPTY_POSITIVE_AXIS_UPPER = 0.01

# Headroom multiplier above the largest RPDf so markers are not clipped
# by the y-axis cap.
_POSITIVE_AXIS_PADDING = 1.05

# Minimum normalized-time x-axis upper. Prevents the chart from
# squeezing horizontally when every scenario finishes well before t=1.
_MIN_NORMALIZED_TIME_X_UPPER = 1.0

# Upper bound on breakpoints kept per scenario All-mean step series. The raw
# union-of-change-times mean carries 10^5-10^6 points (one per any
# instance's improvement); at this resolution the sub-quantum thinning is
# visually lossless while keeping the emitted HTML to ~1.5 MB.
_ALL_SERIES_MAX_POINTS = 2000

# Upper bound on breakpoints kept per cell series (72 cells × 200 = 14 400
# points per scenario). M=200 gives a y quantum of ~0.5 % of the cell's
# y-range, so at 760 px chart height each quantum is ~4 px.
_CELL_SERIES_MAX_POINTS = 200

# Stored coordinate precision. Hover renders both axes at
# `HOVER_PERCENT_DECIMALS` (=1e-5), so y at 5 decimals exactly matches what
# is displayed. x keeps one spare digit: normalized time is divided by a
# per-instance limit, and 1e-6 stays finer than a millisecond for every
# time limit up to ~1000 s (1e-5 would be 1.8 ms at a 180 s limit).
_X_ROUND_DECIMALS = 6
_Y_ROUND_DECIMALS = 5


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
        for idx, name in enumerate(
            pd.unique(order_source_df["subroutine_name"]), start=1
        )
    }
    work_df["subroutine_order"] = work_df["subroutine_name"].map(order_map)
    return work_df.dropna(subset=["subroutine_order"]).copy()


def _build_progression_by_instance(
    raw_progression_df: pd.DataFrame | None,
) -> dict[str, pd.DataFrame]:
    """Per-instance progression frames, CP-callback noise stripped.

    Strips points that don't strictly improve the per-instance global running
    min (keeping each call's endpoint). Without this, the union across
    instances explodes to 10^5–10^6 points and the HTML balloons past 100MB.
    Mirrors what rpdf_scatter_chart applies per scenario.

    This is the single most expensive step in the chart pipeline, so it is
    computed **once per scenario** and handed to every
    :func:`_build_scenario_progression_models` call — including the per-cell
    ones, which cover subsets of the same instances.
    """
    if raw_progression_df is None or raw_progression_df.empty:
        return {}
    sort_cols = [
        c
        for c in ["norm_time", "global_sec", "call_index"]
        if c in raw_progression_df.columns
    ]
    return {
        str(ins): keep_strict_global_improvements_or_endpoints(
            grp.sort_values(sort_cols)
        )
        for ins, grp in raw_progression_df.groupby("instance_id", sort=True)
    }


def _build_scenario_progression_models(
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
    *,
    progression_by_instance: dict[str, pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    if progression_by_instance is None:
        progression_by_instance = _build_progression_by_instance(raw_progression_df)

    models: list[dict[str, Any]] = []
    for ins, ep_grp in endpoint_df.groupby("instance_id", sort=True):
        ep_grp = ep_grp.sort_values(
            ["norm_time", "subroutine_order", "subroutine_name"]
        )
        prog_grp = progression_by_instance.get(str(ins))
        source_grp = ep_grp if prog_grp is None or prog_grp.empty else prog_grp
        models.append(
            {
                "instance_id": str(ins),
                "progression_points": build_best_so_far_progression_points(source_grp),
            }
        )
    return models


def _fill_missing_subroutine_endpoints(endpoint_df: pd.DataFrame) -> pd.DataFrame:
    """For each instance, add a synthetic endpoint row for every scenario-level
    subroutine the instance never reached. The synthetic row copies the
    instance's last actual endpoint (norm_time, obj_value, rpd_f, ...) —
    i.e. the step is treated as having run for 0 seconds at the controller's
    stop time. Without this, the guide-marker average for a step that only
    a subset of instances reached would sit at that subset's mean, which
    misleads when most instances never got there.
    """
    if endpoint_df.empty:
        return endpoint_df
    all_subroutines = list(pd.unique(endpoint_df["subroutine_name"]))
    order_by_name = (
        endpoint_df[["subroutine_name", "subroutine_order"]]
        .drop_duplicates()
        .set_index("subroutine_name")["subroutine_order"]
        .to_dict()
    )
    synth_rows: list[dict[str, Any]] = []
    for _ins, grp in endpoint_df.groupby("instance_id", sort=False):
        present = set(grp["subroutine_name"])
        missing = [s for s in all_subroutines if s not in present]
        if not missing:
            continue
        last = grp.sort_values("norm_time").iloc[-1].to_dict()
        for s in missing:
            row = dict(last)
            row["subroutine_name"] = s
            row["subroutine_order"] = order_by_name[s]
            synth_rows.append(row)
    if not synth_rows:
        return endpoint_df
    return pd.concat([endpoint_df, pd.DataFrame(synth_rows)], ignore_index=True)


def _build_scenario_mean_series(
    scenario_label: str,
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
    *,
    cell_by_instance: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any] | None:
    endpoint_df = _fill_missing_subroutine_endpoints(endpoint_df)
    # Computed once and reused by every per-cell model build below. Rebuilding
    # it per cell would re-filter *every* instance once per cell — 73x the work
    # at 72 cells.
    progression_by_instance = _build_progression_by_instance(raw_progression_df)
    models = _build_scenario_progression_models(
        endpoint_df,
        raw_progression_df,
        progression_by_instance=progression_by_instance,
    )
    models = [m for m in models if m["progression_points"]]
    if not models:
        return None

    model_arrays = [
        progression_points_to_arrays(m["progression_points"]) for m in models
    ]
    all_mean_x, all_mean_y = step_function_mean_over_union(model_arrays)
    all_mean_x, all_mean_y = decimate_step_series(
        all_mean_x, all_mean_y, max_points=_ALL_SERIES_MAX_POINTS
    )
    all_mean_x, all_mean_y = round_step_series(
        all_mean_x,
        all_mean_y,
        x_decimals=_X_ROUND_DECIMALS,
        y_decimals=_Y_ROUND_DECIMALS,
    )

    guide_df = (
        endpoint_df.sort_values(["subroutine_order", "subroutine_name", "norm_time"])
        .groupby("subroutine_name", as_index=False, sort=False)
        .agg(avg_norm_time=("norm_time", "mean"))
    )
    guide_x = [
        round(float(v), _X_ROUND_DECIMALS)
        for v in guide_df["avg_norm_time"].astype(float)
    ]
    guide_text = guide_df["subroutine_name"].astype(str).tolist()

    n_all = len(models)
    result: dict[str, Any] = {
        "scenario": scenario_label,
        "all": {
            "x": all_mean_x,
            "y": all_mean_y,
            "n": n_all,
            "guide_x": guide_x,
            "guide_text": guide_text,
        },
        "meta": [scenario_label, n_all],
    }

    # Cell-level mean series.
    if cell_by_instance is not None:
        # One groupby instead of a boolean mask per instance (which was
        # O(instances x rows)).
        ep_by_instance = {
            str(ins): grp for ins, grp in endpoint_df.groupby("instance_id", sort=False)
        }
        cell_chunks: dict[str, list[pd.DataFrame]] = {}
        for ins_id, ck_tuple in cell_by_instance.items():
            ins_rows = ep_by_instance.get(str(ins_id))
            if ins_rows is not None and not ins_rows.empty:
                cell_chunks.setdefault("|".join(ck_tuple), []).append(ins_rows)
        cells: dict[str, dict[str, Any]] = {}
        for ck, chunks in cell_chunks.items():
            cell_ep = pd.concat(chunks, ignore_index=True)
            cell_models = _build_scenario_progression_models(
                cell_ep,
                None,
                progression_by_instance=progression_by_instance,
            )
            cell_models = [m for m in cell_models if m["progression_points"]]
            if not cell_models:
                continue
            cell_arrays = [
                progression_points_to_arrays(m["progression_points"])
                for m in cell_models
            ]
            cell_x, cell_y = step_function_mean_over_union(cell_arrays)
            cell_x, cell_y = decimate_step_series(
                cell_x, cell_y, max_points=_CELL_SERIES_MAX_POINTS
            )
            cell_x, cell_y = round_step_series(
                cell_x,
                cell_y,
                x_decimals=_X_ROUND_DECIMALS,
                y_decimals=_Y_ROUND_DECIMALS,
            )
            # Emitted in the scenario-level ``guide_text`` order so the JS
            # weighted merge can index cells positionally against the shared
            # label list. ``_fill_missing_subroutine_endpoints`` guarantees
            # every instance carries every subroutine, so no name is missing.
            cell_guide_mean = (
                cell_ep.groupby("subroutine_name")["norm_time"].mean().to_dict()
            )
            cell_guide_x = [
                round(float(cell_guide_mean[name]), _X_ROUND_DECIMALS)
                for name in guide_text
            ]
            cells[ck] = {
                "x": cell_x,
                "y": cell_y,
                "n": len(cell_models),
                "guide_x": cell_guide_x,
            }
        if cells:
            result["cells"] = cells

    return result


def _drop_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["norm_time", "rpd_f", "subroutine_name"]).copy()


def _build_payload(
    scenario_metrics: list[tuple[str, pd.DataFrame] | dict[str, Any]],
    *,
    cell_by_instance: dict[str, tuple[str, ...]] | None = None,
    dim_values: dict[str, list[str]] | None = None,
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
            str(scenario["label"]),
            endpoint_work,
            progression_work,
            cell_by_instance=cell_by_instance,
        )
        if mean_series is None:
            continue
        traces.append(mean_series)
        all_x.extend(float(x) for x in mean_series["all"]["x"])
        all_y.extend(float(y) for y in mean_series["all"]["y"])
        # Include cell values in axis range.
        cells = mean_series.get("cells")
        if cells:
            for c in cells.values():
                all_x.extend(float(x) for x in c["x"])
                all_y.extend(float(y) for y in c["y"])
    result: dict = {
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
  $cell_filter_toolbar
  <div id="multi-scenario-method-chart" style="width: 100%; height: 760px;"></div>
  <script>
    const payload = $payload_json;
    const SERIES_COLORS = $series_colors_json;
    const SYMBOL_MAP = $symbol_map_json;

    $cell_filter_js

    const TRACES_PER_SCENARIO = 3;

    // Guide-marker x per scenario for the *currently rendered* filter, filled
    // by buildTraces(). The dotted vertical lines must sit on the same x as
    // the guide markers, so both read this — not the All-only payload field.
    let currentGuideX = [];

    function buildVisibleGuideShapes(plotData) {
      return payload.traces.flatMap((trace, idx) => {
        const lineTrace = plotData?.[idx * TRACES_PER_SCENARIO];
        const isVisible = lineTrace && lineTrace.visible !== "legendonly";
        if (!isVisible) return [];
        const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
        return (currentGuideX[idx] || []).map((x) => ({
          type: "line", xref: "x", yref: "paper",
          x0: x, x1: x, y0: 0, y1: 1,
          line: { color: seriesColor, width: 1, dash: "dot" }
        }));
      });
    }

    function resolveTraceData(trace) {
      const cellKey = getSelectedCellKeys();
      const hasCells = trace.cells && Object.keys(trace.cells).length > 0;
      if (cellKey === null || !hasCells) {
        return { trace, useAll: true };
      }
      const selectedKeys = cellKey.split("|");
      const dims = ["t_factor","r_factor","job_cnt","stage_cnt"];
      const matched = [];
      Object.entries(trace.cells).forEach(([ck, c]) => {
        const parts = ck.split("|");
        const match = dims.every((d, i) => selectedKeys[i] === "All" || selectedKeys[i] === parts[i]);
        if (match) matched.push(c);
      });
      if (matched.length === 0) return { trace, useAll: true };
      const merged = mergeCells(matched);
      if (!merged) return { trace, useAll: true };
      // Merge guide_x by weighted average.
      const totalN = matched.reduce((s, c) => s + c.n, 0);
      const mergedGuideX = matched[0].guide_x.map((_, gi) => {
        let acc = 0;
        matched.forEach(c => { acc += c.n * c.guide_x[gi]; });
        return acc / totalN;
      });
      return { trace, useAll: false, merged, mergedGuideX, totalN };
    }

    function buildTraces() {
      currentGuideX = [];
      return payload.traces.flatMap((trace, idx) => {
        const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
        const resolved = resolveTraceData(trace);

        let lineX, lineY, startX, startY, instanceN, guideX, guideText;
        if (resolved.useAll) {
          const sp = buildStepPath(trace.all.x, trace.all.y);
          lineX = sp.x; lineY = sp.y;
          startX = trace.all.x.length > 0 ? [trace.all.x[0]] : [];
          startY = trace.all.y.length > 0 ? [trace.all.y[0]] : [];
          instanceN = trace.all.n;
          guideX = trace.all.guide_x;
          guideText = trace.all.guide_text || [];
        } else {
          const sp = buildStepPath(resolved.merged.x, resolved.merged.y);
          lineX = sp.x; lineY = sp.y;
          startX = resolved.merged.x.length > 0 ? [resolved.merged.x[0]] : [];
          startY = resolved.merged.y.length > 0 ? [resolved.merged.y[0]] : [];
          instanceN = resolved.totalN;
          guideX = resolved.mergedGuideX;
          guideText = trace.all.guide_text || [];
        }

        currentGuideX[idx] = guideX;
        const guideCustomdata = guideX.map((x, i) => [trace.scenario, guideText[i] || ""]);

        return [
          { type: "scatter", mode: "lines",
            name: trace.scenario, legendgroup: trace.scenario,
            x: lineX, y: lineY,
            meta: [trace.scenario, instanceN],
            line: { width: 2, color: seriesColor },
            hovertemplate:
              "scenario=%{meta[0]}<br>" +
              "instance_cnt=%{meta[1]}<br>" +
              "Time%=%{x:.$hover_decimals%}<br>" +
              "Mean RPDf=%{y:.$hover_decimals%}<extra></extra>",
            showlegend: true },
          { type: "scatter", mode: "markers",
            name: trace.scenario, legendgroup: trace.scenario,
            x: guideX,
            y: guideX.map(() => 0),
            text: guideText,
            customdata: guideCustomdata,
            marker: {
              size: 8, color: seriesColor,
              symbol: guideText.map((name) => SYMBOL_MAP[name] || "circle")
            },
            hovertemplate:
              "scenario=%{customdata[0]}<br>" +
              "subroutine=%{customdata[1]}<br>" +
              "avg end Time%=%{x:.$hover_decimals%}<extra></extra>",
            showlegend: false },
          { type: "scatter", mode: "markers",
            name: trace.scenario, legendgroup: trace.scenario,
            x: startX, y: startY,
            meta: [trace.scenario, instanceN],
            marker: { size: 9, symbol: "circle-open", line: { width: 2 }, color: seriesColor },
            hovertemplate:
              "scenario=%{meta[0]}<br>" +
              "instance_cnt=%{meta[1]}<br>" +
              "all instances scheduled at Time%=%{x:.$hover_decimals%}<br>" +
              "Mean RPDf=%{y:.$hover_decimals%}<extra></extra>",
            showlegend: false }
        ];
      });
    }

    function renderChart() {
      const traces = buildTraces();
      const layout = {
        title: { text: "$chart_title" },
        xaxis: { title: { text: "$x_axis_label" }, tickformat: ".$x_percent_decimals%", range: [0, payload.x_max] },
        yaxis: { title: { text: "Mean RPDf" }, tickformat: ".$y_percent_decimals%", range: [payload.y_min, payload.y_max] },
        template: "plotly_white",
        hovermode: "closest",
        legend: { orientation: "h", groupclick: "togglegroup" },
        margin: { l: 70, r: 20, t: 70, b: 70 },
        shapes: buildVisibleGuideShapes(traces)
      };

      Plotly.react("multi-scenario-method-chart", traces, layout, { responsive: true })
        .then((gd) => {
          const sync = () => Plotly.relayout(gd, { shapes: buildVisibleGuideShapes(gd.data) });
          gd.on("plotly_restyle", sync);
        });
    }

    renderChart();

    document.querySelectorAll("#cell-filter-toolbar select").forEach(el => {
      el.addEventListener("change", renderChart);
    });
  </script>
</body>
</html>
""")


def _render_html(
    payload: dict,
    x_decimals: int,
    y_decimals: int,
    *,
    title: str | None = None,
    x_label: str | None = None,
    dim_values: dict[str, list[str]] | None = None,
) -> str:
    _title = title or "Subroutine flow mean over-time RPDf by scenario"
    _x_label = x_label or "Normalized time"
    # Always emitted — see the same note in method_mean_scatter._render_html.
    toolbar = cell_filter_toolbar_html(dim_values) if dim_values else ""
    return _HTML_TEMPLATE.substitute(
        payload_json=json.dumps(payload, separators=(",", ":")),
        chart_title=_title,
        x_axis_label=_x_label,
        x_percent_decimals=x_decimals,
        y_percent_decimals=y_decimals,
        hover_decimals=HOVER_PERCENT_DECIMALS,
        series_colors_json=series_colors_json(),
        symbol_map_json=symbol_map_json(),
        cell_filter_toolbar=toolbar,
        cell_filter_js=CELL_FILTER_JS,
    )


def export_multi_scenario_method_rpdf_comparison_html(
    scenario_metrics: list[tuple[str, pd.DataFrame] | dict[str, Any]],
    output_path: Path,
    *,
    x_percent_decimals: int = 1,
    y_percent_decimals: int = 1,
    title: str | None = None,
    x_label: str | None = None,
    cell_by_instance: dict[str, tuple[str, ...]] | None = None,
    dim_values: dict[str, list[str]] | None = None,
) -> bool:
    """Render the run-level scenario-comparison chart. Returns ``False`` when
    no scenario yielded usable data.
    """
    payload = _build_payload(
        scenario_metrics, cell_by_instance=cell_by_instance, dim_values=dim_values
    )
    if not payload["traces"]:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(
            payload,
            x_percent_decimals,
            y_percent_decimals,
            title=title,
            x_label=x_label,
            dim_values=dim_values,
        ),
        encoding="utf-8",
    )
    logger.info("Multi-scenario method comparison HTML saved to %s", output_path)
    return True
