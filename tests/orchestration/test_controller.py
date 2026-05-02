from __future__ import annotations

import pandas as pd
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


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
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
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


def test_neh_cp_registers_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.neh_cp(cp_tl=1.0)

    assert report.obj_value is not None
    assert report.obj_bound is None
    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None

    # Every instance job must be scheduled at every stage.
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_build_full_sch_from_last_stage_only_sch() -> None:
    """``build_full_sch_from_last_stage_only_sch`` extends a partial
    last-stage-only schedule into a feasible full incumbent via reverse
    dispatch.
    """
    instance = _make_instance()
    controller = _make_controller(instance)

    controller.apply_lb_by_mcf()
    controller.single_pass_last_stage_only_sch_from_mcf_lb(total_tl=1.0)
    assert controller.last_stage_only_sol is not None

    report = controller.build_full_sch_from_last_stage_only_sch()

    assert report.obj_value is not None
    assert report.obj_bound == 0.0
    assert report.elapsed_time >= 0

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value
    assert incumbent.obj_bound == 0.0

    # Every instance job must be scheduled at every stage.
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value

    # Phase schedule entries appended for post-run Gantt rendering.
    phase_names = [name for name, _ in controller.mcf_lb_phase_schedules]
    assert "6_full_sch_from_ls_only_sch" in phase_names
    if instance.stage_count > 1:
        assert "3_ls_only_sch_delayed" in phase_names
        assert "4_ls_only_sch_flipped" in phase_names
        assert "5_full_sch_before_unflip" in phase_names


def test_run_mcf_lb_then_neh_cp_registers_incumbent() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.run_mcf_lb_then_neh_cp(cp_tl=1.0)

    assert report.obj_value is not None
    assert report.obj_bound is not None
    assert report.obj_bound >= 0
    assert report.obj_value >= report.obj_bound  # weighted ET dominates MCF LB

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value
    assert incumbent.obj_bound == report.obj_bound

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value

    # MCF preemptive schedule must be retained for the post-run Gantt pipeline.
    assert controller.mcf_preemptive_schedule is not None
    assert any(
        name == "1_mcf_preemptive_sch"
        for name, _ in controller.mcf_lb_phase_schedules
    )


def test_run_mcf_lb_then_neh_cp_uses_window_width_sequence() -> None:
    """Sanity check: the controller-derived sequence is a valid permutation
    of the instance jobs (the dispatcher's own validation would otherwise
    raise ValueError)."""
    from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf

    instance = _make_instance()
    controller = _make_controller(instance)
    mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    mcf.solve()

    sequence = controller._mcf_window_width_job_sequence(mcf, instance)

    assert sorted(sequence) == sorted(instance.job_id_list)
