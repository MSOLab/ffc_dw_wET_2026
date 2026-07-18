"""``time_factor`` (CSR coarse-grid) support for the MCF-LB pipeline.

W4 of ``plans/experiment/20260711/csr_solve_flow.md``. The MCF arc-cost construction
lives in ``algorithm/parallel_mc_pmtn.py`` (outside the ``mcf_lb`` package),
so exact ``time_factor`` threading of the *lower bound* is out of this
workstream's file ownership. The documented fallback (plan §3) is
implemented instead: for ``time_factor > 1`` the pipeline still derives
every schedule (with ``time_factor`` threaded into all schedule-evaluation
and due-window-comparison paths that ARE in scope), but reports its LB
(``final_obj_bound``) as ``None`` and flags the suppression on the record.

Test angles (per the W4 brief):
  (b) invariance — ``time_factor=1`` reproduces the default behaviour;
  (c) the reported derived-schedule obj equals
      ``compute_weighted_earliness_tardiness(derived, coarse, time_factor=F)``;
  (d) fallback — ``final_obj_bound is None`` for ``time_factor > 1`` while
      schedules are still produced.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.mcf_lb.full_sch_builder import (
    build_full_sch_from_last_stage_only_sch,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_sch_builder import (
    heuristic_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline import (
    calc_mcf_lb_and_derive_full_sch,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.option import MCFLBOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

FACTOR = 50


def _make_small_instance(name: str = "tf_small") -> FFcDDWParameters:
    """Same-scale (``time_factor=1``) fixture; mirrors the pipeline tests."""
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_base_instance(name: str = "tf_base") -> FFcDDWParameters:
    """Original-scale instance whose ``coarsen_processing_times(·, 50)`` keeps
    coarse processing times ``[[2,3],[2,2],[2,1]]`` and original-scale due
    windows. Real completion of a coarse ``C^c`` is ``50 * C^c``.
    """
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[100, 150], [100, 100], [100, 50]]),
        ),
        job_2_due_window_map={"j0": (200, 250), "j1": (150, 200), "j2": (0, 500)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_coarse_instance() -> FFcDDWParameters:
    return FFcDDWParameters.coarsen_processing_times(_make_base_instance(), FACTOR)


def _completion_map(schedule, instance: FFcDDWParameters) -> dict[str, int]:
    last_stage = instance.stage_id_list[-1]
    return {j: schedule.get_job_end_time(last_stage, j) for j in instance.job_id_list}


# --------------------------------------------------------------------------
# (b) invariance at time_factor == 1
# --------------------------------------------------------------------------


def test_pipeline_time_factor_1_matches_default() -> None:
    inst = _make_small_instance()

    base = calc_mcf_lb_and_derive_full_sch(inst)
    tf1 = calc_mcf_lb_and_derive_full_sch(inst, time_factor=1)

    assert tf1.best_obj == base.best_obj
    assert tf1.final_obj_bound == base.final_obj_bound
    assert tf1.final_obj_bound is not None  # not suppressed at tf=1
    assert tf1.lb_suppressed_by_time_factor is False
    assert tf1.time_factor == 1
    assert _completion_map(tf1.best_schedule, inst) == _completion_map(
        base.best_schedule, inst
    )
    assert [lbl for lbl, _ in tf1.r1_phase_schedules] == [
        lbl for lbl, _ in base.r1_phase_schedules
    ]


def test_heuristic_time_factor_1_matches_default() -> None:
    inst = _make_small_instance()
    pmtn = apply_lb_by_mcf(inst).mcf_preemptive_schedule

    base = heuristic_last_stage_only_from_mcf_lb(inst, pmtn)
    tf1 = heuristic_last_stage_only_from_mcf_lb(inst, pmtn, time_factor=1)

    assert tf1.obj_value == base.obj_value
    assert _completion_map(tf1.schedule, inst) == _completion_map(base.schedule, inst)


def test_build_full_time_factor_1_matches_default() -> None:
    inst = _make_small_instance()
    pmtn = apply_lb_by_mcf(inst).mcf_preemptive_schedule
    heur = heuristic_last_stage_only_from_mcf_lb(inst, pmtn)

    base = build_full_sch_from_last_stage_only_sch(inst, heur.schedule)
    tf1 = build_full_sch_from_last_stage_only_sch(inst, heur.schedule, time_factor=1)

    assert tf1.dispatched_obj == base.dispatched_obj
    assert base.schedule is not None and tf1.schedule is not None
    assert _completion_map(tf1.schedule, inst) == _completion_map(base.schedule, inst)


# --------------------------------------------------------------------------
# (d) fallback: LB suppressed for time_factor > 1, schedules still produced
# --------------------------------------------------------------------------


def test_pipeline_time_factor_gt1_suppresses_lb_but_derives_schedule() -> None:
    coarse = _make_coarse_instance()

    result = calc_mcf_lb_and_derive_full_sch(coarse, time_factor=FACTOR)

    assert result.best_schedule is not None
    assert result.best_obj is not None
    assert result.final_obj_bound is None
    assert result.lb_suppressed_by_time_factor is True
    assert result.time_factor == FACTOR
    # r1 MCF still solved (used for the heuristic seed), just not reported as LB.
    assert result.r1_apply is not None


def test_pipeline_time_factor_gt1_r2_still_suppresses_lb() -> None:
    coarse = _make_coarse_instance()

    result = calc_mcf_lb_and_derive_full_sch(
        coarse,
        time_factor=FACTOR,
        adjust_p=True,
        adjust_r=True,
        proceed_r2_when_nonpositive_cmax=True,
    )

    assert result.final_obj_bound is None
    assert result.lb_suppressed_by_time_factor is True
    if result.best_schedule is not None:
        sum_e, sum_t = compute_weighted_earliness_tardiness(
            result.best_schedule, coarse, time_factor=FACTOR
        )
        assert result.best_obj == float(sum_e + sum_t)


# --------------------------------------------------------------------------
# (c) reported derived-schedule obj == recomputed wET with time_factor=F
# --------------------------------------------------------------------------


def test_pipeline_best_obj_matches_recomputed_wet() -> None:
    coarse = _make_coarse_instance()

    result = calc_mcf_lb_and_derive_full_sch(coarse, time_factor=FACTOR)

    assert result.best_schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        result.best_schedule, coarse, time_factor=FACTOR
    )
    assert result.best_obj == float(sum_e + sum_t)


def test_heuristic_obj_matches_recomputed_wet() -> None:
    coarse = _make_coarse_instance()
    pmtn = apply_lb_by_mcf(coarse).mcf_preemptive_schedule

    heur = heuristic_last_stage_only_from_mcf_lb(coarse, pmtn, time_factor=FACTOR)

    sum_e, sum_t = compute_weighted_earliness_tardiness(
        heur.schedule, coarse, time_factor=FACTOR
    )
    assert heur.obj_value == float(sum_e + sum_t)


def test_build_full_obj_matches_recomputed_wet() -> None:
    coarse = _make_coarse_instance()
    pmtn = apply_lb_by_mcf(coarse).mcf_preemptive_schedule
    heur = heuristic_last_stage_only_from_mcf_lb(coarse, pmtn, time_factor=FACTOR)

    build = build_full_sch_from_last_stage_only_sch(
        coarse, heur.schedule, time_factor=FACTOR
    )

    assert build.schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        build.schedule, coarse, time_factor=FACTOR
    )
    assert build.dispatched_obj == float(sum_e + sum_t)


def test_time_factor_scaling_changes_reported_obj_scale() -> None:
    """Sanity: the coarse-grid penalty scales with ``time_factor``. A tardy
    coarse completion scored at ``time_factor=F`` must exceed the same
    schedule scored at ``time_factor=1`` (different objective functions), so
    the threading is not a silent no-op for F > 1.
    """
    coarse = _make_coarse_instance()
    pmtn = apply_lb_by_mcf(coarse).mcf_preemptive_schedule

    heur_f = heuristic_last_stage_only_from_mcf_lb(coarse, pmtn, time_factor=FACTOR)
    same_scale = compute_weighted_earliness_tardiness(heur_f.schedule, coarse)
    factor_scale = compute_weighted_earliness_tardiness(
        heur_f.schedule, coarse, time_factor=FACTOR
    )
    # heur_f.obj_value is the factor-scaled score (what the algorithm optimised).
    assert heur_f.obj_value == float(sum(factor_scale))
    assert sum(factor_scale) != sum(same_scale)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_mcflb_option_time_factor_validation() -> None:
    assert MCFLBOption(time_factor=1).time_factor == 1
    assert MCFLBOption(time_factor=FACTOR).time_factor == FACTOR
    with pytest.raises(ValueError, match="time_factor"):
        MCFLBOption(time_factor=0)
    with pytest.raises(ValueError, match="time_factor"):
        MCFLBOption(time_factor=-3)


@pytest.mark.parametrize(
    "fn_kwargs",
    [
        {},  # calc_mcf_lb_and_derive_full_sch
    ],
)
def test_pipeline_time_factor_validation(fn_kwargs: dict) -> None:
    inst = _make_small_instance()
    with pytest.raises(ValueError, match="time_factor"):
        calc_mcf_lb_and_derive_full_sch(inst, time_factor=0, **fn_kwargs)


def test_heuristic_and_build_time_factor_validation() -> None:
    inst = _make_small_instance()
    pmtn = apply_lb_by_mcf(inst).mcf_preemptive_schedule
    with pytest.raises(ValueError, match="time_factor"):
        heuristic_last_stage_only_from_mcf_lb(inst, pmtn, time_factor=0)
    heur = heuristic_last_stage_only_from_mcf_lb(inst, pmtn)
    with pytest.raises(ValueError, match="time_factor"):
        build_full_sch_from_last_stage_only_sch(inst, heur.schedule, time_factor=0)
