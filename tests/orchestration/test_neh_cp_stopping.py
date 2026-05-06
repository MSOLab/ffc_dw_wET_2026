from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "neh_cp_orch_stop_test") -> FFcDDWParameters:
    """5-job, 2-stage, m=1 instance — yields multiple NEH-CP batches."""
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


def test_neh_cp_preflight_guard_returns_stop_report_when_timer_over() -> None:
    """When the controller's timelimit is already exceeded at NEH-CP entry,
    the pre-flight guard should return a stop-report without invoking the
    dispatcher (no incumbent registered)."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp", "cp_tl": 0.5, "added_batch_size": 1}],
        stopping_criteria=StoppingCriteria({"timelimit": 0.001}),
    )
    controller.timer.set_start_time(datetime.now() - timedelta(seconds=10))

    controller.run()

    assert controller.solution_manager.best_obj_value is None


def test_neh_cp_registers_recovered_schedule_when_stop_fires_mid_dispatch() -> None:
    """When stop_predicate fires inside the dispatcher's batch loop, the
    dispatcher recovers a full schedule (dispatch remaining jobs by
    earliest start) and the controller registers it as an incumbent."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp", "cp_tl": 0.5, "added_batch_size": 1}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    counter = {"calls": 0}

    def fake_stopping_condition(**kwargs: object) -> bool:
        counter["calls"] += 1
        # Routix calls before invoking flow step (call 1, want False);
        # neh_cp pre-flight (call 2, want False);
        # dispatcher's per-batch check (call 3+, fire to break mid-loop).
        return counter["calls"] >= 3

    controller.is_stopping_condition = fake_stopping_condition  # type: ignore[method-assign]

    controller.run()

    assert controller.solution_manager.best_obj_value is not None


def test_neh_cp_completes_with_generous_timelimit() -> None:
    """With an ample timelimit, NEH-CP runs to completion and registers
    an incumbent."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp", "cp_tl": 0.5, "added_batch_size": 1}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )

    controller.run()

    assert controller.solution_manager.best_obj_value is not None
    assert controller.solution_manager.best_obj_value >= 0
