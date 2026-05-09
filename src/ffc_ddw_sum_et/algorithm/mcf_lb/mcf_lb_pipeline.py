"""MCF-LB → full-schedule pipeline (one or two rounds).

Pure algorithm-level composite: ties ``apply_lb_by_mcf``,
``heuristic_last_stage_only_from_mcf_lb``, and
``build_full_sch_from_last_stage_only_sch`` into a single call. The
orchestration layer wraps this with diagnostic recording, artifact
emission, and solution-manager registration.

Round 1 always runs with no augmentation, so the resulting MCF LB is a
valid global bound on the original instance. Round 2 runs only when
``(adjust_p or adjust_r)`` is True, the stop predicate is False, r1
produced a full schedule, AND the signed makespan delta
``r1_full_sch_makespan - r1_ls_only_pmtn_makespan`` is strictly
positive. The raw signed delta is recorded on the result regardless of
whether r2 actually runs (so a non-positive delta is captured rather
than dropped).
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
    "calc_mcf_lb_and_derive_full_sch",
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
# Round-2 build_full_sch labels — keeps ``lastS_only_before_rs`` at index
# 4. When r2 ran with ``adjust_p=True`` the rebuilt schedule differs from
# label 3; otherwise it's a deepcopy of label 3 (kept for column
# alignment with R2_LABEL_ORDER on the orchestration side).
_R2_BUILD_FULL_SCH_LABELS: tuple[str, ...] = (
    "lastS_only_before_rs",
    "lastS_only_after_rs",
    "lastS_only_flipped",
    "fullS_before_unflip",
    "fullS_after_unflip",
    "fullS_after_sa_iti",
)


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


def calc_mcf_lb_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    adjust_p: bool = False,
    adjust_r: bool = False,
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

    r1_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []
    r2_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []

    def _stop_check() -> bool:
        return stop_predicate is not None and stop_predicate()

    def _build_result(
        *,
        best_schedule: FFcSchedule | None,
        best_obj: float | None,
        r1_apply: ApplyLbByMcfResult | None,
        r1_heuristic: HeuristicLastStageOnlyResult | None,
        r1_build_full: BuildFullSchResult | None,
        r2_apply: ApplyLbByMcfResult | None = None,
        r2_heuristic: HeuristicLastStageOnlyResult | None = None,
        r2_build_full: BuildFullSchResult | None = None,
        makespan_delta: int | None = None,
        r2_ran: bool = False,
        r2_skip_reason: (
            Literal["no_adjust", "stop_guard", "s1_none", "delta_le_0"] | None
        ) = None,
        r2_p_increment: int | None = None,
        r2_r_increment: int | None = None,
    ) -> CalcMcfLbAndDeriveFullSchResult:
        return CalcMcfLbAndDeriveFullSchResult(
            best_schedule=best_schedule,
            best_obj=best_obj,
            final_obj_bound=r1_apply.mcf_lb if r1_apply is not None else None,
            elapsed_sec=time.monotonic() - start_elapsed,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_apply=r2_apply,
            r2_heuristic=r2_heuristic,
            r2_build_full=r2_build_full,
            makespan_delta=makespan_delta,
            r2_ran=r2_ran,
            r2_skip_reason=r2_skip_reason,
            r2_p_increment=r2_p_increment,
            r2_r_increment=r2_r_increment,
            r1_phase_schedules=r1_phase_schedules,
            r2_phase_schedules=r2_phase_schedules,
        )

    # ------------------- Round 1 -------------------
    if _stop_check():
        return _build_result(
            best_schedule=None,
            best_obj=None,
            r1_apply=None,
            r1_heuristic=None,
            r1_build_full=None,
            r2_skip_reason="stop_guard",
        )

    try:
        r1_apply = apply_lb_by_mcf(
            instance,
            draw_heatmap=draw_pmtn_sch_heatmap,
            heatmap_sort=heatmap_sort,
            heatmap_yaml_path=r1_heatmap_yaml_path,
            stop_predicate=stop_predicate,
            logger=logger,
        )
    except MCFLBStopRequested:
        return _build_result(
            best_schedule=None,
            best_obj=None,
            r1_apply=None,
            r1_heuristic=None,
            r1_build_full=None,
            r2_skip_reason="stop_guard",
        )

    r1_phase_schedules.append(("1_mcf_preemptive", r1_apply.mcf_preemptive_schedule))

    if _stop_check():
        return _build_result(
            best_schedule=None,
            best_obj=None,
            r1_apply=r1_apply,
            r1_heuristic=None,
            r1_build_full=None,
            r2_skip_reason="stop_guard",
        )

    r1_heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        r1_apply.mcf_preemptive_schedule,
        logger=logger,
        job_priority=job_placement_priority,
        placement_priority=last_stage_only_placement_criteria,
    )
    for label, sched in r1_heuristic.intermediate_schedules:
        r1_phase_schedules.append((f"2_{label}", sched))
    r1_phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", r1_heuristic.schedule)
    )

    if _stop_check():
        return _build_result(
            best_schedule=None,
            best_obj=None,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=None,
            r2_skip_reason="stop_guard",
        )

    r1_build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        r1_heuristic.schedule,
        rebuild_last_stage_with_original_p=False,
        logger=logger,
    )
    r1_kept = {
        label: sched
        for label, sched in r1_build_full.intermediate_schedules
        if label != "lastS_only_before_rs"
    }
    for offset, label in enumerate(_R1_BUILD_FULL_SCH_LABELS):
        sched = r1_kept.get(label)
        if sched is not None:
            r1_phase_schedules.append((f"{4 + offset}_{label}", sched))

    s1_schedule: FFcSchedule | None = r1_build_full.schedule
    s1_obj: float | None = r1_build_full.dispatched_obj

    # ------------------- Round 2 skip gates -------------------
    if not (adjust_p or adjust_r):
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_skip_reason="no_adjust",
        )

    if _stop_check():
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_skip_reason="stop_guard",
        )

    if s1_schedule is None:
        return _build_result(
            best_schedule=None,
            best_obj=None,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_skip_reason="s1_none",
        )

    incumbent_makespan = int(s1_schedule.makespan)
    ls_only_pmtn_makespan = int(r1_apply.mcf_preemptive_schedule.makespan)
    makespan_delta = incumbent_makespan - ls_only_pmtn_makespan

    if makespan_delta <= 0:
        if logger is not None:
            logger.info(
                "calc_mcf_lb_and_derive_full_sch: round1 makespan=%d, "
                "ls_only_pmtn makespan=%d, delta=%d <= 0 — skipping adjust round",
                incumbent_makespan,
                ls_only_pmtn_makespan,
                makespan_delta,
            )
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            makespan_delta=makespan_delta,
            r2_skip_reason="delta_le_0",
        )

    if _stop_check():
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            makespan_delta=makespan_delta,
            r2_skip_reason="stop_guard",
        )

    n = instance.job_count
    m_last = instance.last_stage_mc_count
    r2_p_increment = math.ceil(makespan_delta * m_last / n) if adjust_p else 0
    r2_r_increment = math.ceil(makespan_delta / 2) if adjust_r else 0

    # ------------------- Round 2 -------------------
    try:
        r2_apply = apply_lb_by_mcf(
            instance,
            p_increment=r2_p_increment,
            r_increment=r2_r_increment,
            draw_heatmap=draw_pmtn_sch_heatmap,
            heatmap_sort=heatmap_sort,
            heatmap_yaml_path=r2_heatmap_yaml_path,
            stop_predicate=stop_predicate,
            logger=logger,
        )
    except MCFLBStopRequested:
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            makespan_delta=makespan_delta,
            r2_p_increment=r2_p_increment,
            r2_r_increment=r2_r_increment,
            r2_skip_reason="stop_guard",
        )

    r2_phase_schedules.append(("1_mcf_preemptive", r2_apply.mcf_preemptive_schedule))

    if _stop_check():
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_apply=r2_apply,
            makespan_delta=makespan_delta,
            r2_p_increment=r2_p_increment,
            r2_r_increment=r2_r_increment,
            r2_skip_reason="stop_guard",
        )

    r2_heuristic = heuristic_last_stage_only_from_mcf_lb(
        instance,
        r2_apply.mcf_preemptive_schedule,
        logger=logger,
        job_priority=job_placement_priority,
        placement_priority=last_stage_only_placement_criteria,
        p_increment=r2_p_increment,
        r_increment=r2_r_increment,
    )
    for label, sched in r2_heuristic.intermediate_schedules:
        r2_phase_schedules.append((f"2_{label}", sched))
    r2_phase_schedules.append(
        ("3_lastS_only_from_mcf_lb_after_sa_iti", r2_heuristic.schedule)
    )

    if _stop_check():
        return _build_result(
            best_schedule=s1_schedule,
            best_obj=s1_obj,
            r1_apply=r1_apply,
            r1_heuristic=r1_heuristic,
            r1_build_full=r1_build_full,
            r2_apply=r2_apply,
            r2_heuristic=r2_heuristic,
            makespan_delta=makespan_delta,
            r2_p_increment=r2_p_increment,
            r2_r_increment=r2_r_increment,
            r2_skip_reason="stop_guard",
        )

    r2_build_full = build_full_sch_from_last_stage_only_sch(
        instance,
        r2_heuristic.schedule,
        rebuild_last_stage_with_original_p=(r2_p_increment != 0),
        logger=logger,
    )
    r2_kept = dict(r2_build_full.intermediate_schedules)
    for offset, label in enumerate(_R2_BUILD_FULL_SCH_LABELS):
        sched = r2_kept.get(label)
        if sched is not None:
            r2_phase_schedules.append((f"{4 + offset}_{label}", sched))

    s2_schedule = r2_build_full.schedule
    s2_obj = r2_build_full.dispatched_obj

    best_schedule, best_obj = s1_schedule, s1_obj
    if s2_schedule is not None and (s1_schedule is None or s2_obj <= s1_obj):
        best_schedule, best_obj = s2_schedule, s2_obj

    return _build_result(
        best_schedule=best_schedule,
        best_obj=best_obj,
        r1_apply=r1_apply,
        r1_heuristic=r1_heuristic,
        r1_build_full=r1_build_full,
        r2_apply=r2_apply,
        r2_heuristic=r2_heuristic,
        r2_build_full=r2_build_full,
        makespan_delta=makespan_delta,
        r2_ran=True,
        r2_skip_reason=None,
        r2_p_increment=r2_p_increment,
        r2_r_increment=r2_r_increment,
    )
