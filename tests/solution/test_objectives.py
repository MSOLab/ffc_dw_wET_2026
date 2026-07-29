"""Tests for objective-function helpers in ``solution/objectives.py``."""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import (
    compute_job_2_obj_contrib_map,
    compute_weighted_earliness_tardiness,
)


def _make_instance(
    *,
    due_windows: dict[str, tuple[int, int]] | None = None,
    ewt_map: dict[str, int] | None = None,
    twt_map: dict[str, int] | None = None,
) -> FFcDDWParameters:
    jobs = ["j0", "j1", "j2"]
    return FFcDDWParameters(
        name="obj_test",
        job_id_list=jobs,
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="obj_test_p",
            df=pd.DataFrame([[2], [3], [1]]),
        ),
        job_2_due_window_map=due_windows
        or {
            "j0": (3, 6),
            "j1": (4, 7),
            "j2": (5, 8),
        },
        job_2_ewt_map=ewt_map if ewt_map is not None else {"j0": 1, "j1": 2, "j2": 1},
        job_2_twt_map=twt_map if twt_map is not None else {"j0": 2, "j1": 1, "j2": 2},
    )


def _make_schedule(instance: FFcDDWParameters, end_times: list[int]) -> FFcSchedule:
    """Build a single-stage schedule with non-overlapping operations."""
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    stage = instance.stage_id_list[0]
    prev_end = 0
    for idx, j in enumerate(instance.job_id_list):
        e = end_times[idx]
        s = max(prev_end, e - [2, 3, 1][idx])
        schedule.add_ops_times_2_mc(stage, "i0_0", j, s, e)
        prev_end = e
    return schedule


def test_job_contrib_sum_equals_total_et() -> None:
    """P1: job별 기여도 합 == compute_weighted_earliness_tardiness 총합."""
    instance = _make_instance()
    schedule = _make_schedule(instance, end_times=[5, 10, 14])

    total_e, total_t = compute_weighted_earliness_tardiness(schedule, instance)
    contrib_map = compute_job_2_obj_contrib_map(schedule, instance)

    assert sum(contrib_map.values()) == total_e + total_t


def test_job_inside_due_window_has_zero_contrib() -> None:
    """P1: due window 안에 있는 job의 기여도는 0."""
    instance = _make_instance()
    schedule = _make_schedule(instance, end_times=[5, 9, 13])

    contrib_map = compute_job_2_obj_contrib_map(schedule, instance)
    assert contrib_map["j0"] == 0


def test_time_factor_scaling() -> None:
    """P1: time_factor=2 환산 — C_j를 2배로 해석."""
    instance = _make_instance()
    schedule = _make_schedule(instance, end_times=[5, 10, 14])

    contrib_default = compute_job_2_obj_contrib_map(schedule, instance, time_factor=1)
    contrib_scaled = compute_job_2_obj_contrib_map(schedule, instance, time_factor=2)
    assert contrib_scaled != contrib_default


def test_early_job_earliness_only() -> None:
    """early job은 earliness만 (tardiness는 0)."""
    instance = _make_instance(
        due_windows={"j0": (20, 30), "j1": (20, 30), "j2": (20, 30)},
    )
    schedule = _make_schedule(instance, end_times=[4, 8, 12])
    stage = instance.stage_id_list[0]

    contrib_map = compute_job_2_obj_contrib_map(schedule, instance)
    for j in instance.job_id_list:
        c = schedule.get_job_end_time(stage, j)
        assert contrib_map[j] == instance.job_2_ewt_map[j] * (20 - c)


def test_late_job_tardiness_only() -> None:
    """late job은 tardiness만 (earliness는 0)."""
    instance = _make_instance(
        due_windows={"j0": (1, 2), "j1": (1, 2), "j2": (1, 2)},
    )
    schedule = _make_schedule(instance, end_times=[4, 8, 12])
    stage = instance.stage_id_list[0]

    contrib_map = compute_job_2_obj_contrib_map(schedule, instance)
    for j in instance.job_id_list:
        c = schedule.get_job_end_time(stage, j)
        assert contrib_map[j] == instance.job_2_twt_map[j] * (c - 2)


def test_missing_weight_defaults_to_1() -> None:
    """P1: 가중치 맵에 없는 job은 기본 1."""
    instance = _make_instance(
        ewt_map={"j0": 1},
        twt_map={"j0": 1},
        due_windows={
            "j0": (5, 6),
            "j1": (5, 6),
            "j2": (5, 6),
        },
    )
    schedule = _make_schedule(instance, end_times=[3, 7, 11])

    contrib_map = compute_job_2_obj_contrib_map(schedule, instance)
    assert contrib_map["j0"] == 1 * max(5 - 3, 0) + 1 * max(3 - 6, 0)
    assert contrib_map["j1"] == 1 * max(5 - 7, 0) + 1 * max(7 - 6, 0)
