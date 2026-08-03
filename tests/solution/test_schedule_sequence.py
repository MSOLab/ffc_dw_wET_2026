"""Tests for schedule_job_sequence and normalized_mean_rank_distance."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def _make_schedule(
    ops: list[tuple[str, str, str, int, int]],
) -> FFcSchedule:
    stages = sorted(set(stage for stage, _, _, _, _ in ops))
    jobs = sorted(set(job for _, _, job, _, _ in ops))
    mc_per_stage: dict[str, set[str]] = {}
    for stage, mc, _, _, _ in ops:
        mc_per_stage.setdefault(stage, set()).add(mc)
    sch = FFcSchedule(
        jobs=jobs,
        stages=stages,
        machines_per_stage={s: sorted(mcs) for s, mcs in mc_per_stage.items()},
    )
    for stage, mc, job, start, end in ops:
        sch.add_ops_times_2_mc(stage, mc, job, start, end)
    return sch


def test_schedule_sequence_importable() -> None:
    from ffc_ddw_sum_et.solution.schedule_sequence import (  # noqa: F401
        ScheduleSeqSource,
        normalized_mean_rank_distance,
        schedule_job_sequence,
    )


def _run(
    schedule: FFcSchedule, source: str, *, tiebreak_source=None, tiebreak_rank=None
):
    from ffc_ddw_sum_et.solution.schedule_sequence import schedule_job_sequence

    return schedule_job_sequence(
        schedule, source, tiebreak_source=tiebreak_source, tiebreak_rank=tiebreak_rank
    )


# ── midpoint mode ────────────────────────────────────────────────────────────
def test_midpoint_mode() -> None:
    """Midpoint = (first-stage start + last-stage end)/2, tiebreak by first start."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 6),
        ("s0", "m0", "j2", 6, 8),
        ("s0", "m0", "j3", 8, 11),
        ("s1", "m0", "j1", 6, 8),
        ("s1", "m0", "j3", 8, 10),
        ("s1", "m0", "j2", 10, 13),
        ("s1", "m0", "j0", 13, 15),
    ]
    sch = _make_schedule(ops)
    result = _run(sch, "midpoint")
    assert result == ["j1", "j0", "j3", "j2"]


# ── first_stage mode ─────────────────────────────────────────────────────────
def test_first_stage_mode() -> None:
    """First-stage start ascending, tiebreak by last-stage end."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 5),
        ("s0", "m0", "j2", 5, 7),
        ("s1", "m0", "j0", 3, 8),
        ("s1", "m1", "j1", 5, 8),
        ("s1", "m0", "j2", 8, 12),
    ]
    sch = _make_schedule(ops)
    result = _run(sch, "first_stage")
    assert result == ["j0", "j1", "j2"]


def test_first_stage_tiebreak_by_completion() -> None:
    """When two jobs share first-stage start, last-stage end breaks tie."""
    ops = [
        ("s0", "m0", "j0", 2, 5),
        ("s0", "m1", "j1", 2, 5),  # same first-stage placement, diff machine
        ("s1", "m0", "j0", 5, 8),
        ("s1", "m0", "j1", 8, 11),
    ]
    sch = _make_schedule(ops)
    result = _run(sch, "first_stage")
    assert result == ["j0", "j1"]


# ── completion mode ──────────────────────────────────────────────────────────
def test_completion_mode() -> None:
    """Last-stage end ascending, tiebreak by first-stage start."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 5),
        ("s0", "m0", "j2", 5, 7),
        ("s1", "m0", "j0", 3, 8),
        ("s1", "m1", "j1", 5, 8),
        ("s1", "m0", "j2", 8, 12),
    ]
    sch = _make_schedule(ops)
    result = _run(sch, "completion")
    assert result == ["j0", "j1", "j2"]


def test_completion_tiebreak_by_first_start() -> None:
    """When two jobs share last-stage end, first-stage start breaks tie."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 5, 8),
        ("s1", "m0", "j0", 3, 10),
        ("s1", "m1", "j1", 8, 10),  # same completion as j0
    ]
    sch = _make_schedule(ops)
    result = _run(sch, "completion")
    assert result == ["j0", "j1"]


# ── bottleneck mode ──────────────────────────────────────────────────────────
# Both cases below stagger the two stages into *opposite* job orders, so the
# assertion only holds for the stage that actually wins the idle-time contest.
def test_bottleneck_mode_picks_first_stage_when_it_has_least_idle() -> None:
    """Bottleneck = stage with minimum total idle time (here s0, idle 0)."""
    ops = [
        # s0 runs back-to-back → idle 0
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 6),
        ("s0", "m0", "j2", 6, 9),
        # s1 runs in the reverse order with gaps 2 and 4 → idle 6
        ("s1", "m0", "j2", 10, 12),
        ("s1", "m0", "j1", 14, 16),
        ("s1", "m0", "j0", 20, 23),
    ]
    sch = _make_schedule(ops)
    assert _run(sch, "bottleneck") == ["j0", "j1", "j2"]  # s0's order
    assert _run(sch, "completion") == ["j2", "j1", "j0"]  # s1's order


def test_bottleneck_mode_picks_last_stage_when_it_has_least_idle() -> None:
    """Mirror image: s1 now has idle 0, so bottleneck order flips."""
    ops = [
        # s0 with gaps 2 and 4 → idle 6
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 5, 8),
        ("s0", "m0", "j2", 12, 15),
        # s1 runs back-to-back in the reverse order → idle 0
        ("s1", "m0", "j2", 15, 18),
        ("s1", "m0", "j1", 18, 21),
        ("s1", "m0", "j0", 21, 24),
    ]
    sch = _make_schedule(ops)
    assert _run(sch, "bottleneck") == ["j2", "j1", "j0"]  # s1's order
    assert _run(sch, "first_stage") == ["j0", "j1", "j2"]  # s0's order


# ── partial schedules ────────────────────────────────────────────────────────
def test_jobs_without_operations_are_skipped() -> None:
    """A job listed in ``schedule.jobs`` but carrying no operation is dropped
    rather than crashing on a ``None`` sort key."""
    sch = FFcSchedule(
        jobs=["j0", "j1", "j2"],
        stages=["s0", "s1"],
        machines_per_stage={"s0": ["m0"], "s1": ["m0"]},
    )
    for stage, mc, job, start, end in [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 6),
        ("s1", "m0", "j0", 3, 5),
        ("s1", "m0", "j1", 6, 9),
    ]:
        sch.add_ops_times_2_mc(stage, mc, job, start, end)

    for source in ("midpoint", "first_stage", "bottleneck", "completion"):
        assert _run(sch, source) == ["j0", "j1"]


# ── tiebreak_rank ────────────────────────────────────────────────────────────
def test_tiebreak_rank_breaks_midpoint_ties() -> None:
    """tiebreak_rank determines order when midpoint and fs_start keys are equal."""
    ops = [
        ("s0", "m0", "j0", 0, 2),
        ("s0", "m1", "j1", 0, 2),
        ("s1", "m0", "j0", 4, 10),
        ("s1", "m1", "j1", 4, 10),
    ]
    sch = _make_schedule(ops)
    # j0: (0+10)/2=5.0, fs=0; j1: (0+10)/2=5.0, fs=0 → full tie
    result = _run(sch, "midpoint", tiebreak_rank={"j1": 0, "j0": 1})
    assert result == ["j1", "j0"]


def test_tiebreak_rank_none_uses_schedule_jobs_order() -> None:
    """When tiebreak_rank is None, schedule.jobs original index is used."""
    ops = [
        ("s0", "m0", "j0", 0, 2),
        ("s0", "m1", "j1", 0, 2),
        ("s1", "m0", "j0", 4, 10),
        ("s1", "m1", "j1", 4, 10),
    ]
    sch = _make_schedule(ops)
    assert sch.jobs == ["j0", "j1"]
    result = _run(sch, "midpoint", tiebreak_rank=None)
    assert result == ["j0", "j1"]


# ── normalized_mean_rank_distance ────────────────────────────────────────────
def test_normalized_distance_same_sequence() -> None:
    from ffc_ddw_sum_et.solution.schedule_sequence import (
        normalized_mean_rank_distance,
    )

    assert normalized_mean_rank_distance(["j0", "j1", "j2"], ["j0", "j1", "j2"]) == 0.0


def test_normalized_distance_reverse() -> None:
    from ffc_ddw_sum_et.solution.schedule_sequence import (
        normalized_mean_rank_distance,
    )

    d = normalized_mean_rank_distance(["j0", "j1", "j2"], ["j2", "j1", "j0"])
    assert d > 0


def test_normalized_distance_single_or_zero_common() -> None:
    from ffc_ddw_sum_et.solution.schedule_sequence import (
        normalized_mean_rank_distance,
    )

    assert normalized_mean_rank_distance(["j0"], ["j0"]) == 0.0
    assert normalized_mean_rank_distance([], []) == 0.0


# ── tiebreak_source parameter ────────────────────────────────────────────────
def test_midpoint_tiebreak_completion_reverses_ties() -> None:
    """When midpoint ties exactly, completion tiebreak reverses the fs order."""
    ops = [
        ("s0", "m0", "j0", 0, 4),
        ("s0", "m1", "j1", 2, 2),
        ("s1", "m1", "j1", 2, 8),
        ("s1", "m0", "j0", 4, 10),
    ]
    # j0: m=(0+10)/2=5, fs=0, ls=10
    # j1: m=(2+8)/2=5,  fs=2, ls=8  → same midpoint!
    # default (fs secondary): j0 (0) < j1 (2)  → j0, j1
    # tiebreak="completion" (ls secondary): j1 (8) < j0 (10) → j1, j0
    sch = _make_schedule(ops)
    assert _run(sch, "midpoint") == ["j0", "j1"]
    assert _run(sch, "midpoint", tiebreak_source="completion") == ["j1", "j0"]


# ── alias regression: tiebreak that is algebraically identical to default ────
def test_completion_tiebreak_midpoint_equals_default() -> None:
    """completion with tiebreak_source="midpoint" ≡ completion default.
    Rationale: when ls is fixed, midpoint = (fs+ls)/2 is monotonic in fs."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 5, 8),
        ("s0", "m0", "j2", 8, 11),
        ("s0", "m0", "j3", 12, 14),
        ("s1", "m0", "j1", 8, 10),
        ("s1", "m1", "j0", 3, 10),
        ("s1", "m1", "j3", 14, 20),
        ("s1", "m0", "j2", 11, 18),
    ]
    sch = _make_schedule(ops)
    # j0 and j1 share ls=10, so the secondary key actually decides here —
    # without a real tie the equality below would hold vacuously.
    end_map = sch.get_ji_2_end_time_map()
    assert end_map[("j0", "s1")] == end_map[("j1", "s1")] == 10
    default = _run(sch, "completion")
    assert default == ["j0", "j1", "j2", "j3"]
    assert _run(sch, "completion", tiebreak_source="midpoint") == default


def test_first_stage_tiebreak_midpoint_equals_default() -> None:
    """first_stage with tiebreak_source="midpoint" ≡ first_stage default.
    Rationale: when fs is fixed, midpoint = (fs+ls)/2 is monotonic in ls."""
    ops = [
        ("s0", "m0", "j0", 2, 5),
        ("s0", "m1", "j1", 2, 4),
        ("s0", "m2", "j2", 4, 6),
        ("s0", "m3", "j3", 6, 9),
        ("s1", "m1", "j1", 4, 12),
        ("s1", "m0", "j0", 5, 10),
        ("s1", "m2", "j2", 6, 14),
        ("s1", "m3", "j3", 9, 15),
    ]
    sch = _make_schedule(ops)
    # j0 and j1 share fs=2, so the secondary key actually decides here —
    # without a real tie the equality below would hold vacuously.
    start_map = sch.get_ji_2_start_time_map()
    assert start_map[("j0", "s0")] == start_map[("j1", "s0")] == 2
    default = _run(sch, "first_stage")
    assert default == ["j0", "j1", "j2", "j3"]
    assert _run(sch, "first_stage", tiebreak_source="midpoint") == default


# ── validation errors ─────────────────────────────────────────────────────────
def test_bottleneck_tiebreak_raises_valueerror() -> None:
    """bottleneck mode rejects any tiebreak_source."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s0", "m0", "j1", 3, 6),
        ("s1", "m0", "j1", 6, 10),
        ("s1", "m0", "j0", 10, 16),
    ]
    sch = _make_schedule(ops)
    with pytest.raises(ValueError, match="bottleneck"):
        _run(sch, "bottleneck", tiebreak_source="completion")


def test_tiebreak_source_equals_source_raises_valueerror() -> None:
    """tiebreak_source must differ from source."""
    ops = [
        ("s0", "m0", "j0", 0, 3),
        ("s1", "m0", "j0", 3, 5),
    ]
    sch = _make_schedule(ops)
    with pytest.raises(ValueError, match="must differ"):
        _run(sch, "midpoint", tiebreak_source="midpoint")
