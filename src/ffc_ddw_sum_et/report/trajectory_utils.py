"""Per-instance trajectory primitives shared by the chart writers.

These helpers are consumed by both ``rpdf_scatter_chart`` (per-scenario)
and ``multi_scenario_method_chart`` (run-level). They sit upstream of any
plotting — purely on the (time, rpd_f) trajectory shape.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ProgressionPoint:
    """One sample on an instance's RPDf-vs-norm-time trajectory."""

    time: float
    rpd_f: float


def _compute_best_so_far_y_values(y_values: list[float]) -> list[float]:
    best: list[float] = []
    current: float | None = None
    for y in y_values:
        current = y if current is None else min(current, y)
        best.append(current)
    return best


def _dedupe_progression_points(
    points: list[ProgressionPoint],
) -> list[ProgressionPoint]:
    deduped_by_time: dict[float, ProgressionPoint] = {}
    for p in points:
        deduped_by_time[p.time] = p
    return [deduped_by_time[t] for t in sorted(deduped_by_time)]


def build_best_so_far_progression_points(grp: pd.DataFrame) -> list[ProgressionPoint]:
    """Convert a per-instance trajectory frame to a sorted, deduped list of
    :class:`ProgressionPoint`. ``rpd_f`` is replaced by the running min,
    so each point's y is the best-so-far at that time.
    """
    if grp.empty:
        return []
    x_values = grp["norm_time"].tolist()
    best_y = _compute_best_so_far_y_values(grp["rpd_f"].tolist())
    points: list[ProgressionPoint] = [
        ProgressionPoint(time=float(x), rpd_f=float(y))
        for x, y in zip(x_values, best_y)
    ]
    return _dedupe_progression_points(points)


def keep_strict_global_improvements_or_endpoints(
    progression_grp: pd.DataFrame,
) -> pd.DataFrame:
    """Keep rows whose ``rpd_f`` strictly improves the *global* running min
    over the whole instance trajectory, plus each ``call_index`` group's
    last row (endpoint, always kept regardless of improvement).

    The marker y-value plotted by the chart is the global best-so-far at the
    marker's time; filtering by per-call improvement leaves clusters of
    markers all stacked at the same y when a call's per-point rpd_f never
    beats the global best set by an earlier call. Filtering by global
    improvement guarantees each non-endpoint marker sits at a distinct y.

    Sort key is ``norm_time`` with ``global_sec`` as a tiebreaker when
    present. Required columns: ``call_index``, ``rpd_f``, ``norm_time``.
    """
    if progression_grp.empty:
        return progression_grp
    sort_cols = [c for c in ["norm_time", "global_sec"] if c in progression_grp.columns]
    ordered = progression_grp.sort_values(sort_cols)
    endpoint_indices: set = set()
    for _, sub_grp in ordered.groupby("call_index", sort=False):
        endpoint_indices.add(sub_grp.index[-1])
    keep_indices: list = []
    running_min = float("inf")
    for idx, rpdf in zip(ordered.index, ordered["rpd_f"].tolist()):
        is_strict = rpdf < running_min
        is_endpoint = idx in endpoint_indices
        if is_strict or is_endpoint:
            keep_indices.append(idx)
        if is_strict:
            running_min = rpdf
    return progression_grp.loc[keep_indices].sort_values(sort_cols)
