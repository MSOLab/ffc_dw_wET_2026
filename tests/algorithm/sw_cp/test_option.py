"""Sanity tests for SwCpOption defaults / validation."""

from __future__ import annotations

import dataclasses

import pytest

from ffc_ddw_sum_et.algorithm.sw_cp import SwCpOption


def test_defaults_match_plan() -> None:
    opt = SwCpOption()
    assert opt.solver_thread_cnt == 1
    assert opt.batch_size == 1
    assert opt.step_size == 1
    assert opt.unfixed_batch_count == 1
    assert opt.left_profile_fixed_batch_count == 0
    assert opt.right_profile_fixed_batch_count == 0
    assert opt.enable_promotion_profile_fixed is False
    assert opt.pf_method == "PF1"
    assert opt.cp_tl_seconds is None
    assert opt.total_timelimit_seconds is None
    assert opt.batch_tl_mode == "constant"
    assert opt.apply_cumulative_tl is False
    assert opt.wall_clock_deadline_sec is None
    assert opt.error_if_infeasible is False
    assert opt.keep_step_schedules is False


def test_frozen() -> None:
    opt = SwCpOption()
    with pytest.raises(dataclasses.FrozenInstanceError):
        opt.solver_thread_cnt = 4  # type: ignore[misc]


def test_validation_step_size_min() -> None:
    with pytest.raises(ValueError, match="step_size must be >= 1"):
        SwCpOption(step_size=0)


def test_validation_unfixed_min() -> None:
    with pytest.raises(ValueError, match="unfixed_batch_count must be >= 1"):
        SwCpOption(unfixed_batch_count=0)


def test_validation_left_profile_nonneg() -> None:
    with pytest.raises(ValueError, match="left_profile_fixed_batch_count must be >= 0"):
        SwCpOption(left_profile_fixed_batch_count=-1)


def test_validation_batch_size_min() -> None:
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        SwCpOption(batch_size=0)


def test_default_kappa_is_none() -> None:
    opt = SwCpOption()
    assert opt.non_time_fixed_op_time_limit_multiplier is None


def test_validation_kappa_must_be_positive() -> None:
    with pytest.raises(
        ValueError, match="non_time_fixed_op_time_limit_multiplier must be > 0"
    ):
        SwCpOption(non_time_fixed_op_time_limit_multiplier=0.0)


def test_validation_proportional_requires_kappa() -> None:
    with pytest.raises(ValueError, match="batch_tl_mode='proportional' requires"):
        SwCpOption(batch_tl_mode="proportional")


def test_proportional_with_kappa_is_valid() -> None:
    opt = SwCpOption(
        batch_tl_mode="proportional",
        non_time_fixed_op_time_limit_multiplier=0.5,
    )
    assert opt.batch_tl_mode == "proportional"
    assert opt.non_time_fixed_op_time_limit_multiplier == 0.5


def test_kappa_allowed_with_non_proportional_mode() -> None:
    opt = SwCpOption(
        batch_tl_mode="constant",
        non_time_fixed_op_time_limit_multiplier=0.5,
    )
    assert opt.batch_tl_mode == "constant"
    assert opt.non_time_fixed_op_time_limit_multiplier == 0.5


def test_rj_scope_default_is_rtf_only() -> None:
    assert SwCpOption().rj_right_justify_scope == "rtf_only"


def test_rj_scope_all_ops_is_valid() -> None:
    assert (
        SwCpOption(rj_right_justify_scope="all_ops").rj_right_justify_scope == "all_ops"
    )


def test_rj_scope_invalid_rejected() -> None:
    with pytest.raises(ValueError, match="rj_right_justify_scope must be one of"):
        SwCpOption(rj_right_justify_scope="everything")  # type: ignore[arg-type]
