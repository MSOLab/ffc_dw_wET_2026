"""Direct tests for the last-stage dual-seed chooser (plan §2.3 / §2.4).

Validate ``_build_best_full_from_last_stage_seeds``
(:mod:`ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline`):

* The chosen full schedule is feasible and its wET is ``<=`` the minimum of
  the two single-method baselines (midpoint-only, simple-only) built directly
  from the same MCF preemptive window.
* ``seed_method`` names the method whose direct baseline is the lower one.
* **Regression**: when midpoint wins, the r1 ``build_full.schedule``
  makespan/obj equals the current single-seed (midpoint-only) pipeline path,
  so the comparison never regresses today's output.
* On an instance constructed so ``simple`` strictly beats ``midpoint``, the
  chooser reports ``seed_method == "simple"``.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.mcf_lb.full_sch_builder import (
    build_full_sch_from_last_stage_only_sch,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_sch_builder import (
    heuristic_last_stage_only_from_mcf_lb,
    simple_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline import (
    LastStageSeedChoice,
    _build_best_full_from_last_stage_seeds,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import validate_schedule

# Default chooser kwargs that reproduce the r1 (no-augmentation) call path
# (``mcf_lb_pipeline.calc_mcf_lb_r1_and_derive_full_sch``): the midpoint
# branch uses the same ``job_priority`` / ``placement_priority`` defaults the
# pipeline passes, and no p/r augmentation, so ``rebuild=False`` everywhere.
_R1_CHOOSER_KWARGS = dict(
    job_placement_priority="end_time",
    last_stage_only_placement_criteria="dist",
    p_increment=0,
    r_multiplier=1.0,
    r_increment=0,
    rebuild_last_stage_with_original_p=False,
    seed_compare=True,
    logger=None,
)


def _make_multi_stage_instance() -> FFcDDWParameters:
    """Same 3-job / 2-stage fixture as the existing pipeline tests."""
    return FFcDDWParameters(
        name="dual_seed_multi",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="dual_seed_multi_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _direct_baselines(
    instance: FFcDDWParameters,
) -> tuple[float | None, float | None]:
    """Build the two single-method full-schedule baselines directly.

    Returns ``(midpoint_obj, simple_obj)`` — each the ``dispatched_obj`` of the
    full schedule reverse-dispatched from that method's last-stage-only seed,
    using the same r1 (no-augmentation, ``rebuild=False``) settings the chooser
    uses. ``None`` if a build produced no schedule.
    """
    apply = apply_lb_by_mcf(instance)

    midpoint_heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        job_priority="end_time",
        placement_priority="dist",
    )
    midpoint_full = build_full_sch_from_last_stage_only_sch(
        instance,
        midpoint_heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
    )

    simple_heuristic = simple_last_stage_only_from_mcf_lb(
        instance, apply.mcf_preemptive_schedule
    )
    simple_full = build_full_sch_from_last_stage_only_sch(
        instance,
        simple_heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
    )

    return midpoint_full.dispatched_obj, simple_full.dispatched_obj


def _choice(instance: FFcDDWParameters) -> LastStageSeedChoice:
    apply = apply_lb_by_mcf(instance)
    return _build_best_full_from_last_stage_seeds(instance, apply, **_R1_CHOOSER_KWARGS)


def test_chooser_returns_feasible_min_of_both_baselines() -> None:
    """The chosen full schedule is feasible and its wET equals the lower of
    the two directly-built baselines; ``seed_method`` names that lower one.
    Ties favour midpoint (the chooser only adds a candidate).
    """
    instance = _make_multi_stage_instance()
    midpoint_obj, simple_obj = _direct_baselines(instance)

    choice = _choice(instance)

    # Winner schedule is a feasible full schedule on the original instance.
    assert choice.build_full.schedule is not None
    validate_schedule(choice.build_full.schedule, instance.stage_2_job_2_p_map)

    # Both baselines built here; the lower bound is well-defined.
    assert midpoint_obj is not None
    assert simple_obj is not None
    lower = min(midpoint_obj, simple_obj)

    # The chosen wET is never worse than the better baseline.
    assert choice.build_full.dispatched_obj is not None
    assert choice.build_full.dispatched_obj <= lower
    # In fact it equals the lower (the chooser builds the same two seeds).
    assert choice.build_full.dispatched_obj == lower

    # seed_method names the lower baseline; ties favour midpoint.
    if simple_obj < midpoint_obj:
        assert choice.seed_method == "simple"
        assert choice.alt_dispatched_obj == midpoint_obj
    else:
        assert choice.seed_method == "midpoint"
        assert choice.alt_dispatched_obj == simple_obj


def test_midpoint_win_regression_matches_single_seed_pipeline() -> None:
    """Regression: when midpoint wins (the default fixture), the chosen
    ``build_full.schedule`` makespan/obj equals the current single-seed
    (midpoint-only) pipeline result built directly. The dual-seed comparison
    must never produce a schedule worse than today's midpoint-only output.
    """
    instance = _make_multi_stage_instance()
    midpoint_obj, simple_obj = _direct_baselines(instance)

    # This fixture is a midpoint-win (or tie) case; guard the assumption so a
    # future fixture change surfaces here rather than silently passing.
    assert midpoint_obj is not None
    assert simple_obj is not None
    assert midpoint_obj <= simple_obj, (
        "regression fixture expects midpoint to win/tie; "
        f"got midpoint={midpoint_obj}, simple={simple_obj}"
    )

    choice = _choice(instance)
    assert choice.seed_method == "midpoint"

    # Rebuild the single-seed (midpoint-only) pipeline schedule directly.
    apply = apply_lb_by_mcf(instance)
    midpoint_heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        job_priority="end_time",
        placement_priority="dist",
    )
    midpoint_full = build_full_sch_from_last_stage_only_sch(
        instance,
        midpoint_heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
    )

    assert choice.build_full.schedule is not None
    assert midpoint_full.schedule is not None
    assert choice.build_full.dispatched_obj == midpoint_full.dispatched_obj
    assert choice.build_full.full_sch_makespan == midpoint_full.full_sch_makespan


def _make_simple_wins_instance() -> FFcDDWParameters:
    """Instance constructed so the ``simple`` last-stage seed strictly beats
    the ``midpoint`` one (midpoint full wET = 39, simple full wET = 30).

    Construction rationale: the simple seed sorts jobs by their MCF-window
    ``t_max`` and greedily left-packs them on the single last-stage machine
    (EDD-like on the LP-derived urgency), whereas the midpoint seed places each
    job at its window midpoint, then refines with semi-active + idle-insertion.
    With a single last-stage machine, overlapping due windows, and asymmetric
    earliness/tardiness weights, the midpoint placement spreads jobs toward
    their window centres and the post-refinement left-shift settles into a
    costlier ordering, whereas the simple left-pack lands the urgent jobs
    first and yields a lower wET.

    This particular 5-job / 2-stage instance was found by a randomized search
    over small due-window / processing-time / weight layouts (seed 1); it is
    asserted (not merely assumed) below — the test fails loudly if the
    relationship ever flips.
    """
    return FFcDDWParameters(
        name="dual_seed_simple_wins",
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="dual_seed_simple_wins_p",
            df=pd.DataFrame([[3, 2], [2, 3], [4, 3], [2, 2], [1, 5]]),
        ),
        job_2_due_window_map={
            "j0": (4, 7),
            "j1": (6, 8),
            "j2": (4, 7),
            "j3": (8, 10),
            "j4": (4, 7),
        },
        job_2_ewt_map={"j0": 2, "j1": 4, "j2": 4, "j3": 4, "j4": 1},
        job_2_twt_map={"j0": 1, "j1": 2, "j2": 2, "j3": 2, "j4": 2},
    )


def test_simple_strictly_wins_case() -> None:
    """On the constructed instance, ``simple`` strictly beats ``midpoint`` and
    the chooser reports ``seed_method == "simple"`` with a feasible schedule
    whose wET equals the simple-only baseline.
    """
    instance = _make_simple_wins_instance()
    midpoint_obj, simple_obj = _direct_baselines(instance)

    assert midpoint_obj is not None
    assert simple_obj is not None
    assert simple_obj < midpoint_obj, (
        "simple-wins fixture must have simple strictly lower; "
        f"got midpoint={midpoint_obj}, simple={simple_obj}"
    )

    choice = _choice(instance)
    assert choice.seed_method == "simple"
    assert choice.build_full.schedule is not None
    validate_schedule(choice.build_full.schedule, instance.stage_2_job_2_p_map)
    assert choice.build_full.dispatched_obj == simple_obj
    assert choice.alt_dispatched_obj == midpoint_obj
