from __future__ import annotations

import pandas as pd
import pytest
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
    controller.heuristic_last_stage_only_sch_from_mcf_lb()
    assert controller.last_stage_only_sol is not None

    report = controller.build_full_sch_from_last_stage_only_sch()

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value
    assert incumbent.obj_bound is None

    # Every instance job must be scheduled at every stage.
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value

    # Phase schedule entries appended for post-run Gantt rendering. Names
    # are call_context-prefixed (e.g.
    # "1-calc_mcf_lb_..._9_fullS_after_sa_iti") so the runner-side
    # filenames sort by subroutine-flow step on disk; assert via suffix
    # match against the local phase label.
    phase_names = [name for name, _ in controller.mcf_lb_phase_schedules]
    assert any(name.endswith("_9_fullS_after_sa_iti") for name in phase_names)
    if instance.stage_count > 1:
        assert any(name.endswith("_4_lastS_only_before_rs") for name in phase_names)
        assert any(name.endswith("_5_lastS_only_after_rs") for name in phase_names)
        assert any(name.endswith("_6_lastS_only_flipped") for name in phase_names)
        assert any(name.endswith("_7_fullS_before_unflip") for name in phase_names)
        assert any(name.endswith("_8_fullS_after_unflip") for name in phase_names)


def test_heuristic_last_stage_only_sch_from_mcf_lb_sets_solution() -> None:
    """The heuristic step populates ``last_stage_only_sol`` (no incumbent
    yet — that is what ``build_full_sch_from_last_stage_only_sch`` does)
    and appends a labelled snapshot to ``mcf_lb_phase_schedules``.
    """
    instance = _make_instance()
    controller = _make_controller(instance)

    controller.apply_lb_by_mcf()
    report = controller.heuristic_last_stage_only_sch_from_mcf_lb()

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0

    assert controller.last_stage_only_sol is not None
    assert controller.last_stage_only_sol.schedule is not None
    assert controller.last_stage_only_sol.obj_value == report.obj_value
    assert controller.last_stage_only_sol_p_increment == 0

    phase_names = [name for name, _ in controller.mcf_lb_phase_schedules]
    assert any(
        name.endswith("_2_lastS_only_from_mcf_lb_before_sa_iti") for name in phase_names
    )
    assert any(
        name.endswith("_3_lastS_only_from_mcf_lb_after_sa_iti") for name in phase_names
    )


def test_heuristic_last_stage_only_sch_then_build_full() -> None:
    """Chaining the heuristic last-stage-only step with
    ``build_full_sch_from_last_stage_only_sch`` produces a full incumbent
    covering every (stage, job).
    """
    instance = _make_instance()
    controller = _make_controller(instance)

    controller.apply_lb_by_mcf()
    controller.heuristic_last_stage_only_sch_from_mcf_lb()
    report = controller.build_full_sch_from_last_stage_only_sch()

    assert report.obj_value is not None
    assert report.obj_bound is None

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_r_increment_negative_raises() -> None:
    """Both MCF-LB controller steps must reject ``r_increment < 0`` with
    ``ValueError`` (mirroring the ``p_increment`` / ``r_multiplier``
    guards) so a typo cannot silently shift release dates the wrong way.
    """
    instance = _make_instance()
    controller = _make_controller(instance)

    with pytest.raises(ValueError):
        controller.apply_lb_by_mcf(r_increment=-1)

    # Establish a valid mcf_preemptive_schedule + diagnostic so the
    # heuristic step's r_increment guard is what trips, not the
    # missing-prerequisite guard.
    controller.apply_lb_by_mcf()
    with pytest.raises(ValueError):
        controller.heuristic_last_stage_only_sch_from_mcf_lb(r_increment=-1)


def test_apply_lb_by_mcf_r_increment_voids_lb_and_does_not_decrease() -> None:
    """``r_increment > 0`` shifts every release date later, so the MCF
    objective on the augmented instance cannot be smaller than the
    baseline. It is also no longer a valid global LB on the original
    instance, so ``SubroutineReport.obj_bound`` must be ``None``.
    """
    instance = _make_instance()

    baseline_controller = _make_controller(instance)
    baseline_report = baseline_controller.apply_lb_by_mcf()
    assert baseline_report.obj_bound is not None
    baseline_mcf_lb = baseline_controller.mcf_lb_diagnostic.mcf_lb

    incremented_controller = _make_controller(instance)
    incremented_report = incremented_controller.apply_lb_by_mcf(r_increment=4)

    assert incremented_report.obj_bound is None
    assert incremented_controller.mcf_lb_diagnostic.mcf_lb >= baseline_mcf_lb
