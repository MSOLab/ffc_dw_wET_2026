"""Job-order extraction from an existing FFcSchedule."""

from __future__ import annotations

from typing import Callable, Literal, Mapping, Sequence

from .ffc_schedule import FFcSchedule

__all__ = [
    "SCHEDULE_SEQ_SOURCES",
    "ScheduleSeqSource",
    "schedule_job_sequence",
    "normalized_mean_rank_distance",
]

ScheduleSeqSource = Literal["midpoint", "first_stage", "completion"]

#: Every value of ``ScheduleSeqSource``, in the module's canonical order.
#: Callers that need to enumerate the modes (the controller's sequence
#: diagnostics) read this instead of repeating the literal list.
SCHEDULE_SEQ_SOURCES: tuple[ScheduleSeqSource, ...] = (
    "midpoint",
    "first_stage",
    "completion",
)

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

_VALID_SOURCES: frozenset[str] = frozenset(SCHEDULE_SEQ_SOURCES)


def schedule_job_sequence(
    schedule: FFcSchedule,
    source: ScheduleSeqSource,
    *,
    tiebreak_source: ScheduleSeqSource | None = None,
    tiebreak_rank: Mapping[str, int] | None = None,
    end_stage_index: int = -1,
) -> list[str]:
    """Order ``schedule``'s jobs by a schedule-derived sort key.

    The sort key is ``(primary, secondary, rank)``, where ``primary`` is
    ``source``'s key and ``secondary`` is ``tiebreak_source``'s.
    ``tiebreak_source=None`` selects the default partner in
    ``_DEFAULT_TIEBREAK``, reproducing the original behavior. ``rank``
    settles the rest: both of its sources (``tiebreak_rank``, else the
    schedule's own job order) assign a distinct rank per job, so the sort
    is total and ``job_id`` is never needed to break a tie.

    Only ``source="midpoint"`` has a tie-break choice that changes the
    order. For a fixed ``ls`` the midpoint is monotonic in ``fs`` and for
    a fixed ``fs`` it is monotonic in ``ls``, so on ``completion`` /
    ``first_stage`` every candidate secondary key is order-equivalent to
    the default. On ``midpoint`` the two candidates are opposites —
    ``ls = 2m - fs`` is *decreasing* in ``fs`` — so ``"completion"``
    reverses each tie group. ``tests/solution/test_schedule_sequence.py``
    pins all three cases; the experiment that rests on this algebra is
    ``plans/experiment/20260803/neh_cp_midpoint_tiebreak.md``.

    ``end_stage_index`` selects which stage's end time is used as ``ls``
    in the primary and secondary keys. It is a negative index into
    ``schedule.stages``: ``-1`` (default) = last stage, ``-2`` = second
    to last, etc. ``-1`` is byte-identical to the previous behaviour.
    ``midpoint``'s and ``completion``'s tie-break degeneracy argument
    does **not** carry over to a different ``end_stage_index``: the
    (last-1)-stage end ``ls'`` is not monotonic in the last-stage end
    ``ls`` because ``insert_idle_time`` inserts per-job varying idle only
    on the last stage, so ``ls'`` and ``ls`` can rank jobs differently.

    Jobs whose operations the ``source`` needs are missing from
    ``schedule`` are **skipped**, so the result may be shorter than
    ``schedule.jobs``. ``FFcSchedule.remove_jobs`` /
    ``remove_operations`` / ``deepcopy(job_subset=...)`` all keep
    ``jobs`` intact while stripping operations, so a partial schedule
    reaches here with no times to sort by; callers that need a full
    permutation (notably the ``neh_cp_*_seq`` steps) append the skipped
    jobs themselves.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(
            f"Unknown source '{source}'; expected one of {sorted(_VALID_SOURCES)}"
        )

    num_stages = len(schedule.stages)
    if not (-num_stages <= end_stage_index <= -1):
        raise ValueError(
            f"end_stage_index={end_stage_index} out of range "
            f"[-{num_stages}, -1] for a {num_stages}-stage schedule"
        )

    if tiebreak_source is not None:
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
    end_stage = schedule.stages[end_stage_index]

    ts_tuples: list[tuple] = []
    for job_id in jobs:
        fs_start = start_map.get((job_id, first_stage), None)
        ls_end = end_map.get((job_id, end_stage), None)
        rank = idx_map.get(job_id, fallback_idx)

        if fs_start is None or ls_end is None:
            continue
        primary = _KEY_FN[source](fs_start, ls_end)
        secondary = _KEY_FN[effective_tiebreak](fs_start, ls_end)
        ts_tuples.append((primary, secondary, rank, job_id))

    ts_tuples.sort(key=lambda t: t[:-1])
    return [t[-1] for t in ts_tuples]


def normalized_mean_rank_distance(
    reference_sequence: Sequence[str],
    candidate_sequence: Sequence[str],
) -> float:
    """Normalized mean absolute rank distance between two sequences.

    Normalization divisor is ``n² / 2`` where ``n = len(reference)``,
    so a full reversal yields 1.0 and an identical sequence yields 0.0.
    Jobs present in ``candidate`` but absent from ``reference`` are
    dropped; ``n`` is always the ``reference`` length so the divisor is
    fixed regardless of how many candidate jobs match.
    """
    n = len(reference_sequence)
    if n <= 1:
        return 0.0
    ref_rank = {job: i for i, job in enumerate(reference_sequence)}
    total = 0.0
    for i, job in enumerate(candidate_sequence):
        ref_i = ref_rank.get(job)
        if ref_i is not None:
            total += abs(i - ref_i)
    return (2.0 * total) / (n * n)
