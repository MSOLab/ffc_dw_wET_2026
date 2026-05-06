from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.mcf_lb.diagnostic import MCFLBDiagnostic
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.solution_manager import FFcDDWSolution
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def _make_instance(name: str = "stop_test") -> FFcDDWParameters:
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[1]]),
        ),
        job_2_due_window_map={"j0": (0, 1)},
        job_2_ewt_map={"j0": 1},
        job_2_twt_map={"j0": 1},
    )


def _make_controller(timelimit: float = 60.0) -> FFcDDWSubroutineController:
    return FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": timelimit}),
    )


def _register_incumbent(controller: FFcDDWSubroutineController, obj: float) -> None:
    schedule = FFcSchedule(
        jobs=["j0"], stages=["i0"], machines_per_stage={"i0": ["i0_0"]}
    )
    controller.solution_manager.register(
        SubroutineReport(elapsed_time=0.0, obj_value=obj, obj_bound=None),
        FFcDDWSolution(schedule=schedule, obj_value=obj),
    )


def test_timelimit_triggers_is_stopping_condition() -> None:
    controller = _make_controller(timelimit=0.001)
    controller.timer.set_start_time(datetime.now() - timedelta(seconds=10))

    assert controller.is_stopping_condition() is True


def test_no_incumbent_means_not_proven() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 5.0
    controller.mcf_lb_diagnostic = diag

    assert controller._optimality_proven() is False
    assert controller.is_stopping_condition() is False


def test_optimality_proven_when_ceil_lb_equals_ub() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 4.3
    controller.mcf_lb_diagnostic = diag
    _register_incumbent(controller, obj=5.0)

    assert controller.get_current_valid_lb() == 4.3
    assert controller._optimality_proven() is True
    assert controller.is_stopping_condition() is True


def test_invalid_lb_substitutes_zero() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 7.0
    diag.adjust_p_increment_added = 2  # invalidates LB
    controller.mcf_lb_diagnostic = diag
    _register_incumbent(controller, obj=7.0)

    assert controller.get_current_valid_lb() == 0.0
    assert controller._optimality_proven() is False


def test_invalid_lb_substitutes_zero_optimal_at_zero() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 7.0
    diag.adjust_r_increment_added = 1
    controller.mcf_lb_diagnostic = diag
    _register_incumbent(controller, obj=0.0)

    assert controller.get_current_valid_lb() == 0.0
    assert controller._optimality_proven() is True


def test_lb_greater_than_ub_raises_value_error() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 10.7
    controller.mcf_lb_diagnostic = diag
    _register_incumbent(controller, obj=5.0)

    with pytest.raises(ValueError, match="exceeds incumbent UB"):
        controller._optimality_proven()


def test_make_stop_report_uses_valid_lb_only() -> None:
    controller = _make_controller()
    diag = MCFLBDiagnostic()
    diag.mcf_lb = 6.0
    controller.mcf_lb_diagnostic = diag

    report = controller._make_stop_report()
    assert report.elapsed_time == 0.0
    assert report.obj_value is None
    assert report.obj_bound == 6.0

    diag.adjust_p_increment_added = 1
    report = controller._make_stop_report()
    assert report.obj_bound is None
