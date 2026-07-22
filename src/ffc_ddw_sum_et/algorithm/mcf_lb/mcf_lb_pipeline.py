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
    wET; ties favour r2.

    ``final_obj_bound`` is ``r1_apply.mcf_lb`` when r1's LP solved and
    ``time_factor == 1`` — that bound is valid on the original instance
    regardless of any r2 augmentation. When ``time_factor > 1`` (CSR coarse
    mode) it is ``None`` and ``lb_suppressed_by_time_factor`` is ``True``:
    the coarse-problem MCF lower bound is not exactly re-derived because the
    arc-cost construction (``algorithm/parallel_mc_pmtn.py``) is outside this
    package (plan 20260711 §3 LB-soundness fallback). The pipeline still
    derives and scores every schedule at the coarse scale; only the bound is
    suppressed. ``time_factor`` echoes the value the pipeline ran with.

    ``r1_phase_schedules`` and ``r2_phase_schedules`` are pre-numbered
    (``"<n>_<label>"``). r1 has up to 8 entries (1..8); r2 has up to 9
    entries (1..9). The orchestration wrapper iterates them directly
    when emitting per-round JSON artifacts.
    """

    best_schedule: FFcSchedule | None
    best_obj: float | None
    final_obj_bound: float | None
    time_factor: int
    lb_suppressed_by_time_factor: bool
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
    time_factor: int = 1,
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

    ``time_factor`` (CSR coarse-grid scale, ``>= 1``) is threaded into the
    heuristic and full-schedule builders so every schedule is positioned and
    scored as if a coarse completion ``C^c`` were ``time_factor * C^c``. It is
    **not** threaded into ``apply_lb_by_mcf``: the MCF arc-cost construction
    (``algorithm/parallel_mc_pmtn.py``) is outside this package, so in coarse
    mode the returned ``apply.mcf_lb`` is not a re-derived coarse bound. The
    composite suppresses it (reports ``final_obj_bound=None``) for
    ``time_factor > 1``.
    """
    if time_factor < 1:
        raise ValueError(f"time_factor must be >= 1, got {time_factor}")
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
        time_factor=time_factor,
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
        time_factor=time_factor,
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
    p_adjust_coeff: float = 1.0,
    r_adjust_coeff: float = 0.5,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    last_stage_rebuild_config: Literal[
        "original_pr", "increased_pr", "best"
    ] = "increased_pr",
    time_factor: int = 1,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbR2Result:
    """Run round 2 (with delta-derived augmentation) of the pipeline.

    Pipeline: ``apply_lb_by_mcf`` (with ``p_increment`` / ``r_increment``,
    always augmented) → ``heuristic_last_stage_only_from_mcf_lb`` →
    ``build_full_sch_from_last_stage_only_sch``.

    The round-2 MCF LB (``apply``) is always computed on the augmented
    instance. ``last_stage_rebuild_config`` only selects how the
    last-stage schedule fed to reverse-dispatch is *generated*:

    - ``"increased_pr"`` (default): generate the last-stage schedule with the
      *increased* p/r (``p_increment`` / ``r_increment``), then rebuild it
      back to the original last-stage processing times while preserving
      completion times (``rebuild_last_stage_with_original_p=(p_increment
      != 0)``; inter-op gaps appear) before reverse-dispatch. This is the
      historical behaviour.
    - ``"original_pr"``: generate the last-stage schedule with the *original*
      p/r (increments not applied to the heuristic) and reverse-dispatch it
      directly (no rebuild).
    - ``"best"``: run both variants and keep the one whose pre-unflip
      makespan (``BuildFullSchResult.before_unflip_makespan``) is smaller;
      ties keep ``"original_pr"``.

    Both ``"original_pr"`` and ``"increased_pr"`` yield problem-feasible final
    schedules (the original-p durations are present after generation or
    after the rebuild, respectively), including single-stage instances
    where reverse-dispatch's ``make_semi_active`` is skipped.

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

    ``time_factor`` (CSR coarse-grid scale, ``>= 1``) is forwarded to the
    heuristic and full-schedule builders (same semantics as round 1). The
    ``makespan_delta`` / ``p_increment`` / ``r_increment`` augmentation math
    is on the coarse grid (a difference of coarse makespans, coarse durations
    and releases) and is scale-free — ``time_factor`` does not enter it.
    """
    if time_factor < 1:
        raise ValueError(f"time_factor must be >= 1, got {time_factor}")
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

    # The round-2 MCF LB (``apply``) is always computed on the augmented
    # instance. ``last_stage_rebuild_config`` only selects how the
    # last-stage schedule fed to reverse-dispatch is generated:
    #   "original_pr": generate with the *original* p/r (no increments) and
    #               reverse-dispatch directly (no rebuild).
    #   "increased_pr": generate with the *increased* p/r, then restore the
    #               original last-stage processing times while preserving
    #               completion times (rebuild; inter-op gaps appear) before
    #               reverse-dispatch. This is the historical default.
    #   "best":     run both and keep the one whose pre-unflip makespan is
    #               smaller.
    # ``"increased_pr"`` rebuilds to original p only when there is a p inflation
    # to undo; with ``p_increment == 0`` the rebuild is a no-op identity.
    modified_rebuild = p_increment != 0

    def _run_heuristic(*, use_increments: bool) -> HeuristicLastStageOnlyResult:
        return heuristic_last_stage_only_from_mcf_lb(
            instance,
            apply.mcf_preemptive_schedule,
            logger=logger,
            job_priority=job_placement_priority,
            placement_priority=last_stage_only_placement_criteria,
            p_increment=p_increment if use_increments else 0,
            r_increment=r_increment if use_increments else 0,
            time_factor=time_factor,
        )

    def _run_build(
        heuristic_schedule: FFcSchedule, *, rebuild: bool
    ) -> BuildFullSchResult:
        return build_full_sch_from_last_stage_only_sch(
            instance,
            heuristic_schedule,
            rebuild_last_stage_with_original_p=rebuild,
            time_factor=time_factor,
            logger=logger,
        )

    def _record_heuristic_phases(h: HeuristicLastStageOnlyResult) -> None:
        for label, sched in h.intermediate_schedules:
            phase_schedules.append((f"2_{label}", sched))
        phase_schedules.append(("3_lastS_only_from_mcf_lb_after_sa_iti", h.schedule))

    if last_stage_rebuild_config == "best":
        heuristic_orig = _run_heuristic(use_increments=False)
        if _stop_check():
            _record_heuristic_phases(heuristic_orig)
            return _build(
                stop_reason="stop_guard", apply=apply, heuristic=heuristic_orig
            )
        build_orig = _run_build(heuristic_orig.schedule, rebuild=False)

        heuristic_mod = _run_heuristic(use_increments=True)
        if _stop_check():
            _record_heuristic_phases(heuristic_mod)
            return _build(
                stop_reason="stop_guard", apply=apply, heuristic=heuristic_mod
            )
        build_mod = _run_build(heuristic_mod.schedule, rebuild=modified_rebuild)

        # Select by pre-unflip makespan (smaller wins). If one build failed,
        # keep the other; ties and double-failures keep ``"original_pr"``.
        mk_o = build_orig.before_unflip_makespan
        mk_m = build_mod.before_unflip_makespan
        if build_mod.schedule is None:
            heuristic, build_full, rebuild_used = heuristic_orig, build_orig, False
        elif build_orig.schedule is None:
            heuristic, build_full, rebuild_used = (
                heuristic_mod,
                build_mod,
                modified_rebuild,
            )
        elif mk_m is not None and (mk_o is None or mk_m < mk_o):
            heuristic, build_full, rebuild_used = (
                heuristic_mod,
                build_mod,
                modified_rebuild,
            )
        else:
            heuristic, build_full, rebuild_used = heuristic_orig, build_orig, False
        _record_heuristic_phases(heuristic)
    else:
        use_increments = last_stage_rebuild_config == "increased_pr"
        rebuild_used = modified_rebuild if use_increments else False
        heuristic = _run_heuristic(use_increments=use_increments)
        _record_heuristic_phases(heuristic)
        if _stop_check():
            return _build(stop_reason="stop_guard", apply=apply, heuristic=heuristic)
        build_full = _run_build(heuristic.schedule, rebuild=rebuild_used)

    r2_kept = dict(build_full.intermediate_schedules)
    # ``lastS_only_before_rs`` equals label 3 unless the winning variant
    # actually rebuilt the seed with the original ``p`` — only possible
    # when ``rebuild_used`` and ``p_increment != 0``. Drop the duplicate
    # otherwise (mirrors r1's drop). Index 4 simply isn't recorded in the
    # duplicate case; later labels keep their indices 5..9 via
    # ``enumerate`` over the full ``_R2_BUILD_FULL_SCH_LABELS`` tuple.
    if not (rebuild_used and p_increment != 0):
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
    makespan_delta_ref: Literal[
        "mcfLbMakespan", "lastStageOnlyMakespan"
    ] = "mcfLbMakespan",
    adjust_p: bool = False,
    adjust_r: bool = False,
    p_adjust_coeff: float = 1.0,
    r_adjust_coeff: float = 0.5,
    last_stage_rebuild_config: Literal[
        "original_pr", "increased_pr", "best"
    ] = "increased_pr",
    proceed_r2_when_nonpositive_cmax: bool = False,
    time_factor: int = 1,
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
        last_stage_rebuild_config: Round-2 last-stage generation policy.
            ``"increased_pr"`` (default) generates the last-stage schedule with
            the increased p/r and rebuilds it to original ``p`` (preserving
            completion times) before reverse-dispatch — the historical
            behaviour. ``"original_pr"`` generates with the original p/r and
            reverse-dispatches directly. ``"best"`` runs both and keeps the
            smaller pre-unflip makespan. See
            ``calc_mcf_lb_r2_and_derive_full_sch`` for details.
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
        proceed_r2_when_nonpositive_cmax: When False (default), the
            historical ``delta_le_0`` skip applies (see above). When True,
            that skip is bypassed.
        time_factor: CSR coarse-grid scale ``>= 1``. When ``> 1``, the
            instance is a coarsened one (coarse processing times, original
            due windows); a coarse completion ``C^c`` is interpreted as
            original-scale ``time_factor * C^c`` when scored against the due
            window. It is threaded into every schedule-construction and
            objective-evaluation path (heuristic placement tie-break,
            ``insert_idle_time``, pre-flip delay cap, and
            ``compute_weighted_earliness_tardiness``), so ``best_obj`` is the
            coarse-scale wET of ``best_schedule``. **LB soundness (plan
            20260711 §3):** the MCF lower bound is *not* re-derived for the
            coarse scale — the arc-cost construction lives in
            ``algorithm/parallel_mc_pmtn.py`` (outside this package). For
            ``time_factor > 1`` the pipeline reports ``final_obj_bound=None``
            and sets ``lb_suppressed_by_time_factor=True``. ``1`` (default)
            reproduces current behaviour exactly.
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
    if time_factor < 1:
        raise ValueError(f"time_factor must be >= 1, got {time_factor}")

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
        # LB soundness fallback (plan 20260711 §3): for time_factor > 1 the
        # coarse-problem MCF bound is not re-derived (arc costs live outside
        # this package), so suppress it. At time_factor == 1 the r1 MCF LB is
        # a valid global bound and is reported unchanged.
        lb_suppressed = time_factor > 1
        if lb_suppressed:
            final_obj_bound = None
        else:
            final_obj_bound = r1.apply.mcf_lb if r1.apply is not None else None
        return CalcMcfLbAndDeriveFullSchResult(
            best_schedule=best_schedule,
            best_obj=best_obj,
            final_obj_bound=final_obj_bound,
            time_factor=time_factor,
            lb_suppressed_by_time_factor=lb_suppressed,
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
        time_factor=time_factor,
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
        last_stage_rebuild_config=last_stage_rebuild_config,
        time_factor=time_factor,
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
