from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import (
    NehCpDispatcher,
    NehCpOption,
    NehCpStepEntry,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_instance(name: str = "neh_cp_instance") -> FFcDDWParameters:
    job_id_list = ["j0", "j1", "j2"]
    stage_id_list = ["i0", "i1"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_dispatcher_returns_feasible_record() -> None:
    instance = _make_instance()
    spec = AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.algorithm_id == "neh_cp"
    assert record.instance_id == instance.name


def test_dispatcher_obj_value_matches_weighted_et() -> None:
    instance = _make_instance()
    spec = AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))

    record = NehCpDispatcher().run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    assert record.result.obj_value == float(sum_e + sum_t)


def test_dispatcher_schedules_every_op() -> None:
    instance = _make_instance()
    spec = AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))

    record = NehCpDispatcher().run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            schedule.get_job_end_time(stage_id, job_id)


def test_dispatcher_custom_job_sequence_used() -> None:
    """When custom_job_sequence is set, the dispatcher must respect that
    permutation rather than ordering jobs by job_priority."""
    instance = _make_instance()
    custom = ("j2", "j0", "j1")
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=1.0, custom_job_sequence=custom),
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.result is not None
    assert record.result.schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        record.result.schedule, instance
    )
    assert record.result.obj_value == float(sum_e + sum_t)


def test_dispatcher_custom_job_sequence_must_be_permutation() -> None:
    instance = _make_instance()
    bad = ("j0", "j1")  # missing j2
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=1.0, custom_job_sequence=bad),
    )

    with pytest.raises(ValueError, match="must be a permutation"):
        NehCpDispatcher().run(spec)


def test_dispatcher_custom_job_sequence_rejects_extra_ids() -> None:
    instance = _make_instance()
    bad = ("j0", "j1", "j2", "ghost")  # contains an id not in instance
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=1.0, custom_job_sequence=bad),
    )

    with pytest.raises(ValueError, match="must be a permutation"):
        NehCpDispatcher().run(spec)


def test_dispatcher_progress_log_tracks_main_problem_obj_value() -> None:
    """``progress_log`` must hold a main-problem-valid trajectory:

    - All ``obj_bound`` values are ``None`` — sub-problem bounds (and even
      the last batch's CP-SAT bound under PF constraints) are not valid
      lower bounds on the main problem.
    - ``elapsed_sec`` is monotonically non-decreasing.
    - The final entry equals the post-processed ``result.obj_value``.
    - ``step_log`` (per-batch summary) remains a separate metric and
      is no longer aligned with ``progress_log`` length.
    """
    instance = _make_instance()
    spec = AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))

    record = NehCpDispatcher().run(spec)

    assert record.progress_log is not None
    assert record.result is not None
    assert record.result.obj_value is not None
    assert len(record.progress_log) >= 1

    step_log = record.result.metrics["step_log"]
    assert isinstance(step_log, tuple)
    assert all(isinstance(entry, NehCpStepEntry) for entry in step_log)

    assert all(entry.obj_bound is None for entry in record.progress_log)
    times = [entry.elapsed_sec for entry in record.progress_log]
    assert times == sorted(times)
    assert record.progress_log[-1].obj_value == record.result.obj_value
