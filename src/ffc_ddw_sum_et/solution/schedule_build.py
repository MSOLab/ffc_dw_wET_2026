"""Build an ``FFcSchedule`` from CP-SAT operation start/end times."""

from __future__ import annotations

from collections.abc import Sequence

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffc_schedule import FFcSchedule

__all__ = ["build_schedule_from_op_starts"]


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
