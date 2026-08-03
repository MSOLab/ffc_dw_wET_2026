"""Job-order extraction from an existing FFcSchedule."""

from __future__ import annotations

from typing import Callable, Literal, Mapping, Sequence

from .ffc_schedule import FFcSchedule, StageIdType

__all__ = [
    "ScheduleSeqSource",
    "schedule_job_sequence",
    "normalized_mean_rank_distance",
]

ScheduleSeqSource = Literal["midpoint", "first_stage", "bottleneck", "completion"]

_KEY_FN: dict[str, Callable[[float, float], float]] = {
    "midpoint": lambda fs, ls: (fs + ls) / 2.0,
    "first_stage": lambda fs, ls: fs,
    "completion": lambda fs, ls: ls,
}

_DEFAULT_TIEBREAK: dict[str, ScheduleSeqSource] = {
    "midpoint": "first_stage",
    "first_stage": "completion",
    "completion": "first_stage",
}


def schedule_job_sequence(
    schedule: FFcSchedule,
    source: ScheduleSeqSource,
    *,
    tiebreak_source: ScheduleSeqSource | None = None,
    tiebreak_rank: Mapping[str, int] | None = None,
) -> list[str]:
    """Order ``schedule``'s jobs by a schedule-derived sort key.

    The sort key is ``(primary, secondary, rank, job_id)``, where
    ``primary`` is ``source``'s key and ``secondary`` is
    ``tiebreak_source``'s. ``tiebreak_source=None`` selects the default
    partner in ``_DEFAULT_TIEBREAK``, reproducing the original behavior.

    Only ``source="midpoint"`` has a tie-break choice that changes the
    order. For a fixed ``ls`` the midpoint is monotonic in ``fs`` and for
    a fixed ``fs`` it is monotonic in ``ls``, so on ``completion`` /
    ``first_stage`` every candidate secondary key is order-equivalent to
    the default. On ``midpoint`` the two candidates are opposites —
    ``ls = 2m - fs`` is *decreasing* in ``fs`` — so ``"completion"``
    reverses each tie group. ``tests/solution/test_schedule_sequence.py``
    pins all three cases; the experiment that rests on this algebra is
    ``plans/experiment/20260803/neh_cp_midpoint_tiebreak.md``.

    ``bottleneck`` takes no ``tiebreak_source`` (``ValueError``): its
    secondary key is the bottleneck stage's own midpoint, which is not
    one of the standard source keys.

    Jobs whose operations the ``source`` needs are missing from
    ``schedule`` are **skipped**, so the result may be shorter than
    ``schedule.jobs``. ``FFcSchedule.remove_jobs`` /
    ``remove_operations`` / ``deepcopy(job_subset=...)`` all keep
    ``jobs`` intact while stripping operations, so a partial schedule
    reaches here with no times to sort by; callers that need a full
    permutation (notably the ``neh_cp_*_seq`` steps) append the skipped
    jobs themselves.
    """
    if tiebreak_source is not None:
        if source == "bottleneck":
            raise ValueError("tiebreak_source is not supported for source='bottleneck'")
        if tiebreak_source == source:
            raise ValueError(f"tiebreak_source must differ from source ({source})")

    effective_tiebreak = (
        tiebreak_source
        if tiebreak_source is not None
        else _DEFAULT_TIEBREAK.get(source)
    )

    start_map = schedule.get_ji_2_start_time_map()
    end_map = schedule.get_ji_2_end_time_map()
    jobs = list(schedule.jobs)
    idx_map = (
        tiebreak_rank
        if tiebreak_rank is not None
        else {job_id: i for i, job_id in enumerate(jobs)}
    )
    fallback_idx = len(jobs)
    first_stage = schedule.stages[0]
    last_stage = schedule.stages[-1]

    if source == "bottleneck":
        bottleneck = _find_bottleneck_stage(schedule)
    else:
        bottleneck = first_stage

    ts_tuples: list[tuple] = []
    for job_id in jobs:
        fs_start = start_map.get((job_id, first_stage), None)
        ls_end = end_map.get((job_id, last_stage), None)
        bn_start = start_map.get((job_id, bottleneck), None)
        bn_end = end_map.get((job_id, bottleneck), None)
        rank = idx_map.get(job_id, fallback_idx)

        if source == "bottleneck":
            if bn_start is None or bn_end is None:
                continue
            bn_mid = (bn_start + bn_end) / 2.0
            ts_tuples.append((bn_start, bn_mid, rank, job_id))
            continue

        if fs_start is None or ls_end is None:
            continue
        primary = _KEY_FN[source](fs_start, ls_end)
        secondary = _KEY_FN[effective_tiebreak](fs_start, ls_end)
        ts_tuples.append((primary, secondary, rank, job_id))

    ts_tuples.sort(key=lambda t: t[:-1])
    return [t[-1] for t in ts_tuples]


def _find_bottleneck_stage(schedule: FFcSchedule) -> StageIdType:
    stage_2_mc_2_idle_time_map = schedule.get_stage_2_mc_2_idle_time_map()
    stage_2_total_idle = {
        stage_id: sum(mc_idle.values())
        for stage_id, mc_idle in stage_2_mc_2_idle_time_map.items()
    }
    return min(
        stage_2_total_idle,
        key=lambda s: (stage_2_total_idle[s], schedule.stage_2_index[s]),
    )


def normalized_mean_rank_distance(
    reference_sequence: Sequence[str],
    candidate_sequence: Sequence[str],
) -> float:
    if len(reference_sequence) <= 1:
        return 0.0
    ref_rank = {job: i for i, job in enumerate(reference_sequence)}
    total = 0.0
    for i, job in enumerate(candidate_sequence):
        ref_i = ref_rank.get(job)
        if ref_i is not None:
            total += abs(i - ref_i)
    n = len(reference_sequence)
    return (2.0 * total) / (n * n) if n > 0 else 0.0
