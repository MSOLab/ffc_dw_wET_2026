"""NEH-CP-style last-stage-only schedule construction from an MCF preemptive LB.

Pure algorithm module — no controller / orchestration dependency. The
controller wires this in via a thin step method that supplies the
`mcf_preemptive_schedule` and `mcf_lb` from prior subroutine state.

Algorithm:
  1. Sort jobs by ascending normalized window width
     ``(t_max_j - t_min_j) / p_{c,j}`` derived from the preemptive
     schedule's segments.
  2. Partition into batches of ``batch_size``.
  3. For each batch, solve the last-stage-only CP-SAT model on the
     sub-instance restricted to jobs accumulated so far. Step 0
     warm-starts from a dispatch on the first batch's jobs only;
     step k>0 warm-starts from step k-1's CP schedule with the new
     batch dispatched onto its tail.
  4. If ``total_tl_seconds`` runs out before all jobs are placed, the
     loop breaks early and any un-placed jobs are dispatched onto the
     last successful CP schedule so the returned schedule covers every
     job.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..cumulative import PFMethod
from ..cumulative_routine import (
    LastStageSolveResult,
    solve_last_stage_with_profile_fix,
)
from .utils import (
    jobs_sorted_by_normalized_window_width,
    window_map_from_preemptive_schedule,
)

__all__ = [
    "NehCpLastStageOnlyResult",
    "neh_cp_last_stage_only_from_mcf_lb",
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


def neh_cp_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
    batch_size: int = 5,
    cp_pf_method: PFMethod | None = "PF1",
    cp_solver_thread_cnt: int = 1,
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
    job_sequence = jobs_sorted_by_normalized_window_width(
        window_map, duration_map, instance, logger=log
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

        if last_cp_schedule is None:
            ref = FFcSchedule(
                jobs=sub_instance.job_id_list,
                stages=sub_instance.stage_id_list,
                machines_per_stage=sub_instance.stage_2_machines_map,
            )
            ref.dispatch_stage_by_jobs(
                last_stage_id,
                current_jobs,
                duration_map,
                job_2_release=sub_job_2_release,
                force_job_id_seq_as_priority=True,
            )
        else:
            ref = _extend_last_stage_schedule(
                last_cp_schedule,
                sub_instance,
                last_stage_id=last_stage_id,
                job_2_release=sub_job_2_release,
                duration_map=duration_map,
                appended=batch,
            )

        result, batch_solve_sec, batch_status = solve_last_stage_with_profile_fix(
            ref,
            sub_instance,
            last_stage_id,
            sub_job_2_release,
            logger=log,
            obj_lb=mcf_lb if is_last_step else None,
            pf_method=cp_pf_method,
            solver_thread_cnt=cp_solver_thread_cnt,
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
        final_schedule = _extend_last_stage_schedule(
            last_cp_schedule,
            instance,
            last_stage_id=last_stage_id,
            job_2_release=job_2_release_map,
            duration_map=duration_map,
            appended=remaining_jobs,
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


def _extend_last_stage_schedule(
    base_sch: FFcSchedule,
    instance_for_extension: FFcDDWParameters,
    last_stage_id: str,
    job_2_release: Mapping[str, int],
    duration_map: Mapping[str, int],
    appended: Sequence[str],
) -> FFcSchedule:
    """Build a fresh FFcSchedule on ``instance_for_extension``'s wider job
    set, copy ``base_sch``'s last-stage operations for the jobs already in
    ``base_sch.jobs``, then dispatch ``appended`` greedily onto the tail
    of the last stage in the given order.

    Used for batch step k>0 (where the new sub_instance has more jobs than
    the previous batch's CP schedule) and for the budget-exhausted
    re-dispatch fallback (where the parent instance has all jobs and we
    append whatever's left).
    """
    new_sch = FFcSchedule(
        jobs=instance_for_extension.job_id_list,
        stages=instance_for_extension.stage_id_list,
        machines_per_stage=instance_for_extension.stage_2_machines_map,
    )
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
    new_sch.dispatch_stage_by_jobs(
        last_stage_id,
        list(appended),
        duration_map,
        job_2_release=job_2_release,
        force_job_id_seq_as_priority=True,
    )
    return new_sch
