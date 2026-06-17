"""Tests for the all-stages MCF-LB projection (``lb_stage_scope="all_stages"``).

Covers two surfaces:

* The pure pipeline ``calc_mcf_lb_all_stages_and_derive_full_sch``: the
  combined bound dominates the last-stage-only bound, equals the max over
  per-stage valid LBs, the incumbent is no worse than the last-stage seed,
  per-stage records are well-formed (one per stage, last = ``full_ET``,
  intermediates = ``tardiness_only``), and ``best_schedule`` is a feasible
  full schedule consistent with ``best_sched_source``.
* The controller branch: the ``last_stage`` scope is byte-equal (LB,
  schedule makespan, obj) to the existing ``calc_mcf_lb_and_derive_full_sch``
  default, and the ``all_stages`` branch registers ``obj_bound == combined_lb``
  while populating ``c_diag.per_stage_records``.

The fixture is a 3-stage instance whose middle stage is the bottleneck, so
the intermediate tardiness-only LB strictly improves on the last-stage LB —
exercising the ``combined_lb > last-stage LB`` and ``argmax_stage_id`` paths.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import yaml
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline import (
    CalcMcfLbAllStagesResult,
    calc_mcf_lb_all_stages_and_derive_full_sch,
    calc_mcf_lb_and_derive_full_sch,
)
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import validate_schedule


def _make_three_stage_instance() -> FFcDDWParameters:
    """A 3-stage instance (so there is exactly one intermediate stage)
    whose middle stage ``i1`` is the bottleneck (single machine), making
    the intermediate tardiness-only LB the binding bound.
    """
    return FFcDDWParameters(
        name="all_stages_three_stage",
        job_id_list=["j0", "j1", "j2", "j3"],
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={
            "i0": ["i0_0", "i0_1"],
            "i1": ["i1_0"],
            "i2": ["i2_0", "i2_1"],
        },
        p_manager=JobStageProcessingTimeManager(
            name="all_stages_three_stage_p",
            df=pd.DataFrame([[2, 3, 2], [3, 2, 2], [2, 2, 3], [3, 3, 2]]),
        ),
        job_2_due_window_map={
            "j0": (5, 6),
            "j1": (4, 5),
            "j2": (6, 8),
            "j3": (3, 4),
        },
        job_2_ewt_map={"j0": 1, "j1": 2, "j2": 1, "j3": 1},
        job_2_twt_map={"j0": 2, "j1": 1, "j2": 1, "j3": 3},
    )


def _make_controller(instance: FFcDDWParameters) -> FFcDDWSubroutineController:
    return FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60}),
    )


def test_combined_lb_dominates_last_stage_and_equals_per_stage_max() -> None:
    """``combined_lb`` is >= the last-stage-only LB and exactly equals the
    max over the per-stage valid LBs. On the bottleneck-middle fixture the
    inequality is strict, with the argmax landing on the intermediate stage.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(instance)

    assert isinstance(result, CalcMcfLbAllStagesResult)

    last_stage_lb = result.last_stage_result.final_obj_bound
    assert last_stage_lb is not None
    assert result.combined_lb is not None
    # combined_lb is never worse than the last-stage-only bound.
    assert result.combined_lb >= last_stage_lb

    # combined_lb is exactly the max over the per-stage valid LBs.
    valid_lbs = [
        record.mcf_lb
        for record in result.stage_records
        if record.mcf_lb_valid and record.mcf_lb is not None
    ]
    assert valid_lbs  # every stage contributes a valid LB
    assert result.combined_lb == max(valid_lbs)

    # The argmax stage must be the one whose LB attains the combined bound.
    argmax_record = next(
        record
        for record in result.stage_records
        if record.stage_id == result.argmax_stage_id
    )
    assert argmax_record.mcf_lb == result.combined_lb

    # On this fixture the middle stage is the bottleneck, so the combined
    # bound strictly improves on the last-stage LB at an intermediate stage.
    assert result.combined_lb > last_stage_lb
    assert result.argmax_stage_id != instance.stage_id_list[-1]
    assert argmax_record.is_last_stage is False
    assert argmax_record.bound_kind == "tardiness_only"


def test_all_stages_incumbent_no_worse_than_last_stage_seed() -> None:
    """The all-stages incumbent (global min-wET over the last-stage pipeline
    result and every intermediate seed) is never worse than the
    last-stage-only seed it starts from.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(instance)

    last_stage_best_obj = result.last_stage_result.best_obj
    assert last_stage_best_obj is not None
    assert result.best_obj is not None
    assert result.best_obj <= last_stage_best_obj


def test_per_stage_records_well_formed() -> None:
    """One record per stage in ascending stage order; the last stage carries
    the full-ET bound, every intermediate the tardiness-only bound.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(instance)

    c = instance.stage_count
    assert len(result.stage_records) == c

    # Ascending stage order (stage 1 … c).
    assert [r.stage_id for r in result.stage_records] == instance.stage_id_list

    last_record = result.stage_records[-1]
    assert last_record.stage_id == instance.stage_id_list[-1]
    assert last_record.is_last_stage is True
    assert last_record.bound_kind == "full_ET"
    assert last_record.best_candidate == "last_stage_pipeline"

    for record in result.stage_records[:-1]:
        assert record.is_last_stage is False
        assert record.bound_kind == "tardiness_only"
        # Intermediate seeds win via one of the two stage-seed candidates.
        assert record.best_candidate in ("two_way", "seq_both_ways")
        # Round-1-only tardiness LB is a valid bound on OPT.
        assert record.mcf_lb_valid is True
        assert record.mcf_lb is not None
        assert record.init_sched_obj is not None
        assert record.delta == record.init_sched_obj - record.mcf_lb


def test_best_schedule_is_feasible_and_source_consistent() -> None:
    """``best_schedule`` is a feasible full schedule, and ``best_sched_source``
    points at the candidate (last-stage pipeline or an intermediate stage)
    whose objective equals ``best_obj``.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(instance)

    assert result.best_schedule is not None
    # Feasible full schedule: correct durations, precedence, no overlap, and
    # every (stage, job) covered.
    validate_schedule(result.best_schedule, instance.stage_2_job_2_p_map)
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            result.best_schedule.get_job_end_time(stage_id, job_id)

    # best_sched_source identifies which candidate produced best_schedule.
    if result.best_sched_source == "last_stage_pipeline":
        assert result.best_schedule is result.last_stage_result.best_schedule
        assert result.best_obj == result.last_stage_result.best_obj
    else:
        winning_record = next(
            record
            for record in result.stage_records
            if record.stage_id == result.best_sched_source
        )
        assert winning_record.is_last_stage is False
        assert result.best_obj == winning_record.init_sched_obj


def test_controller_last_stage_scope_matches_default_pipeline() -> None:
    """REGRESSION: the ``last_stage`` controller path is identical (LB,
    schedule makespan, obj) to the existing default
    ``calc_mcf_lb_and_derive_full_sch`` algorithm pipeline.
    """
    instance = _make_three_stage_instance()

    controller = _make_controller(instance)
    report = controller.calc_mcf_lb_and_derive_full_sch(lb_stage_scope="last_stage")
    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None

    algo_result = calc_mcf_lb_and_derive_full_sch(instance)
    assert algo_result.best_schedule is not None

    # Same LB, same obj, same schedule makespan as the default pipeline.
    assert report.obj_bound == algo_result.final_obj_bound
    assert report.obj_value == algo_result.best_obj
    assert incumbent.schedule.makespan == algo_result.best_schedule.makespan
    # Exactly one register per controller call.
    assert len(controller.solution_manager.history) == 1


def test_controller_all_stages_branch_registers_combined_lb() -> None:
    """The ``all_stages`` controller branch registers a schedule whose
    ``obj_bound`` is the combined LB, and populates the all-stages diagnostic
    fields (``per_stage_records`` with one record per stage).
    """
    instance = _make_three_stage_instance()

    controller = _make_controller(instance)
    report = controller.calc_mcf_lb_and_derive_full_sch(lb_stage_scope="all_stages")

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert len(controller.solution_manager.history) == 1

    c_diag = controller.calc_mcf_lb_and_derive_full_sch_diagnostic
    assert c_diag.lb_stage_scope_used == "all_stages"
    assert c_diag.combined_lb is not None
    assert len(c_diag.per_stage_records) == instance.stage_count

    # The registered report carries the combined LB as its bound, and the
    # combined LB dominates the last-stage-only LB.
    assert report.obj_bound == c_diag.combined_lb
    assert incumbent.obj_bound == c_diag.combined_lb

    last_stage_only = _make_controller(instance)
    last_stage_report = last_stage_only.calc_mcf_lb_and_derive_full_sch(
        lb_stage_scope="last_stage"
    )
    assert last_stage_report.obj_bound is not None
    assert c_diag.combined_lb >= last_stage_report.obj_bound
    # The all-stages incumbent objective is no worse than the last-stage one.
    assert report.obj_value is not None
    assert last_stage_report.obj_value is not None
    assert report.obj_value <= last_stage_report.obj_value


def test_full_et_approx_excludes_intermediate_from_combined_lb() -> None:
    """``intermediate_stage_cost="full_et_approx"``: the intermediate MCF is an
    approximate (non-LB) objective, so it is excluded from ``combined_lb`` —
    which equals the last-stage full-ET LB alone — and the intermediate records
    carry ``bound_kind="full_et_approx"`` with ``mcf_lb_valid=False``. The
    intermediate seeds still compete for the incumbent.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(
        instance, intermediate_stage_cost="full_et_approx"
    )

    last_stage_lb = result.last_stage_result.final_obj_bound
    assert last_stage_lb is not None
    # combined_lb is the last-stage full-ET bound alone (intermediates dropped).
    assert result.combined_lb == last_stage_lb
    assert result.argmax_stage_id == instance.stage_id_list[-1]

    # Last stage: still a valid full-ET LB.
    last_record = result.stage_records[-1]
    assert last_record.is_last_stage is True
    assert last_record.bound_kind == "full_ET"
    assert last_record.mcf_lb_valid is True

    # Intermediate stages: approximate, non-LB objective used only for seeding.
    for record in result.stage_records[:-1]:
        assert record.is_last_stage is False
        assert record.bound_kind == "full_et_approx"
        assert record.mcf_lb_valid is False
        assert record.mcf_lb is not None  # the approximate objective is recorded
        assert record.init_sched_obj is not None
        assert record.best_candidate in ("two_way", "seq_both_ways")

    # The incumbent (min-wET over last stage + intermediate seeds) is still no
    # worse than the last-stage-only seed.
    assert result.best_obj is not None
    assert result.last_stage_result.best_obj is not None
    assert result.best_obj <= result.last_stage_result.best_obj


def test_full_et_approx_matches_tardonly_last_stage_lb() -> None:
    """The last-stage LB is identical across both intermediate modes (the last
    stage is untouched): ``full_et_approx``'s ``combined_lb`` equals the
    ``tardiness_only`` run's last-stage bound.
    """
    instance = _make_three_stage_instance()

    tardonly = calc_mcf_lb_all_stages_and_derive_full_sch(
        instance, intermediate_stage_cost="tardiness_only"
    )
    etapprox = calc_mcf_lb_all_stages_and_derive_full_sch(
        instance, intermediate_stage_cost="full_et_approx"
    )

    assert tardonly.last_stage_result.final_obj_bound is not None
    assert (
        etapprox.combined_lb
        == etapprox.last_stage_result.final_obj_bound
        == tardonly.last_stage_result.final_obj_bound
    )


def test_controller_full_et_approx_registers_last_stage_lb() -> None:
    """The controller ``all_stages`` + ``full_et_approx`` branch registers a
    schedule whose ``obj_bound`` is the last-stage full-ET LB (intermediate
    stages excluded), and records the mode on the diagnostic.
    """
    instance = _make_three_stage_instance()

    controller = _make_controller(instance)
    report = controller.calc_mcf_lb_and_derive_full_sch(
        lb_stage_scope="all_stages", intermediate_stage_cost="full_et_approx"
    )

    incumbent = controller.solution_manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.schedule is not None
    assert len(controller.solution_manager.history) == 1

    c_diag = controller.calc_mcf_lb_and_derive_full_sch_diagnostic
    assert c_diag.lb_stage_scope_used == "all_stages"
    assert c_diag.intermediate_stage_cost_used == "full_et_approx"
    assert c_diag.combined_lb is not None

    # Registered bound is the combined LB, which equals the last-stage LB alone.
    assert report.obj_bound == c_diag.combined_lb
    assert incumbent.obj_bound == c_diag.combined_lb

    last_stage_only = _make_controller(instance)
    last_stage_report = last_stage_only.calc_mcf_lb_and_derive_full_sch(
        lb_stage_scope="last_stage"
    )
    assert last_stage_report.obj_bound is not None
    assert c_diag.combined_lb == last_stage_report.obj_bound

    # Every intermediate record is the approximate, non-valid kind.
    for record in c_diag.per_stage_records[:-1]:
        assert record.bound_kind == "full_et_approx"
        assert record.mcf_lb_valid is False


def test_controller_default_intermediate_cost_is_tardiness_only() -> None:
    """REGRESSION: the default ``all_stages`` path records
    ``intermediate_stage_cost_used == "tardiness_only"`` and keeps the
    intermediate bounds valid (byte-identical to the historical behaviour).
    """
    instance = _make_three_stage_instance()

    controller = _make_controller(instance)
    controller.calc_mcf_lb_and_derive_full_sch(lb_stage_scope="all_stages")

    c_diag = controller.calc_mcf_lb_and_derive_full_sch_diagnostic
    assert c_diag.intermediate_stage_cost_used == "tardiness_only"
    for record in c_diag.per_stage_records[:-1]:
        assert record.bound_kind == "tardiness_only"
        assert record.mcf_lb_valid is True


def test_per_stage_records_are_yaml_serializable() -> None:
    """REGRESSION: per-stage diagnostic scalars must be plain Python types.

    The benchmark loader yields numpy-typed processing times, so the derived
    ``load_index`` (``Σ p_q / |M_q|``) and ``max_release`` are numpy scalars
    unless coerced. numpy scalars have no YAML representer, which crashes the
    runner's ``instance_result`` manifest writer (``dump_yaml`` →
    ``RepresenterError``). This fixture's pandas-backed processing times are
    numpy ``int64`` too, so it reproduces the boundary exactly: assert the
    coercion holds and the records round-trip through ``yaml.safe_dump`` the
    way the manifest writer does.
    """
    instance = _make_three_stage_instance()

    result = calc_mcf_lb_all_stages_and_derive_full_sch(instance)

    for record in result.stage_records:
        if record.load_index is not None:
            assert type(record.load_index) is float
        if record.max_release is not None:
            assert type(record.max_release) is int

    # Exactly the operation the manifest writer performs on the diagnostic.
    yaml.safe_dump([dataclasses.asdict(record) for record in result.stage_records])
