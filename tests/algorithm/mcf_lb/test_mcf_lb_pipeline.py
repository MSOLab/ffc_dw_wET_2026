"""Direct tests for ``calc_mcf_lb_and_derive_full_sch``.

Verify the algorithm-level composite that ties ``apply_lb_by_mcf``,
``heuristic_last_stage_only_from_mcf_lb``, and
``build_full_sch_from_last_stage_only_sch``: per-round phase snapshots,
``r2`` skip-reason gates (``no_adjust`` / ``s1_none`` / ``delta_le_0`` /
``stop_guard``), the signed makespan delta recorded *before* the skip
decision (Rep3 fix), and the round-2 increment math.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline import (
    CalcMcfLbAndDeriveFullSchResult,
    calc_mcf_lb_and_derive_full_sch,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_multi_stage_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="pipeline_multi",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="pipeline_multi_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_single_stage_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="pipeline_single",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="pipeline_single_p",
            df=pd.DataFrame([[3], [2]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
    )


def test_default_flow_skips_r2_with_no_adjust_reason() -> None:
    """``adjust_p=False, adjust_r=False`` — round 2 is skipped with
    reason ``no_adjust`` and the r1 build_full result is returned.
    """
    instance = _make_multi_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(instance)

    assert isinstance(result, CalcMcfLbAndDeriveFullSchResult)
    assert result.r1_apply is not None
    assert result.r1_heuristic is not None
    assert result.r1_build_full is not None
    assert result.r1_build_full.schedule is not None
    assert result.best_schedule is result.r1_build_full.schedule
    assert result.best_obj == result.r1_build_full.dispatched_obj
    assert result.final_obj_bound == result.r1_apply.mcf_lb
    # No-adjust skip leaves r2 sub-results empty and skip_reason set.
    assert result.r2_ran is False
    assert result.r2_skip_reason == "no_adjust"
    assert result.r2_apply is None
    assert result.r2_heuristic is None
    assert result.r2_build_full is None
    assert result.makespan_delta is None
    assert result.r2_p_increment is None
    assert result.r2_r_increment is None
    # Round 1 emits 8 numbered phase snapshots; round 2 emits none.
    r1_labels = [label for label, _ in result.r1_phase_schedules]
    assert r1_labels == [
        "1_mcf_preemptive",
        "2_lastS_only_from_mcf_lb_before_sa_iti",
        "3_lastS_only_from_mcf_lb_after_sa_iti",
        "4_lastS_only_after_rs",
        "5_lastS_only_flipped",
        "6_fullS_before_unflip",
        "7_fullS_after_unflip",
        "8_fullS_after_sa_iti",
    ]
    assert result.r2_phase_schedules == []


def test_single_stage_short_circuits_through_r1_only() -> None:
    """Single-stage instances skip the reverse-dispatch path inside
    ``build_full_sch_from_last_stage_only_sch``; only ``mcf_preemptive``,
    the two heuristic snapshots, and ``fullS_after_sa_iti`` are emitted
    in round 1.
    """
    instance = _make_single_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(instance)

    assert result.r1_build_full is not None
    assert result.r1_build_full.schedule is not None
    r1_labels = [label for label, _ in result.r1_phase_schedules]
    assert r1_labels == [
        "1_mcf_preemptive",
        "2_lastS_only_from_mcf_lb_before_sa_iti",
        "3_lastS_only_from_mcf_lb_after_sa_iti",
        "8_fullS_after_sa_iti",
    ]


def test_adjust_runs_r2_or_records_signed_delta() -> None:
    """``adjust_p=True, adjust_r=True``: either round 2 actually runs
    (``makespan_delta > 0``) and the recorded increments match the
    documented arithmetic, or round 2 is skipped via
    ``delta_le_0`` with the signed delta still recorded on the result
    (the Rep3 fix). Either branch is acceptable for the test fixture;
    the assertion shape captures both invariants.
    """
    instance = _make_multi_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(instance, adjust_p=True, adjust_r=True)

    assert result.r1_build_full is not None
    assert result.r1_build_full.schedule is not None
    assert result.makespan_delta is not None
    if result.r2_ran:
        n = instance.job_count
        m_last = instance.last_stage_mc_count
        assert result.r2_skip_reason is None
        assert result.r2_apply is not None
        assert result.r2_heuristic is not None
        assert result.r2_build_full is not None
        assert result.r2_p_increment == math.ceil(result.makespan_delta * m_last / n)
        assert result.r2_r_increment == math.ceil(result.makespan_delta / 2)
        # final_obj_bound is always r1's MCF LB (the global LB on the
        # original instance) — r2's bound is on the augmented problem.
        assert result.final_obj_bound == result.r1_apply.mcf_lb
        # best_schedule is from r1 or r2 (whichever has lower dispatched obj).
        assert result.best_schedule in (
            result.r1_build_full.schedule,
            result.r2_build_full.schedule,
        )
    else:
        assert result.r2_skip_reason == "delta_le_0"
        assert result.makespan_delta <= 0
        assert result.r2_apply is None
        assert result.r2_heuristic is None
        assert result.r2_build_full is None
        assert result.r2_p_increment is None
        assert result.r2_r_increment is None
        # best_schedule falls through to r1 in the skip case.
        assert result.best_schedule is result.r1_build_full.schedule


def test_adjust_p_only_zeroes_r_increment_when_r2_runs() -> None:
    """When only ``adjust_p`` is on and round 2 runs, the recorded
    ``r2_r_increment`` is 0 (not ``None``).
    """
    instance = _make_multi_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(instance, adjust_p=True)

    if result.r2_ran:
        assert result.r2_p_increment is not None
        assert result.r2_p_increment > 0
        assert result.r2_r_increment == 0
    else:
        # The fixture didn't trigger r2; the contract is documented in
        # the other test (``test_adjust_runs_r2_or_records_signed_delta``).
        assert result.r2_skip_reason == "delta_le_0"


def test_proceed_r2_when_nonpositive_cmax_forces_r2_with_clamped_delta() -> None:
    """``proceed_r2_when_nonpositive_cmax=True`` bypasses the
    ``delta_le_0`` skip: round 2 runs even when the signed delta is
    ``<= 0``, with the delta clamped to ``>=1`` for increment math.
    The raw signed delta is still preserved on
    ``CalcMcfLbAndDeriveFullSchResult.makespan_delta``.
    """
    instance = _make_multi_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(
        instance,
        adjust_p=True,
        adjust_r=True,
        proceed_r2_when_nonpositive_cmax=True,
    )

    assert result.r1_build_full is not None
    assert result.r1_build_full.schedule is not None
    assert result.makespan_delta is not None

    # Verify the test fixture actually produces a non-positive delta
    assert result.makespan_delta <= 0, (
        f"Expected non-positive delta, got {result.makespan_delta}. "
        f"r1_full_sch_makespan={result.r1_build_full.schedule.makespan}, "
        f"r1_ls_only_pmtn_makespan={result.r1_apply.mcf_preemptive_schedule.makespan}"
    )

    # r2 always runs under the flag (modulo the no_adjust gate, which
    # this test bypasses by passing both adjust_p and adjust_r).
    assert result.r2_ran is True
    assert result.r2_skip_reason is None
    assert result.r2_apply is not None
    assert result.r2_heuristic is not None
    assert result.r2_build_full is not None

    # Increments computed from max(makespan_delta, 1). Clamped iff the
    # raw delta is <= 0; otherwise math matches the un-clamped formula.
    n = instance.job_count
    m_last = instance.last_stage_mc_count
    delta_for_inc = max(result.makespan_delta, 1)

    # Explicitly verify clamping applied (delta=0 → clamped to 1)
    assert delta_for_inc == 1, (
        f"Expected clamped delta=1 for raw delta {result.makespan_delta}, got {delta_for_inc}"
    )

    assert result.r2_p_increment == math.ceil(delta_for_inc * m_last / n)
    assert result.r2_r_increment == math.ceil(delta_for_inc / 2)
    assert result.r2_p_increment >= 1
    assert result.r2_r_increment >= 1

    # final_obj_bound is still r1's MCF LB (the global LB on the original
    # instance) — r2's bound is on the augmented problem.
    assert result.final_obj_bound == result.r1_apply.mcf_lb
    assert result.best_schedule in (
        result.r1_build_full.schedule,
        result.r2_build_full.schedule,
    )


def test_r_adjust_coeff_scales_r_increment() -> None:
    """``r_adjust_coeff`` scales the ``adjust_r`` formula:
    ``r_increment = ceil(delta_for_inc * r_adjust_coeff)``. Default
    ``0.5`` reproduces the historical ``ceil(delta / 2)``; passing
    ``1.0`` doubles the increment (when delta is even).
    """
    instance = _make_multi_stage_instance()

    baseline = calc_mcf_lb_and_derive_full_sch(
        instance,
        adjust_r=True,
        proceed_r2_when_nonpositive_cmax=True,
    )
    doubled = calc_mcf_lb_and_derive_full_sch(
        instance,
        adjust_r=True,
        r_adjust_coeff=1.0,
        proceed_r2_when_nonpositive_cmax=True,
    )

    assert baseline.r2_ran is True
    assert doubled.r2_ran is True
    assert baseline.r2_r_increment is not None
    assert doubled.r2_r_increment is not None

    delta_for_inc = max(baseline.makespan_delta, 1)
    assert baseline.r2_r_increment == math.ceil(delta_for_inc * 0.5)
    assert doubled.r2_r_increment == math.ceil(delta_for_inc * 1.0)


def test_makespan_delta_ref_last_stage_only_uses_heuristic_makespan() -> None:
    """``makespan_delta_ref="lastStageOnlyMakespan"`` computes the delta
    against ``r1.heuristic.schedule.makespan`` (non-preemptive last-stage
    schedule) instead of ``r1.apply.mcf_preemptive_schedule.makespan``.
    The recorded delta equals ``full_sch_makespan - chosen_ref`` for
    each mode (the two refs may or may not coincide on a given fixture).
    """
    instance = _make_multi_stage_instance()

    mcf_ref = calc_mcf_lb_and_derive_full_sch(
        instance,
        makespan_delta_ref="mcfLbMakespan",
        adjust_p=True,
        adjust_r=True,
        proceed_r2_when_nonpositive_cmax=True,
    )
    ls_ref = calc_mcf_lb_and_derive_full_sch(
        instance,
        makespan_delta_ref="lastStageOnlyMakespan",
        adjust_p=True,
        adjust_r=True,
        proceed_r2_when_nonpositive_cmax=True,
    )

    assert mcf_ref.r1_build_full is not None
    assert mcf_ref.r1_build_full.schedule is not None
    assert mcf_ref.r1_apply is not None
    assert mcf_ref.r1_heuristic is not None

    full_sch_makespan = int(mcf_ref.r1_build_full.schedule.makespan)
    pmtn_makespan = int(mcf_ref.r1_apply.mcf_preemptive_schedule.makespan)
    ls_only_makespan = int(mcf_ref.r1_heuristic.schedule.makespan)

    assert mcf_ref.makespan_delta == full_sch_makespan - pmtn_makespan
    assert ls_ref.makespan_delta == full_sch_makespan - ls_only_makespan


def test_invalid_makespan_delta_ref_raises() -> None:
    """Any value outside the two literals raises ``ValueError`` from
    the composite (defense-in-depth alongside the ``Literal`` annotation,
    since callers from YAML aren't type-checked).
    """
    instance = _make_multi_stage_instance()

    with pytest.raises(ValueError, match="makespan_delta_ref"):
        calc_mcf_lb_and_derive_full_sch(
            instance,
            makespan_delta_ref="foo",  # type: ignore[arg-type]
        )


def test_stop_predicate_at_entry_returns_empty_result() -> None:
    """Stop predicate firing before round 1 begins: no sub-results
    populated, ``best_schedule`` is ``None``, ``r1_build_full`` is
    ``None`` so the orchestration wrapper short-circuits to a
    stop-report.
    """
    instance = _make_multi_stage_instance()

    result = calc_mcf_lb_and_derive_full_sch(instance, stop_predicate=lambda: True)

    assert result.r1_apply is None
    assert result.r1_heuristic is None
    assert result.r1_build_full is None
    assert result.best_schedule is None
    assert result.best_obj is None
    assert result.r2_ran is False
    assert result.r2_skip_reason == "stop_guard"
    assert result.r1_phase_schedules == []
    assert result.r2_phase_schedules == []
