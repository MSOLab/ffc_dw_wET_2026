"""End-to-end tests for SwCpDispatcher on a small FFcDDW instance."""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import (
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.algorithm.sw_cp import SwCpDispatcher, SwCpOption, SwCpStepEntry
from ffc_ddw_sum_et.algorithm.utils import trunc4
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_instance(name: str = "sw_cp_test") -> FFcDDWParameters:
    """5-job 2-stage instance with parallel machines on stage i0."""
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2], [3, 1]]),
        ),
        job_2_due_window_map={
            "j0": (4, 5),
            "j1": (3, 4),
            "j2": (5, 8),
            "j3": (5, 6),
            "j4": (8, 10),
        },
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1, "j4": 1},
        job_2_twt_map={"j0": 2, "j1": 2, "j2": 2, "j3": 2, "j4": 2},
    )


def _seed_incumbent(instance: FFcDDWParameters):
    rec = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))
    )
    assert rec.result is not None and rec.result.schedule is not None
    return rec.result.schedule, float(rec.result.obj_value or 0.0)


def test_dispatcher_returns_feasible_record() -> None:
    instance = _make_instance()
    seed, _seed_obj = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.algorithm_id == "sw_cp"
    assert record.instance_id == instance.name
    assert record.termination_reason == TerminationReason.COMPLETED


def test_dispatcher_does_not_worsen_incumbent() -> None:
    instance = _make_instance()
    seed, seed_obj = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.result is not None
    assert record.result.obj_value is not None
    # accept/reject ensures non-worsening
    assert record.result.obj_value <= seed_obj


def test_dispatcher_obj_value_matches_recomputed_weighted_et() -> None:
    instance = _make_instance()
    seed, _ = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    assert record.result.obj_value == float(sum_e + sum_t)


def test_dispatcher_schedules_every_op() -> None:
    instance = _make_instance()
    seed, _ = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            schedule.get_job_end_time(stage_id, job_id)


def test_dispatcher_step_log_present() -> None:
    instance = _make_instance()
    seed, _ = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    step_log = metrics.get("step_log")
    assert step_log is not None
    for entry in step_log:
        assert isinstance(entry, SwCpStepEntry)


def test_dispatcher_progress_log_carries_obj_bound() -> None:
    """CP-SAT's best_objective_bound must flow into progress_log entries
    (mapped into full-instance objective space via full_offset) instead
    of being dropped as ``None`` — this is what populates the obj_log's
    ``obj_bound`` series downstream."""
    instance = _make_instance()
    seed, _ = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.progress_log is not None
    assert any(entry.obj_bound is not None for entry in record.progress_log)

    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    step_log = metrics.get("step_log")
    assert step_log is not None
    for entry in step_log:
        assert (
            entry.unfixed_op_count + entry.profile_fixed_op_count
            == entry.non_time_fixed_op_count
        )


def test_dispatcher_proportional_tl_equals_kappa_times_ntf() -> None:
    """batch_tl_mode='proportional': each step's applied TL must equal
    kappa * non_time_fixed_op_count (truncated to 4 dp), and the ntf
    invariant (unfixed + profile_fixed == ntf) must still hold."""
    kappa = 0.5
    instance = _make_instance()
    seed, _ = _seed_incumbent(instance)

    spec = AlgSpec(
        instance=instance,
        option=SwCpOption(
            batch_tl_mode="proportional",
            non_time_fixed_op_time_limit_multiplier=kappa,
            unfixed_batch_count=2,
        ),
        ref_solution=seed,
    )
    record = SwCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    step_log = metrics.get("step_log")
    assert step_log
    for entry in step_log:
        assert isinstance(entry, SwCpStepEntry)
        expected_tl = trunc4(kappa * entry.non_time_fixed_op_count)
        assert entry.TL == expected_tl
        assert (
            entry.unfixed_op_count + entry.profile_fixed_op_count
            == entry.non_time_fixed_op_count
        )
