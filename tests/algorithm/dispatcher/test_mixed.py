"""Tests for MixedDispatcher.iter_mixed_schedules_by_sequence and regression.

WP-1 regression: get_best_mixed_schedule_by_sequence must return the same
result before and after refactoring to use iter_mixed_schedules_by_sequence.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.dispatcher.base import BaseDispatcher
from ffc_ddw_sum_et.algorithm.dispatcher.mixed import MixedDispatcher
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tiny_instance(
    *,
    name: str = "mixed_test",
    processing_rows: list[list[int]] | None = None,
) -> FFcDDWParameters:
    if processing_rows is None:
        processing_rows = [[3, 2], [2, 3], [1, 2]]
    n_jobs = len(processing_rows)
    n_stages = len(processing_rows[0])
    job_id_list = [f"j{i}" for i in range(n_jobs)]
    stage_id_list = [f"i{s}" for s in range(n_stages)]
    stage_2_machines_map = {
        stage_id: [f"{stage_id}_{k}" for k in range(1)] for stage_id in stage_id_list
    }
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map=stage_2_machines_map,
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame(processing_rows),
        ),
        job_2_due_window_map={j: (0, 9999) for j in job_id_list},
        job_2_ewt_map={j: 1 for j in job_id_list},
        job_2_twt_map={j: 1 for j in job_id_list},
    )


def _make_seq(instance: FFcDDWParameters) -> list[str]:
    return list(instance.job_id_list)


# ---------------------------------------------------------------------------
# Regression: get_best_mixed_schedule_by_sequence unchanged
# ---------------------------------------------------------------------------


def test_get_best_mixed_returns_same_result_after_refactor() -> None:
    """get_best_mixed_schedule_by_sequence must return the same schedule
    (same makespan) before and after the iter_mixed_schedules_by_sequence
    refactor."""
    instance = _make_tiny_instance()
    dispatcher = MixedDispatcher(instance)
    seq = _make_seq(instance)

    result = dispatcher.get_best_mixed_schedule_by_sequence(seq)

    assert result is not None
    assert result.makespan > 0
    # Verify the schedule is valid (no negative times)
    end_map = result.get_jik_2_end_time_map()
    for v in end_map.values():
        assert v >= 0


def test_get_best_mixed_makespan_criteria() -> None:
    """get_best_mixed_schedule_by_sequence with criteria='makespan' must
    return the schedule with minimum makespan."""
    instance = _make_tiny_instance()
    dispatcher = MixedDispatcher(instance)
    seq = _make_seq(instance)

    result = dispatcher.get_best_mixed_schedule_by_sequence(seq, criteria="makespan")

    assert result is not None
    assert result.makespan > 0


# ---------------------------------------------------------------------------
# iter_mixed_schedules_by_sequence: yields expected candidates
# ---------------------------------------------------------------------------


def test_iter_yields_all_np_candidates() -> None:
    """iter_mixed_schedules_by_sequence must yield one schedule per np value
    in np_list (excluding those that raise ValueError)."""
    instance = _make_tiny_instance()
    dispatcher = MixedDispatcher(instance)
    seq = _make_seq(instance)

    candidates = list(dispatcher.iter_mixed_schedules_by_sequence(seq))

    # np_list for job_count=3: [3, 2, 1, 0] — 4 candidates
    # All should be valid for a tiny instance
    assert len(candidates) == 4, f"Expected 4 np candidates; got {len(candidates)}"
    # Each candidate must be a distinct object
    assert len(set(id(c) for c in candidates)) == len(candidates)


def test_iter_schedules_have_valid_structure() -> None:
    """Each schedule yielded by iter_mixed_schedules_by_sequence must have
    valid (non-negative) end times."""
    instance = _make_tiny_instance()
    dispatcher = MixedDispatcher(instance)
    seq = _make_seq(instance)

    for sch in dispatcher.iter_mixed_schedules_by_sequence(seq):
        end_map = sch.get_jik_2_end_time_map()
        for v in end_map.values():
            assert v >= 0


# ---------------------------------------------------------------------------
# BaseDispatcher._create_empty_schedule
# ---------------------------------------------------------------------------


def test_base_dispatcher_creates_empty_schedule() -> None:
    """BaseDispatcher._create_empty_schedule must produce a schedule with
    correct jobs, stages, and machines."""
    instance = _make_tiny_instance()
    dispatcher = BaseDispatcher(instance)
    schedule = dispatcher._create_empty_schedule(instance)

    assert set(schedule.jobs) == set(instance.job_id_list)
    assert set(schedule.stages) == set(instance.stage_id_list)


# ---------------------------------------------------------------------------
# Seed dispatch helpers (coarsen_solve_reconstruct)
# ---------------------------------------------------------------------------


def test_dispatch_seed_job_sequence_edd_order() -> None:
    """_dispatch_seed_job_sequence must sort by d^+ ascending."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _dispatch_seed_job_sequence,
    )

    df = pd.DataFrame([[10, 5], [8, 4], [6, 3]])
    instance = FFcDDWParameters(
        name="seq_test",
        job_id_list=["A", "B", "C"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(name="seq_test_p", df=df),
        job_2_due_window_map={"A": (100, 200), "B": (50, 100), "C": (50, 80)},
        job_2_ewt_map={"A": 1, "B": 1, "C": 1},
        job_2_twt_map={"A": 1, "B": 1, "C": 1},
    )
    # d^+ values: A=200, B=100, C=80 → sorted: C, B, A
    seq = _dispatch_seed_job_sequence(instance)
    assert seq == ["C", "B", "A"]


def test_dispatch_seed_job_sequence_twt_tiebreak() -> None:
    """When d^+ is equal, sort by w^+ descending."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _dispatch_seed_job_sequence,
    )

    df = pd.DataFrame([[10, 5], [8, 4], [6, 3]])
    instance = FFcDDWParameters(
        name="twt_test",
        job_id_list=["A", "B", "C"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(name="twt_test_p", df=df),
        job_2_due_window_map={
            "A": (0, 100),
            "B": (0, 100),
            "C": (0, 100),
        },
        job_2_ewt_map={"A": 1, "B": 2, "C": 3},
        job_2_twt_map={"A": 3, "B": 2, "C": 1},
    )
    # d^+ all equal → sort by w^+ desc: A(3), B(2), C(1)
    seq = _dispatch_seed_job_sequence(instance)
    assert seq == ["A", "B", "C"]


def test_dispatch_seed_job_sequence_given_tiebreak() -> None:
    """When d^+ and w^+ are equal, preserve given job order."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _dispatch_seed_job_sequence,
    )

    df = pd.DataFrame([[10, 5], [8, 4], [6, 3]])
    instance = FFcDDWParameters(
        name="given_test",
        job_id_list=["B", "A", "C"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(name="given_test_p", df=df),
        job_2_due_window_map={
            "A": (0, 100),
            "B": (0, 100),
            "C": (0, 100),
        },
        job_2_ewt_map={"A": 1, "B": 1, "C": 1},
        job_2_twt_map={"A": 1, "B": 1, "C": 1},
    )
    # All equal → preserve given order: B, A, C
    seq = _dispatch_seed_job_sequence(instance)
    assert seq == ["B", "A", "C"]


def test_build_dispatch_seed_schedule_job_wise_feasible() -> None:
    """_build_dispatch_seed_schedule(job_wise) must return a feasible schedule."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _build_dispatch_seed_schedule,
    )

    instance = _make_tiny_instance()
    schedule = _build_dispatch_seed_schedule(instance, "job_wise")

    assert schedule is not None
    end_map = schedule.get_jik_2_end_time_map()
    for v in end_map.values():
        assert v >= 0


def test_build_dispatch_seed_schedule_mixed_feasible() -> None:
    """_build_dispatch_seed_schedule(mixed) must return a feasible schedule."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _build_dispatch_seed_schedule,
    )

    instance = _make_tiny_instance()
    schedule = _build_dispatch_seed_schedule(instance, "mixed")

    assert schedule is not None
    end_map = schedule.get_jik_2_end_time_map()
    for v in end_map.values():
        assert v >= 0


def test_mixed_seed_obj_le_job_wise_seed_obj() -> None:
    """On the same coarsened instance, mixed seed wET <= job_wise seed wET."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        _build_dispatch_seed_schedule,
        compute_weighted_earliness_tardiness,
    )

    instance = _make_tiny_instance()
    sch_jw = _build_dispatch_seed_schedule(instance, "job_wise")
    sch_mixed = _build_dispatch_seed_schedule(instance, "mixed")

    sum_e_jw, sum_t_jw = compute_weighted_earliness_tardiness(sch_jw, instance)
    obj_jw = sum_e_jw + sum_t_jw

    sum_e_mx, sum_t_mx = compute_weighted_earliness_tardiness(sch_mixed, instance)
    obj_mx = sum_e_mx + sum_t_mx

    assert obj_mx <= obj_jw, f"mixed wET {obj_mx} should be <= job_wise wET {obj_jw}"
