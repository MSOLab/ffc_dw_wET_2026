"""Numpy helpers for trajectory aggregation in chart writers.

Currently exports :func:`step_function_mean_over_union`, the vectorized
inner loop shared by per-scenario (``rpdf_scatter_chart``) and run-level
(``multi_scenario_method_chart``) mean-RPDf renderers.
"""

import numpy as np

from .trajectory_utils import ProgressionPoint


def step_function_mean_over_union(
    model_arrays: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[list[float], list[float]]:
    """Mean step function across a list of per-instance piecewise-constant
    trajectories, sampled at the union of all change times within
    ``[max(first_times), max(last_times)]``.

    Each ``(times, values)`` pair must be a 1-D float64 array sorted
    ascending by ``times`` with no NaNs. At every sample time ``t`` each
    model contributes ``values[searchsorted(times, t, 'right') - 1]``;
    because the sample grid starts at ``max(first_times)``, every model
    has at least one point at ``<= t`` and the index is always valid (no
    None-filtering needed). The grid is extended to the overall last
    time to preserve the original ``union_times[-1] >= end_time``
    invariant the callers depend on for the trailing endpoint.

    Returns ``(mean_x, mean_y)`` as plain Python lists, ready for
    downstream plotting helpers. Caller must guarantee
    ``len(model_arrays) >= 1`` and that every ``times`` array is
    non-empty.
    """
    start_time = max(float(times[0]) for times, _ in model_arrays)
    end_time = max(float(times[-1]) for times, _ in model_arrays)

    in_range = [t[(t >= start_time) & (t <= end_time)] for t, _ in model_arrays]
    event_times = np.unique(np.concatenate(in_range))
    if event_times.size == 0:
        event_times = (
            np.array([start_time, end_time], dtype=np.float64)
            if end_time > start_time
            else np.array([start_time], dtype=np.float64)
        )
    elif event_times[-1] < end_time:
        event_times = np.append(event_times, end_time)

    sum_y = np.zeros(event_times.shape, dtype=np.float64)
    for times, values in model_arrays:
        idx = np.searchsorted(times, event_times, side="right") - 1
        sum_y += values[idx]
    mean_y_arr = sum_y / len(model_arrays)

    return event_times.tolist(), mean_y_arr.tolist()


def progression_points_to_arrays(
    progression_points: list[ProgressionPoint],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert a list of :class:`ProgressionPoint` into the
    ``(times, values)`` ndarray pair consumed by
    :func:`step_function_mean_over_union`.
    """
    n = len(progression_points)
    times = np.fromiter((p.time for p in progression_points), dtype=np.float64, count=n)
    values = np.fromiter(
        (p.rpd_f for p in progression_points), dtype=np.float64, count=n
    )
    return times, values
