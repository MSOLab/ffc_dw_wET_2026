"""Unit tests for ``ffc_ddw_sum_et.report.np_utils.decimate_step_series``.

The mean step function emitted by ``step_function_mean_over_union`` samples
at the union of every instance's change times — 10^5-10^6 points at scale.
``decimate_step_series`` collapses that to a bounded, visually-lossless set.
These tests pin the contract the flow-comparison chart depends on: bounded
output, preserved endpoints, and sub-quantum thinning.
"""

from __future__ import annotations

from ffc_ddw_sum_et.report.np_utils import decimate_step_series


def test_short_series_returned_unchanged() -> None:
    xs, ys = [0.0, 1.0], [0.5, 0.4]
    assert decimate_step_series(xs, ys, max_points=10) == (xs, ys)


def test_flat_series_collapses_to_endpoints() -> None:
    xs = [0.0, 0.3, 0.6, 1.0]
    ys = [0.5, 0.5, 0.5, 0.5]
    out_x, out_y = decimate_step_series(xs, ys, max_points=100)
    assert out_x == [0.0, 1.0]
    assert out_y == [0.5, 0.5]


def test_monotone_series_bounded_by_max_points() -> None:
    n = 5000
    xs = [i / n for i in range(n)]
    # strictly decreasing from 1.0 to ~0.0
    ys = [1.0 - i / n for i in range(n)]
    out_x, out_y = decimate_step_series(xs, ys, max_points=100)
    # <= max_points interior drops + first + last
    assert len(out_x) <= 102
    assert out_x[0] == xs[0] and out_x[-1] == xs[-1]
    assert out_y[0] == ys[0] and out_y[-1] == ys[-1]


def test_sub_quantum_moves_dropped() -> None:
    # y drops by a tiny amount many times, then one big drop. With a coarse
    # quantum the tiny moves collapse and only the big drop survives.
    xs = [0.0, 0.1, 0.2, 0.3, 0.4]
    ys = [1.0, 0.999, 0.998, 0.997, 0.0]
    out_x, out_y = decimate_step_series(xs, ys, max_points=4)
    # quantum = (1.0 - 0.0) / 4 = 0.25; only the drop to 0.0 clears it.
    assert out_x == [0.0, 0.4]
    assert out_y == [1.0, 0.0]


def test_max_points_two_or_less_is_noop() -> None:
    xs = [0.0, 0.5, 1.0]
    ys = [1.0, 0.5, 0.0]
    assert decimate_step_series(xs, ys, max_points=2) == (xs, ys)
