"""Direct tests for ``build_full_sch_from_last_stage_only_sch``.

Verify the algorithm-level wrapper around ``reverse_dispatch_full_schedule``:
final schedule, dispatched obj, intermediate snapshot ordering for the
multi-stage path, and the single-stage shortcut.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.mcf_lb.full_sch_builder import (
    BuildFullSchResult,
    build_full_sch_from_last_stage_only_sch,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_sch_builder import (
    heuristic_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_multi_stage_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="bfs_multi",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="bfs_multi_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_single_stage_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="bfs_single",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="bfs_single_p",
            df=pd.DataFrame([[3], [2]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
    )


def test_build_full_sch_multi_stage_emits_expected_intermediates() -> None:
    instance = _make_multi_stage_instance()
    apply_res = apply_lb_by_mcf(instance)
    h_res = heuristic_last_stage_only_from_mcf_lb(
        instance, apply_res.mcf_preemptive_schedule
    )

    result = build_full_sch_from_last_stage_only_sch(instance, h_res.schedule)

    assert isinstance(result, BuildFullSchResult)
    assert result.schedule is not None
    assert result.dispatched_obj is not None
    assert result.full_sch_makespan is not None
    assert result.dispatch_sec >= 0
    # Multi-stage path emits 5 intermediates plus the final.
    labels = [label for label, _ in result.intermediate_schedules]
    assert labels == [
        "lastS_only_before_rs",
        "lastS_only_after_rs",
        "lastS_only_flipped",
        "fullS_before_unflip",
        "fullS_after_unflip",
        "fullS_after_sa_iti",
    ]
    # Every job is scheduled at every stage.
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            assert result.schedule.get_job_end_time(stage_id, job_id) >= 0


def test_build_full_sch_single_stage_short_circuits() -> None:
    """Single-stage instances skip reverse-dispatch; only the final
    snapshot label is emitted.
    """
    instance = _make_single_stage_instance()
    apply_res = apply_lb_by_mcf(instance)
    h_res = heuristic_last_stage_only_from_mcf_lb(
        instance, apply_res.mcf_preemptive_schedule
    )

    result = build_full_sch_from_last_stage_only_sch(instance, h_res.schedule)

    assert result.schedule is not None
    labels = [label for label, _ in result.intermediate_schedules]
    assert labels == ["fullS_after_sa_iti"]


def test_build_full_sch_with_rebuild_collapses_inflated_durations() -> None:
    """``rebuild_last_stage_with_original_p=True`` rebuilds the input
    last-stage schedule under the original (uninflated) durations before
    reverse-dispatch. Verify the final last-stage durations match the
    original ``p_map``.
    """
    instance = _make_multi_stage_instance()
    apply_res = apply_lb_by_mcf(instance)
    p_inc = 5
    h_res = heuristic_last_stage_only_from_mcf_lb(
        instance, apply_res.mcf_preemptive_schedule, p_increment=p_inc
    )

    result = build_full_sch_from_last_stage_only_sch(
        instance, h_res.schedule, rebuild_last_stage_with_original_p=True
    )

    assert result.schedule is not None
    last_stage = instance.stage_id_list[-1]
    p_map = instance.get_job_2_p_map_for_stage(last_stage)
    durations: dict[str, int] = {
        job_id: end - start
        for _, start, end, job_id in result.schedule.iter_operations_on_stage(
            last_stage
        )
    }
    for job_id in instance.job_id_list:
        assert durations[job_id] == p_map[job_id]
