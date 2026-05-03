"""Last-stage-only schedule construction starting from an MCF preemptive LB.

Pure algorithm module — no controller / orchestration dependency. The
controller wires these in via thin step methods that supply the
``mcf_preemptive_schedule`` and ``mcf_lb`` from prior subroutine state.

Two algorithm entry points are exported:

  - ``single_pass_last_stage_only_from_mcf_lb``: build a midpoint
    warm-start across all jobs from the MCF preemptive window
    (``desired_start = (t_min + t_max - p_j) // 2``), then run a single
    profile-fix CP-SAT solve on the full job set. The midpoint
    warm-start is the profile-fix schedule, so its placement directly
    constrains the final solve.

  - ``neh_cp_last_stage_only_from_mcf_lb``: NEH-style batched
    construction. Jobs are sorted by ascending normalized window width
    ``(t_max - t_min) / p_{c,j}`` and partitioned into batches of
    ``batch_size``; each batch's CP-SAT solve is warm-started from the
    previous batch's CP schedule plus midpoint placements for the new
    batch. If the time budget is exhausted, un-placed jobs are
    re-dispatched onto the last successful CP schedule so the returned
    schedule still covers every job.

Both entry points share the placement helper
``_insert_jobs_at_desired_starts`` and use it with a configurable
``placement_priority`` lex-tiebreak between weighted-ET contribution
and start-time distance.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..cumulative import PFMethod
from ..cumulative_routine import (
    LastStageSolveResult,
    solve_last_stage_with_profile_fix,
)
from ..pm_pmtn_sorter import PmPrmpSortKey
from .utils import (
    pm_pmtn_sort_job_sequence_with_log,
    window_map_from_preemptive_schedule,
)

__all__ = [
    "NehCpLastStageOnlyResult",
    "heuristic_last_stage_only_from_mcf_lb",
    "neh_cp_last_stage_only_from_mcf_lb",
    "single_pass_last_stage_only_from_mcf_lb",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class NehCpLastStageOnlyResult:
    """Aggregate result of one NEH-CP last-stage-only construction."""

    schedule: FFcSchedule

    obj_value: float

    obj_bound: float
    """
    NOT a lower bound on the main (full-instance) problem.
    ``obj_bound`` is the CP-SAT ``best_objective_bound`` reported by the
    *last* successful NEH-CP iteration, which solves a sub-instance
    restricted to the jobs accumulated up to that step. It is therefore
    only valid against ``obj_value`` of that same iteration's
    sub-instance — useful for inspecting the UB - LB gap of the last
    NEH-CP iteration. Do NOT compare it to the full-instance objective
    or use it as a global LB.
    """

    elapsed_time: float

    cp_solve_sec: float

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
    r_multiplier: float = 1.0,
    r_increment: int = 0,
) -> NehCpLastStageOnlyResult:
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
        r_multiplier: Scales the per-job release times used for both
            midpoint placement and the subsequent ``make_semi_active``
            left-shift; each value becomes ``ceil(r_j * r_multiplier)``.
            ``1.0`` (default) preserves the current behaviour.
        r_increment: Integer ``>= 0`` added to every release time
            *after* the ``r_multiplier`` scaling, so the effective
            release becomes ``ceil(r_j * r_multiplier) + r_increment``.
            ``0`` (default) preserves the current behaviour.
    """
    if r_multiplier < 0:
        raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
    if r_increment < 0:
        raise ValueError(
            f"r_increment must be 0 or a positive integer; got {r_increment}."
        )
    log = logger or logging.getLogger(__name__)
    start = time.monotonic()

    last_stage_id = instance.stage_id_list[-1]
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()
    if r_multiplier != 1.0:
        job_2_release_map = {
            j: math.ceil(v * r_multiplier) for j, v in job_2_release_map.items()
        }
    if r_increment != 0:
        job_2_release_map = {j: v + r_increment for j, v in job_2_release_map.items()}

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    job_sequence = pm_pmtn_sort_job_sequence_with_log(
        window_map,
        duration_map,
        instance,
        logger=log,
        job_priority=job_priority,
    )

    schedule = _insert_jobs_at_desired_starts(
        None,
        instance,
        last_stage_id=last_stage_id,
        job_2_release=job_2_release_map,
        duration_map=duration_map,
        window_map=window_map,
        appended=job_sequence,
        placement_priority=placement_priority,
    )

    schedule.make_semi_active(
        instance.stage_2_job_2_p_map,
        start_from_stage=last_stage_id,
        job_2_release_map=job_2_release_map,
    )
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )

    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    obj_value = float(sum_e + sum_t)

    elapsed = time.monotonic() - start
    return NehCpLastStageOnlyResult(
        schedule=schedule,
        obj_value=obj_value,
        # Heuristic refinement does not produce a CP-style bound.
        obj_bound=0.0,
        elapsed_time=elapsed,
        cp_solve_sec=0.0,
        status="HEURISTIC",
        intermediate_schedules=[],
    )


def single_pass_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    placement_priority: Literal["contrib", "dist"] = "contrib",
    pf_method: PFMethod | None = "PF1",
    solver_thread_cnt: int = 1,
    total_tl_seconds: float | None = None,
    mcf_lb: float | None = None,
    log_cp_search_progress: bool = False,
    solver_log_path_getter: Callable[[str], Path] | None = None,
) -> NehCpLastStageOnlyResult:
    """Build a midpoint warm-start across all jobs from the MCF preemptive
    LB and run one profile-fix CP-SAT solve. The midpoint placement is
    the profile-fix schedule, so ``placement_priority`` directly steers
    the final solve (not just a hint).
    """
    log = logger or logging.getLogger(__name__)
    start = time.monotonic()

    last_stage_id = instance.stage_id_list[-1]
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    job_sequence = pm_pmtn_sort_job_sequence_with_log(
        window_map,
        duration_map,
        instance,
        logger=log,
        job_priority=job_priority,
    )

    ref = _insert_jobs_at_desired_starts(
        None,
        instance,
        last_stage_id=last_stage_id,
        job_2_release=job_2_release_map,
        duration_map=duration_map,
        window_map=window_map,
        appended=job_sequence,
        placement_priority=placement_priority,
    )

    result, solve_sec, status_name = solve_last_stage_with_profile_fix(
        ref,
        instance,
        last_stage_id,
        job_2_release_map,
        logger=log,
        obj_lb=mcf_lb,
        pf_method=pf_method,
        solver_thread_cnt=solver_thread_cnt,
        repeat_while_improving=False,
        max_time_in_seconds=total_tl_seconds,
        log_search_progress=log_cp_search_progress,
        solver_log_path_getter=solver_log_path_getter,
        profile_fix_schedule=ref,
    )
    if result is None:
        raise RuntimeError(
            f"single_pass_last_stage_only_from_mcf_lb: "
            f"CP returned {status_name} on the full job set."
        )

    elapsed = time.monotonic() - start
    return NehCpLastStageOnlyResult(
        schedule=result.schedule,
        obj_value=result.objective,
        # CP-SAT best_objective_bound from the single profile-fix solve;
        # valid against result.objective on the full instance if pf_method is None,
        # otherwise not a global LB.
        obj_bound=float(result.bound),
        elapsed_time=elapsed,
        cp_solve_sec=solve_sec,
        status=status_name,
        intermediate_schedules=[],
    )


def neh_cp_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    hint_placement_priority: Literal["contrib", "dist"] = "contrib",
    batch_size: int = 5,
    pf_method: PFMethod | None = "PF1",
    solver_thread_cnt: int = 1,
    total_tl_seconds: float | None = None,
    mcf_lb: float | None = None,
    log_cp_search_progress: bool = False,
    solver_log_path_getter: Callable[[str], Path] | None = None,
) -> NehCpLastStageOnlyResult:
    """Build a last-stage-only NEH-CP schedule starting from the MCF
    preemptive LB.

    See module docstring for the algorithm outline.
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive; got {batch_size}.")

    log = logger or logging.getLogger(__name__)
    start = time.monotonic()

    last_stage_id = instance.stage_id_list[-1]
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    job_sequence = pm_pmtn_sort_job_sequence_with_log(
        window_map,
        duration_map,
        instance,
        logger=log,
        job_priority=job_priority,
    )
    n = len(job_sequence)
    batches = [job_sequence[i : i + batch_size] for i in range(0, n, batch_size)]

    current_jobs: list[str] = []
    last_cp_schedule: FFcSchedule | None = None
    last_result: LastStageSolveResult | None = None
    last_status: str = "NO_BATCH_SOLVED"
    cumulative_solve_sec = 0.0
    intermediate: list[tuple[str, FFcSchedule]] = []

    max_step_count = len(batches)
    if total_tl_seconds is not None:
        batch_tl_seconds = total_tl_seconds / max_step_count
    else:
        batch_tl_seconds = None

    for step, batch in enumerate(batches):
        is_first_step = step == 0
        is_last_step = step == max_step_count - 1

        current_jobs.extend(batch)
        sub_instance = FFcDDWParameters.create_instance_of_job_subset(
            instance, set(current_jobs)
        )
        sub_job_2_release = {j: job_2_release_map[j] for j in current_jobs}

        ref = _insert_jobs_at_desired_starts(
            last_cp_schedule,
            sub_instance,
            last_stage_id=last_stage_id,
            job_2_release=sub_job_2_release,
            duration_map=duration_map,
            window_map=window_map,
            appended=current_jobs if last_cp_schedule is None else batch,
            placement_priority=hint_placement_priority,
        )

        result, batch_solve_sec, batch_status = solve_last_stage_with_profile_fix(
            ref,
            sub_instance,
            last_stage_id,
            sub_job_2_release,
            logger=log,
            obj_lb=mcf_lb if is_last_step else None,
            pf_method=pf_method,
            solver_thread_cnt=solver_thread_cnt,
            repeat_while_improving=False,
            max_time_in_seconds=batch_tl_seconds,
            log_search_progress=log_cp_search_progress,
            solver_log_path_getter=solver_log_path_getter,
            profile_fix_schedule=None if is_first_step else last_cp_schedule,
        )
        cumulative_solve_sec += batch_solve_sec
        last_status = batch_status
        if result is None:
            log.warning(
                "neh_cp_last_stage_only step %d: CP returned %s; stopping batch loop.",
                step,
                batch_status,
            )
            break
        last_result = result
        last_cp_schedule = result.schedule
        log.info(
            "neh_cp_last_stage_only step %d: status=%s, jobs=%d/%d, obj=%.2f, "
            "batch_sec=%.2f.",
            step,
            batch_status,
            len(current_jobs),
            n,
            float(result.objective),
            batch_solve_sec,
        )
        intermediate.append(
            (f"2_neh_cp_step_{step:03d}_last_stage_only", last_cp_schedule)
        )

    if last_result is None or last_cp_schedule is None:
        raise RuntimeError(
            "neh_cp_last_stage_only_from_mcf_lb produced no schedule; "
            f"last_status={last_status}."
        )

    placed = set(current_jobs)
    remaining_jobs = [j for j in job_sequence if j not in placed]
    if remaining_jobs:
        final_schedule = _insert_jobs_at_desired_starts(
            last_cp_schedule,
            instance,
            last_stage_id=last_stage_id,
            job_2_release=job_2_release_map,
            duration_map=duration_map,
            window_map=window_map,
            appended=remaining_jobs,
            placement_priority=hint_placement_priority,
        )
        se, st = compute_weighted_earliness_tardiness(final_schedule, instance)
        final_obj = float(se + st)
        final_status = f"{last_result.status_name}+RE_DISPATCH"
        log.info(
            "neh_cp_last_stage_only: re-dispatched %d remaining jobs; final obj=%.2f.",
            len(remaining_jobs),
            final_obj,
        )
    else:
        final_schedule = last_result.schedule
        final_obj = float(last_result.objective)
        final_status = last_result.status_name

    intermediate.append(("3_neh_cp_last_stage_only_final", final_schedule))
    elapsed = time.monotonic() - start
    return NehCpLastStageOnlyResult(
        schedule=final_schedule,
        obj_value=final_obj,
        # See dataclass docstring: this is the LAST NEH-CP iteration's
        # sub-instance CP LB, not a global bound on the full problem.
        obj_bound=float(last_result.bound),
        elapsed_time=elapsed,
        cp_solve_sec=cumulative_solve_sec,
        status=final_status,
        intermediate_schedules=intermediate,
    )


def _insert_jobs_at_desired_starts(
    base_sch: FFcSchedule | None,
    instance_for_extension: FFcDDWParameters,
    *,
    last_stage_id: str,
    job_2_release: Mapping[str, int],
    duration_map: Mapping[str, int],
    window_map: Mapping[str, tuple[int, int] | None],
    appended: Sequence[str],
    placement_priority: Literal["contrib", "dist"] = "contrib",
) -> FFcSchedule:
    """Build a fresh FFcSchedule on ``instance_for_extension``'s job set,
    copy ``base_sch``'s last-stage operations (when provided), then place
    each ``appended`` job at the midpoint of its MCF preemptive window.

    For job ``j`` with preemptive window ``(t_min, t_max)`` and last-stage
    duration ``p_j``:

      desired_start = max((t_min + t_max - p_j) // 2, 0)

    1. If ``[desired_start, desired_start + p_j)`` is free on at least one
       machine (in ``machines_per_stage`` order), place there.
    2. Otherwise, build two candidates across machines:
       (A) earliest feasible start ``>= desired_start``;
       (B) latest feasible end ``<= desired_start + p_j`` (may be missing).
       Pick between them by lex-tiebreak on ``placement_priority``:
         - ``"contrib"``: ``(weighted_ET_contrib, start_distance)`` —
           minimize the weighted-ET contribution first, break ties by
           start distance from ``desired_start``.
         - ``"dist"``: ``(start_distance, weighted_ET_contrib)`` —
           minimize start distance first, break ties by weighted-ET
           contribution.

    Jobs whose ``window_map[j]`` is ``None`` skip the desired-start logic
    and are appended via greedy tail-dispatch at the end.
    """
    if placement_priority not in ("contrib", "dist"):
        raise ValueError(
            f"Invalid placement_priority {placement_priority}; "
            "must be 'contrib' or 'dist'."
        )

    new_sch = FFcSchedule(
        jobs=instance_for_extension.job_id_list,
        stages=instance_for_extension.stage_id_list,
        machines_per_stage=instance_for_extension.stage_2_machines_map,
    )
    if base_sch is not None:
        base_jobs = set(base_sch.jobs)
        for mc_id in base_sch.machines_per_stage[last_stage_id]:
            for job_id, start_time, end_time in base_sch.get_job_sequence(
                last_stage_id, mc_id
            ):
                if job_id in base_jobs:
                    new_sch.add_ops_times_2_mc(
                        stage_id=last_stage_id,
                        mc_id=mc_id,
                        job_id=job_id,
                        start_time=start_time,
                        end_time=end_time,
                    )

    machine_ids = instance_for_extension.stage_2_machines_map[last_stage_id]
    ewt_map = instance_for_extension.job_2_ewt_map
    twt_map = instance_for_extension.job_2_twt_map
    due_map = instance_for_extension.job_2_due_window_map

    no_window_jobs: list[str] = []
    for job_id in appended:
        window = window_map[job_id]
        p_j = duration_map[job_id]
        if window is None:
            no_window_jobs.append(job_id)
            continue
        t_min, t_max = window
        # TODO: 0 -> job_2_release[job_id]
        desired_start = max((t_min + t_max - p_j) // 2, 0)
        desired_end = desired_start + p_j

        chosen_mc: str | None = None
        for mc_id in machine_ids:
            if _interval_free(
                new_sch, last_stage_id, mc_id, desired_start, desired_end
            ):
                chosen_mc = mc_id
                break
        if chosen_mc is not None:
            new_sch.add_ops_times_2_mc(
                stage_id=last_stage_id,
                mc_id=chosen_mc,
                job_id=job_id,
                start_time=desired_start,
                end_time=desired_end,
            )
            continue

        # 2-2-1: best earliest-start across machines (>= desired_start).
        es_best: tuple[int, int, str] | None = None
        for idx, mc_id in enumerate(machine_ids):
            es = new_sch.get_machine_earliest_start_time(
                last_stage_id, mc_id, p_j, release_t=desired_start
            )
            cand = (es, idx, mc_id)
            if es_best is None or cand < es_best:
                es_best = cand
        assert es_best is not None
        es_start, _, es_mc = es_best
        end_a = es_start + p_j

        # 2-2-2: best latest-end across machines (<= desired_end), or None.
        # Tuple shape: (-le_end, idx, le_start, le_end, mc_id) so that the
        # smallest tuple corresponds to the largest le_end with machine
        # list order as tie-break.
        le_best: tuple[int, int, int, int, str] | None = None
        for idx, mc_id in enumerate(machine_ids):
            try:
                le_start, le_end = new_sch._get_latest_feasible_slot_on_machine(
                    last_stage_id, mc_id, p_j, upper_bound=desired_end
                )
            except ValueError:
                continue
            if le_start < job_2_release[job_id]:
                # If the latest feasible slot starts before the job's release time,
                # it won't be a valid candidate.
                continue
            cand = (-le_end, idx, le_start, le_end, mc_id)
            if le_best is None or cand < le_best:
                le_best = cand

        ewt = ewt_map[job_id]
        twt = twt_map[job_id]
        d_lo, d_hi = due_map[job_id]
        contrib_a = ewt * max(d_lo - end_a, 0) + twt * max(end_a - d_hi, 0)
        dist_a = abs(es_start - desired_start)

        if le_best is None:
            chosen_start, chosen_end, chosen_mc = es_start, end_a, es_mc
        else:
            _, _, le_start, le_end, le_mc = le_best
            contrib_b = ewt * max(d_lo - le_end, 0) + twt * max(le_end - d_hi, 0)
            dist_b = abs(le_start - desired_start)
            if placement_priority == "contrib":
                criteria_a = (contrib_a, dist_a)
                criteria_b = (contrib_b, dist_b)
            elif placement_priority == "dist":
                criteria_a = (dist_a, contrib_a)
                criteria_b = (dist_b, contrib_b)
            if criteria_a <= criteria_b:
                chosen_start, chosen_end, chosen_mc = es_start, end_a, es_mc
            else:
                chosen_start, chosen_end, chosen_mc = le_start, le_end, le_mc

        new_sch.add_ops_times_2_mc(
            stage_id=last_stage_id,
            mc_id=chosen_mc,
            job_id=job_id,
            start_time=chosen_start,
            end_time=chosen_end,
        )

    if no_window_jobs:
        new_sch.dispatch_stage_by_jobs(
            last_stage_id,
            no_window_jobs,
            duration_map,
            job_2_release=job_2_release,
            force_job_id_seq_as_priority=True,
        )
    return new_sch


def _interval_free(
    sch: FFcSchedule,
    stage_id: str,
    mc_id: str,
    start: int,
    end: int,
) -> bool:
    for _, op_start, op_end in sch.get_job_sequence(stage_id, mc_id):
        if op_start >= end:
            break
        if op_end > start:
            return False
    return True
