from __future__ import annotations

import time

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import (
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_instance(name: str = "neh_cp_stop_test") -> FFcDDWParameters:
    """5-job, 2-stage, m=1 instance — yields 4 batches with added_batch_size=1."""
    job_id_list = ["j0", "j1", "j2", "j3", "j4"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2], [3, 1]]),
        ),
        job_2_due_window_map={
            "j0": (4, 5),
            "j1": (3, 4),
            "j2": (0, 10),
            "j3": (5, 6),
            "j4": (2, 8),
        },
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1, "j4": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1, "j4": 1},
    )


def test_stop_predicate_breaks_after_first_batch_recovers_full_schedule() -> None:
    instance = _make_instance()
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=0.5, added_batch_size=1),
        stop_predicate=lambda: True,
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.STOP_REQUESTED
    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.obj_value is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        record.result.schedule, instance
    )
    assert record.result.obj_value == float(sum_e + sum_t)
    metrics = record.result.metrics
    assert metrics is not None
    assert metrics["stopped_after_batch"] == 0
    # First batch covers 2 jobs (max(added_batch_size, max_m*2)=2);
    # the remaining 3 jobs are recovered by earliest-start dispatch.
    recovered = metrics["recovered_jobs"]
    assert len(recovered) == 3
    assert set(recovered).issubset(set(instance.job_id_list))


def test_wall_clock_deadline_in_past_recovers_all_jobs() -> None:
    instance = _make_instance()
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(
            cp_tl_seconds=0.5,
            added_batch_size=1,
            wall_clock_deadline_sec=time.monotonic() - 1.0,
        ),
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.STOP_REQUESTED
    assert record.result is not None
    assert record.result.schedule is not None
    metrics = record.result.metrics
    assert metrics is not None
    # Deadline is past at first iteration → break before primary CP-SAT solve.
    # No partial_sol from CP-SAT → recovery dispatches all 5 jobs.
    assert len(metrics["recovered_jobs"]) == instance.job_count


def test_no_stop_predicate_no_deadline_runs_to_completion() -> None:
    instance = _make_instance()
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=0.5, added_batch_size=1),
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.COMPLETED
    assert record.result is not None
    assert record.result.schedule is not None


def test_stop_predicate_false_runs_to_completion() -> None:
    instance = _make_instance()
    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=0.5, added_batch_size=1),
        stop_predicate=lambda: False,
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.COMPLETED
    assert record.result is not None
    assert record.result.schedule is not None


def test_stop_after_last_batch_keeps_partial_sol_unmodified() -> None:
    """When stop_predicate fires after the last batch completes, all jobs
    are already in partial_sol — recovery skips dispatch and reports
    empty ``recovered_jobs``."""
    instance = _make_instance()
    counter = {"calls": 0}

    def predicate() -> bool:
        counter["calls"] += 1
        # 4 batches → predicate is checked 4 times. Fire on the 4th
        # (after the last batch is fully processed).
        return counter["calls"] >= 4

    spec = AlgSpec(
        instance=instance,
        option=NehCpOption(cp_tl_seconds=0.5, added_batch_size=1),
        stop_predicate=predicate,
    )

    record = NehCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.STOP_REQUESTED
    assert record.result is not None
    assert record.result.schedule is not None
    metrics = record.result.metrics
    assert metrics is not None
    assert metrics["recovered_jobs"] == ()
    assert metrics["stopped_after_batch"] == 3  # last batch index (4 batches: 0..3)
