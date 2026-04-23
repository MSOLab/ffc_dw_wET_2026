from __future__ import annotations

import pandas as pd
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "c_instance") -> FFcDDWParameters:
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
    )


def _make_controller(instance: FFcDDWParameters) -> FFcDDWSubroutineController:
    return FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60}),
    )


def test_run_fam_default_sequence() -> None:
    controller = _make_controller(_make_instance())

    report = controller.run_fam()

    assert report.obj_value is not None
    assert len(controller.solution_manager.history) == 1
    assert controller.solution_manager.get_incumbent() is not None


def test_run_fam_with_sequence() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report_default = controller.run_fam()
    report_reversed = controller.run_fam(job_sequence=("j2", "j1", "j0"))

    assert report_default.obj_value is not None
    assert report_reversed.obj_value is not None
    assert len(controller.solution_manager.history) == 2


def test_work_status_feasible() -> None:
    controller = _make_controller(_make_instance())
    controller.run_fam()

    assert controller.work_status is WorkStatus.FEASIBLE


def test_best_obj_value_after_run() -> None:
    controller = _make_controller(_make_instance())

    report = controller.run_fam()

    assert controller.best_obj_value == report.obj_value


def test_numpy_float_conversion() -> None:
    controller = _make_controller(_make_instance())
    report = controller.run_fam()

    assert type(report.obj_value) is float
    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert type(incumbent.obj_value) is float


def test_run_mcf_lb_registers_dispatch_incumbent() -> None:
    """run_mcf_lb now seeds a feasible incumbent via MixedDispatcher and
    reports its ET as obj_value while keeping the MCF cost as obj_bound.
    """
    controller = _make_controller(_make_instance())

    report = controller.run_mcf_lb_4()

    assert report.obj_value is not None
    assert report.obj_bound is not None
    assert report.obj_bound >= 0
    assert report.elapsed_time >= 0
    # Feasible obj must dominate the LB.
    assert report.obj_value >= report.obj_bound
    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value
    assert incumbent.obj_bound == report.obj_bound


def test_run_mcf_lb_not_greater_than_fam() -> None:
    """LB from MCF should be ≤ feasible FAM objective for the same instance."""
    controller = _make_controller(_make_instance())

    lb_report = controller.run_mcf_lb_4()
    fam_report = controller.run_fam()

    assert lb_report.obj_bound is not None
    assert fam_report.obj_value is not None
    assert lb_report.obj_bound <= fam_report.obj_value
