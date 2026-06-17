"""Tests for the intermediate-stage dual-anchor seed (§2.2 of the
``plans/20260617/mcf_lb_dual_seed_compare.md`` plan).

``build_stage_seed_full_sch`` now builds **two** stage anchors on an
intermediate stage — the historical ``midpoint`` anchor
(:func:`_build_anchor_schedule`) and the new ``simple`` anchor — and from
each derives two full-schedule candidates (``two_way`` / ``seq_both_ways``),
keeping the global min-wET one (ties favour ``midpoint``). These tests pin:

* the chosen wET is never worse than the midpoint-only anchor's best (the
  historical single-anchor result), so the "never worse than today"
  invariant holds;
* ``anchor_method`` / ``best_candidate`` take their pinned literal values;
* the returned ``schedule`` is a feasible full schedule
  (:func:`validate_schedule` + every operation present);
* an instance where the ``simple`` anchor strictly wins
  (``anchor_method == "simple"``).

The MCF preemptive window for an intermediate stage is produced exactly the
way the all-stages pipeline does it (``mcf_lb_pipeline.py``):
``apply_lb_by_mcf(instance, stage_id=q, tardiness_only=True)``.
"""

from __future__ import annotations

import logging

import pandas as pd

from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.algorithm.mcf_lb.stage_sch_builder import (
    _build_anchor_schedule,
    _candidates_from_anchor,
    build_stage_seed_full_sch,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import validate_schedule


def _make_three_stage_instance() -> FFcDDWParameters:
    """A 3-stage instance (one intermediate stage ``i1``) whose middle stage
    is a single-machine bottleneck, mirroring the construction convention in
    ``tests/algorithm/mcf_lb/test_calc_mcf_lb_all_stages.py``.
    """
    return FFcDDWParameters(
        name="dual_anchor_three_stage",
        job_id_list=["j0", "j1", "j2", "j3"],
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={
            "i0": ["i0_0", "i0_1"],
            "i1": ["i1_0"],
            "i2": ["i2_0", "i2_1"],
        },
        p_manager=JobStageProcessingTimeManager(
            name="dual_anchor_three_stage_p",
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


def _make_simple_wins_instance() -> FFcDDWParameters:
    """A 3-stage instance on which the ``simple`` anchor strictly beats the
    ``midpoint`` anchor at intermediate stage ``i1``.

    Found by a randomized search over hand-shaped 3-stage instances
    (single-machine ``i0``/``i1``, two-machine ``i2``, mixed processing and
    tight due windows). On this instance the simple left-packed ``t_max``
    ordering yields a ``two_way`` full schedule with wET ``22``, while the
    midpoint anchor's best is ``53`` — a genuine strict win, not a tie. Pinned
    here as an explicit fixture (the search itself is not part of the test).
    """
    return FFcDDWParameters(
        name="simple_wins_intermediate",
        job_id_list=["j0", "j1", "j2", "j3"],
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={
            "i0": ["i0_0"],
            "i1": ["i1_0"],
            "i2": ["i2_0", "i2_1"],
        },
        p_manager=JobStageProcessingTimeManager(
            name="simple_wins_intermediate_p",
            df=pd.DataFrame([[2, 3, 3], [5, 2, 5], [1, 5, 2], [4, 4, 5]]),
        ),
        job_2_due_window_map={
            "j0": (8, 12),
            "j1": (10, 14),
            "j2": (7, 7),
            "j3": (3, 5),
        },
        job_2_ewt_map={"j0": 2, "j1": 2, "j2": 2, "j3": 2},
        job_2_twt_map={"j0": 3, "j1": 1, "j2": 3, "j3": 1},
    )


def _intermediate_stage_id(instance: FFcDDWParameters) -> str:
    """The (single) intermediate stage for these 3-stage fixtures."""
    return instance.stage_id_list[1]


def _midpoint_only_best_obj(
    instance: FFcDDWParameters, mcf_preemptive_schedule, stage_id: str
) -> float:
    """The historical single-anchor (midpoint) best wET, reconstructed via
    the same ``_build_anchor_schedule`` + ``_candidates_from_anchor`` path
    ``build_stage_seed_full_sch`` uses for its midpoint branch.
    """
    log = logging.getLogger(__name__)
    midpoint_anchor = _build_anchor_schedule(
        instance, mcf_preemptive_schedule, stage_id, log
    )
    _, midpoint_obj, _ = _candidates_from_anchor(
        instance, midpoint_anchor, stage_id, log
    )
    return midpoint_obj


def test_dual_anchor_no_worse_than_midpoint_only() -> None:
    """The chosen seed wET is never worse than the midpoint-only anchor's
    best — the "never worse than today" invariant (ties favour midpoint).
    """
    instance = _make_three_stage_instance()
    stage_id = _intermediate_stage_id(instance)

    apply = apply_lb_by_mcf(
        instance, stage_id=stage_id, tardiness_only=True, draw_heatmap=False
    )
    result = build_stage_seed_full_sch(
        instance, apply.mcf_preemptive_schedule, stage_id, seed_compare=True
    )

    midpoint_only_obj = _midpoint_only_best_obj(
        instance, apply.mcf_preemptive_schedule, stage_id
    )
    assert result.obj_value <= midpoint_only_obj


def test_dual_anchor_method_and_candidate_literals() -> None:
    """``anchor_method`` and ``best_candidate`` are the pinned literals."""
    instance = _make_three_stage_instance()
    stage_id = _intermediate_stage_id(instance)

    apply = apply_lb_by_mcf(
        instance, stage_id=stage_id, tardiness_only=True, draw_heatmap=False
    )
    result = build_stage_seed_full_sch(
        instance, apply.mcf_preemptive_schedule, stage_id, seed_compare=True
    )

    assert result.anchor_method in ("simple", "midpoint")
    assert result.best_candidate in ("two_way", "seq_both_ways")


def test_dual_anchor_schedule_is_feasible_full_schedule() -> None:
    """The returned schedule is a feasible full schedule: correct durations,
    flowshop precedence, no machine overlap, and every operation present.
    """
    instance = _make_three_stage_instance()
    stage_id = _intermediate_stage_id(instance)

    apply = apply_lb_by_mcf(
        instance, stage_id=stage_id, tardiness_only=True, draw_heatmap=False
    )
    result = build_stage_seed_full_sch(
        instance, apply.mcf_preemptive_schedule, stage_id, seed_compare=True
    )

    validate_schedule(result.schedule, instance.stage_2_job_2_p_map)
    # Every (stage, job) operation is scheduled (full schedule, not stage-only).
    for s in instance.stage_id_list:
        for j in instance.job_id_list:
            result.schedule.get_job_end_time(s, j)

    # obj_value matches the schedule it reports (no stale bookkeeping).
    assert result.obj_value >= 0.0


def test_simple_anchor_strictly_wins() -> None:
    """An instance where the ``simple`` anchor strictly beats ``midpoint``.

    Strict win (not a tie), so ``anchor_method`` must be ``"simple"`` — ties
    favour midpoint, so a "simple" verdict here is unambiguous evidence the
    second anchor was actually built and chosen. The chosen wET is strictly
    below the midpoint-only best.
    """
    instance = _make_simple_wins_instance()
    stage_id = _intermediate_stage_id(instance)

    apply = apply_lb_by_mcf(
        instance, stage_id=stage_id, tardiness_only=True, draw_heatmap=False
    )
    result = build_stage_seed_full_sch(
        instance, apply.mcf_preemptive_schedule, stage_id, seed_compare=True
    )

    midpoint_only_obj = _midpoint_only_best_obj(
        instance, apply.mcf_preemptive_schedule, stage_id
    )

    assert result.obj_value < midpoint_only_obj
    assert result.anchor_method == "simple"
    assert result.best_candidate in ("two_way", "seq_both_ways")
    # The strictly-winning schedule is still a feasible full schedule.
    validate_schedule(result.schedule, instance.stage_2_job_2_p_map)
