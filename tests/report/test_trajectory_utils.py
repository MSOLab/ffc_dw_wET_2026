"""Regression tests for ``build_best_so_far_progression_points``.

Contract: progression points are the deduped, running-min-reduced form of the
input rows — **no synthetic origin point is inserted**. An instance whose
first observation is at ``norm_time == 1.0`` contributes a single point at
``1.0``; the mean-step helper ``step_function_mean_over_union`` then samples
``[max(first_times), max(last_times)]`` = ``[1.0, 1.0]`` and emits one sample.
Rendering a single-sample series is the chart layer's responsibility (it
draws an open marker, not a line), not the trajectory primitive's.

History: commit 28f5ff5 inserted a synthetic ``t=0`` point to force every
trajectory to start at 0 so that ``mode="lines"`` would draw something. That
destroyed the ``max(first_times)`` start semantics the mean-step helper
relies on (the start point is "the moment all instances have a valid
schedule"), so it was removed again — see
``plans/experiment/20260729/flow_chart_backfill_removal.md``.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.report.np_utils import (
    progression_points_to_arrays,
    step_function_mean_over_union,
)
from ffc_ddw_sum_et.report.trajectory_utils import (
    build_best_so_far_progression_points,
)


def _grp(rows: list[tuple[float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["norm_time", "rpd_f"])


def test_trajectory_starting_after_zero_is_not_back_filled() -> None:
    """Given a trajectory whose first sample is at norm_time=0.4,
    When the progression points are built,
    Then no synthetic t=0 point is inserted — the first point stays at 0.4.

    ``step_function_mean_over_union`` defines its sample-grid start as
    ``max(first_times)`` so the run-level flow chart's first point marks
    "the moment every instance has a valid schedule". A synthetic t=0 would
    pull that start back to 0 and mis-locate the first observed RPDf.
    """
    points = build_best_so_far_progression_points(_grp([(0.4, 0.9), (0.8, 0.5)]))

    assert [p.time for p in points] == [0.4, 0.8]
    assert [p.rpd_f for p in points] == [0.9, 0.5]


def test_single_point_trajectory_stays_single_point() -> None:
    """Given an instance that registered only one step, at the stop time,
    When the progression points are built,
    Then the trajectory stays a single point at that time — it is the chart
    layer's job to render a single-sample series, not the trajectory's."""
    points = build_best_so_far_progression_points(_grp([(1.0, 0.75)]))

    assert [p.time for p in points] == [1.0]
    assert [p.rpd_f for p in points] == [0.75]


def test_trajectory_already_starting_at_zero_is_untouched() -> None:
    """Given a trajectory that already starts at t=0,
    When the progression points are built,
    Then no duplicate origin point is inserted."""
    points = build_best_so_far_progression_points(_grp([(0.0, 1.2), (0.5, 0.3)]))

    assert [p.time for p in points] == [0.0, 0.5]


def test_empty_group_stays_empty() -> None:
    assert build_best_so_far_progression_points(_grp([])) == []


def test_mean_series_starts_at_max_first_time_without_back_fill() -> None:
    """Given one instance that only reached its first step at the stop time,
    alongside instances with full trajectories,
    When the scenario mean step function is computed,
    Then the sample grid starts at ``max(first_times)`` — i.e. the moment
    every instance has a valid schedule — instead of being forced back to 0.

    The last instance's first observation is at t=1.0, so the whole series
    collapses to a single sample at t=1.0. Rendering that one point is the
    chart layer's responsibility (an open marker), not the trajectory
    primitive's.
    """
    models = [
        build_best_so_far_progression_points(_grp([(0.1, 1.0), (0.6, 0.4)])),
        build_best_so_far_progression_points(_grp([(0.2, 0.9), (0.7, 0.3)])),
        build_best_so_far_progression_points(_grp([(1.0, 0.8)])),  # degenerate
    ]
    mean_x, mean_y = step_function_mean_over_union(
        [progression_points_to_arrays(p) for p in models]
    )

    assert len(mean_x) == 1, (
        "grid must start at max(first_times), so the single late starter collapses the series"
    )
    assert mean_x[0] == 1.0
    assert len(mean_y) == len(mean_x)
