"""Unit tests for ``ffc_ddw_sum_et.report.np_utils``.

The mean step function emitted by ``step_function_mean_over_union`` samples
at the union of every instance's change times — 10^5-10^6 points at scale.
``decimate_step_series`` collapses that to a bounded, visually-lossless set.
``round_step_series`` rounds coords to display resolution, reducing payload
byte size without dropping any points.
"""

from __future__ import annotations

import math

from ffc_ddw_sum_et.report.np_utils import decimate_step_series, round_step_series


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


# ── round_step_series ──────────────────────────────────────────────────


def test_round_step_series_rounds_preserves_length() -> None:
    xs = [0.0, 0.123456789, 1.0]
    ys = [0.5, 0.4834094857291839, 0.0]
    rx, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    assert len(rx) == len(xs)
    assert len(ry) == len(ys)
    assert rx == [0.0, 0.123457, 1.0]
    assert ry == [0.5, 0.48341, 0.0]


def test_round_step_series_preserves_monotonicity() -> None:
    xs = [0.0, 0.3, 0.7, 1.0]
    ys = [0.5, 0.49999, 0.49998, 0.0]
    _, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    for i in range(1, len(ry)):
        assert ry[i] <= ry[i - 1], f"y broke monotonicity at i={i}"


def test_round_step_series_preserves_endpoints() -> None:
    xs = [0.0, 0.25, 0.75, 1.0]
    ys = [0.6, 0.49999, 0.30001, 0.0]
    rx, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    assert math.isclose(rx[0], round(xs[0], 6))
    assert math.isclose(rx[-1], round(xs[-1], 6))
    assert math.isclose(ry[0], round(ys[0], 5))
    assert math.isclose(ry[-1], round(ys[-1], 5))


def test_round_step_series_idempotent() -> None:
    xs = [0.0, 0.123457, 1.0]
    ys = [0.5, 0.48341, 0.0]
    rx, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    assert rx == xs
    assert ry == ys


def test_round_step_series_flat_equal_ys_no_reorder() -> None:
    xs = [0.0, 0.1, 0.2]
    ys = [0.500001, 0.500003, 0.500002]
    _, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    for i in range(1, len(ry)):
        assert ry[i] <= ry[i - 1], f"flat y reordered at i={i}"
