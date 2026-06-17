"""Helpers for MCF-LB-driven job sequencing.

The actual sort logic lives in
:mod:`ffc_ddw_sum_et.algorithm.sort_keys.pm_pmtn_sort_job_sequence_from_window_map`
(SSOT). This module wraps it with two MCF-LB-specific concerns:

* a window-map builder for ``MCFPreemptiveSchedule`` (the stored, post-
  controller-discard form), and
* a logging-enabled wrapper that emits the rank-by-rank table consumed by
  the NEH-CP last-stage diagnostics.
"""

from __future__ import annotations

import logging
from typing import Literal, Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..pm_pmtn_sorter import PmPrmpSortKey, pm_pmtn_sort_job_sequence_from_window_map

__all__ = [
    "insert_jobs_at_desired_starts",
    "pm_pmtn_sort_job_sequence_with_log",
    "window_map_from_preemptive_schedule",
]


def window_map_from_preemptive_schedule(
    schedule: MCFPreemptiveSchedule,
    job_id_list: Sequence[str],
) -> dict[str, tuple[int, int] | None]:
    """For each job, return ``(min start, max end)`` across its segments.

    Jobs with no segments map to ``None``. Mirrors the contract of
    ``ParallelMachinePreemptionMcf.get_job_2_time_window_map`` but works
    off the stored preemptive schedule rather than the live MCF handle.
    """
    window_map: dict[str, tuple[int, int] | None] = {j: None for j in job_id_list}
    for _mc, job_id, start, end in schedule.segments:
        cur = window_map.get(job_id)
        if cur is None:
            window_map[job_id] = (start, end)
        else:
            window_map[job_id] = (min(cur[0], start), max(cur[1], end))
    return window_map


def pm_pmtn_sort_job_sequence_with_log(
    window_map: Mapping[str, tuple[int, int] | None],
    duration_map: Mapping[str, int],
    instance: FFcDDWParameters,
    *,
    logger: logging.Logger | None = None,
    log_level: int = logging.DEBUG,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
) -> list[str]:
    """Sort jobs by a :data:`PmPrmpSortKey`, with optional rank-by-rank logging.

    Delegates the sort to
    :func:`pm_pmtn_sort_job_sequence_from_window_map`. When ``logger`` is
    provided, emits a rank-by-rank table at ``log_level`` (default
    :data:`logging.DEBUG`) so a reader can verify the ordering.
    """
    sorted_jobs = pm_pmtn_sort_job_sequence_from_window_map(
        window_map, duration_map, instance, job_priority
    )

    if logger is not None:
        job_id_list = instance.job_id_list
        job_2_pos = {j: i for i, j in enumerate(job_id_list)}
        ewt = instance.job_2_ewt_map or dict.fromkeys(job_id_list, 1)
        twt = instance.job_2_twt_map or dict.fromkeys(job_id_list, 1)
        id_w: int = max(len(j) for j in job_id_list)
        logger.log(
            log_level,
            "MCF-induced job sequence "
            "(rank | %-*s | width | p_cj | width/p_cj | (w-+w+) | native_pos):",
            id_w,
            "job_id",
        )
        for rank, j in enumerate(sorted_jobs):
            window = window_map[j]
            width = (window[1] - window[0]) if window is not None else None
            ratio = (width / duration_map[j]) if width is not None else None
            logger.log(
                log_level,
                "  %4d | %-*s | %s | %4d | %s | %4d | %4d",
                rank,
                id_w,
                j,
                f"{width:>5}" if width is not None else f"{'None':>5}",
                duration_map[j],
                f"{ratio:>10.4f}" if ratio is not None else f"{'None':>10}",
                ewt[j] + twt[j],
                job_2_pos[j],
            )

    return sorted_jobs


def insert_jobs_at_desired_starts(
    base_sch: FFcSchedule | None,
    instance_for_extension: FFcDDWParameters,
    *,
    stage_id: str,
    job_2_release: Mapping[str, int],
    duration_map: Mapping[str, int],
    window_map: Mapping[str, tuple[int, int] | None],
    appended: Sequence[str],
    placement_priority: Literal["contrib", "dist"] = "contrib",
) -> FFcSchedule:
    """Build a fresh FFcSchedule on ``instance_for_extension``'s job set,
    copy ``base_sch``'s stage-``stage_id`` operations (when provided), then
    place each ``appended`` job at the midpoint of its MCF preemptive window
    on stage ``stage_id``.

    For job ``j`` with preemptive window ``(t_min, t_max)`` and stage
    duration ``p_j``:

      desired_start = max((t_min + t_max - p_j) // 2, job_2_release[j])

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
        for mc_id in base_sch.machines_per_stage[stage_id]:
            for job_id, start_time, end_time in base_sch.get_job_sequence(
                stage_id, mc_id
            ):
                if job_id in base_jobs:
                    new_sch.add_ops_times_2_mc(
                        stage_id=stage_id,
                        mc_id=mc_id,
                        job_id=job_id,
                        start_time=start_time,
                        end_time=end_time,
                    )

    machine_ids = instance_for_extension.stage_2_machines_map[stage_id]
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
        desired_start = max((t_min + t_max - p_j) // 2, job_2_release[job_id])
        desired_end = desired_start + p_j

        chosen_mc: str | None = None
        for mc_id in machine_ids:
            if _interval_free(new_sch, stage_id, mc_id, desired_start, desired_end):
                chosen_mc = mc_id
                break
        if chosen_mc is not None:
            new_sch.add_ops_times_2_mc(
                stage_id=stage_id,
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
                stage_id, mc_id, p_j, release_t=desired_start
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
                    stage_id, mc_id, p_j, upper_bound=desired_end
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
            stage_id=stage_id,
            mc_id=chosen_mc,
            job_id=job_id,
            start_time=chosen_start,
            end_time=chosen_end,
        )

    if no_window_jobs:
        new_sch.dispatch_stage_by_jobs(
            stage_id,
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
