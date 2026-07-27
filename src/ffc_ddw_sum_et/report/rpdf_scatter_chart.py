"""Per-scenario interactive RPDf vs normalized-time chart.

Adapted from ``hybridflowshop/hybridflowshop/report/method_summary_chart.py``
(``export_method_rpdf_scatter_html``). The vendored chart accepts a
pre-built ``endpoint_df`` (one row per (instance, controller step) with
``rpd_f`` already computed) plus an optional ``raw_progression_df`` (one
row per trajectory point) and emits a self-contained Plotly HTML.

Two changes vs the upstream:

* The upstream took ``metrics_long_df`` + ``baseline_df`` and merged them
  on the fly. Here the merge happens in
  :func:`ffc_ddw_sum_et.report.post_run_chart_writer.attach_rpdf_columns`,
  so this function takes the merged frame directly.
* Filter / grouping dimensions are PRA2017 ``T`` (tardiness factor) and
  ``R`` (due-date range) instead of upstream's ``job_cnt`` / ``stage_cnt``.
  Both are floats sourced from
  ``benchmarks/PRA2017/pra2017_instance_table.csv`` and joined onto the
  endpoint frame by the writer.
"""

from __future__ import annotations

import bisect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pandas as pd

from ._chart_constants import (
    HOVER_PERCENT_DECIMALS,
    series_colors_json,
    symbol_map_json,
)
from .np_utils import progression_points_to_arrays, step_function_mean_over_union
from .step_path import build_step_path
from .trajectory_utils import (
    ProgressionPoint,
    build_best_so_far_progression_points,
    keep_strict_global_improvements_or_endpoints,
)

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"instance_id", "subroutine_name", "norm_time", "rpd_f", "t_factor", "r_factor"}
)

T = TypeVar("T")


@dataclass(frozen=True)
class _MarkerMeta:
    instance_id: str
    t_factor: float
    r_factor: float
    subroutine_name: str
    time: float


@dataclass(frozen=True)
class _RawInstanceProgression:
    series_id: str
    instance_id: str
    t_factor: float
    r_factor: float
    progression_points: list[ProgressionPoint]
    raw_marker_meta_by_time: dict[float, _MarkerMeta]
    endpoint_marker_meta_by_time: dict[float, _MarkerMeta]


@dataclass(frozen=True)
class _MeanVerticalGuide:
    subroutine_name: str
    x: float


def _format_factor(value: float) -> str:
    """Stable string form for T/R floats (PRA2017 grid is at one decimal)."""
    return f"{float(value):.1f}"


def _validate_columns(df: pd.DataFrame, name: str) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {name}: {sorted(missing)}")


def _lookup_rpdf_at_or_before(
    progression_points: list[ProgressionPoint], query_time: float
) -> float | None:
    """Largest ``rpd_f`` whose ``time <= query_time``, or ``None`` when all
    points lie after ``query_time``.

    ``progression_points`` must be sorted ascending by ``time`` with
    unique keys (as produced by
    :func:`build_best_so_far_progression_points`).
    """
    if not progression_points:
        return None
    times = [p.time for p in progression_points]
    idx = bisect.bisect_right(times, query_time) - 1
    if idx < 0:
        return None
    return progression_points[idx].rpd_f


def _build_step_aligned_values(base_values: list[T], y_values: list[float]) -> list[T]:
    out: list[T] = []
    for idx, base in enumerate(base_values):
        if idx == 0:
            out.append(base)
            continue
        out.append(base_values[idx - 1])
        if y_values[idx] < y_values[idx - 1]:
            out.append(base)
    return out


def _build_marker_meta_by_time(
    instance_id: object, grp: pd.DataFrame, t_factor: float, r_factor: float
) -> dict[float, _MarkerMeta]:
    meta_by_time: dict[float, _MarkerMeta] = {}
    for row in grp.itertuples():
        t = float(row.norm_time)
        if t in meta_by_time:
            raise ValueError(
                f"Duplicate endpoint marker time for instance {instance_id}: {t}"
            )
        meta_by_time[t] = _MarkerMeta(
            instance_id=str(instance_id),
            t_factor=t_factor,
            r_factor=r_factor,
            subroutine_name=str(row.subroutine_name),
            time=t,
        )
    return meta_by_time


def _build_raw_instance_progression(
    instance_id: object,
    endpoint_grp: pd.DataFrame,
    progression_grp: pd.DataFrame | None,
    t_factor: float,
    r_factor: float,
) -> _RawInstanceProgression:
    series_id = f"instance={instance_id}"
    endpoint_meta = _build_marker_meta_by_time(
        instance_id, endpoint_grp, t_factor, r_factor
    )
    if progression_grp is None or progression_grp.empty:
        raw_source = endpoint_grp
    else:
        raw_source = keep_strict_global_improvements_or_endpoints(progression_grp)
    raw_meta = _build_marker_meta_by_time(instance_id, raw_source, t_factor, r_factor)
    points = build_best_so_far_progression_points(raw_source)
    return _RawInstanceProgression(
        series_id=series_id,
        instance_id=str(instance_id),
        t_factor=t_factor,
        r_factor=r_factor,
        progression_points=points,
        raw_marker_meta_by_time=raw_meta,
        endpoint_marker_meta_by_time=endpoint_meta,
    )


def _build_raw_plotly_series(model: _RawInstanceProgression) -> dict:
    progression_x = [p.time for p in model.progression_points]
    progression_y = [p.rpd_f for p in model.progression_points]
    step_x, step_y = build_step_path(progression_x, progression_y)

    marker_x = sorted(model.raw_marker_meta_by_time)
    marker_meta = [model.raw_marker_meta_by_time[t] for t in marker_x]
    marker_y = [
        _lookup_rpdf_at_or_before(model.progression_points, m.time) for m in marker_meta
    ]
    filtered = [
        (x, y, m) for x, y, m in zip(marker_x, marker_y, marker_meta) if y is not None
    ]
    return {
        "series_id": model.series_id,
        "instance_id": model.instance_id,
        "t_factor": _format_factor(model.t_factor),
        "r_factor": _format_factor(model.r_factor),
        "x": [x for x, _, _ in filtered],
        "y": [y for _, y, _ in filtered],
        "step_x": step_x,
        "step_y": step_y,
        "text": [m.subroutine_name for _, _, m in filtered],
        "customdata": [
            [
                m.instance_id,
                _format_factor(m.t_factor),
                _format_factor(m.r_factor),
                m.subroutine_name,
            ]
            for _, _, m in filtered
        ],
    }


def _build_raw_instance_progression_models(
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
) -> list[_RawInstanceProgression]:
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

    models: list[_RawInstanceProgression] = []
    for ins, ep_grp in endpoint_df.groupby("instance_id", sort=True):
        ep_grp = ep_grp.sort_values(
            ["norm_time", "subroutine_order", "subroutine_name"]
        )
        t_val = float(ep_grp["t_factor"].iloc[0])
        r_val = float(ep_grp["r_factor"].iloc[0])
        models.append(
            _build_raw_instance_progression(
                instance_id=ins,
                endpoint_grp=ep_grp,
                progression_grp=progression_by_instance.get(str(ins)),
                t_factor=t_val,
                r_factor=r_val,
            )
        )
    return models


def _build_mean_vertical_guides(
    models: list[_RawInstanceProgression],
) -> list[_MeanVerticalGuide]:
    times_by_sub: dict[str, list[float]] = {}
    for m in models:
        for t in sorted(m.endpoint_marker_meta_by_time):
            meta = m.endpoint_marker_meta_by_time[t]
            times_by_sub.setdefault(meta.subroutine_name, []).append(t)
    return [
        _MeanVerticalGuide(subroutine_name=name, x=sum(ts) / len(ts))
        for name, ts in sorted(times_by_sub.items())
        if ts
    ]


def _build_mean_series_payload(
    raw_models: list[_RawInstanceProgression],
) -> list[dict]:
    by_group: dict[tuple[float, float], list[_RawInstanceProgression]] = {}
    for m in raw_models:
        if not m.progression_points:
            continue
        by_group.setdefault((m.t_factor, m.r_factor), []).append(m)

    out: list[dict] = []
    for (t_factor, r_factor), models in sorted(by_group.items()):
        model_arrays = [
            progression_points_to_arrays(m.progression_points) for m in models
        ]
        mean_x, mean_y = step_function_mean_over_union(model_arrays)

        t_str = _format_factor(t_factor)
        r_str = _format_factor(r_factor)
        series_id = f"mean(T={t_str},R={r_str})"
        step_x, step_y = build_step_path(mean_x, mean_y)
        point_customdata = [[series_id, t_str, r_str, len(models)] for _ in mean_x]
        step_customdata = _build_step_aligned_values(point_customdata, mean_y)
        guides = _build_mean_vertical_guides(models)
        out.append(
            {
                "series_id": series_id,
                "t_factor": t_str,
                "r_factor": r_str,
                "x": mean_x,
                "y": mean_y,
                "step_x": step_x,
                "step_y": step_y,
                "instance_count": len(models),
                "vertical_guides": [
                    {"subroutine_name": g.subroutine_name, "x": g.x} for g in guides
                ],
                "guide_marker_x": [g.x for g in guides],
                "guide_marker_text": [g.subroutine_name for g in guides],
                "customdata": point_customdata,
                "step_customdata": step_customdata,
            }
        )
    return out


def _drop_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["norm_time", "rpd_f", "subroutine_name"]).copy()


def _build_html_payload(
    endpoint_df: pd.DataFrame,
    raw_progression_df: pd.DataFrame | None,
) -> dict:
    if endpoint_df.empty:
        return {
            "t_factor_values": [],
            "r_factor_values": [],
            "raw_series": [],
            "mean_series": [],
        }
    order_map = {
        name: idx
        for idx, name in enumerate(pd.unique(endpoint_df["subroutine_name"]), start=1)
    }
    work = endpoint_df.copy()
    work["subroutine_order"] = work["subroutine_name"].map(order_map)

    t_values = sorted(float(v) for v in work["t_factor"].unique())
    r_values = sorted(float(v) for v in work["r_factor"].unique())

    progression_work = None
    if raw_progression_df is not None and not raw_progression_df.empty:
        progression_work = raw_progression_df.copy()
        progression_work["subroutine_order"] = progression_work["subroutine_name"].map(
            order_map
        )
        progression_work = progression_work.dropna(subset=["subroutine_order"]).copy()

    raw_models = _build_raw_instance_progression_models(work, progression_work)
    raw_series = [_build_raw_plotly_series(m) for m in raw_models]
    mean_series = _build_mean_series_payload(raw_models)
    return {
        "t_factor_values": [_format_factor(v) for v in t_values],
        "r_factor_values": [_format_factor(v) for v in r_values],
        "raw_series": raw_series,
        "mean_series": mean_series,
    }


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RPDf vs Time% Interactive</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 18px; color: #1b1b1b; }}
    .toolbar {{ display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }}
    .control {{ display: flex; gap: 8px; align-items: center; }}
    label {{ font-size: 14px; font-weight: 600; }}
    select {{ font-size: 14px; padding: 4px 8px; min-width: 100px; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <div class="control">
      <label for="mode-filter">mode</label>
      <select id="mode-filter">
        <option value="raw">instance progression</option>
        <option value="mean">mean progression by (T,R)</option>
      </select>
    </div>
    <div class="control">
      <label for="t-filter">T</label>
      <select id="t-filter">{t_options}</select>
    </div>
    <div class="control">
      <label for="r-filter">R</label>
      <select id="r-filter">{r_options}</select>
    </div>
  </div>
  <div id="rpdf-chart" style="width: 100%; height: 720px;"></div>
  <script>
    const DATA = {data_json};
    const xTickFormat = ".{x_decimals}%";
    const yTickFormat = ".{y_decimals}%";
    const SERIES_COLORS = {series_colors_json};
    const SYMBOL_MAP = {symbol_map_json};
    const modeFilter = document.getElementById("mode-filter");
    const tFilter = document.getElementById("t-filter");
    const rFilter = document.getElementById("r-filter");

    function buildLayout(titleText) {{
      return {{
        title: {{ text: titleText }},
        xaxis: {{ title: {{ text: "Time%" }}, tickformat: xTickFormat, rangemode: "tozero" }},
        yaxis: {{ title: {{ text: "RPDf%" }}, tickformat: yTickFormat, rangemode: "tozero" }},
        template: "plotly_white",
        margin: {{ l: 60, r: 30, t: 70, b: 60 }},
        showlegend: false,
        hovermode: "closest"
      }};
    }}

    function applyFilters() {{
      const modeVal = modeFilter.value;
      const tVal = tFilter.value;
      const rVal = rFilter.value;
      const source = modeVal === "mean" ? DATA.mean_series : DATA.raw_series;
      const selected = source.filter((s) => {{
        const tMatch = (tVal === "All" || String(s.t_factor) === tVal);
        const rMatch = (rVal === "All" || String(s.r_factor) === rVal);
        return tMatch && rMatch;
      }});

      const traces = selected.flatMap((s, idx) => {{
        const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
        const traceName = modeVal === "mean"
          ? `T=${{s.t_factor}}, R=${{s.r_factor}}`
          : `instance=${{s.instance_id}}`;

        if (modeVal === "raw") {{
          const symbols = s.text.map((name) => SYMBOL_MAP[name] || "circle");
          return [
            {{ type: "scatter", mode: "lines", x: s.step_x, y: s.step_y,
               name: traceName, line: {{ width: 1.0, color: seriesColor }},
               hoverinfo: "skip", showlegend: false }},
            {{ type: "scatter", mode: "markers", x: s.x, y: s.y,
               customdata: s.customdata, name: traceName,
               marker: {{ size: 7, symbol: symbols, color: seriesColor }},
               hovertemplate:
                 "series=%{{customdata[0]}}<br>" +
                 "T=%{{customdata[1]}}<br>" +
                 "R=%{{customdata[2]}}<br>" +
                 "subroutine=%{{customdata[3]}}<br>" +
                 "Time%=%{{x:{hover_fmt}}}<br>" +
                 "RPDf=%{{y:{hover_fmt}}}<extra></extra>",
               showlegend: false }}
          ];
        }}

        return [
          {{ type: "scatter", mode: "lines", x: s.step_x, y: s.step_y,
             customdata: s.step_customdata, name: traceName,
             line: {{ width: 2.0, color: seriesColor }},
             hovertemplate:
               "series=%{{customdata[0]}}<br>" +
               "T=%{{customdata[1]}}<br>" +
               "R=%{{customdata[2]}}<br>" +
               "instance_cnt=%{{customdata[3]}}<br>" +
               "Time%=%{{x:{hover_fmt}}}<br>" +
               "RPDf=%{{y:{hover_fmt}}}<extra></extra>",
             showlegend: false }},
          {{ type: "scatter", mode: "markers",
             x: s.guide_marker_x || [],
             y: (s.guide_marker_x || []).map(() => 0),
             text: s.guide_marker_text || [],
             name: traceName,
             customdata: (s.guide_marker_text || []).map((name) => [
               traceName, s.t_factor, s.r_factor, name
             ]),
             marker: {{ size: 8,
               symbol: (s.guide_marker_text || []).map((name) => SYMBOL_MAP[name] || "circle"),
               color: seriesColor }},
             hovertemplate:
               "series=%{{customdata[0]}}<br>" +
               "T=%{{customdata[1]}}<br>" +
               "R=%{{customdata[2]}}<br>" +
               "subroutine=%{{customdata[3]}}<br>" +
               "avg end Time%=%{{x:{hover_fmt}}}<extra></extra>",
             showlegend: false }}
        ];
      }});

      const modeLabel = modeVal === "mean"
        ? "mean progression by (T, R)"
        : "instance progression";
      const layout = buildLayout(`RPDf vs Time% - ${{modeLabel}} (${{selected.length}} lines)`);
      if (modeVal === "mean") {{
        layout.shapes = selected.flatMap((series, idx) => {{
          const seriesColor = SERIES_COLORS[idx % SERIES_COLORS.length];
          return (series.vertical_guides || []).map((guide) => ({{
            type: "line", xref: "x", yref: "paper",
            x0: guide.x, x1: guide.x, y0: 0, y1: 1,
            line: {{ color: seriesColor, width: 1, dash: "dot" }}
          }}));
        }});
      }}
      Plotly.react("rpdf-chart", traces, layout, {{ responsive: true }});
    }}

    modeFilter.addEventListener("change", applyFilters);
    tFilter.addEventListener("change", applyFilters);
    rFilter.addEventListener("change", applyFilters);
    applyFilters();
  </script>
</body>
</html>
"""


def _render_html(payload: dict, x_decimals: int, y_decimals: int) -> str:
    t_options = "".join(
        f'<option value="{v}">{v}</option>'
        for v in ["All", *payload["t_factor_values"]]
    )
    r_options = "".join(
        f'<option value="{v}">{v}</option>'
        for v in ["All", *payload["r_factor_values"]]
    )
    return _HTML_TEMPLATE.format(
        hover_fmt=f".{HOVER_PERCENT_DECIMALS}%",
        t_options=t_options,
        r_options=r_options,
        data_json=json.dumps(payload, separators=(",", ":")),
        x_decimals=x_decimals,
        y_decimals=y_decimals,
        series_colors_json=series_colors_json(),
        symbol_map_json=symbol_map_json(),
    )


def export_method_rpdf_scatter_html(
    endpoint_df: pd.DataFrame,
    output_path: Path,
    *,
    raw_progression_df: pd.DataFrame | None = None,
    x_percent_decimals: int = 1,
    y_percent_decimals: int = 1,
) -> bool:
    """Render the per-scenario interactive RPDf chart.

    ``endpoint_df`` and (optional) ``raw_progression_df`` must already
    contain ``rpd_f`` and PRA2017 ``t_factor`` / ``r_factor`` columns
    (computed by the caller). Returns ``False`` when no valid data is
    available.
    """
    _validate_columns(endpoint_df, "endpoint_df")
    if raw_progression_df is not None and not raw_progression_df.empty:
        _validate_columns(raw_progression_df, "raw_progression_df")

    cleaned_endpoint = _drop_invalid_rows(endpoint_df)
    cleaned_progression: pd.DataFrame | None = None
    if raw_progression_df is not None and not raw_progression_df.empty:
        cleaned_progression = _drop_invalid_rows(raw_progression_df)

    payload = _build_html_payload(cleaned_endpoint, cleaned_progression)
    if not payload["raw_series"] and not payload["mean_series"]:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(payload, x_percent_decimals, y_percent_decimals),
        encoding="utf-8",
    )
    logger.info("Method RPD scatter HTML saved to %s", output_path)
    return True
