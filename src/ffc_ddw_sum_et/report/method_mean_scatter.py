from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from string import Template
from typing import Any

from ._chart_constants import series_colors_json, symbol_map_json
from .obj_log_loader import InstanceProgression, build_endpoint_df

logger = logging.getLogger(__name__)

_EMPTY_POSITIVE_AXIS_UPPER = 0.01
_POSITIVE_AXIS_PADDING = 1.05
_MIN_NORMALIZED_TIME_X_UPPER = 1.0


def _rpd_f(obj: float, ref: float) -> float:
    denom = obj + ref
    if denom == 0:
        return 0.0 if obj == ref else math.nan
    return 2.0 * (obj - ref) / denom


def _build_timelimit_map(
    progressions: list[InstanceProgression],
) -> dict[str, float]:
    return {p.instance_id: p.timelimit_sec for p in progressions}


def load_method_mean_metrics(
    progressions: list[InstanceProgression],
    *,
    baseline_obj_by_instance: dict[str, float],
    drop_non_improving_methods: bool = True,
) -> list[dict[str, Any]]:
    if not progressions:
        return []

    timelimit_map = _build_timelimit_map(progressions)
    endpoint_df = build_endpoint_df(progressions)
    if endpoint_df.empty:
        return []

    instance_data: dict[str, list[tuple[int, str, float, float, float]]] = {}
    for ins_id, ins_grp in endpoint_df.groupby("instance_id", sort=True):
        ins_id_str = str(ins_id)
        timelimit = timelimit_map.get(ins_id_str)
        if not timelimit or timelimit <= 0:
            continue
        ref = baseline_obj_by_instance.get(ins_id_str)
        if ref is None:
            continue
        methods: list[tuple[int, str, float, float, float]] = []
        for ci, ci_grp in ins_grp.groupby("call_index", sort=True):
            best_idx = ci_grp["global_end_sec"].idxmax()
            best_row = ci_grp.loc[best_idx]
            method_name = str(best_row["subroutine_name"]).split(".", 1)[0]
            time_pct = float(best_row["global_end_sec"]) / timelimit
            obj = float(best_row["obj_value"])
            rp = _rpd_f(obj, ref)
            if math.isnan(rp):
                continue
            methods.append((int(ci), method_name, time_pct, rp, obj))
        methods.sort(key=lambda x: x[0])
        instance_data[ins_id_str] = methods

    method_order: dict[int, str] = {}
    for methods in instance_data.values():
        for ci, name, _, _, _ in methods:
            if ci not in method_order:
                method_order[ci] = name
    sorted_ci = sorted(method_order)

    prev_obj_by_instance: dict[str, float] = {}
    candidates: list[dict[str, Any]] = []
    for ci in sorted_ci:
        method_name = method_order[ci]
        contributions: list[tuple[str, float, float, float]] = []
        improves = False
        for ins_id, methods in instance_data.items():
            for m_ci, m_name, t_pct, r, obj in methods:
                if m_ci == ci:
                    prior = prev_obj_by_instance.get(ins_id)
                    if prior is None or obj < prior:
                        improves = True
                    contributions.append((ins_id, t_pct, r, obj))
                    break
        if not contributions:
            continue
        for ins_id, _, _, obj in contributions:
            prev_obj_by_instance[ins_id] = obj
        time_pcts = [t for _, t, _, _ in contributions]
        rpdfs = [r for _, _, r, _ in contributions]
        candidates.append(
            {
                "method": method_name,
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
                    cand["method"],
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
        counts = [int(p["instance_count"]) for p in method_points]
        traces.append(
            {
                "scenario": str(scenario["label"]),
                "x": xs,
                "y": ys,
                "method": names,
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
      const customdata = trace.method.map((name, i) => [trace.scenario, name, trace.instance_count[i]]);
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
