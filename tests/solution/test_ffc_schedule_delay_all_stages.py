"""Tests for FFcSchedule.delay_job_latest_leq_obj_contrib_all_stages.

Invariants verified:
1. Per-job objective contribution is non-increasing (and in practice
   unchanged on the last stage by the delegate's contract).
2. Per-machine sequence order is preserved.
3. Every duration is preserved.
4. Final schedule passes ``validate_schedule`` (no overlap, all
   precedences satisfied).
5. Every operation moves no earlier than its input position.
6. Earlier-stage passes do not touch any last-stage ``C_j`` (i.e.
   running ``..._all_stages`` produces the same last-stage end-times as
   the last-stage delegate alone).
7. Explicit job-stage precedence: ``end[j,i] <= start[j,i+1]``.
8. With a single-stage schedule, ``..._all_stages`` reduces to the
   last-stage delegate (no earlier-stage passes to run).
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import validate_schedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="rj_all_stages_test",
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={
            "i0": ["i0_0", "i0_1"],
            "i1": ["i1_0"],
            "i2": ["i2_0", "i2_1"],
        },
        p_manager=JobStageProcessingTimeManager(
            name="rj_all_stages_test_p",
            df=pd.DataFrame([[2, 3, 2], [2, 2, 1], [2, 1, 3], [1, 2, 2], [3, 1, 2]]),
        ),
        job_2_due_window_map={
            "j0": (5, 7),
            "j1": (4, 6),
            "j2": (6, 9),
            "j3": (5, 7),
            "j4": (8, 10),
        },
        job_2_ewt_map={j: 1 for j in ["j0", "j1", "j2", "j3", "j4"]},
        job_2_twt_map={j: 2 for j in ["j0", "j1", "j2", "j3", "j4"]},
    )


def _build_seed_schedule(instance: FFcDDWParameters):
    rec = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))
    )
    sched = rec.result.schedule.deepcopy()
    sched.make_semi_active(instance.stage_2_job_2_p_map)
    sched.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return sched


def test_delay_all_stages_preserves_durations() -> None:
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    before_start = sched.get_jik_2_start_time_map()
    before_end = sched.get_jik_2_end_time_map()

    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    after_start = sched.get_jik_2_start_time_map()
    after_end = sched.get_jik_2_end_time_map()

    for op in before_start:
        before_dur = before_end[op] - before_start[op]
        after_dur = after_end[op] - after_start[op]
        assert before_dur == after_dur, op


def test_delay_all_stages_preserves_machine_sequence_order() -> None:
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    before_seqs: dict[tuple[str, str], list[str]] = {}
    for stage_id in sched.stages:
        for mc_id in sched.machines_per_stage[stage_id]:
            before_seqs[(stage_id, mc_id)] = [
                j for j, _, _ in sched.get_job_sequence(stage_id, mc_id)
            ]

    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    for (stage_id, mc_id), before_jobs in before_seqs.items():
        after_jobs = [j for j, _, _ in sched.get_job_sequence(stage_id, mc_id)]
        assert after_jobs == before_jobs, (stage_id, mc_id)


def test_delay_all_stages_preserves_objective() -> None:
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    sum_e_before, sum_t_before = compute_weighted_earliness_tardiness(sched, instance)

    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    sum_e_after, sum_t_after = compute_weighted_earliness_tardiness(sched, instance)
    # The last-stage helper is objective-non-increasing; earlier-stage
    # passes do not alter C_j. So obj is non-increasing; in practice
    # often equal.
    obj_before = sum_e_before + sum_t_before
    obj_after = sum_e_after + sum_t_after
    assert obj_after <= obj_before


def test_delay_all_stages_yields_valid_schedule() -> None:
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)
    validate_schedule(sched, instance.stage_2_job_2_p_map)


def test_delay_all_stages_pushes_ops_no_earlier() -> None:
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    before_start = sched.get_jik_2_start_time_map()
    before_end = sched.get_jik_2_end_time_map()

    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    after_start = sched.get_jik_2_start_time_map()
    after_end = sched.get_jik_2_end_time_map()
    # last-stage tardy ops may stay (allowed); others should not move earlier
    for op in before_start:
        assert after_start[op] >= before_start[op], op
        assert after_end[op] >= before_end[op], op


def test_delay_all_stages_matches_last_stage_helper_on_last_stage() -> None:
    """Earlier-stage passes never touch any C_j: the last-stage end-times
    produced by ``..._all_stages`` equal those produced by the last-stage
    delegate alone."""
    instance = _make_instance()
    sched_all = _build_seed_schedule(instance)
    sched_last_only = sched_all.deepcopy()

    sched_last_only.delay_job_latest_leq_obj_contrib(instance.job_2_dw_ub_map)
    sched_all.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    last_stage_id = instance.stage_id_list[-1]
    for job_id in instance.job_id_list:
        assert sched_all.get_job_end_time(
            last_stage_id, job_id
        ) == sched_last_only.get_job_end_time(last_stage_id, job_id), job_id


def test_delay_all_stages_per_job_objective_non_increasing() -> None:
    """Per-job (not just summed) weighted E+T is non-increasing."""
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    last_stage_id = instance.stage_id_list[-1]

    def per_job_et(s) -> dict[str, int]:
        out: dict[str, int] = {}
        for j in instance.job_id_list:
            c_j = s.get_job_end_time(last_stage_id, j)
            d_lo, d_hi = instance.job_2_due_window_map[j]
            e = max(0, d_lo - c_j) * instance.job_2_ewt_map[j]
            t = max(0, c_j - d_hi) * instance.job_2_twt_map[j]
            out[j] = e + t
        return out

    before = per_job_et(sched)
    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)
    after = per_job_et(sched)
    for j in instance.job_id_list:
        assert after[j] <= before[j], (j, before[j], after[j])


def test_delay_all_stages_satisfies_explicit_job_stage_precedence() -> None:
    """``end[j,i] <= start[j,i+1]`` for every job and consecutive stage
    pair after the call. ``validate_schedule`` covers this too, but this
    test makes the precedence invariant locally visible."""
    instance = _make_instance()
    sched = _build_seed_schedule(instance)
    sched.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    start_map = sched.get_jik_2_start_time_map()
    end_map = sched.get_jik_2_end_time_map()
    # rebuild (j, i) -> end / start from the (j, i, k) maps
    ji_end: dict[tuple[str, str], int] = {}
    ji_start: dict[tuple[str, str], int] = {}
    for (j, i, _k), e in end_map.items():
        ji_end[(j, i)] = e
    for (j, i, _k), s in start_map.items():
        ji_start[(j, i)] = s

    stages = instance.stage_id_list
    for j in instance.job_id_list:
        for i, next_i in zip(stages[:-1], stages[1:]):
            assert ji_end[(j, i)] <= ji_start[(j, next_i)], (j, i, next_i)


def test_delay_all_stages_single_stage_equivalent_to_last_stage_helper() -> None:
    """With a single-stage schedule the earlier-stage loop is empty, so
    ``..._all_stages`` is exactly the last-stage delegate."""
    single_stage_instance = FFcDDWParameters(
        name="rj_single_stage",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"]},
        p_manager=JobStageProcessingTimeManager(
            name="rj_single_stage_p",
            df=pd.DataFrame([[2], [3], [1]]),
        ),
        job_2_due_window_map={"j0": (4, 6), "j1": (5, 7), "j2": (3, 5)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 2, "j1": 2, "j2": 2},
    )
    sched_all = _build_seed_schedule(single_stage_instance)
    sched_last_only = sched_all.deepcopy()

    sched_last_only.delay_job_latest_leq_obj_contrib(
        single_stage_instance.job_2_dw_ub_map
    )
    sched_all.delay_job_latest_leq_obj_contrib_all_stages(
        single_stage_instance.job_2_dw_ub_map
    )

    assert sched_all.get_jik_2_start_time_map() == sched_last_only.get_jik_2_start_time_map()
    assert sched_all.get_jik_2_end_time_map() == sched_last_only.get_jik_2_end_time_map()
