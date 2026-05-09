"""Direct tests for ``heuristic_last_stage_only_from_mcf_lb``.

Bypass the controller wrapper to verify:
  - the algorithm function consumes an MCF preemptive schedule and emits
    a feasible last-stage-only schedule plus an intermediate snapshot;
  - ``p_increment > 0`` builds an augmented instance internally without
    requiring caller-side wrapping;
  - negative inputs raise ``ValueError``.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_sch_builder import (
    HeuristicLastStageOnlyResult,
    heuristic_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "heuristic_test") -> FFcDDWParameters:
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_heuristic_returns_feasible_last_stage_only_schedule() -> None:
    instance = _make_instance()
    apply_result = apply_lb_by_mcf(instance)

    result = heuristic_last_stage_only_from_mcf_lb(
        instance, apply_result.mcf_preemptive_schedule
    )

    assert isinstance(result, HeuristicLastStageOnlyResult)
    assert result.status == "HEURISTIC"
    assert result.elapsed_time >= 0
    # The last stage carries an op for every job.
    last_stage = instance.stage_id_list[-1]
    for job_id in instance.job_id_list:
        assert result.schedule.get_job_end_time(last_stage, job_id) >= 0
    # Exactly one intermediate snapshot (before SA+ITI).
    assert len(result.intermediate_schedules) == 1
    label, _ = result.intermediate_schedules[0]
    assert label == "lastS_only_from_mcf_lb_before_sa_iti"


def test_heuristic_p_increment_inflates_durations() -> None:
    """With ``p_increment > 0`` the algorithm builds an augmented instance
    internally; resulting last-stage operations span the inflated durations.
    """
    instance = _make_instance()
    apply_result = apply_lb_by_mcf(instance)
    p_inc = 5

    result = heuristic_last_stage_only_from_mcf_lb(
        instance, apply_result.mcf_preemptive_schedule, p_increment=p_inc
    )

    last_stage = instance.stage_id_list[-1]
    p_map = instance.get_job_2_p_map_for_stage(last_stage)
    durations: dict[str, int] = {
        job_id: end - start
        for _, start, end, job_id in result.schedule.iter_operations_on_stage(
            last_stage
        )
    }
    for job_id in instance.job_id_list:
        assert durations[job_id] == p_map[job_id] + p_inc


@pytest.mark.parametrize(
    "kwargs",
    [
        {"p_increment": -1},
        {"r_multiplier": -0.5},
        {"r_increment": -1},
    ],
)
def test_heuristic_negative_inputs_raise(kwargs: dict) -> None:
    instance = _make_instance()
    apply_result = apply_lb_by_mcf(instance)

    with pytest.raises(ValueError):
        heuristic_last_stage_only_from_mcf_lb(
            instance, apply_result.mcf_preemptive_schedule, **kwargs
        )
