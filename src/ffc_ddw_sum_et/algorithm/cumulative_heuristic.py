"""Cumulative heuristic for the last-stage-only scheduling problem.

A drop-in alternative to :func:`solve_last_stage_with_profile_fix` that
returns the same :class:`LastStageSolveResult` container. Starts from a
reference last-stage-only schedule, derives a job sequence, and refines it
by single-job reinsertion until no improvement is possible or the time
budget is exhausted.

Within each pass jobs are tried in descending order of their current
weighted E+T contribution, so high-impact jobs get priority.
"""

from __future__ import annotations

import logging
import time

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from ..solution.objectives import compute_weighted_earliness_tardiness
from .cumulative_routine import LastStageSolveResult

__all__ = ["solve_last_stage_by_cumulative_heuristic"]

_STATUS_NAME = "HEURISTIC"


def solve_last_stage_by_cumulative_heuristic(
    reference_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    last_stage_id: str,
    job_2_release: dict[str, int],
    obj_lb: float,
    *,
    logger: logging.Logger | None = None,
    time_limit_seconds: float | None = None,
    first_improvement_restart: bool = False,
    insert_radius: int | None = None,
) -> tuple[LastStageSolveResult, float, str, list[tuple[float, float]], dict]:
    """Refine a last-stage-only schedule by single-job reinsertion.

    Within each pass, jobs are sorted by descending weighted E+T contribution
    computed from the current incumbent schedule, so high-impact jobs are
    tried first.

    Args:
        reference_schedule: Last-stage-only schedule whose job sequence (sorted
            by start time, then end time, then instance job index) seeds the
            local search.
        instance: The FFc DDW instance.
        last_stage_id: Stage id whose operations are being refined.
        job_2_release: Per-job earliest start at the last stage (typically the
            sum of processing times on stages 1..c-1).
        obj_lb: Lower bound on the objective (e.g., the MCF LB) — copied into
            the returned result's ``bound`` field.
        time_limit_seconds: Wall-clock budget. The heuristic stops as soon as
            the elapsed time exceeds this value (checked before each job's
            position scan). ``None`` means no limit.
        first_improvement_restart: When True, restart the outer loop from the
            beginning of the sequence as soon as one job is moved to a better
            position. When False (default), complete a full pass over the
            sequence and restart only if any move was made.
        insert_radius: Maximum number of positions a job may move from its
            current position during a single reinsertion scan. ``None`` (the
            default) lets each job consider every other position in the
            sequence; setting a finite radius bounds the per-job work and
            biases moves to be local.

    Returns:
        ``(result, solve_sec, status_name, progress, scan_stats)`` where
        ``progress`` is a list of ``(elapsed_time, obj_value)`` pairs starting
        with the initial evaluation and one entry per accepted improvement, and
        ``scan_stats`` is ``{"mean_sec": float, "max_sec": float,
        "n_scans": int}`` aggregated across all job scans.
    """
    t_start = time.monotonic()

    def _elapsed() -> float:
        return time.monotonic() - t_start

    def _timed_out() -> bool:
        return time_limit_seconds is not None and _elapsed() >= time_limit_seconds

    job_2_inst_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    ops: list[tuple[str, int, int]] = []
    for _, s, e, j in reference_schedule.iter_operations_on_stage(last_stage_id):
        ops.append((j, s, e))
    sequence: list[str] = [
        j for j, _, _ in sorted(ops, key=lambda t: (t[1], t[2], job_2_inst_pos[t[0]]))
    ]

    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    due_window_map = instance.job_2_due_window_map
    ewt_map = instance.job_2_ewt_map
    twt_map = instance.job_2_twt_map

    def _evaluate(seq: list[str]) -> tuple[float, FFcSchedule]:
        sch = FFcSchedule(
            jobs=instance.job_id_list,
            stages=instance.stage_id_list,
            machines_per_stage=instance.stage_2_machines_map,
        )
        sch.dispatch_stage_by_jobs(
            last_stage_id,
            seq,
            duration_map,
            job_2_release=job_2_release,
            force_job_id_seq_as_priority=True,
        )
        sch.insert_idle_time(due_window_map, ewt_map, twt_map)
        e_sum, t_sum = compute_weighted_earliness_tardiness(sch, instance)
        return float(e_sum + t_sum), sch

    def _contribution(job: str, sch: FFcSchedule) -> float:
        c_j = sch.get_job_end_time(last_stage_id, job)
        d_lb, d_ub = due_window_map[job]
        w_e = ewt_map[job]
        w_t = twt_map[job]
        return float(w_e * max(d_lb - c_j, 0) + w_t * max(c_j - d_ub, 0))

    current_obj, current_sch = _evaluate(sequence)
    progress: list[tuple[float, float]] = [(_elapsed(), current_obj)]

    if logger is not None:
        logger.info(
            "cumulative_heuristic: initial seq obj=%.3f (mode=%s, n=%d, tl=%s)",
            current_obj,
            "first-improvement" if first_improvement_restart else "full-pass",
            len(sequence),
            f"{time_limit_seconds:.1f}s" if time_limit_seconds is not None else "none",
        )

    n = len(sequence)
    scan_times: list[float] = []
    n_passes = 0
    n_considered = 0
    n_moves = 0
    while not _timed_out():
        job_pass_order = sorted(
            sequence,
            key=lambda j: _contribution(j, current_sch),
            reverse=True,
        )
        job_2_seq_pos = {j: i for i, j in enumerate(sequence)}

        improved_in_pass = False
        for job in job_pass_order:
            if _timed_out():
                break
            current_pos = job_2_seq_pos[job]
            # Restrict insertion range by penalty direction:
            # - earliness (finish too early) → push later   → positions after current
            # - tardiness (finish too late)  → pull earlier → positions before current
            # - on-time (finish on time)     → stay         → skip
            c_j = current_sch.get_job_end_time(last_stage_id, job)
            d_lb, d_ub = due_window_map[job]
            if c_j < d_lb:
                range_end = (
                    n
                    if insert_radius is None
                    else min(n, current_pos + 1 + insert_radius)
                )
                pos_range = range(current_pos + 1, range_end)
            elif c_j > d_ub:
                range_start = (
                    0 if insert_radius is None else max(0, current_pos - insert_radius)
                )
                pos_range = range(range_start, current_pos)
            else:
                continue
            best_pos = current_pos
            best_obj = current_obj
            best_sch = current_sch
            t_scan = time.monotonic()
            for new_pos in pos_range:
                if _timed_out():
                    break
                trial = sequence[:current_pos] + sequence[current_pos + 1 :]
                trial.insert(new_pos, job)
                trial_obj, trial_sch = _evaluate(trial)
                if trial_obj < best_obj:
                    best_obj = trial_obj
                    best_pos = new_pos
                    best_sch = trial_sch
            scan_times.append(time.monotonic() - t_scan)
            n_considered += 1
            if best_pos != current_pos:
                sequence.pop(current_pos)
                sequence.insert(best_pos, job)
                job_2_seq_pos = {j: i for i, j in enumerate(sequence)}
                current_obj = best_obj
                current_sch = best_sch
                progress.append((_elapsed(), current_obj))
                improved_in_pass = True
                n_moves += 1
                if first_improvement_restart:
                    break
        n_passes += 1
        if not improved_in_pass:
            break

    elapsed = _elapsed()

    j_i_2_end = {
        (j, last_stage_id): current_sch.get_job_end_time(last_stage_id, j)
        for j in instance.job_id_list
    }
    makespan = max(j_i_2_end.values())

    result = LastStageSolveResult(
        status_name=_STATUS_NAME,
        schedule=current_sch,
        objective=current_obj,
        bound=obj_lb,
        j_i_2_end=j_i_2_end,
        makespan=makespan,
    )

    scan_stats: dict = (
        {
            "mean_sec": sum(scan_times) / len(scan_times),
            "max_sec": max(scan_times),
            "n_scans": len(scan_times),
            "n_passes": n_passes,
            "n_considered": n_considered,
            "n_moves": n_moves,
        }
        if scan_times
        else {
            "mean_sec": 0.0,
            "max_sec": 0.0,
            "n_scans": 0,
            "n_passes": n_passes,
            "n_considered": 0,
            "n_moves": 0,
        }
    )

    if logger is not None:
        logger.info(
            "cumulative_heuristic: final obj=%.3f, passes=%d, considered=%d, "
            "moves=%d, elapsed=%.3fs, scan mean=%.4fs max=%.4fs n=%d",
            current_obj,
            n_passes,
            n_considered,
            n_moves,
            elapsed,
            scan_stats["mean_sec"],
            scan_stats["max_sec"],
            scan_stats["n_scans"],
        )

    return result, elapsed, _STATUS_NAME, progress, scan_stats
