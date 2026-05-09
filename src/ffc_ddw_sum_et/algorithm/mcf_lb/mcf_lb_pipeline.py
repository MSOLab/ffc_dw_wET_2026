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
``r1_full_sch_makespan - r1_ls_only_pmtn_makespan`` is strictly
positive OR ``proceed_r2_when_nonpositive_cmax`` is True (in which case
the delta is clamped to ``>=1`` for increment computation only — the
raw signed delta is still recorded). The ``proceed_r2_when_nonpositive_cmax``
flag defaults to False; with the default, behavior matches the original
``delta_le_0`` skip semantics exactly.
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
from .full_sch_builder import (
    BuildFullSchResult,
    build_full_sch_from_last_stage_only_sch,
)
from .last_stage_sch_builder import (
    HeuristicLastStageOnlyResult,
    heuristic_last_stage_only_from_mcf_lb,
)
from .lb_last_stage_pmtn import (
    ApplyLbByMcfResult,
    MCFLBStopRequested,
    apply_lb_by_mcf,
)

# Phase schedule type alias mirrors ``controller_core.MCFLBPhaseSchedule``.
MCFLBPhaseSchedule = FFcSchedule | MCFPreemptiveSchedule

__all__ = [
    "CalcMcfLbAndDeriveFullSchResult",
    "CalcMcfLbR1Result",
    "CalcMcfLbR2Result",
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


def calc_mcf_lb_r1_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
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
    ) -> CalcMcfLbR1Result:
        return CalcMcfLbR1Result(
            apply=apply,
            heuristic=heuristic,
            build_full=build_full,
            phase_schedules=phase_schedules,
            elapsed_sec=time.monotonic() - start_elapsed,
            stop_reason=stop_reason,
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

    heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        logger=logger,
        job_priority=job_placement_priority,
        placement_priority=last_stage_only_placement_criteria,
    )
    for label, sched in heuristic.intermediate_schedules:
        phase_schedules.append((f"2_{label}", sched))
    phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", heuristic.schedule)
    )

    if _stop_check():
        return _build(stop_reason="stop_guard", apply=apply, heuristic=heuristic)

    build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
        logger=logger,
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
        stop_reason=None, apply=apply, heuristic=heuristic, build_full=build_full
    )


def calc_mcf_lb_r2_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    makespan_delta: int,
    adjust_p: bool,
    adjust_r: bool,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
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
    """
    start_elapsed = time.monotonic()
    phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []

    delta_for_inc = max(makespan_delta, 1)
    n = instance.job_count
    m_last = instance.last_stage_mc_count
    p_increment = math.ceil(delta_for_inc * m_last / n) if adjust_p else 0
    r_increment = math.ceil(delta_for_inc / 2) if adjust_r else 0

    def _stop_check() -> bool:
        return stop_predicate is not None and stop_predicate()

    def _build(
        *,
        stop_reason: Literal["stop_guard"] | None,
        apply: ApplyLbByMcfResult | None = None,
        heuristic: HeuristicLastStageOnlyResult | None = None,
        build_full: BuildFullSchResult | None = None,
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

    heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        apply.mcf_preemptive_schedule,
        logger=logger,
        job_priority=job_placement_priority,
        placement_priority=last_stage_only_placement_criteria,
        p_increment=p_increment,
        r_increment=r_increment,
    )
    for label, sched in heuristic.intermediate_schedules:
        phase_schedules.append((f"2_{label}", sched))
    phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", heuristic.schedule)
    )

    if _stop_check():
        return _build(stop_reason="stop_guard", apply=apply, heuristic=heuristic)

    build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        heuristic.schedule,
        rebuild_last_stage_with_original_p=(p_increment != 0),
        logger=logger,
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
        stop_reason=None, apply=apply, heuristic=heuristic, build_full=build_full
    )


def calc_mcf_lb_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    adjust_p: bool = False,
    adjust_r: bool = False,
    proceed_r2_when_nonpositive_cmax: bool = False,
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
        adjust_p: When True, round 2 inflates last-stage processing
            times by ``ceil(makespan_delta * m_last / n)``.
        adjust_r: When True, round 2 inflates per-job releases by
            ``ceil(makespan_delta / 2)`` (the historical
            ``adjust_r_by_half`` behaviour is bundled here).
        proceed_r2_when_nonpositive_cmax: When False (default), the
            historical ``delta_le_0`` skip applies — round 2 is skipped
            with ``r2_skip_reason="delta_le_0"`` whenever the signed
            ``r1_full_sch_makespan - r1_ls_only_pmtn_makespan`` is
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
        )

    # ------------------- Round 1 -------------------
    r1 = calc_mcf_lb_r1_and_derive_full_sch(
        instance,
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
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
    ls_only_pmtn_makespan = int(r1.apply.mcf_preemptive_schedule.makespan)
    makespan_delta = incumbent_makespan - ls_only_pmtn_makespan

    if makespan_delta <= 0 and not proceed_r2_when_nonpositive_cmax:
        if logger is not None:
            logger.info(
                "calc_mcf_lb_and_derive_full_sch: round1 makespan=%d, "
                "ls_only_pmtn makespan=%d, delta=%d <= 0 — skipping adjust round",
                incumbent_makespan,
                ls_only_pmtn_makespan,
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
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
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
