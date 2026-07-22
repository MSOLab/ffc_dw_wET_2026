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


def _validate_coarse_schedule_covers_instance(
    coarse_schedule: FFcSchedule, instance: FFcDDWParameters
) -> None:
    """Raise if ``coarse_schedule`` omits any ``(job, stage)`` operation."""
    present = {(j, i) for (j, i, _mc) in coarse_schedule.get_jik_2_start_time_map()}
    missing = [
        (j, i)
        for i in instance.stage_id_list
        for j in instance.job_id_list
        if (j, i) not in present
    ]
    if missing:
        shown = ", ".join(f"({j}, {i})" for j, i in missing[:5])
        suffix = f", … (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        raise ValueError(
            f"Coarse schedule is missing {len(missing)} of "
            f"{len(instance.job_id_list) * len(instance.stage_id_list)} "
            f"(job, stage) operations: {shown}{suffix}. Reconstruction would "
            f"drop them silently and score a truncated schedule as complete."
        )


def reconstruct_raw_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    factor: int,
) -> FFcSchedule:
    """Carry a coarse-scale schedule onto the original time scale (no idle time).

    What a coarse solution actually decides is **which machine runs each
    operation** and **in what order within that machine**; its times are an
    artifact of the coarse grid and are discarded downstream anyway. So this
    transfers assignment and order verbatim and re-derives times with one
    forward sweep over stages, placing each operation as early as its machine
    and its own previous stage allow::

        start[j, i] = max(end[j, i - 1], machine_end[k])
        end[j, i]   = start[j, i] + original_p[j][i]

    This is total: ``machine_end`` is non-decreasing within a machine and
    ``end[j, i - 1]`` is fixed before stage ``i`` is processed, so neither an
    overlap nor a precedence violation is constructible — for any coarsening
    rule, including ones where ``factor * coarse_p < p`` (``round`` / ``floor``)
    which the earlier scale-the-start-times reconstruction could not represent.

    The result is left-shifted (semi-active) but **before** ``insert_idle_time``
    — use :func:`reconstruct_coarse_schedule` for the ET-aligned schedule, or
    follow this call with the postprocess when the raw snapshot must be kept
    distinct.

    The coarse schedule's origin (CP solver, dispatch, etc.) is irrelevant.

    Args:
        coarse_schedule: Schedule on the coarsened instance. Must share the
            original instance's job/stage/machine layout, which
            ``FFcDDWParameters.coarsen_processing_times`` guarantees, and must
            cover every ``(job, stage)`` pair.
        instance: The original-scale instance supplying processing times.
        factor: Retained for call-site and API compatibility; **unused**, since
            times are derived from ``instance``'s processing times and
            precedence rather than by scaling the coarse times.

    Raises:
        ValueError: If ``coarse_schedule`` is missing any ``(job, stage)``
            operation. Reconstruction only visits operations the coarse
            schedule contains, so a missing one would vanish silently and the
            truncated result would be scored as a complete solution. Only one
            of the callers runs ``check_feasibility`` on the output, so this
            has to fail here. (Duplicates need no check —
            ``FFcSchedule.add_ops_times_2_mc`` already rejects a job appearing
            twice within a stage.)
    """
    del factor  # see docstring: times are re-derived, not scaled
    _validate_coarse_schedule_covers_instance(coarse_schedule, instance)
    original_p = instance.job_2_stage_2_p_map
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )

    job_2_prev_stage_end: dict[str, int] = {}
    for i in instance.stage_id_list:
        stage_end: dict[str, int] = {}
        for mc_id in instance.stage_2_machines_map[i]:
            machine_end = 0
            for j, _coarse_start, _coarse_end in coarse_schedule.get_job_sequence(
                i, mc_id
            ):
                start = max(job_2_prev_stage_end.get(j, 0), machine_end)
                end = start + original_p[j][i]
                schedule.add_ops_times_2_mc(i, mc_id, j, start, end)
                machine_end = end
                stage_end[j] = end
        job_2_prev_stage_end.update(stage_end)

    return schedule


def reconstruct_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    factor: int,
) -> FFcSchedule:
    """Reconstruct a coarse-scale schedule onto the original time scale.

    Thin wrapper over :func:`reconstruct_raw_coarse_schedule`: builds the raw
    reconstruction, then runs ``insert_idle_time`` on the original-scale
    instance to land operations at ET-optimal positions.

    There is no ``make_semi_active`` call: the raw reconstruction already
    left-shifts under exactly that rule, so it would be a guaranteed no-op.
    ``test_reconstruct_raw_is_semi_active`` pins the property this relies on.

    ``factor`` is unused — see :func:`reconstruct_raw_coarse_schedule`.
    """
    schedule = reconstruct_raw_coarse_schedule(coarse_schedule, instance, factor)
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return schedule
