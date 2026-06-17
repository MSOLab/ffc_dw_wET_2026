"""Last-stage-only schedule construction starting from an MCF preemptive LB.

Pure algorithm module — no controller / orchestration dependency. The
controller wires this in via a thin step method that supplies the
``mcf_preemptive_schedule`` and ``mcf_lb`` from prior subroutine state.

Single algorithm entry point:

  - ``heuristic_last_stage_only_from_mcf_lb``: build a midpoint warm-start
    across all jobs from the MCF preemptive window
    (``desired_start = (t_min + t_max - p_j) // 2``), then refine
    deterministically: ``make_semi_active`` left-shift on the last stage
    with upstream release times, followed by ``insert_idle_time`` at
    ET-optimal positions.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import Literal

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..pm_pmtn_sorter import PmPrmpSortKey
from .utils import (
    build_simple_stage_seed,
    insert_jobs_at_desired_starts,
    pm_pmtn_sort_job_sequence_with_log,
    window_map_from_preemptive_schedule,
)

__all__ = [
    "HeuristicLastStageOnlyResult",
    "heuristic_last_stage_only_from_mcf_lb",
    "simple_last_stage_only_from_mcf_lb",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class HeuristicLastStageOnlyResult:
    """Aggregate result of one heuristic last-stage-only construction."""

    schedule: FFcSchedule

    obj_value: float

    elapsed_time: float

    status: str

    intermediate_schedules: list[tuple[str, FFcSchedule]]
    """
    Ordered (label, schedule) snapshots for diagnostic Gantt rendering;
    callers append to e.g. ``self.mcf_lb_phase_schedules``.
    """


def heuristic_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    placement_priority: Literal["contrib", "dist"] = "contrib",
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
) -> HeuristicLastStageOnlyResult:
    """Build a midpoint warm-start across all jobs from the MCF preemptive
    LB and refine it heuristically (no CP solve): left-shift via
    :meth:`FFcSchedule.make_semi_active` on the last stage with upstream
    release times, then apply :meth:`FFcSchedule.insert_idle_time` to
    insert idle time at ET-optimal positions.

    The schedule remains last-stage-only (other stages stay empty); the
    caller is expected to extend it to a full schedule via the
    reverse-dispatch pipeline (the same downstream path used by the
    single-pass / NEH-CP variants).

    Args:
        p_increment: Integer ``>= 0``. When non-zero, the placement +
            heuristic refinement run on an *augmented* instance whose
            last-stage processing times are increased by ``p_increment``
            for every job. The returned schedule is feasible for the
            augmented problem only; downstream consumers (e.g.
            ``build_full_sch_from_last_stage_only_sch`` with
            ``rebuild_last_stage_with_original_p=True``) must rebuild it
            under original durations before reverse-dispatch. ``0``
            (default) preserves the current behaviour.
        r_multiplier: Scales the per-job release times used for both
            midpoint placement and the subsequent ``make_semi_active``
            left-shift; each value becomes ``ceil(r_j * r_multiplier)``.
            ``1.0`` (default) preserves the current behaviour.
        r_increment: Integer ``>= 0`` added to every release time
            *after* the ``r_multiplier`` scaling, so the effective
            release becomes ``ceil(r_j * r_multiplier) + r_increment``.
            ``0`` (default) preserves the current behaviour.
    """
    if p_increment < 0:
        raise ValueError(
            f"p_increment must be 0 or a positive integer; got {p_increment}."
        )
    if r_multiplier < 0:
        raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
    if r_increment < 0:
        raise ValueError(
            f"r_increment must be 0 or a positive integer; got {r_increment}."
        )
    log = logger or logging.getLogger(__name__)
    start = time.monotonic()

    if p_increment == 0:
        instance_for_solve = instance
    else:
        last_stage_id_for_aug = instance.stage_id_list[-1]
        instance_for_solve = FFcDDWParameters.with_stage_processing_time_increment(
            instance, last_stage_id_for_aug, p_increment
        )

    last_stage_id = instance_for_solve.stage_id_list[-1]
    duration_map = instance_for_solve.get_job_2_p_map_for_stage(last_stage_id)
    job_2_release_map = instance_for_solve.get_job_2_p_sum_except_last_stage()
    if r_multiplier != 1.0:
        job_2_release_map = {
            j: math.ceil(v * r_multiplier) for j, v in job_2_release_map.items()
        }
    if r_increment != 0:
        job_2_release_map = {j: v + r_increment for j, v in job_2_release_map.items()}

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance_for_solve.job_id_list
    )
    job_sequence = pm_pmtn_sort_job_sequence_with_log(
        window_map,
        duration_map,
        instance_for_solve,
        logger=log,
        job_priority=job_priority,
    )

    schedule = insert_jobs_at_desired_starts(
        None,
        instance_for_solve,
        stage_id=last_stage_id,
        job_2_release=job_2_release_map,
        duration_map=duration_map,
        window_map=window_map,
        appended=job_sequence,
        placement_priority=placement_priority,
    )
    before_sa_iti = schedule.deepcopy()

    schedule.make_semi_active(
        instance_for_solve.stage_2_job_2_p_map,
        start_from_stage=last_stage_id,
        job_2_release_map=job_2_release_map,
    )
    schedule.insert_idle_time(
        instance_for_solve.job_2_due_window_map,
        instance_for_solve.job_2_ewt_map,
        instance_for_solve.job_2_twt_map,
    )

    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance_for_solve)
    obj_value = float(sum_e + sum_t)

    elapsed = time.monotonic() - start
    return HeuristicLastStageOnlyResult(
        schedule=schedule,
        obj_value=obj_value,
        elapsed_time=elapsed,
        status="HEURISTIC",
        intermediate_schedules=[
            ("lastS_only_from_mcf_lb_before_sa_iti", before_sa_iti),
        ],
    )


def simple_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
) -> HeuristicLastStageOnlyResult:
    """Build a last-stage-only seed with the ``simple`` method (D1).

    Sort jobs by their MCF window ``t_max`` (native index tie-break) and
    greedily left-pack them on the last stage with the original processing
    times and the upstream processing-time sums as releases. No
    augmentation is applied (original ``p``); the only round-dependence is
    the ordering taken from the MCF window. The returned schedule is
    last-stage-only and original-feasible; the caller extends it to a full
    schedule via the reverse-dispatch pipeline.
    """
    _ = logger  # no logging in the simple seed path
    start = time.monotonic()

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    last_stage_id = instance.stage_id_list[-1]
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    job_2_release = instance.get_job_2_p_sum_except_last_stage()

    schedule = build_simple_stage_seed(
        instance,
        window_map,
        stage_id=last_stage_id,
        duration_map=duration_map,
        job_2_release=job_2_release,
    )

    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    obj_value = float(sum_e + sum_t)

    elapsed = time.monotonic() - start
    return HeuristicLastStageOnlyResult(
        schedule=schedule,
        obj_value=obj_value,
        elapsed_time=elapsed,
        status="HEURISTIC_SIMPLE",
        intermediate_schedules=[
            ("lastS_only_simple_seed", schedule),
        ],
    )
