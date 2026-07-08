from __future__ import annotations

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.orchestration.controller import (
    FFcDDWSubroutineController,
    MixedDispatcher,
    dispatch_seq_job_sequence,
)
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


def test_initialize_by_eddub_twt_registers_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_eddub_twt()

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0
    assert len(controller.solution_manager.history) == 1

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value
    assert incumbent.obj_bound is None

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_eddub_twt_feasible_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_eddub_twt()
    assert report.obj_value is not None

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    sched = incumbent.schedule

    # Every job must have a valid end time at every stage.
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            e = sched.get_job_end_time(stage_id, job_id)
            assert e > 0


# -------------------------------------------------------------------
# initialize_by_reversed_dispatch
# -------------------------------------------------------------------


def test_initialize_by_reversed_dispatch_lsl_registers_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_reversed_dispatch(sequence="lsl")

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0
    assert len(controller.solution_manager.history) == 1

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_reversed_dispatch_osl_registers_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_reversed_dispatch(sequence="osl")

    assert report.obj_value is not None
    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_reversed_dispatch_unknown_key_raises() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    with pytest.raises(ValueError, match="Unknown DispatchSeqKey"):
        controller.initialize_by_reversed_dispatch(sequence="not_a_key")


# -------------------------------------------------------------------
# initialize_by_simple_dispatch (forward job-centric MixedDispatcher decode)
# -------------------------------------------------------------------


def test_initialize_by_simple_dispatch_registers_full_schedule() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_simple_dispatch(sequence="edd")

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0
    assert len(controller.solution_manager.history) == 1

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value

    # job-centric decode places every job at every stage
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_simple_dispatch_lsl_osl_feasible() -> None:
    for sequence in ("lsl", "osl"):
        instance = _make_instance()
        controller = _make_controller(instance)

        report = controller.initialize_by_simple_dispatch(sequence=sequence)

        incumbent = controller.solution_manager.get_incumbent()
        assert incumbent is not None
        assert incumbent.schedule is not None
        sum_e, sum_t = compute_weighted_earliness_tardiness(
            incumbent.schedule, instance
        )
        assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_simple_dispatch_unknown_key_raises() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    with pytest.raises(ValueError, match="Unknown DispatchSeqKey"):
        controller.initialize_by_simple_dispatch(sequence="not_a_key")


def _make_iit_instance(name: str = "iit_instance") -> FFcDDWParameters:
    """Instance designed so that forward decode produces earliness on the last stage.

    Single-machine per stage ensures EDD order is preserved. Processing times:
    j0: i0=2, i1=1; j1: i0=1, i1=2; j2: i0=1, i1=3
    Due windows: j0=[5,10], j1=[4,10], j2=[3,10]
    With EDD order (j0, j1, j2) the last-stage completion times are 3, 5, 8,
    giving earliness of 2+1+0=3 and zero tardiness — IIT shifts all right.
    """
    job_id_list = ["j0", "j1", "j2"]
    stage_id_list = ["i0", "i1"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 1], [1, 2], [1, 3]]),
        ),
        job_2_due_window_map={"j0": (5, 10), "j1": (4, 10), "j2": (3, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_initialize_by_simple_dispatch_iit_improves_or_equals_raw() -> None:
    """Verify that make_semi_active + insert_idle_time never worsens E+T.

    Raw left-pack decode produces a schedule with no idle time between stages.
    After applying make_semi_active + insert_idle_time the weighted E+T must be
    ≤ the raw value, and at least one operation start time must be right-shifted.
    """
    instance = _make_iit_instance()
    controller = _make_controller(instance)

    # Build raw left-pack schedule (no IIT)
    job_sequence = dispatch_seq_job_sequence(instance, "edd")
    dispatcher = MixedDispatcher(instance, logger=controller.logger)
    raw_schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
    raw_e, raw_t = compute_weighted_earliness_tardiness(raw_schedule, instance)
    raw_obj = float(raw_e + raw_t)
    raw_start_map = raw_schedule.get_jik_2_start_time_map()

    # Apply the same transformations that initialize_by_simple_dispatch now uses
    raw_schedule.make_semi_active(instance.stage_2_job_2_p_map)
    raw_schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    iit_e, iit_t = compute_weighted_earliness_tardiness(raw_schedule, instance)
    iit_obj = float(iit_e + iit_t)
    iit_start_map = raw_schedule.get_jik_2_start_time_map()

    # IIT must not worsen the objective
    assert iit_obj <= raw_obj, (
        f"IIT-applied obj ({iit_obj}) > raw left-pack obj ({raw_obj})"
    )

    # At least one start time must be right-shifted (idle actually inserted)
    any_right_shift = any(iit_start_map[k] > raw_start_map[k] for k in raw_start_map)
    assert any_right_shift, "No idle time was inserted; all start times unchanged"


def test_initialize_by_dispatch_v3_registers_single_incumbent() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_dispatch_v3()

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0
    assert len(controller.solution_manager.history) == 1

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_dispatch_v3_picks_min_of_six() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_dispatch_v3()

    # Call helpers directly to compute all 6 obj values
    helpers = []
    for p in ("edd", "wspt_twt", "wxd2"):
        seq = dispatch_seq_job_sequence(instance, p)
        sd_sch, sd_obj = controller._dispatch_by_simple_sequence_with_iit(seq)
        helpers.append((sd_obj, f"sd:{p}"))
        rd_sch, rd_obj = controller._dispatch_by_reversed_sequence_with_iit(seq)
        helpers.append((rd_obj, f"rd:{p}"))

    min_obj = min(obj for obj, _ in helpers)
    assert report.obj_value == min_obj


def test_initialize_by_dispatch_v3_is_deterministic() -> None:
    instance = _make_instance()
    controller1 = _make_controller(instance)
    controller2 = _make_controller(instance)

    report1 = controller1.initialize_by_dispatch_v3()
    report2 = controller2.initialize_by_dispatch_v3()

    assert report1.obj_value == report2.obj_value


def test_initialize_by_simple_dispatch_uses_helper() -> None:
    """Regression: ensure initialize_by_simple_dispatch produces same result
    as calling the extracted helper directly."""
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_simple_dispatch(sequence="edd")
    seq = dispatch_seq_job_sequence(instance, "edd")
    helper_sch, helper_obj = controller._dispatch_by_simple_sequence_with_iit(seq)

    assert report.obj_value == helper_obj
    assert report.obj_bound is None


# ---------------------------------------------------------------------------
# initialize_by_dispatch_v4
# ---------------------------------------------------------------------------


def test_initialize_by_dispatch_v4_registers_single_incumbent() -> None:
    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_dispatch_v4()

    assert report.obj_value is not None
    assert report.obj_bound is None
    assert report.elapsed_time >= 0
    assert len(controller.solution_manager.history) == 1

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert incumbent.obj_value == report.obj_value

    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            incumbent.schedule.get_job_end_time(stage_id, job_id)

    sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent.schedule, instance)
    assert float(sum_e + sum_t) == report.obj_value


def test_initialize_by_dispatch_v4_picks_min_of_N() -> None:
    from ffc_ddw_sum_et.parameters.sorter import V4_PRIORITY_SET

    instance = _make_instance()
    controller = _make_controller(instance)

    report = controller.initialize_by_dispatch_v4()

    helpers = []
    for p in V4_PRIORITY_SET:
        seq = dispatch_seq_job_sequence(instance, p)
        sd_sch, sd_obj = controller._dispatch_by_simple_sequence_with_iit(seq)
        helpers.append((sd_obj, f"sd:{p}"))
        rd_sch, rd_obj = controller._dispatch_by_reversed_sequence_with_iit(seq)
        helpers.append((rd_obj, f"rd:{p}"))

    min_obj = min(obj for obj, _ in helpers)
    assert report.obj_value == min_obj


def test_initialize_by_dispatch_v4_is_deterministic() -> None:
    instance = _make_instance()
    controller1 = _make_controller(instance)
    controller2 = _make_controller(instance)

    report1 = controller1.initialize_by_dispatch_v4()
    report2 = controller2.initialize_by_dispatch_v4()

    assert report1.obj_value == report2.obj_value
