from __future__ import annotations

import logging
import math

from ffc_ddw_sum_et.algorithm.neh_cp.tl_schedule import resolve_per_step_tl


def _logger() -> logging.Logger:
    return logging.getLogger("test_tl_schedule")


def test_total_seconds_none_and_cp_tl_none_returns_none() -> None:
    result = resolve_per_step_tl(
        cp_tl_from_arg=None,
        total_seconds=None,
        num_batches=None,
        batch_count=4,
        batch_tl_mode="constant",
        batch_tl_offset_seconds=0.01,
        logger=_logger(),
    )
    assert result is None


def test_total_seconds_none_with_cp_tl_returns_flat_list() -> None:
    result = resolve_per_step_tl(
        cp_tl_from_arg=2.5,
        total_seconds=None,
        num_batches=None,
        batch_count=4,
        batch_tl_mode="constant",
        batch_tl_offset_seconds=0.01,
        logger=_logger(),
    )
    assert result == [2.5, 2.5, 2.5, 2.5]


def test_constant_mode_divides_total_evenly() -> None:
    result = resolve_per_step_tl(
        cp_tl_from_arg=None,
        total_seconds=10.0,
        num_batches=None,
        batch_count=4,
        batch_tl_mode="constant",
        batch_tl_offset_seconds=0.01,
        logger=_logger(),
    )
    assert result == [2.5, 2.5, 2.5, 2.5]


def test_constant_mode_with_num_batches_overrides_divisor() -> None:
    result = resolve_per_step_tl(
        cp_tl_from_arg=None,
        total_seconds=10.0,
        num_batches=5,
        batch_count=4,
        batch_tl_mode="constant",
        batch_tl_offset_seconds=0.01,
        logger=_logger(),
    )
    # divisor = num_batches = 5 → 10 / 5 = 2.0
    assert result == [2.0, 2.0, 2.0, 2.0]


def test_linear_mode_sums_to_total() -> None:
    total_seconds = 10.0
    batch_count = 4
    offset = 0.1
    result = resolve_per_step_tl(
        cp_tl_from_arg=None,
        total_seconds=total_seconds,
        num_batches=None,
        batch_count=batch_count,
        batch_tl_mode="linear",
        batch_tl_offset_seconds=offset,
        logger=_logger(),
    )
    assert result is not None
    assert math.isclose(sum(result), total_seconds)
    # monotonically non-decreasing
    for prev, curr in zip(result, result[1:]):
        assert curr >= prev
    # first entry is offset + x, where x = 2*(T - B*offset)/(B*(B+1))
    x = 2.0 * (total_seconds - batch_count * offset) / (batch_count * (batch_count + 1))
    assert math.isclose(result[0], offset + x)


def test_linear_mode_falls_back_to_constant_when_offset_too_large() -> None:
    total_seconds = 10.0
    batch_count = 4
    offset = 10.0  # B * offset = 40 > total_seconds = 10 → fallback
    result = resolve_per_step_tl(
        cp_tl_from_arg=None,
        total_seconds=total_seconds,
        num_batches=None,
        batch_count=batch_count,
        batch_tl_mode="linear",
        batch_tl_offset_seconds=offset,
        logger=_logger(),
    )
    assert result == [2.5, 2.5, 2.5, 2.5]
