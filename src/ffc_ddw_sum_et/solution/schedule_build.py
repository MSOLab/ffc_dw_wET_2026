"""Build an ``FFcSchedule`` from CP-SAT operation start/end times."""

from __future__ import annotations

from typing import Sequence

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffc_schedule import FFcSchedule

__all__ = [
    "build_schedule_from_op_starts",
    "reconstruct_raw_coarse_schedule",
    "reconstruct_coarse_schedule",
]


def build_schedule_from_op_starts(
    instance: FFcDDWParameters,
    j_i_2_start: dict[tuple[str, str], int],
    j_i_2_end: dict[tuple[str, str], int],
    stages: Sequence[str] | None = None,
    jobs: Sequence[str] | None = None,
) -> FFcSchedule:
    """Greedy interval-graph coloring to assign machines from CP-SAT starts.

    The cumulative constraint at each stage caps concurrent intervals at
    ``|M_i|``, so a free machine is always available at any operation's
    start time. ``stages`` restricts the loop to a subset of stages; other
    stages remain empty in the returned schedule. ``jobs`` restricts the
    loop to a subset of jobs (the returned schedule still uses
    ``instance.job_id_list`` so missing jobs can be appended later).
    """
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    job_ids = list(jobs) if jobs is not None else instance.job_id_list
    for i in stages if stages is not None else instance.stage_id_list:
        machines = list(instance.stage_2_machines_map[i])
        machine_end: dict[str, int] = {k: 0 for k in machines}
        ordered_jobs = sorted(
            job_ids,
            key=lambda j: (j_i_2_start[j, i], j_i_2_end[j, i], j),
        )
        for j in ordered_jobs:
            s = j_i_2_start[j, i]
            e = j_i_2_end[j, i]
            picked = next((k for k in machines if machine_end[k] <= s), None)
            if picked is None:
                raise RuntimeError(
                    f"No free machine at stage {i} for job {j} start={s}"
                )
            schedule.add_ops_times_2_mc(i, picked, j, s, e)
            machine_end[picked] = e
    return schedule


def reconstruct_raw_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    factor: int,
) -> FFcSchedule:
    """Scale a coarse-scale schedule onto the original time scale (no postprocess).

    Scales each operation's start time up by ``factor`` and reapplies the
    original processing times (``end = start * factor + original_p``), then
    rebuilds on the original-scale instance. The result is the *raw*
    reconstruction **before** ``make_semi_active`` / ``insert_idle_time`` — use
    :func:`reconstruct_coarse_schedule` for the ET-aligned schedule, or follow
    this call with those two steps when the raw snapshot must be kept distinct.

    The coarse schedule's origin (CP solver, dispatch, etc.) is irrelevant —
    this function only consumes ``(job, stage) → start`` mappings.
    """
    original_p = instance.job_2_stage_2_p_map

    # Extract (job, stage) → start from the coarse schedule.
    # get_jik_2_start_time_map returns (job, stage, machine) → start;
    # deduplicate by dropping the machine key (one machine per operation).
    jik_2_start = coarse_schedule.get_jik_2_start_time_map()
    ji_2_start: dict[tuple[str, str], int] = {}
    for (j, i, _mc), t in jik_2_start.items():
        ji_2_start[j, i] = t

    reconstructed_start: dict[tuple[str, str], int] = {
        (j, i): ji_2_start[j, i] * factor for (j, i) in ji_2_start
    }
    reconstructed_end: dict[tuple[str, str], int] = {
        (j, i): reconstructed_start[j, i] + original_p[j][i]
        for (j, i) in reconstructed_start
    }

    return build_schedule_from_op_starts(
        instance, reconstructed_start, reconstructed_end
    )


def reconstruct_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    factor: int,
) -> FFcSchedule:
    """Reconstruct a coarse-scale schedule onto the original time scale.

    Thin wrapper over :func:`reconstruct_raw_coarse_schedule`: builds the raw
    reconstruction, then runs ``make_semi_active`` and ``insert_idle_time`` on
    the original-scale instance to land operations at ET-optimal positions.
    """
    schedule = reconstruct_raw_coarse_schedule(coarse_schedule, instance, factor)
    schedule.make_semi_active(instance.stage_2_job_2_p_map)
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return schedule
