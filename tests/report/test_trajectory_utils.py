"""Regression tests for ``build_best_so_far_progression_points``.

Bug (CSR inner flow chart): an instance whose child solver only got through
one step before the controller stopped contributes a *single* progression
point, at ``norm_time == 1.0``. ``step_function_mean_over_union`` starts its
sample grid at ``max(first_times)``, so one such instance pushed
``start_time`` to 1.0 == ``end_time`` and collapsed the scenario's whole mean
series to a single sample. Plotly's ``mode="lines"`` draws nothing for a
one-point trace, which is why the chart showed only the vertical step-endpoint
guides and no RPDf curve.

Contract: every trajectory starts at t=0, back-filled with its first observed
best-so-far value.
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


def test_trajectory_starting_after_zero_gets_synthetic_origin_point() -> None:
    """Given a trajectory whose first sample is at norm_time=0.4,
    When the progression points are built,
    Then a synthetic point at t=0 carries that first best-so-far value."""
    points = build_best_so_far_progression_points(_grp([(0.4, 0.9), (0.8, 0.5)]))

    assert [p.time for p in points] == [0.0, 0.4, 0.8]
    assert points[0].rpd_f == points[1].rpd_f == 0.9


def test_single_point_trajectory_spans_zero_to_one() -> None:
    """Given an instance that registered only one step, at the stop time,
    When the progression points are built,
    Then the trajectory spans [0, 1] instead of degenerating to one point."""
    points = build_best_so_far_progression_points(_grp([(1.0, 0.75)]))

    assert [p.time for p in points] == [0.0, 1.0]
    assert all(p.rpd_f == 0.75 for p in points)


def test_trajectory_already_starting_at_zero_is_untouched() -> None:
    """Given a trajectory that already starts at t=0,
    When the progression points are built,
    Then no duplicate origin point is inserted."""
    points = build_best_so_far_progression_points(_grp([(0.0, 1.2), (0.5, 0.3)]))

    assert [p.time for p in points] == [0.0, 0.5]


def test_empty_group_stays_empty() -> None:
    assert build_best_so_far_progression_points(_grp([])) == []


def test_mean_series_is_drawable_despite_a_single_point_instance() -> None:
    """Given one instance that only reached its first step at the stop time,
    alongside instances with full trajectories,
    When the scenario mean step function is computed,
    Then it still spans more than one sample — i.e. a line can be drawn."""
    models = [
        build_best_so_far_progression_points(_grp([(0.1, 1.0), (0.6, 0.4)])),
        build_best_so_far_progression_points(_grp([(0.2, 0.9), (0.7, 0.3)])),
        build_best_so_far_progression_points(_grp([(1.0, 0.8)])),  # degenerate
    ]
    mean_x, mean_y = step_function_mean_over_union(
        [progression_points_to_arrays(p) for p in models]
    )

    assert len(mean_x) > 1, "mean series collapsed to a single point (invisible line)"
    assert mean_x[0] == 0.0
    assert len(mean_y) == len(mean_x)
