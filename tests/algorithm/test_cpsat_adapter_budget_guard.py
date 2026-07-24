from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cpsat_adapter import CpsatAdapter, CpsatOption
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import (
    JobStageProcessingTimeManager,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="cpsat_budget_guard",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="p", df=pd.DataFrame([[2, 3], [2, 2], [1, 1]])
        ),
        job_2_due_window_map={"j0": (5, 8), "j1": (4, 7), "j2": (3, 6)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_budget_guard_with_incumbent_returns_fallback() -> None:
    instance = _make_instance()
    fam_record = FAMDispatcher().run(
        AlgSpec(instance=instance, option=FAMOption(job_sequence=("j2", "j1", "j0")))
    )
    assert fam_record.result is not None
    assert fam_record.result.schedule is not None
    incumbent = fam_record.result.schedule
    incumbent_obj = float(fam_record.result.obj_value)

    record = CpsatAdapter().run(
        AlgSpec(
            instance=instance,
            option=CpsatOption(timelimit_sec=0.0, solver_thread_cnt=1),
            ref_solution=incumbent,
        )
    )

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.termination_reason is not None
    assert record.termination_reason.value == "time_limit"
    assert record.error is None
    assert record.result is not None
    assert record.result.schedule is incumbent
    assert record.result.obj_value == incumbent_obj
    assert record.result.metrics is not None
    assert record.result.metrics["cpsat_status"] == "budget_exhausted_before_solve"
    assert record.result.metrics["fallback"] == "incumbent"


def test_budget_guard_without_incumbent_returns_no_solution() -> None:
    instance = _make_instance()

    record = CpsatAdapter().run(
        AlgSpec(
            instance=instance,
            option=CpsatOption(timelimit_sec=0.0, solver_thread_cnt=1),
            ref_solution=None,
        )
    )

    assert record.work_status == WorkStatus.ERROR
    assert record.result is not None
    assert record.result.schedule is None
    assert record.result.obj_value is None
