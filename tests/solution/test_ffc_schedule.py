from __future__ import annotations

import pytest

from ffc_ddw_sum_et.solution.ffc_schedule import (
    FFcSchedule,
    _build_idle_gaps_from_ops,
)
from ffc_ddw_sum_et.solution.ffc_schedule import (
    get_bottleneck_stage_job_sequence as new_get_bottleneck_stage_job_sequence,
)
from ffc_ddw_sum_et.solution.ffc_schedule import (
    get_first_stage_start_sequence as new_get_first_stage_start_sequence,
)
from ffc_ddw_sum_et.solution.ffc_schedule import (
    get_midpoint_sequence as new_get_midpoint_sequence,
)
from ffc_ddw_sum_et.solution.ffc_schedule import (
    validate_schedule as new_validate_schedule,
)

from ..reference_impl.schedule_lite import (
    HybridFlowshopLiteSchedule,
)
from ..reference_impl.schedule_lite import (
    get_bottleneck_stage_job_sequence as old_get_bottleneck_stage_job_sequence,
)
from ..reference_impl.schedule_lite import (
    get_first_stage_start_sequence as old_get_first_stage_start_sequence,
)
from ..reference_impl.schedule_lite import (
    get_midpoint_sequence as old_get_midpoint_sequence,
)
from ..reference_impl.schedule_lite import (
    validate_schedule as old_validate_schedule,
)

JOBS = ["j1", "j2", "j3", "j4"]
STAGES = ["s1", "s2", "s3"]
MACHINES = {
    "s1": ["s1_m1", "s1_m2"],
    "s2": ["s2_m1", "s2_m2"],
    "s3": ["s3_m1"],
}
DURATIONS = {
    "s1": {"j1": 3, "j2": 2, "j3": 3, "j4": 4},
    "s2": {"j1": 4, "j2": 2, "j3": 2, "j4": 3},
    "s3": {"j1": 2, "j2": 3, "j3": 2, "j4": 4},
}
MANUAL_OPS = [
    ("s1", "s1_m1", "j1", 0, 3),
    ("s1", "s1_m2", "j2", 1, 3),
    ("s1", "s1_m2", "j4", 3, 7),
    ("s1", "s1_m1", "j3", 5, 8),
    ("s2", "s2_m1", "j1", 3, 7),
    ("s2", "s2_m2", "j2", 4, 6),
    ("s2", "s2_m2", "j4", 7, 10),
    ("s2", "s2_m1", "j3", 8, 10),
    ("s3", "s3_m1", "j1", 7, 9),
    ("s3", "s3_m1", "j2", 10, 13),
    ("s3", "s3_m1", "j3", 13, 15),
    ("s3", "s3_m1", "j4", 15, 19),
]
REVERSE_STAGE3_OPS = [
    ("s3", "s3_m1", "j1", 8, 10),
    ("s3", "s3_m1", "j2", 10, 13),
    ("s3", "s3_m1", "j3", 13, 15),
    ("s3", "s3_m1", "j4", 15, 19),
]


def make_old_schedule() -> HybridFlowshopLiteSchedule:
    return HybridFlowshopLiteSchedule(
        jobs=list(JOBS),
        stages=list(STAGES),
        machines_per_stage={
            stage: list(machines) for stage, machines in MACHINES.items()
        },
    )


def make_new_schedule() -> FFcSchedule:
    return FFcSchedule(
        jobs=list(JOBS),
        stages=list(STAGES),
        machines_per_stage={
            stage: list(machines) for stage, machines in MACHINES.items()
        },
    )


def build_manual_schedules() -> tuple[HybridFlowshopLiteSchedule, FFcSchedule]:
    old_schedule = make_old_schedule()
    new_schedule = make_new_schedule()
    for stage_id, mc_id, job_id, start_time, end_time in MANUAL_OPS:
        old_schedule.add_ops_times_2_mc(stage_id, mc_id, job_id, start_time, end_time)
        new_schedule.add_ops_times_2_mc(stage_id, mc_id, job_id, start_time, end_time)
    return old_schedule, new_schedule


def build_reverse_ready_schedules() -> tuple[HybridFlowshopLiteSchedule, FFcSchedule]:
    old_schedule = make_old_schedule()
    new_schedule = make_new_schedule()
    for stage_id, mc_id, job_id, start_time, end_time in REVERSE_STAGE3_OPS:
        old_schedule.add_ops_times_2_mc(stage_id, mc_id, job_id, start_time, end_time)
        new_schedule.add_ops_times_2_mc(stage_id, mc_id, job_id, start_time, end_time)
    return old_schedule, new_schedule


def normalize_old_sequence(
    sequence: list[tuple[int, int, str]],
) -> list[tuple[str, int, int]]:
    return [(job_id, start_time, end_time) for start_time, end_time, job_id in sequence]


def assert_public_state_equal(
    old_schedule: HybridFlowshopLiteSchedule, new_schedule: FFcSchedule
) -> None:
    for stage_id in STAGES:
        for mc_id in MACHINES[stage_id]:
            assert normalize_old_sequence(
                old_schedule.get_job_sequence(stage_id, mc_id)
            ) == (new_schedule.get_job_sequence(stage_id, mc_id))
    assert old_schedule.makespan == new_schedule.makespan
    assert old_schedule.get_operation_set() == new_schedule.get_operation_set()
    assert (
        old_schedule.get_jik_2_start_time_map()
        == new_schedule.get_jik_2_start_time_map()
    )
    assert (
        old_schedule.get_jik_2_end_time_map() == new_schedule.get_jik_2_end_time_map()
    )
    assert old_schedule.get_ji_2_end_time_map() == new_schedule.get_ji_2_end_time_map()
    assert old_schedule.get_stage_2_mc_2_last_end_time_map() == (
        new_schedule.get_stage_2_mc_2_last_end_time_map()
    )
    assert old_schedule.get_stage_2_mc_2_idle_time_map() == (
        new_schedule.get_stage_2_mc_2_idle_time_map()
    )


def assert_cache_consistency(schedule: FFcSchedule) -> None:
    for stage_id in schedule.stages:
        expected_start: dict[str, int] = {}
        expected_end: dict[str, int] = {}
        for mc_id in schedule.machines_per_stage[stage_id]:
            sequence = schedule.get_job_sequence(stage_id, mc_id)
            start_times = [start_time for _, start_time, _ in sequence]
            assert start_times == sorted(start_times)
            for idx in range(len(sequence) - 1):
                assert sequence[idx][2] <= sequence[idx + 1][1]
            for job_id, start_time, end_time in sequence:
                expected_start[job_id] = start_time
                expected_end[job_id] = end_time
        assert (
            schedule._FFcSchedule__stage_2_job_2_start_time[stage_id] == expected_start
        )
        assert schedule._FFcSchedule__stage_2_job_2_end_time[stage_id] == expected_end


def test_manual_schedule_parity_and_helpers() -> None:
    old_schedule, new_schedule = build_manual_schedules()

    assert_public_state_equal(old_schedule, new_schedule)
    old_validate_schedule(old_schedule, DURATIONS)
    new_validate_schedule(new_schedule, DURATIONS)

    assert old_schedule.get_machine_earliest_start_time(
        "s1", "s1_m1", 1, release_t=3
    ) == (new_schedule.get_machine_earliest_start_time("s1", "s1_m1", 1, release_t=3))
    assert old_schedule.get_eat_for_machine("s2", "s2_m1", 1, release_t=7) == (
        new_schedule.get_eat_for_machine("s2", "s2_m1", 1, release_t=7)
    )
    assert old_schedule.select_machine_by_earliest_start_then_idle(
        "s2", 2, release_t=6
    ) == (new_schedule.select_machine_by_earliest_start_then_idle("s2", 2, release_t=6))
    assert old_schedule.get_job_end_time("s2", "j3") == new_schedule.get_job_end_time(
        "s2", "j3"
    )
    assert old_schedule.get_prev_stage_end_time("s3", "j4") == (
        new_schedule.get_prev_stage_end_time("s3", "j4")
    )

    assert old_get_midpoint_sequence(old_schedule) == new_get_midpoint_sequence(
        new_schedule
    )
    assert old_get_bottleneck_stage_job_sequence(old_schedule) == (
        new_get_bottleneck_stage_job_sequence(new_schedule)
    )
    assert old_get_first_stage_start_sequence(old_schedule) == (
        new_get_first_stage_start_sequence(new_schedule)
    )

    old_copy = old_schedule.deepcopy(job_subsequence={"j1", "j3"})
    new_copy = new_schedule.deepcopy(job_subset={"j1", "j3"})
    assert_public_state_equal(old_copy, new_copy)

    old_reversed = old_schedule.as_reversed()
    new_reversed = new_schedule.as_reversed()
    assert_public_state_equal(old_reversed, new_reversed)
    assert_cache_consistency(new_schedule)
    assert_cache_consistency(new_copy)
    assert_cache_consistency(new_reversed)


def test_stage_dispatch_parity() -> None:
    old_schedule = make_old_schedule()
    new_schedule = make_new_schedule()
    job_order = ["j2", "j1", "j4", "j3"]

    old_schedule.dispatch_stage_by_jobs("s1", job_order, DURATIONS["s1"])
    new_schedule.dispatch_stage_by_jobs("s1", job_order, DURATIONS["s1"])
    old_schedule.dispatch_stage_by_jobs("s2", job_order, DURATIONS["s2"])
    new_schedule.dispatch_stage_by_jobs("s2", job_order, DURATIONS["s2"])
    old_schedule.dispatch_stage_by_jobs("s3", job_order, DURATIONS["s3"])
    new_schedule.dispatch_stage_by_jobs("s3", job_order, DURATIONS["s3"])

    assert_public_state_equal(old_schedule, new_schedule)
    assert_cache_consistency(new_schedule)


def test_reverse_dispatch_parity() -> None:
    old_schedule, new_schedule = build_reverse_ready_schedules()
    mc_2_lct_stage_2 = {"s2_m1": 19, "s2_m2": 19}
    mc_2_lct_stage_1 = {"s1_m1": 19, "s1_m2": 19}
    job_order = ["j4", "j3", "j2", "j1"]

    old_schedule.dispatch_stage_reversed_by_jobs(
        "s2", job_order, DURATIONS["s2"], mc_2_lct_stage_2
    )
    new_schedule.dispatch_stage_reversed_by_jobs(
        "s2", job_order, DURATIONS["s2"], mc_2_lct_stage_2
    )
    old_schedule.dispatch_stage_reversed_by_jobs(
        "s1", job_order, DURATIONS["s1"], mc_2_lct_stage_1
    )
    new_schedule.dispatch_stage_reversed_by_jobs(
        "s1", job_order, DURATIONS["s1"], mc_2_lct_stage_1
    )

    assert_public_state_equal(old_schedule, new_schedule)
    assert_cache_consistency(new_schedule)


@pytest.mark.parametrize("use_palmer_index", [False, True])
def test_machine_centric_dispatch_parity(use_palmer_index: bool) -> None:
    old_schedule = make_old_schedule()
    new_schedule = make_new_schedule()
    for schedule in (old_schedule, new_schedule):
        schedule.add_ops_times_2_mc("s1", "s1_m1", "j1", 3, 6)
        schedule.add_ops_times_2_mc("s1", "s1_m2", "j2", 0, 2)

    old_schedule.machine_centric_dispatch_4(
        "s1",
        ["j3", "j4"],
        DURATIONS,
        job_2_release={"j3": 0, "j4": 1},
        use_palmer_index=use_palmer_index,
    )
    new_schedule.machine_centric_dispatch_4(
        "s1",
        ["j3", "j4"],
        DURATIONS,
        job_2_release={"j3": 0, "j4": 1},
        use_palmer_index=use_palmer_index,
    )

    assert_public_state_equal(old_schedule, new_schedule)
    assert_cache_consistency(new_schedule)


def test_retiming_swap_and_analysis_parity() -> None:
    old_schedule, new_schedule = build_manual_schedules()

    assert old_schedule.collect_stage_machine_suffix_job_ids("s2", "s2_m1", "j1") == (
        new_schedule.collect_stage_machine_suffix_job_ids("s2", "s2_m1", "j1")
    )
    assert old_schedule.calculate_slack(DURATIONS) == new_schedule.calculate_slack(
        DURATIONS
    )
    assert old_schedule.find_critical_blocks(DURATIONS, include_singletons=True) == (
        new_schedule.find_critical_blocks(DURATIONS, include_singletons=True)
    )

    old_schedule.swap_two_operations_within_stage("s2", "j1", "j4", DURATIONS)
    new_schedule.swap_two_operations_within_stage("s2", "j1", "j4", DURATIONS)
    assert_public_state_equal(old_schedule, new_schedule)

    old_schedule.swap_stage_machine_operation_sets(
        "s1",
        "s1_m1",
        ["j1"],
        "s1_m2",
        ["j2"],
        DURATIONS,
        do_make_semi_active=True,
    )
    new_schedule.swap_stage_machine_operation_sets(
        "s1",
        "s1_m1",
        ["j1"],
        "s1_m2",
        ["j2"],
        DURATIONS,
        do_make_semi_active=True,
    )
    assert_public_state_equal(old_schedule, new_schedule)

    old_schedule.make_right_justified(DURATIONS)
    new_schedule.make_right_justified(DURATIONS)
    assert_public_state_equal(old_schedule, new_schedule)

    old_schedule.right_shift(2)
    new_schedule.right_shift(2)
    assert_public_state_equal(old_schedule, new_schedule)
    assert_cache_consistency(new_schedule)


def test_remove_operations_and_jobs_parity() -> None:
    old_schedule, new_schedule = build_manual_schedules()
    removed_ops = {("j2", "s2", "s2_m2"), ("j3", "s1", "s1_m1")}

    old_schedule.remove_operations(removed_ops)
    new_schedule.remove_operations(removed_ops)
    assert_public_state_equal(old_schedule, new_schedule)

    old_schedule.remove_jobs({"j4"})
    new_schedule.remove_jobs({"j4"})
    assert_public_state_equal(old_schedule, new_schedule)
    assert_cache_consistency(new_schedule)


def test_cache_invariants_after_mutations() -> None:
    schedule = make_new_schedule()
    schedule.dispatch_stage_by_jobs("s1", ["j1", "j2", "j3", "j4"], DURATIONS["s1"])
    assert_cache_consistency(schedule)

    schedule.dispatch_stage_by_jobs("s2", ["j1", "j2", "j3", "j4"], DURATIONS["s2"])
    assert_cache_consistency(schedule)

    schedule.dispatch_stage_by_jobs("s3", ["j1", "j2", "j3", "j4"], DURATIONS["s3"])
    assert_cache_consistency(schedule)

    schedule.make_right_justified(DURATIONS)
    assert_cache_consistency(schedule)

    schedule.right_shift(3)
    assert_cache_consistency(schedule)

    schedule.remove_operations({("j2", "s3", "s3_m1")})
    assert_cache_consistency(schedule)


def test_raw_swap_paths_invalidate_start_and_end_caches() -> None:
    _, schedule = build_manual_schedules()
    schedule.swap_two_operations_within_stage(
        "s2", "j1", "j4", DURATIONS, do_make_semi_active=False
    )
    assert "j1" not in schedule._FFcSchedule__stage_2_job_2_start_time["s2"]
    assert "j4" not in schedule._FFcSchedule__stage_2_job_2_start_time["s2"]
    assert "j1" not in schedule._FFcSchedule__stage_2_job_2_end_time["s2"]
    assert "j4" not in schedule._FFcSchedule__stage_2_job_2_end_time["s2"]

    _, schedule = build_manual_schedules()
    schedule.swap_stage_machine_operation_sets(
        "s1",
        "s1_m1",
        ["j1"],
        "s1_m2",
        ["j2"],
        DURATIONS,
        do_make_semi_active=False,
    )
    assert "j1" not in schedule._FFcSchedule__stage_2_job_2_start_time["s1"]
    assert "j2" not in schedule._FFcSchedule__stage_2_job_2_start_time["s1"]
    assert "j1" not in schedule._FFcSchedule__stage_2_job_2_end_time["s1"]
    assert "j2" not in schedule._FFcSchedule__stage_2_job_2_end_time["s1"]


def test_release_priority_queue_and_regressions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_schedule = FFcSchedule(
        jobs=["j1", "j2"],
        stages=["s1"],
        machines_per_stage={"s1": ["m1"]},
    )
    assert queue_schedule.get_job_priority_queue_for_stage_dispatch(
        "s1", ["j1", "j2"], job_2_release={"j1": 10, "j2": 0}
    ) == ["j2", "j1"]

    sort_schedule = make_new_schedule()
    sort_schedule._FFcSchedule__stage_2_mc_2_job_tuple_seq["s1"]["s1_m1"] = [
        ("j2", 5, 7),
        ("j1", 0, 3),
    ]
    sort_schedule.sort_by_start_times()
    assert sort_schedule.get_job_sequence("s1", "s1_m1") == [
        ("j1", 0, 3),
        ("j2", 5, 7),
    ]

    assert _build_idle_gaps_from_ops(
        [("j1", 2, 5), ("j2", 8, 10)],
        inf_end=12,
    ) == [[0, 2], [5, 8], [10, 12]]

    _, schedule = build_manual_schedules()
    original_get_job_sequence = schedule.get_job_sequence

    def cache_only_get_job_sequence(stage_id: str, mc_id: str):
        if stage_id == "s2":
            raise AssertionError(
                "next stage scan should not be needed when cache exists"
            )
        return original_get_job_sequence(stage_id, mc_id)

    monkeypatch.setattr(schedule, "get_job_sequence", cache_only_get_job_sequence)
    assert schedule._get_next_stage_start_time("s1", "j1") == 3

    _, schedule = build_manual_schedules()
    schedule.right_shift(4)
    assert schedule._FFcSchedule__stage_2_job_2_start_time["s1"]["j1"] == 4
    assert schedule._FFcSchedule__stage_2_job_2_end_time["s1"]["j1"] == 7


def test_error_cases() -> None:
    schedule = make_new_schedule()

    with pytest.raises(ValueError, match="Invalid stage ID"):
        schedule.get_job_sequence("missing", "s1_m1")
    with pytest.raises(ValueError, match="Invalid machine ID"):
        schedule.get_job_sequence("s1", "missing")
    with pytest.raises(ValueError, match="Invalid job ID"):
        schedule.add_operation_2_stage("s1", "missing", 2)
    with pytest.raises(ValueError, match="Duration must be greater than 0"):
        schedule.add_operation_2_stage("s1", "j1", 0)
    with pytest.raises(
        ValueError, match="End time 1 cannot be earlier than start time 2"
    ):
        schedule.validate(start_time=2, end_time=1)

    schedule.add_ops_times_2_mc("s1", "s1_m1", "j1", 0, 3)
    with pytest.raises(ValueError, match="already scheduled"):
        schedule.add_ops_times_2_mc("s1", "s1_m2", "j1", 3, 6)
    with pytest.raises(ValueError, match="Operation overlap"):
        schedule.add_ops_times_2_mc("s1", "s1_m1", "j2", 2, 4)

    reverse_schedule = build_reverse_ready_schedules()[1]
    with pytest.raises(ValueError, match="Unable to reverse-dispatch job"):
        reverse_schedule.dispatch_stage_reversed_by_jobs(
            "s2",
            ["j1"],
            {"j1": DURATIONS["s2"]["j1"]},
            {"s2_m1": 1, "s2_m2": 1},
        )

    swap_schedule = build_manual_schedules()[1]
    with pytest.raises(ValueError, match="Duplicate job IDs in from_job_ids"):
        swap_schedule.swap_stage_machine_operation_sets(
            "s1",
            "s1_m1",
            ["j1", "j1"],
            "s1_m2",
            ["j2"],
            DURATIONS,
        )

    broken_duration_schedule = build_manual_schedules()[1]
    with pytest.raises(ValueError, match="Duration mismatch"):
        new_validate_schedule(
            broken_duration_schedule,
            {
                **DURATIONS,
                "s1": {**DURATIONS["s1"], "j1": 99},
            },
        )


# -------------------------------------------------------------------
# insert_idle_time with time_factor
# -------------------------------------------------------------------


def _make_iit_schedule() -> FFcSchedule:
    """2-job × 3-stage schedule, 1 machine per stage."""
    sched = FFcSchedule(
        jobs=["j0", "j1"],
        stages=["s0", "s1", "s2"],
        machines_per_stage={"s0": ["m0"], "s1": ["m1"], "s2": ["m2"]},
    )
    sched.add_ops_times_2_mc("s0", "m0", "j0", 0, 3)
    sched.add_ops_times_2_mc("s0", "m0", "j1", 3, 6)
    sched.add_ops_times_2_mc("s1", "m1", "j0", 3, 7)
    sched.add_ops_times_2_mc("s1", "m1", "j1", 7, 11)
    sched.add_ops_times_2_mc("s2", "m2", "j0", 7, 10)
    sched.add_ops_times_2_mc("s2", "m2", "j1", 10, 14)
    return sched


def test_insert_idle_time_tf1_is_noop() -> None:
    """time_factor=1 must produce identical results to the default call."""
    sched1 = _make_iit_schedule()
    sched2 = _make_iit_schedule()
    dw = {"j0": (8, 12), "j1": (10, 16)}
    ewt = {"j0": 1, "j1": 1}
    twt = {"j0": 1, "j1": 1}

    sched1.insert_idle_time(dw, ewt, twt)
    sched2.insert_idle_time(dw, ewt, twt, time_factor=1)

    assert sched1.get_jik_2_end_time_map() == sched2.get_jik_2_end_time_map()


def test_insert_idle_time_tf_effective_window() -> None:
    """tf=2 with original window (16,24) → effective (8,12), same as tf=1 with (8,12)."""
    # Coarse schedule: j0 ends at 5, j1 ends at 7 on coarse grid
    coarse = FFcSchedule(
        jobs=["j0", "j1"],
        stages=["s0", "s1", "s2"],
        machines_per_stage={"s0": ["m0"], "s1": ["m1"], "s2": ["m2"]},
    )
    coarse.add_ops_times_2_mc("s0", "m0", "j0", 0, 1)
    coarse.add_ops_times_2_mc("s0", "m0", "j1", 1, 2)
    coarse.add_ops_times_2_mc("s1", "m1", "j0", 1, 3)
    coarse.add_ops_times_2_mc("s1", "m1", "j1", 3, 5)
    coarse.add_ops_times_2_mc("s2", "m2", "j0", 3, 5)
    coarse.add_ops_times_2_mc("s2", "m2", "j1", 5, 7)

    # Original window: (16, 24), factor=2 → effective (8, 12)
    orig_dw = {"j0": (16, 24), "j1": (16, 24)}
    ewt = {"j0": 1, "j1": 1}
    twt = {"j0": 1, "j1": 1}

    coarse.insert_idle_time(orig_dw, ewt, twt, time_factor=2)

    # Compare with fine schedule using effective window directly
    fine = FFcSchedule(
        jobs=["j0", "j1"],
        stages=["s0", "s1", "s2"],
        machines_per_stage={"s0": ["m0"], "s1": ["m1"], "s2": ["m2"]},
    )
    fine.add_ops_times_2_mc("s0", "m0", "j0", 0, 1)
    fine.add_ops_times_2_mc("s0", "m0", "j1", 1, 2)
    fine.add_ops_times_2_mc("s1", "m1", "j0", 1, 3)
    fine.add_ops_times_2_mc("s1", "m1", "j1", 3, 5)
    fine.add_ops_times_2_mc("s2", "m2", "j0", 3, 5)
    fine.add_ops_times_2_mc("s2", "m2", "j1", 5, 7)

    eff_dw = {"j0": (8, 12), "j1": (8, 12)}
    fine.insert_idle_time(eff_dw, ewt, twt)

    assert coarse.get_jik_2_end_time_map() == fine.get_jik_2_end_time_map()
