"""Stop-predicate / wall-clock-deadline tests for PwCpDispatcher."""

from __future__ import annotations

import time

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import (
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.algorithm.pw_cp import PwCpDispatcher, PwCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="pw_cp_stop_test",
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="pw_cp_stop_test_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2], [3, 1]]),
        ),
        job_2_due_window_map={
            "j0": (4, 5),
            "j1": (3, 4),
            "j2": (5, 8),
            "j3": (5, 6),
            "j4": (8, 10),
        },
        job_2_ewt_map={j: 1 for j in ["j0", "j1", "j2", "j3", "j4"]},
        job_2_twt_map={j: 2 for j in ["j0", "j1", "j2", "j3", "j4"]},
    )


def _seed(instance):
    rec = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))
    )
    return rec.result.schedule


def test_stop_predicate_breaks_after_first_step() -> None:
    instance = _make_instance()
    seed = _seed(instance)

    spec = AlgSpec(
        instance=instance,
        option=PwCpOption(cp_tl_seconds=0.5, unfixed_batch_count=2),
        ref_solution=seed,
        stop_predicate=lambda: True,
    )
    record = PwCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.STOP_REQUESTED
    assert record.result is not None
    assert record.result.schedule is not None


def test_wall_clock_deadline_in_past_short_circuits() -> None:
    instance = _make_instance()
    seed = _seed(instance)

    spec = AlgSpec(
        instance=instance,
        option=PwCpOption(
            cp_tl_seconds=0.5,
            unfixed_batch_count=2,
            wall_clock_deadline_sec=time.monotonic() - 1.0,
        ),
        ref_solution=seed,
    )
    record = PwCpDispatcher().run(spec)

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason == TerminationReason.STOP_REQUESTED
