"""Mixed-dispatch core routine adapted from hybridflowshop/dispatcher/utils.py.

Targets `FFcSchedule` directly; the `draw_gantt_per_step` hooks from the
upstream version are removed (the project defers all Gantt rendering to the
post-run pass driven by `ArtifactLayout`).
"""

from __future__ import annotations

from typing import Mapping, Sequence, TypeVar

from ...solution.ffc_schedule import FFcSchedule

_T = TypeVar("_T")


def dispatch_job_sequence_by_stages(
    schedule: FFcSchedule,
    job_sequence: Sequence[str],
    job_2_stage_2_p: Mapping[str, Mapping[str, int]],
    from_stage: str | None = None,
    job_2_release_t: Mapping[str, int] | None = None,
) -> None:
    """Dispatch jobs through all stages, job by job, in the given sequence."""
    for job_id in job_sequence:
        schedule.dispatch_job_by_stages(
            job_id,
            dict(job_2_stage_2_p[job_id]),
            from_stage=from_stage,
            release_t=(
                job_2_release_t[job_id] if job_2_release_t is not None else None
            ),
        )


def dispatch_stages_by_job_sequence(
    schedule: FFcSchedule,
    job_sequence: Sequence[str],
    stage_2_job_2_p: Mapping[str, Mapping[str, int]],
    from_stage: str | None = None,
    job_2_release_t: Mapping[str, int] | None = None,
    machine_then_job: bool = False,
) -> None:
    """Dispatch a job sequence through stages, stage by stage."""
    stage_id_list = list(schedule.stages)
    if from_stage is not None:
        from_stage_index = stage_id_list.index(from_stage)
        target_stage_list = stage_id_list[from_stage_index:]
    else:
        target_stage_list = stage_id_list

    if machine_then_job:
        first_target = target_stage_list[0]
        if first_target == stage_id_list[0]:
            schedule.dispatch_stage_by_jobs(
                first_target,
                job_sequence,
                dict(stage_2_job_2_p[first_target]),
                job_2_release=job_2_release_t,
            )
        else:
            schedule.machine_centric_dispatch_4(
                first_target,
                job_sequence,
                stage_2_job_2_p,
                job_2_release=job_2_release_t,
            )
        for stage_id in target_stage_list[1:]:
            schedule.machine_centric_dispatch_4(
                stage_id,
                job_sequence,
                stage_2_job_2_p,
                job_2_release=job_2_release_t,
            )
    else:
        for stage_id in target_stage_list:
            schedule.dispatch_stage_by_jobs(
                stage_id,
                job_sequence,
                dict(stage_2_job_2_p[stage_id]),
                job_2_release=job_2_release_t,
            )


def reverse_even_positions(sequence: list[_T], in_place: bool = False) -> list[_T]:
    """Reverse only even positions (1-based), keeping odd positions fixed.

    For example, ``[A, B, C, D, E, F, G, H]`` -> ``[A, H, C, F, E, D, G, B]``.
    """
    result = sequence if in_place else list(sequence)
    even_position_elements = result[1::2]
    even_position_elements.reverse()
    result[1::2] = even_position_elements
    return result


def from_job_sequence_get_schedule_mixed(
    schedule: FFcSchedule,
    job_sequence: Sequence[str],
    stage_2_job_2_p: Mapping[str, Mapping[str, int]],
    stage_2_head: Mapping[str, int],
    from_stage: str | None = None,
    job_2_release: Mapping[str, int] | None = None,
    machine_then_job: bool = False,
    use_palmer_index: bool = False,
) -> None:
    """Mixed dispatch with per-stage k (= "head") values.

    For each target stage, dispatch the top-k jobs through all remaining
    stages (``dispatch_job_by_stages``), then fill the rest with
    ``dispatch_stage_by_jobs`` (or ``machine_centric_dispatch_4`` when
    ``machine_then_job=True``). Jobs dispatched through all stages are
    removed from subsequent stages' queues.
    """
    if from_stage is not None and from_stage not in schedule.stages:
        raise ValueError(f"from_stage '{from_stage}' is not in schedule.stages")

    if from_stage is None:
        _stage_id_list = list(schedule.stages)
    else:
        start_idx = list(schedule.stages).index(from_stage)
        _stage_id_list = list(schedule.stages)[start_idx:]

    for stage_id, k in stage_2_head.items():
        if stage_id not in _stage_id_list:
            raise ValueError(f"Unknown stage_id in stage_2_head: {stage_id}")
        if k < 0:
            raise ValueError(
                f"stage_2_head values must be non-negative, got {k} for stage {stage_id}"
            )

    for stage_id in _stage_id_list:
        for job_id in job_sequence:
            if job_id not in stage_2_job_2_p.get(stage_id, {}):
                raise ValueError(
                    f"Duration for job ID {job_id} at stage {stage_id} not provided"
                )

    _stage_2_head: dict[str, int] = {}
    remaining = len(job_sequence)
    for stage_id in _stage_id_list:
        if stage_id in stage_2_head:
            _stage_2_head[stage_id] = min(stage_2_head[stage_id], remaining)
            remaining -= _stage_2_head[stage_id]
        else:
            _stage_2_head[stage_id] = 0

    completed: set[str] = set()
    for stage_idx, stage_id in enumerate(_stage_id_list):
        _job_2_release = job_2_release if stage_idx == 0 else None

        remaining_seq = [j for j in job_sequence if j not in completed]
        priority_queue = schedule.get_job_priority_queue_for_stage_dispatch(
            stage_id, remaining_seq, job_2_release=_job_2_release
        )

        stage_k = _stage_2_head.get(stage_id, 0)
        if stage_k > 0:
            stages_from_here = _stage_id_list[stage_idx:]
            first_k = list(priority_queue)[:stage_k]
            for job_id in first_k:
                job_stage_dur = {
                    s: stage_2_job_2_p[s][job_id] for s in stages_from_here
                }
                schedule.dispatch_job_by_stages(
                    job_id,
                    job_stage_dur,
                    from_stage=stage_id,
                    release_t=(_job_2_release.get(job_id, 0) if _job_2_release else 0),
                )
                completed.add(job_id)

        unscheduled = [j for j in priority_queue if j not in completed]
        if not unscheduled:
            continue

        if machine_then_job and stage_id != schedule.stages[0]:
            schedule.machine_centric_dispatch_4(
                stage_id,
                unscheduled,
                stage_2_job_2_p,
                job_2_release=_job_2_release,
                use_palmer_index=use_palmer_index,
            )
        else:
            schedule.dispatch_stage_by_jobs(
                stage_id,
                unscheduled,
                stage_2_job_2_p[stage_id],
                job_2_release=_job_2_release,
            )
