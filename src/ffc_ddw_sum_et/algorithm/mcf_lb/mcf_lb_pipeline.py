"""MCF-LB → full-schedule pipeline (one or two rounds).

Pure algorithm-level composite: ties ``apply_lb_by_mcf``,
``heuristic_last_stage_only_from_mcf_lb``, and
``build_full_sch_from_last_stage_only_sch`` into a single call. The
orchestration layer wraps this with diagnostic recording, artifact
emission, and solution-manager registration.

Round 1 always runs with no augmentation, so the resulting MCF LB is a
valid global bound on the original instance. Round 2 runs only when
``(adjust_p or adjust_r)`` is True, the stop predicate is False, r1
produced a full schedule, AND either the signed makespan delta
``r1_full_sch_makespan - ref_makespan`` is strictly positive OR
``proceed_r2_when_nonpositive_cmax`` is True (in which case the delta
is clamped to ``>=1`` for increment computation only — the raw signed
delta is still recorded). The reference makespan is selected by
``makespan_delta_ref``: ``"mcfLbMakespan"`` (default) uses
``r1.apply.mcf_preemptive_schedule.makespan``;
``"lastStageOnlyMakespan"`` uses ``r1.heuristic.schedule.makespan``.
The ``proceed_r2_when_nonpositive_cmax`` flag defaults to False; with
the default, behavior matches the original ``delta_le_0`` skip
semantics exactly.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from ...io.parallel_mc_cost_heatmap import HeatmapSort
from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..pm_pmtn_sorter import PmPrmpSortKey
from .diagnostic import StageLbRecord
from .full_sch_builder import (
    BuildFullSchResult,
    build_full_sch_from_last_stage_only_sch,
)
from .last_stage_sch_builder import (
    HeuristicLastStageOnlyResult,
    heuristic_last_stage_only_from_mcf_lb,
    simple_last_stage_only_from_mcf_lb,
)
from .lb_last_stage_pmtn import (
    ApplyLbByMcfResult,
    MCFLBStopRequested,
    apply_lb_by_mcf,
)
from .stage_sch_builder import build_stage_seed_full_sch

# Phase schedule type alias mirrors ``controller_core.MCFLBPhaseSchedule``.
MCFLBPhaseSchedule = FFcSchedule | MCFPreemptiveSchedule

__all__ = [
    "CalcMcfLbAllStagesResult",
    "CalcMcfLbAndDeriveFullSchResult",
    "CalcMcfLbR1Result",
    "CalcMcfLbR2Result",
    "LastStageSeedChoice",
    "calc_mcf_lb_all_stages_and_derive_full_sch",
    "calc_mcf_lb_and_derive_full_sch",
    "calc_mcf_lb_r1_and_derive_full_sch",
    "calc_mcf_lb_r2_and_derive_full_sch",
]


# Round-1 build_full_sch labels in record order. ``lastS_only_before_rs``
# is dropped because r1 never rebuilds (it's a deepcopy of label 3 —
# ``lastS_only_from_mcf_lb_after_sa_iti``). Indices start at 4.
_R1_BUILD_FULL_SCH_LABELS: tuple[str, ...] = (
    "lastS_only_after_rs",
    "lastS_only_flipped",
    "fullS_before_unflip",
    "fullS_after_unflip",
    "fullS_after_sa_iti",
)
# Round-2 build_full_sch labels. ``lastS_only_before_rs`` sits at index
# 4 only when ``p_increment != 0`` (the rebuilt last-stage schedule
# differs from label 3); when ``p_increment == 0`` the rebuild is a
# deepcopy of label 3 and r2 drops it the same way r1 does. The full
# tuple is still iterated for index alignment — labels 5..9 keep their
# positions whether index 4 is present or not.
_R2_BUILD_FULL_SCH_LABELS: tuple[str, ...] = (
    "lastS_only_before_rs",
    "lastS_only_after_rs",
    "lastS_only_flipped",
    "fullS_before_unflip",
    "fullS_after_unflip",
    "fullS_after_sa_iti",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageSeedChoice:
    """The winning last-stage seed and full schedule across both methods.

    Built by :func:`_build_best_full_from_last_stage_seeds`, which constructs
    both the ``midpoint`` (today's path) and ``simple`` last-stage-only seeds,
    derives a full schedule from each, and keeps the one with the lower
    ``build_full.dispatched_obj``. Ties favour ``midpoint`` so the chosen
    schedule is always ``<=`` today's wET (the comparison only adds a
    candidate; it never removes the existing one).
    """

    heuristic: HeuristicLastStageOnlyResult
    """Winning seed's last-stage-only heuristic result."""

    build_full: BuildFullSchResult
    """Winning seed's full-schedule build result."""

    seed_method: Literal["simple", "midpoint"]
    """Which method produced the winner."""

    alt_dispatched_obj: float | None
    """Loser's full-schedule wET; ``None`` when the loser's build failed."""


def _build_best_full_from_last_stage_seeds(
    instance: FFcDDWParameters,
    apply: ApplyLbByMcfResult,
    *,
    job_placement_priority: PmPrmpSortKey,
    last_stage_only_placement_criteria: Literal["contrib", "dist"],
    p_increment: int,
    r_multiplier: float,
    r_increment: int,
    rebuild_last_stage_with_original_p: bool,
    seed_compare: bool,
    logger: logging.Logger | None,
) -> LastStageSeedChoice:
    """Build the last-stage seed(s) and keep the lower-wET full schedule.

    The ``midpoint`` branch reproduces today's path verbatim (placement +
    heuristic refinement on the MCF window, with any r2 augmentation). When
    ``seed_compare`` is ``False`` (default policy) only the midpoint branch
    runs and its full schedule is returned unchanged — byte-identical to the
    historical single-seed pipeline, and no extra build cost. When
    ``seed_compare`` is ``True`` the ``simple`` branch (decision D1) also
    builds an original-``p`` left-packed seed with no augmentation; each seed
    is turned into a full schedule via
    ``build_full_sch_from_last_stage_only_sch`` and the winner is the lower
    ``dispatched_obj``. A ``None``-schedule build loses; ties favour
    ``midpoint``. If midpoint's build produced no schedule but simple's did,
    simple wins (strict improvement on availability).
    """
    # ---- midpoint branch (today's path) ----
    midpoint_heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        logger=logger,
        job_priority=job_placement_priority,
        placement_priority=last_stage_only_placement_criteria,
        p_increment=p_increment,
        r_multiplier=r_multiplier,
        r_increment=r_increment,
    )
    midpoint_build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        midpoint_heuristic.schedule,
        rebuild_last_stage_with_original_p=rebuild_last_stage_with_original_p,
        logger=logger,
    )

    if not seed_compare:
        # Comparison disabled: midpoint-only path, byte-identical to the
        # historical single-seed pipeline (no simple seed built).
        return LastStageSeedChoice(
            heuristic=midpoint_heuristic,
            build_full=midpoint_build_full,
            seed_method="midpoint",
            alt_dispatched_obj=None,
        )

    # ---- simple branch (D1: original p, no augmentation) ----
    simple_heuristic = simple_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        logger=logger,
    )
    simple_build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        simple_heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
        logger=logger,
    )

    midpoint_obj = midpoint_build_full.dispatched_obj
    simple_obj = simple_build_full.dispatched_obj
    midpoint_ok = midpoint_build_full.schedule is not None
    simple_ok = simple_build_full.schedule is not None

    # Simple wins only when it produced a schedule AND it strictly beats
    # midpoint (or midpoint produced none). Ties favour midpoint so the
    # chosen schedule is never worse than today's.
    simple_wins = simple_ok and (
        not midpoint_ok or simple_obj < midpoint_obj  # type: ignore[operator]
    )

    if simple_wins:
        return LastStageSeedChoice(
            heuristic=simple_heuristic,
            build_full=simple_build_full,
            seed_method="simple",
            alt_dispatched_obj=midpoint_obj if midpoint_ok else None,
        )
    return LastStageSeedChoice(
        heuristic=midpoint_heuristic,
        build_full=midpoint_build_full,
        seed_method="midpoint",
        alt_dispatched_obj=simple_obj if simple_ok else None,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbR1Result:
    """Result of one ``calc_mcf_lb_r1_and_derive_full_sch`` call.

    Sub-result presence signals where the round halted:

    - ``apply is None``: stop fired before the LP solve.
    - ``apply is not None`` and ``heuristic is None``: stop fired between
      LP solve and the heuristic.
    - ``heuristic is not None`` and ``build_full is None``: stop fired
      between the heuristic and ``build_full_sch_from_last_stage_only_sch``.
    - ``build_full is not None``: round 1 finished (``build_full.schedule``
      may still be ``None`` when reverse-dispatch produced nothing).

    ``phase_schedules`` is pre-numbered (``"<n>_<label>"``) — up to 8
    entries (1..8) when the round runs to completion, fewer when stop
    fired earlier.
    """

    apply: ApplyLbByMcfResult | None
    heuristic: HeuristicLastStageOnlyResult | None
    build_full: BuildFullSchResult | None
    phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]
    elapsed_sec: float
    stop_reason: Literal["stop_guard"] | None
    seed_method: Literal["simple", "midpoint"] | None = None
    alt_dispatched_obj: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbR2Result:
    """Result of one ``calc_mcf_lb_r2_and_derive_full_sch`` call.

    Same sub-result presence semantics as :class:`CalcMcfLbR1Result` plus
    the recorded ``p_increment`` / ``r_increment`` values used to augment
    the relaxation. ``phase_schedules`` carries up to 9 entries (1..9):
    r2 keeps ``lastS_only_before_rs`` at index 4 (which is a deepcopy of
    label 3 when ``p_increment == 0``).
    """

    apply: ApplyLbByMcfResult | None
    heuristic: HeuristicLastStageOnlyResult | None
    build_full: BuildFullSchResult | None
    phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]
    elapsed_sec: float
    p_increment: int
    r_increment: int
    stop_reason: Literal["stop_guard"] | None
    seed_method: Literal["simple", "midpoint"] | None = None
    alt_dispatched_obj: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbAndDeriveFullSchResult:
    """Aggregate result of one ``calc_mcf_lb_and_derive_full_sch`` call.

    Sub-result presence signals what ran:

    - ``r1_apply is None``: stop fired before the r1 LP solve.
    - ``r1_apply is not None`` and ``r1_build_full is None``: stop fired
      after the r1 LP solve but before r1 build_full ran.
    - ``r1_build_full is not None`` and ``r1_build_full.schedule is None``:
      r1 build_full ran but reverse-dispatch produced no schedule.
    - ``r1_build_full.schedule is not None``: r1 succeeded.

    The orchestration wrapper short-circuits to a stop-report when
    ``r1_build_full is None``; otherwise it registers ``best_schedule``
    (which may be ``None`` only when r1 build_full failed mid-pipeline).

    ``best_schedule`` is the full schedule from r1 / r2 with the lower
    wET; ties favour r2. ``final_obj_bound`` is always ``r1_apply.mcf_lb``
    when r1's LP solved — that bound is valid on the original instance
    regardless of any r2 augmentation.

    ``r1_phase_schedules`` and ``r2_phase_schedules`` are pre-numbered
    (``"<n>_<label>"``). r1 has up to 8 entries (1..8); r2 has up to 9
    entries (1..9). The orchestration wrapper iterates them directly
    when emitting per-round JSON artifacts.
    """

    best_schedule: FFcSchedule | None
    best_obj: float | None
    final_obj_bound: float | None
    elapsed_sec: float

    r1_apply: ApplyLbByMcfResult | None
    r1_heuristic: HeuristicLastStageOnlyResult | None
    r1_build_full: BuildFullSchResult | None
    r2_apply: ApplyLbByMcfResult | None
    r2_heuristic: HeuristicLastStageOnlyResult | None
    r2_build_full: BuildFullSchResult | None

    makespan_delta: int | None
    r2_ran: bool
    r2_skip_reason: Literal["no_adjust", "stop_guard", "s1_none", "delta_le_0"] | None
    r2_p_increment: int | None
    r2_r_increment: int | None

    r1_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]
    r2_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]

    last_stage_seed_method: str | None = None
    last_stage_alt_obj: float | None = None


def calc_mcf_lb_r1_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    seed_compare: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbR1Result:
    """Run round 1 (no augmentation) of the MCF-LB → full-schedule pipeline.

    Pipeline: ``apply_lb_by_mcf`` → ``heuristic_last_stage_only_from_mcf_lb``
    → ``build_full_sch_from_last_stage_only_sch`` (with
    ``rebuild_last_stage_with_original_p=False``). Stop checks fire
    between every substep; ``MCFLBStopRequested`` from the LP layer is
    treated as a clean stop too.
    """
    start_elapsed = time.monotonic()
    phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []

    def _stop_check() -> bool:
        return stop_predicate is not None and stop_predicate()

    def _build(
        *,
        stop_reason: Literal["stop_guard"] | None,
        apply: ApplyLbByMcfResult | None = None,
        heuristic: HeuristicLastStageOnlyResult | None = None,
        build_full: BuildFullSchResult | None = None,
        seed_method: Literal["simple", "midpoint"] | None = None,
        alt_dispatched_obj: float | None = None,
    ) -> CalcMcfLbR1Result:
        return CalcMcfLbR1Result(
            apply=apply,
            heuristic=heuristic,
            build_full=build_full,
            phase_schedules=phase_schedules,
            elapsed_sec=time.monotonic() - start_elapsed,
            stop_reason=stop_reason,
            seed_method=seed_method,
            alt_dispatched_obj=alt_dispatched_obj,
        )

    if _stop_check():
        return _build(stop_reason="stop_guard")

    try:
        apply = apply_lb_by_mcf(
            instance,
            draw_heatmap=draw_pmtn_sch_heatmap,
            heatmap_sort=heatmap_sort,
            heatmap_yaml_path=heatmap_yaml_path,
            stop_predicate=stop_predicate,
            logger=logger,
        )
    except MCFLBStopRequested:
        return _build(stop_reason="stop_guard")

    phase_schedules.append(("1_mcf_preemptive", apply.mcf_preemptive_schedule))

    if _stop_check():
        return _build(stop_reason="stop_guard", apply=apply)

    # Build both last-stage seeds (midpoint = today's path, simple = D1
    # original-p) and keep the lower-wET full schedule; ties favour
    # midpoint so the chosen schedule is never worse than today's.
    choice = _build_best_full_from_last_stage_seeds(
        instance,
        apply,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        p_increment=0,
        r_multiplier=1.0,
        r_increment=0,
        rebuild_last_stage_with_original_p=False,
        seed_compare=seed_compare,
        logger=logger,
    )
    heuristic = choice.heuristic
    build_full = choice.build_full
    for label, sched in heuristic.intermediate_schedules:
        phase_schedules.append((f"2_{label}", sched))
    phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", heuristic.schedule)
    )

    if _stop_check():
        return _build(
            stop_reason="stop_guard",
            apply=apply,
            heuristic=heuristic,
            seed_method=choice.seed_method,
            alt_dispatched_obj=choice.alt_dispatched_obj,
        )

    # Drop ``lastS_only_before_rs`` from r1: r1 runs without p-adjustment,
    # so the rebuilt last-stage schedule is identical to label 3
    # (``lastS_only_from_mcf_lb_after_sa_iti``). Recording it separately
    # would emit a duplicate phase snapshot. r2 keeps the label because
    # ``rebuild_last_stage_with_original_p=(p_increment != 0)`` may
    # actually change the schedule there.
    r1_kept = {
        label: sched
        for label, sched in build_full.intermediate_schedules
        if label != "lastS_only_before_rs"
    }
    for offset, label in enumerate(_R1_BUILD_FULL_SCH_LABELS):
        sched = r1_kept.get(label)
        if sched is not None:
            phase_schedules.append((f"{4 + offset}_{label}", sched))

    return _build(
        stop_reason=None,
        apply=apply,
        heuristic=heuristic,
        build_full=build_full,
        seed_method=choice.seed_method,
        alt_dispatched_obj=choice.alt_dispatched_obj,
    )


def calc_mcf_lb_r2_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    makespan_delta: int,
    adjust_p: bool,
    adjust_r: bool,
    p_adjust_coeff: float = 1.0,
    r_adjust_coeff: float = 0.5,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    seed_compare: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbR2Result:
    """Run round 2 (with delta-derived augmentation) of the pipeline.

    Pipeline: ``apply_lb_by_mcf`` (with ``p_increment`` / ``r_increment``)
    → ``heuristic_last_stage_only_from_mcf_lb`` (same increments)
    → ``build_full_sch_from_last_stage_only_sch`` (with
    ``rebuild_last_stage_with_original_p=(p_increment != 0)``).

    ``makespan_delta`` is clamped to ``>= 1`` for increment computation
    so callers may invoke r2 even on a non-positive incumbent-vs-LP gap
    (the gating policy lives in the composite). The clamp is a no-op
    when ``delta > 0``.

    ``p_adjust_coeff`` (default ``1.0``) scales the ``adjust_p`` formula:
    ``p_increment = ceil(p_adjust_coeff * delta_for_inc * m_last / n)``.
    The default matches the historical hard-coded
    ``ceil(delta_for_inc * m_last / n)`` factor.

    ``r_adjust_coeff`` (default ``0.5``) scales the ``adjust_r`` formula:
    ``r_increment = ceil(delta_for_inc * r_adjust_coeff)``. The default
    matches the historical hard-coded ``ceil(delta_for_inc / 2)`` factor.
    """
    start_elapsed = time.monotonic()
    phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []

    delta_for_inc = max(makespan_delta, 1)
    n = instance.job_count
    m_last = instance.last_stage_mc_count
    p_increment = (
        math.ceil(p_adjust_coeff * delta_for_inc * m_last / n) if adjust_p else 0
    )
    r_increment = math.ceil(r_adjust_coeff * delta_for_inc) if adjust_r else 0

    def _stop_check() -> bool:
        return stop_predicate is not None and stop_predicate()

    def _build(
        *,
        stop_reason: Literal["stop_guard"] | None,
        apply: ApplyLbByMcfResult | None = None,
        heuristic: HeuristicLastStageOnlyResult | None = None,
        build_full: BuildFullSchResult | None = None,
        seed_method: Literal["simple", "midpoint"] | None = None,
        alt_dispatched_obj: float | None = None,
    ) -> CalcMcfLbR2Result:
        return CalcMcfLbR2Result(
            apply=apply,
            heuristic=heuristic,
            build_full=build_full,
            phase_schedules=phase_schedules,
            elapsed_sec=time.monotonic() - start_elapsed,
            p_increment=p_increment,
            r_increment=r_increment,
            stop_reason=stop_reason,
            seed_method=seed_method,
            alt_dispatched_obj=alt_dispatched_obj,
        )

    try:
        apply = apply_lb_by_mcf(
            instance,
            p_increment=p_increment,
            r_increment=r_increment,
            draw_heatmap=draw_pmtn_sch_heatmap,
            heatmap_sort=heatmap_sort,
            heatmap_yaml_path=heatmap_yaml_path,
            stop_predicate=stop_predicate,
            logger=logger,
        )
    except MCFLBStopRequested:
        return _build(stop_reason="stop_guard")

    phase_schedules.append(("1_mcf_preemptive", apply.mcf_preemptive_schedule))

    if _stop_check():
        return _build(stop_reason="stop_guard", apply=apply)

    # Build both last-stage seeds (midpoint = today's augmented path,
    # simple = D1 original-p) and keep the lower-wET full schedule; ties
    # favour midpoint so the chosen schedule is never worse than today's.
    choice = _build_best_full_from_last_stage_seeds(
        instance,
        apply,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        p_increment=p_increment,
        r_multiplier=1.0,
        r_increment=r_increment,
        rebuild_last_stage_with_original_p=(p_increment != 0),
        seed_compare=seed_compare,
        logger=logger,
    )
    heuristic = choice.heuristic
    build_full = choice.build_full
    for label, sched in heuristic.intermediate_schedules:
        phase_schedules.append((f"2_{label}", sched))
    phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", heuristic.schedule)
    )

    if _stop_check():
        return _build(
            stop_reason="stop_guard",
            apply=apply,
            heuristic=heuristic,
            seed_method=choice.seed_method,
            alt_dispatched_obj=choice.alt_dispatched_obj,
        )

    r2_kept = dict(build_full.intermediate_schedules)
    # Drop ``lastS_only_before_rs`` when ``p_increment == 0``: the
    # rebuild step uses the same ``p`` as the heuristic input, so the
    # output is identical to label 3 (mirrors r1's drop). When
    # ``p_increment != 0`` the rebuilt schedule actually changes — keep
    # it. Index 4 simply isn't recorded in the duplicate case; later
    # labels keep their indices 5..9 via ``enumerate`` over the full
    # ``_R2_BUILD_FULL_SCH_LABELS`` tuple.
    if p_increment == 0:
        r2_kept.pop("lastS_only_before_rs", None)
    for offset, label in enumerate(_R2_BUILD_FULL_SCH_LABELS):
        sched = r2_kept.get(label)
        if sched is not None:
            phase_schedules.append((f"{4 + offset}_{label}", sched))

    return _build(
        stop_reason=None,
        apply=apply,
        heuristic=heuristic,
        build_full=build_full,
        seed_method=choice.seed_method,
        alt_dispatched_obj=choice.alt_dispatched_obj,
    )


def calc_mcf_lb_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    makespan_delta_ref: Literal[
        "mcfLbMakespan", "lastStageOnlyMakespan"
    ] = "mcfLbMakespan",
    adjust_p: bool = False,
    adjust_r: bool = False,
    p_adjust_coeff: float = 1.0,
    r_adjust_coeff: float = 0.5,
    proceed_r2_when_nonpositive_cmax: bool = False,
    seed_compare: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    r1_heatmap_yaml_path: Path | None = None,
    r2_heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbAndDeriveFullSchResult:
    """Run the MCF-LB → full-schedule pipeline.

    Defaults match the historical controller-side composite signature
    (``heatmap_sort="end_time"``, ``job_placement_priority="end_time"``,
    ``last_stage_only_placement_criteria="dist"``) so call sites that
    relied on the controller defaults keep their behaviour.

    Args:
        instance: Original FFcDDW instance (no augmentation).
        draw_pmtn_sch_heatmap: When True, ``apply_lb_by_mcf`` dumps the
            C-cost heatmap YAML at the matching per-round path.
        heatmap_sort: Forwarded to ``apply_lb_by_mcf`` for both rounds.
        job_placement_priority: Forwarded as ``job_priority`` to
            ``heuristic_last_stage_only_from_mcf_lb`` for both rounds.
        last_stage_only_placement_criteria: Forwarded as
            ``placement_priority`` to the heuristic for both rounds.
        makespan_delta_ref: Reference makespan used as the LB-side term
            in ``makespan_delta = r1_full_sch_makespan - ref_makespan``.
            ``"mcfLbMakespan"`` (default) uses the r1 MCF preemptive LP
            schedule's makespan (``r1.apply.mcf_preemptive_schedule``),
            preserving prior behaviour. ``"lastStageOnlyMakespan"`` uses
            the r1 heuristic non-preemptive last-stage schedule's
            makespan (``r1.heuristic.schedule``). Any other value raises
            ``ValueError``.
        adjust_p: When True, round 2 inflates last-stage processing
            times by ``ceil(p_adjust_coeff * makespan_delta * m_last / n)``.
        adjust_r: When True, round 2 inflates per-job releases by
            ``ceil(makespan_delta * r_adjust_coeff)`` (the historical
            ``adjust_r_by_half`` behaviour is the default).
        p_adjust_coeff: Coefficient on ``makespan_delta * m_last / n``
            in the ``adjust_p`` formula. Default ``1.0`` reproduces the
            historical ``ceil(delta * m_last / n)`` factor; pass a
            different value to scale the processing-time augmentation.
        r_adjust_coeff: Coefficient on ``makespan_delta`` in the
            ``adjust_r`` formula. Default ``0.5`` reproduces the
            historical ``ceil(delta / 2)`` factor; pass a different
            value to scale the release-time augmentation.
        proceed_r2_when_nonpositive_cmax: When False (default), the
            historical ``delta_le_0`` skip applies — round 2 is skipped
            with ``r2_skip_reason="delta_le_0"`` whenever the signed
            ``r1_full_sch_makespan - ref_makespan`` is
            ``<= 0``. When True, that skip is bypassed and round 2
            runs with the delta clamped to ``>=1`` for increment math.
            The raw signed delta is preserved on
            ``CalcMcfLbAndDeriveFullSchResult.makespan_delta`` regardless.
        stop_predicate: Optional probe checked at composite checkpoints
            (after each LP solve and before each heuristic / build_full
            stage). The LP layer also raises ``MCFLBStopRequested`` when
            the predicate fires before solve; both forms short-circuit
            cleanly.
        logger: Optional logger forwarded to all sub-functions.
        r1_heatmap_yaml_path / r2_heatmap_yaml_path: Optional output
            paths for the per-round C-cost heatmap YAML. Ignored when
            ``draw_pmtn_sch_heatmap`` is False.
    """
    if makespan_delta_ref not in ("mcfLbMakespan", "lastStageOnlyMakespan"):
        raise ValueError(
            "makespan_delta_ref must be 'mcfLbMakespan' or "
            f"'lastStageOnlyMakespan'; got {makespan_delta_ref!r}"
        )

    start_elapsed = time.monotonic()

    def _stop_check() -> bool:
        return stop_predicate is not None and stop_predicate()

    def _assemble(
        *,
        best_schedule: FFcSchedule | None,
        best_obj: float | None,
        r1: CalcMcfLbR1Result,
        r2: CalcMcfLbR2Result | None,
        makespan_delta: int | None,
        r2_ran: bool,
        r2_skip_reason: (
            Literal["no_adjust", "stop_guard", "s1_none", "delta_le_0"] | None
        ),
    ) -> CalcMcfLbAndDeriveFullSchResult:
        return CalcMcfLbAndDeriveFullSchResult(
            best_schedule=best_schedule,
            best_obj=best_obj,
            final_obj_bound=r1.apply.mcf_lb if r1.apply is not None else None,
            elapsed_sec=time.monotonic() - start_elapsed,
            r1_apply=r1.apply,
            r1_heuristic=r1.heuristic,
            r1_build_full=r1.build_full,
            r2_apply=r2.apply if r2 is not None else None,
            r2_heuristic=r2.heuristic if r2 is not None else None,
            r2_build_full=r2.build_full if r2 is not None else None,
            makespan_delta=makespan_delta,
            r2_ran=r2_ran,
            r2_skip_reason=r2_skip_reason,
            r2_p_increment=r2.p_increment if r2 is not None else None,
            r2_r_increment=r2.r_increment if r2 is not None else None,
            r1_phase_schedules=r1.phase_schedules,
            r2_phase_schedules=r2.phase_schedules if r2 is not None else [],
            last_stage_seed_method=r1.seed_method,
            last_stage_alt_obj=r1.alt_dispatched_obj,
        )

    # ------------------- Round 1 -------------------
    r1 = calc_mcf_lb_r1_and_derive_full_sch(
        instance,
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        seed_compare=seed_compare,
        stop_predicate=stop_predicate,
        logger=logger,
        heatmap_yaml_path=r1_heatmap_yaml_path,
    )

    if r1.build_full is None:
        # r1 stopped before producing a build_full result; the
        # orchestration wrapper short-circuits to a stop-report.
        return _assemble(
            best_schedule=None,
            best_obj=None,
            r1=r1,
            r2=None,
            makespan_delta=None,
            r2_ran=False,
            r2_skip_reason="stop_guard",
        )

    s1_schedule = r1.build_full.schedule
    s1_obj = r1.build_full.dispatched_obj

    # ------------------- Round 2 skip gates -------------------
    if not (adjust_p or adjust_r):
        return _assemble(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1=r1,
            r2=None,
            makespan_delta=None,
            r2_ran=False,
            r2_skip_reason="no_adjust",
        )

    if _stop_check():
        return _assemble(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1=r1,
            r2=None,
            makespan_delta=None,
            r2_ran=False,
            r2_skip_reason="stop_guard",
        )

    if s1_schedule is None:
        return _assemble(
            best_schedule=None,
            best_obj=None,
            r1=r1,
            r2=None,
            makespan_delta=None,
            r2_ran=False,
            r2_skip_reason="s1_none",
        )

    incumbent_makespan = int(s1_schedule.makespan)
    if makespan_delta_ref == "mcfLbMakespan":
        ref_makespan = int(r1.apply.mcf_preemptive_schedule.makespan)
    else:
        # "lastStageOnlyMakespan" — r1.heuristic is non-None whenever
        # r1.build_full is non-None (build_full requires the heuristic
        # output as input), and we already returned above when
        # ``r1.build_full is None``.
        ref_makespan = int(r1.heuristic.schedule.makespan)
    makespan_delta = incumbent_makespan - ref_makespan

    if makespan_delta <= 0 and not proceed_r2_when_nonpositive_cmax:
        if logger is not None:
            logger.info(
                "calc_mcf_lb_and_derive_full_sch: round1 makespan=%d, "
                "ref_makespan=%d (ref=%s), delta=%d <= 0 — skipping adjust round",
                incumbent_makespan,
                ref_makespan,
                makespan_delta_ref,
                makespan_delta,
            )
        return _assemble(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1=r1,
            r2=None,
            makespan_delta=makespan_delta,
            r2_ran=False,
            r2_skip_reason="delta_le_0",
        )

    if _stop_check():
        return _assemble(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1=r1,
            r2=None,
            makespan_delta=makespan_delta,
            r2_ran=False,
            r2_skip_reason="stop_guard",
        )

    # ------------------- Round 2 -------------------
    r2 = calc_mcf_lb_r2_and_derive_full_sch(
        instance,
        makespan_delta=makespan_delta,
        adjust_p=adjust_p,
        adjust_r=adjust_r,
        p_adjust_coeff=p_adjust_coeff,
        r_adjust_coeff=r_adjust_coeff,
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        seed_compare=seed_compare,
        stop_predicate=stop_predicate,
        logger=logger,
        heatmap_yaml_path=r2_heatmap_yaml_path,
    )

    if r2.stop_reason == "stop_guard":
        return _assemble(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1=r1,
            r2=r2,
            makespan_delta=makespan_delta,
            r2_ran=False,
            r2_skip_reason="stop_guard",
        )

    s2_schedule = r2.build_full.schedule if r2.build_full is not None else None
    s2_obj = r2.build_full.dispatched_obj if r2.build_full is not None else None

    best_schedule, best_obj = s1_schedule, s1_obj
    if s2_schedule is not None and (s1_schedule is None or s2_obj <= s1_obj):
        best_schedule, best_obj = s2_schedule, s2_obj

    return _assemble(
        best_schedule=best_schedule,
        best_obj=best_obj,
        r1=r1,
        r2=r2,
        makespan_delta=makespan_delta,
        r2_ran=True,
        r2_skip_reason=None,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbAllStagesResult:
    """Aggregate result of one ``calc_mcf_lb_all_stages_and_derive_full_sch``.

    The last stage is solved by reusing the existing
    ``calc_mcf_lb_and_derive_full_sch`` pipeline (full earliness+tardiness
    MCF + reverse-dispatch schedule, incl. round 2). Each intermediate
    stage ``q = c-1 … 1`` adds a weighted-tardiness-only MCF lower bound
    and a stage-anchored seed schedule (round-1 only, no drift correction).

    - ``last_stage_result``: the existing pipeline result, kept so the
      controller can reuse the existing diagnostic/artifact-emission code.
    - ``best_schedule`` / ``best_obj``: global argmin-wET across the
      last-stage pipeline result and every intermediate seed.
    - ``combined_lb``: ``max`` over the valid lower bounds (full-ET at the
      last stage + tardiness-only at each intermediate stage). Each
      intermediate LB is a valid LB on OPT (round-1 only), so they are all
      eligible for the max.
    - ``argmax_stage_id``: stage whose LB attains ``combined_lb``.
    - ``best_sched_source``: ``"last_stage_pipeline"`` or the stage id of
      the intermediate seed that produced ``best_schedule``.
    - ``stage_records``: one :class:`StageLbRecord` per stage, ordered in
      **ascending stage order** (stage 1 … c) for readability.
    - ``total_mcf_solve_sec`` / ``mcf_solve_count``: accumulated MCF solve
      time / count (last-stage r1 + last-stage r2 if it ran + one per
      intermediate stage solved).
    - ``elapsed_sec``: wall time of the whole call.
    """

    last_stage_result: CalcMcfLbAndDeriveFullSchResult
    best_schedule: FFcSchedule | None
    best_obj: float | None
    combined_lb: float | None
    argmax_stage_id: str | None
    best_sched_source: str | None
    stage_records: list[StageLbRecord]
    total_mcf_solve_sec: float
    mcf_solve_count: int
    elapsed_sec: float


def calc_mcf_lb_all_stages_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    makespan_delta_ref: Literal[
        "mcfLbMakespan", "lastStageOnlyMakespan"
    ] = "mcfLbMakespan",
    adjust_p: bool = False,
    adjust_r: bool = False,
    p_adjust_coeff: float = 1.0,
    r_adjust_coeff: float = 0.5,
    proceed_r2_when_nonpositive_cmax: bool = False,
    seed_compare: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    r1_heatmap_yaml_path: Path | None = None,
    r2_heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbAllStagesResult:
    """Run the all-stages MCF-LB projection (``lb_stage_scope="all_stages"``).

    The last stage reuses :func:`calc_mcf_lb_and_derive_full_sch` verbatim
    (same kwargs) so the last-stage LB / schedule / round-2 / artifacts are
    identical to the ``last_stage`` scope. Each intermediate stage
    ``q = c-1 … 1`` then adds a weighted-tardiness-only MCF lower bound
    (``apply_lb_by_mcf(..., tardiness_only=True)``) and a stage-anchored
    seed schedule (``build_stage_seed_full_sch``); round-1 only, no
    drift-correction (decision D1).

    The reported bound is
    ``combined_lb = max{ LB^ET_c , max_{q<c} LB_T^(q) }`` and the registered
    schedule is the global min-wET across the last-stage pipeline result and
    every intermediate seed.

    ``stop_predicate`` is probed at each intermediate stage boundary; on stop
    the function returns with the stages solved so far (the last stage is
    always attempted first via the reused pipeline).
    """
    start_elapsed = time.monotonic()

    # Last stage: reuse the existing pipeline verbatim so its LB / schedule /
    # round-2 / artifacts are byte-identical to the ``last_stage`` scope.
    last_stage_result = calc_mcf_lb_and_derive_full_sch(
        instance,
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        makespan_delta_ref=makespan_delta_ref,
        adjust_p=adjust_p,
        adjust_r=adjust_r,
        p_adjust_coeff=p_adjust_coeff,
        r_adjust_coeff=r_adjust_coeff,
        proceed_r2_when_nonpositive_cmax=proceed_r2_when_nonpositive_cmax,
        seed_compare=seed_compare,
        stop_predicate=stop_predicate,
        logger=logger,
        r1_heatmap_yaml_path=r1_heatmap_yaml_path,
        r2_heatmap_yaml_path=r2_heatmap_yaml_path,
    )

    stage_id_list = instance.stage_id_list
    last_stage_id = stage_id_list[-1]

    # Seed the running summaries with the last-stage pipeline result.
    combined_lb: float | None = last_stage_result.final_obj_bound
    argmax_stage_id: str | None = last_stage_id
    best_schedule: FFcSchedule | None = last_stage_result.best_schedule
    best_obj: float | None = last_stage_result.best_obj
    best_sched_source: str | None = "last_stage_pipeline"

    # Accumulators: last-stage r1 always counts; last-stage r2 counts only
    # when it actually ran.
    total_mcf_solve_sec = 0.0
    mcf_solve_count = 0
    if last_stage_result.r1_apply is not None:
        total_mcf_solve_sec += last_stage_result.r1_apply.mcf_solve_sec
        mcf_solve_count += 1
    if last_stage_result.r2_ran and last_stage_result.r2_apply is not None:
        total_mcf_solve_sec += last_stage_result.r2_apply.mcf_solve_sec
        mcf_solve_count += 1

    # Build the last-stage record (full-ET bound). The bound is valid on the
    # original instance; fields are populated from the r1 apply result where
    # available (r1 may be ``None`` only if a stop fired before the LP solve).
    r1_apply = last_stage_result.r1_apply
    last_record = StageLbRecord(
        stage_id=last_stage_id,
        is_last_stage=True,
        bound_kind="full_ET",
        mcf_lb=last_stage_result.final_obj_bound,
        mcf_lb_valid=True,
        init_sched_obj=last_stage_result.best_obj,
        delta=(
            None
            if last_stage_result.best_obj is None
            or last_stage_result.final_obj_bound is None
            else last_stage_result.best_obj - last_stage_result.final_obj_bound
        ),
        best_candidate="last_stage_pipeline",
        mcf_solve_sec=None if r1_apply is None else r1_apply.mcf_solve_sec,
        horizon=None if r1_apply is None else r1_apply.mcf.calT[-1],
        slot_count=None if r1_apply is None else len(r1_apply.mcf.calT),
        load_index=None,
        max_release=None,
        seed_method=last_stage_result.last_stage_seed_method,
    )

    # Records accumulated last → first while iterating; reordered to ascending
    # stage order before returning (see below).
    intermediate_records: list[StageLbRecord] = []

    # Intermediate stages q = c-1 … 1.
    for q in reversed(stage_id_list[:-1]):
        # Stop check at the stage boundary: return with stages solved so far.
        if stop_predicate is not None and stop_predicate():
            break

        apply = apply_lb_by_mcf(
            instance,
            stage_id=q,
            tardiness_only=True,
            draw_heatmap=False,
            stop_predicate=stop_predicate,
            logger=logger,
        )
        total_mcf_solve_sec += apply.mcf_solve_sec
        mcf_solve_count += 1

        seed = build_stage_seed_full_sch(
            instance,
            apply.mcf_preemptive_schedule,
            q,
            seed_compare=seed_compare,
            logger=logger,
        )

        # Stage load index Σ p_q / |M_q| and max upstream release before q.
        # Coerce to plain Python float/int at this diagnostic boundary: the
        # benchmark loader yields numpy-typed processing times, and numpy
        # scalars are not YAML-serializable in the instance_result manifest.
        p_q = instance.get_job_2_p_map_for_stage(q)
        m_q_count = len(instance.stage_2_machines_map[q])
        load_index = float(sum(p_q[j] for j in instance.job_id_list) / m_q_count)
        release_before_q = instance.get_job_2_p_sum_before_stage(q)
        max_release = int(max(release_before_q[j] for j in instance.job_id_list))

        intermediate_records.append(
            StageLbRecord(
                stage_id=q,
                is_last_stage=False,
                bound_kind="tardiness_only",
                mcf_lb=apply.mcf_lb,
                mcf_lb_valid=True,
                init_sched_obj=seed.obj_value,
                delta=seed.obj_value - apply.mcf_lb,
                best_candidate=seed.best_candidate,
                mcf_solve_sec=apply.mcf_solve_sec,
                horizon=apply.mcf.calT[-1],
                slot_count=len(apply.mcf.calT),
                load_index=load_index,
                max_release=max_release,
                seed_method=seed.anchor_method,
            )
        )

        # Update combined LB (max over valid LBs) and best schedule (min wET).
        if combined_lb is None or apply.mcf_lb > combined_lb:
            combined_lb = apply.mcf_lb
            argmax_stage_id = q
        if best_obj is None or seed.obj_value < best_obj:
            best_schedule = seed.schedule
            best_obj = seed.obj_value
            best_sched_source = q

    # Order records ascending (stage 1 … c) for readability: intermediate
    # stages were appended last → first, so reverse them, then the last stage.
    stage_records: list[StageLbRecord] = list(reversed(intermediate_records))
    stage_records.append(last_record)

    return CalcMcfLbAllStagesResult(
        last_stage_result=last_stage_result,
        best_schedule=best_schedule,
        best_obj=best_obj,
        combined_lb=combined_lb,
        argmax_stage_id=argmax_stage_id,
        best_sched_source=best_sched_source,
        stage_records=stage_records,
        total_mcf_solve_sec=total_mcf_solve_sec,
        mcf_solve_count=mcf_solve_count,
        elapsed_sec=time.monotonic() - start_elapsed,
    )
