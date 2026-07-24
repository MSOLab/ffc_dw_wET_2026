"""Unit tests for the coarse-scale reconstruct helpers in ``schedule_build``.

These exercise ``reconstruct_raw_coarse_schedule`` (assignment + order transfer,
no idle-time insertion) and ``reconstruct_coarse_schedule`` (the ET-aligned
wrapper) directly, independent of the coarsen-solve-reconstruct pipeline. The
pipeline itself is covered at the trace level in
``tests/algorithm/test_coarsen_solve_reconstruct.py``; these pin the contract of
the extracted, reusable functions.

Reconstruction carries the coarse solution's **machine assignment and
per-machine job order**, not its times; see
``reconstruct_raw_coarse_schedule``'s docstring for why.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.schedule_build import (
    build_active_except_last_from_reference,
    build_active_from_reference,
    build_schedule_from_op_starts,
    reconstruct_active_coarse_schedule,
    reconstruct_active_except_last_coarse_schedule,
    reconstruct_coarse_schedule,
    reconstruct_raw_coarse_schedule,
    validate_reconstructed_schedule,
)

FACTOR = 10


def _instance() -> FFcDDWParameters:
    """3 jobs × 2 stages; i0 has 2 machines, i1 has 1. Original p = 10 every op."""
    return FFcDDWParameters(
        name="recon_test",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="recon_p", df=pd.DataFrame([[10, 10], [10, 10], [10, 10]])
        ),
        job_2_due_window_map={"j0": (10, 20), "j1": (20, 30), "j2": (30, 40)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _coarse_schedule(
    instance: FFcDDWParameters,
) -> tuple[object, dict[tuple[str, str], int]]:
    """A feasible coarse-scale schedule (coarse p == 1 at FACTOR=10).

    The last stage (i1) is deliberately *gapped* (starts 5/6/7) so that the
    reconstruction's ``make_semi_active`` left-shift is non-trivial, letting
    tests distinguish the raw schedule from the post-processed one.
    """
    coarsened = FFcDDWParameters.coarsen_processing_times(instance, FACTOR)
    coarse_start = {
        ("j0", "i0"): 0,
        ("j1", "i0"): 0,
        ("j2", "i0"): 1,
        ("j0", "i1"): 5,
        ("j1", "i1"): 6,
        ("j2", "i1"): 7,
    }
    coarse_end = {k: v + 1 for k, v in coarse_start.items()}
    coarse_sched = build_schedule_from_op_starts(coarsened, coarse_start, coarse_end)
    return coarse_sched, coarse_start


def _ji_starts(schedule) -> dict[tuple[str, str], int]:
    return {(j, i): s for (j, i, _mc), s in schedule.get_jik_2_start_time_map().items()}


def _ji_ends(schedule) -> dict[tuple[str, str], int]:
    return {(j, i): e for (j, i, _mc), e in schedule.get_jik_2_end_time_map().items()}


def _ji_machines(schedule) -> dict[tuple[str, str], str]:
    return {(j, i): mc for (j, i, mc) in schedule.get_jik_2_start_time_map()}


def _machine_orders(schedule, instance) -> dict[tuple[str, str], list[str]]:
    """(stage, machine) -> job ids in scheduled order."""
    return {
        (i, mc): [j for j, _s, _e in schedule.get_job_sequence(i, mc)]
        for i in instance.stage_id_list
        for mc in instance.stage_2_machines_map[i]
    }


def _underallocated_instance() -> FFcDDWParameters:
    """Same layout as ``_instance`` but original p = 15, so at FACTOR=10 a
    ``floor``-style coarse p of 1 gives ``K*p' = 10 < 15 = p``.

    This is the regime ``ceil`` never produces and that the old
    scale-the-starts reconstruction could not represent.
    """
    return FFcDDWParameters(
        name="recon_underalloc",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="recon_p", df=pd.DataFrame([[15, 15], [15, 15], [15, 15]])
        ),
        job_2_due_window_map={"j0": (10, 20), "j1": (20, 30), "j2": (30, 40)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_reconstruct_raw_preserves_coarse_machine_assignment() -> None:
    """Reconstruction carries the coarse solver's assignment, not a re-derived one."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    assert _ji_machines(raw) == _ji_machines(coarse_sched)


def test_reconstruct_raw_preserves_per_machine_order() -> None:
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    assert _machine_orders(raw, inst) == _machine_orders(coarse_sched, inst)


def test_reconstruct_raw_is_semi_active() -> None:
    """Raw is already left-shifted, so ``make_semi_active`` is a no-op on it."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    before = raw.get_jik_2_start_time_map()
    raw.make_semi_active(inst.stage_2_job_2_p_map)

    assert raw.get_jik_2_start_time_map() == before


def test_reconstruct_raw_handles_underallocated_operations() -> None:
    """``K*p' < p`` must reconstruct feasibly instead of raising RuntimeError.

    This is the regime ``round``/``floor`` coarsening produces and that blocked
    the rounding-mode experiment.
    """
    inst = _underallocated_instance()
    coarse_start = {
        ("j0", "i0"): 0,
        ("j1", "i0"): 0,
        ("j2", "i0"): 1,
        ("j0", "i1"): 2,
        ("j1", "i1"): 3,
        ("j2", "i1"): 4,
    }
    # Coarse p = 1 everywhere: K*p' = 10 < 15 = original p.
    coarse_p = JobStageProcessingTimeManager(
        name="coarse_p", df=pd.DataFrame([[1, 1], [1, 1], [1, 1]])
    )
    coarsened = FFcDDWParameters(
        name="recon_underalloc_coarsen_k10",
        job_id_list=inst.job_id_list,
        stage_id_list=inst.stage_id_list,
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=coarse_p,
        job_2_due_window_map=inst.job_2_due_window_map,
        job_2_ewt_map=inst.job_2_ewt_map,
        job_2_twt_map=inst.job_2_twt_map,
    )
    coarse_sched = build_schedule_from_op_starts(
        coarsened, coarse_start, {k: v + 1 for k, v in coarse_start.items()}
    )

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    assert _ji_machines(raw) == _ji_machines(coarse_sched)
    starts = raw.get_jik_2_start_time_map()
    ends = raw.get_jik_2_end_time_map()
    original_p = inst.job_2_stage_2_p_map
    for (j, i, mc), s in starts.items():
        assert ends[j, i, mc] - s == original_p[j][i]
        # Precedence: stage i1 cannot start before stage i0 of the same job ends.
        if i == "i1":
            assert s >= max(
                e for (jj, ii, _m), e in ends.items() if jj == j and ii == "i0"
            )


def test_reconstruct_raw_rejects_coarse_schedule_missing_an_operation() -> None:
    """An incomplete coarse schedule must fail loudly, not reconstruct partially.

    Reconstruction only visits operations present in the coarse schedule, so a
    missing one would silently vanish and the truncated schedule would be scored
    as if valid — reporting a better objective than the solution deserves. Only
    one of the four call sites runs ``check_feasibility`` afterwards, so the
    guard belongs here.
    """
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)
    coarsened = FFcDDWParameters.coarsen_processing_times(inst, FACTOR)

    # Same coarse schedule minus j2's last-stage operation.
    partial_start = {
        ("j0", "i0"): 0,
        ("j1", "i0"): 0,
        ("j2", "i0"): 1,
        ("j0", "i1"): 5,
        ("j1", "i1"): 6,
    }
    partial = build_schedule_from_op_starts(
        coarsened,
        partial_start,
        {k: v + 1 for k, v in partial_start.items()},
        stages=["i0"],
        jobs=["j0", "j1", "j2"],
    )
    for (j, i), s in partial_start.items():
        if i == "i1":
            partial.add_ops_times_2_mc(i, "i1_0", j, s, s + 1)

    with pytest.raises(ValueError, match=r"j2.*i1|i1.*j2"):
        reconstruct_raw_coarse_schedule(partial, inst, FACTOR)

    # The complete schedule from the same fixture must still pass.
    reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)


def test_reconstruct_raw_reapplies_original_p() -> None:
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    starts = raw.get_jik_2_start_time_map()
    ends = raw.get_jik_2_end_time_map()
    original_p = inst.job_2_stage_2_p_map
    for (j, i, mc), s in starts.items():
        assert ends[j, i, mc] - s == original_p[j][i]


def test_reconstruct_raw_is_pre_idle_time_insertion() -> None:
    """Raw closes the coarse schedule's gaps rather than inflating them.

    The coarse last stage is deliberately gapped (starts 5/6/7). The old
    scale-the-starts reconstruction kept those gaps (50/60/70); the new one
    left-shifts them away.
    """
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    raw_start = _ji_starts(raw)
    # Left-shifted: j0's last-stage op starts as soon as its i0 op ends.
    assert raw_start["j0", "i1"] == _ji_ends(raw)["j0", "i0"]
    # ...not at the inflated coarse position the old contract produced.
    assert raw_start["j0", "i1"] != 5 * FACTOR


def test_reconstruct_coarse_applies_idle_time_on_top_of_raw() -> None:
    """When the semi-active positions miss the due windows, final differs from raw.

    ``_instance``'s windows are hit exactly by the left-shifted schedule, so
    ``insert_idle_time`` is a no-op there. Shifting the windows later forces it
    to act, which is what distinguishes the wrapper from the raw snapshot.
    """
    inst = _instance()
    late = FFcDDWParameters(
        name="recon_test_late_windows",
        job_id_list=inst.job_id_list,
        stage_id_list=inst.stage_id_list,
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=inst.p_manager,
        job_2_due_window_map={"j0": (60, 70), "j1": (70, 80), "j2": (80, 90)},
        job_2_ewt_map=inst.job_2_ewt_map,
        job_2_twt_map=inst.job_2_twt_map,
    )
    coarse_sched, _ = _coarse_schedule(late)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, late, FACTOR)
    final = reconstruct_coarse_schedule(coarse_sched, late, FACTOR)

    assert _ji_starts(raw) != _ji_starts(final)
    # Idle insertion only delays operations; it never pulls them earlier.
    for key, start in _ji_starts(raw).items():
        assert _ji_starts(final)[key] >= start


def test_reconstruct_coarse_equals_raw_plus_postprocess() -> None:
    """The wrapper is exactly raw + insert_idle_time.

    No ``make_semi_active``: raw is already semi-active (see
    ``test_reconstruct_raw_is_semi_active``), so the call would be a no-op.
    """
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    final = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    raw.insert_idle_time(
        inst.job_2_due_window_map,
        inst.job_2_ewt_map,
        inst.job_2_twt_map,
    )

    assert raw.get_jik_2_start_time_map() == final.get_jik_2_start_time_map()
    assert raw.get_jik_2_end_time_map() == final.get_jik_2_end_time_map()


def test_reconstruct_raw_and_coarse_are_distinct_objects() -> None:
    """Separate calls return independent schedules (CSR keeps both as snapshots)."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    final = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    assert raw is not final


def test_reconstruct_raw_not_mutated_by_building_wrapper() -> None:
    """Building the wrapper must not mutate a separately-obtained raw schedule."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    before = _ji_starts(raw)

    _ = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    assert _ji_starts(raw) == before


def test_reconstruct_raw_uses_original_instance_layout() -> None:
    """Reconstruction is on the original instance: stage ids and durations match."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    assert raw.stages == inst.stage_id_list
    assert set(raw.jobs) == set(inst.job_id_list)


# --- active reconstruction (build_active_from_reference / --------------------
# --- reconstruct_active_coarse_schedule) -------------------------------------


def _crowded_reference(inst: FFcDDWParameters) -> FFcSchedule:
    """A feasible reference that crowds every stage-i0 op onto ``i0_0``.

    ``i0_1`` sits idle even though it is free — the semi-active reconstruction
    would carry that waste verbatim, while the active rebuild must spread the
    work across both machines. ``p == 10`` everywhere, so the reference is on
    the original scale (the active rule only reads its start *order*, not the
    scale).
    """
    ref = FFcSchedule(
        jobs=inst.job_id_list,
        stages=inst.stage_id_list,
        machines_per_stage=inst.stage_2_machines_map,
    )
    ref.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 10)
    ref.add_ops_times_2_mc("i0", "i0_0", "j1", 10, 20)
    ref.add_ops_times_2_mc("i0", "i0_0", "j2", 20, 30)
    ref.add_ops_times_2_mc("i1", "i1_0", "j0", 10, 20)
    ref.add_ops_times_2_mc("i1", "i1_0", "j1", 20, 30)
    ref.add_ops_times_2_mc("i1", "i1_0", "j2", 30, 40)
    return ref


def _tiebreak_instance() -> FFcDDWParameters:
    """2 jobs × 2 stages; ``i0`` has 2 machines. Due windows order due2-weight-pos
    as ``[j1, j0]`` — the reverse of position order — so a start-time tie is
    broken by due2wp, not by job id."""
    return FFcDDWParameters(
        name="active_tiebreak",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="tb_p", df=pd.DataFrame([[10, 10], [10, 10]])
        ),
        job_2_due_window_map={"j0": (100, 200), "j1": (10, 20)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
    )


def test_build_active_reassigns_machine_when_reference_crowds() -> None:
    """Active frees the machine assignment: an op the reference crowded onto a
    busy machine moves to the idle one."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_from_reference(
        reference=ref,
        instance=inst,
        stage_2_job_2_duration=inst.stage_2_job_2_p_map,
    )

    # Reference put j1@i0 on the crowded machine; active moves it to the free one.
    assert _ji_machines(ref)["j1", "i0"] == "i0_0"
    assert _ji_machines(active)["j1", "i0"] == "i0_1"


def test_build_active_preserves_reference_start_order() -> None:
    """Primary sort key is the reference start time: an op that started later in
    the reference never starts earlier than one that started before it, when both
    land on the same machine."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)

    # i0_0 keeps its reference order j0 (start 0) before j2 (start 20).
    order_i0_0 = [j for j, _s, _e in active.get_job_sequence("i0", "i0_0")]
    assert order_i0_0 == ["j0", "j2"]


def test_build_active_tie_break_uses_due2_weight_pos() -> None:
    """When two ops share a reference start time, due2-weight-pos order (not job
    id) decides which is dispatched first — and so which machine it lands on."""
    inst = _tiebreak_instance()
    ref = FFcSchedule(
        jobs=inst.job_id_list,
        stages=inst.stage_id_list,
        machines_per_stage=inst.stage_2_machines_map,
    )
    # Both i0 ops start at 0 (a tie); reference assigns j0->i0_0, j1->i0_1.
    ref.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 10)
    ref.add_ops_times_2_mc("i0", "i0_1", "j1", 0, 10)
    ref.add_ops_times_2_mc("i1", "i1_0", "j0", 10, 20)
    ref.add_ops_times_2_mc("i1", "i1_0", "j1", 20, 30)

    active = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)

    due2wp_first = inst.get_due2_weight_pos_job_sequence()[0]
    # The due2wp-first job is dispatched first, so it takes the first machine...
    assert _ji_machines(active)[due2wp_first, "i0"] == "i0_0"
    # ...which is a genuine change: the reference had it on the second machine.
    assert _ji_machines(ref)[due2wp_first, "i0"] == "i0_1"


def test_build_active_is_semi_active() -> None:
    """The active build is already left-shift-minimal, so ``make_semi_active`` is
    a no-op on it (active ⊂ semi-active)."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)
    before = active.get_jik_2_start_time_map()
    active.make_semi_active(inst.stage_2_job_2_p_map)

    assert active.get_jik_2_start_time_map() == before


def test_build_active_is_feasible() -> None:
    """The active build honors durations and precedence (no-overlap is enforced
    by ``add_ops_times_2_mc`` during the build, which would otherwise raise)."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)

    starts = active.get_jik_2_start_time_map()
    ends = active.get_jik_2_end_time_map()
    original_p = inst.job_2_stage_2_p_map
    for (j, i, mc), s in starts.items():
        assert ends[j, i, mc] - s == original_p[j][i]
        if i == "i1":
            i0_end = max(
                e for (jj, ii, _m), e in ends.items() if jj == j and ii == "i0"
            )
            assert s >= i0_end


def test_build_active_rejects_reference_missing_an_operation() -> None:
    """A reference missing a ``(job, stage)`` op must raise, not silently drop it.

    The active build sources its dispatch order from the reference's operations,
    so a missing one would never be dispatched and would vanish from the result —
    the same silent-truncation hazard the semi-active reconstruct guards against.
    """
    inst = _instance()
    ref = FFcSchedule(
        jobs=inst.job_id_list,
        stages=inst.stage_id_list,
        machines_per_stage=inst.stage_2_machines_map,
    )
    ref.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 10)
    ref.add_ops_times_2_mc("i0", "i0_0", "j1", 10, 20)
    ref.add_ops_times_2_mc("i0", "i0_0", "j2", 20, 30)
    ref.add_ops_times_2_mc("i1", "i1_0", "j0", 10, 20)
    ref.add_ops_times_2_mc("i1", "i1_0", "j1", 20, 30)
    # j2@i1 deliberately absent.

    with pytest.raises(ValueError, match=r"j2.*i1|i1.*j2"):
        build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)


def test_reconstruct_active_covers_every_operation() -> None:
    inst = _instance()
    ref = _crowded_reference(inst)

    final = reconstruct_active_coarse_schedule(ref, inst)

    present = {(j, i) for (j, i, _mc) in final.get_jik_2_start_time_map()}
    expected = {(j, i) for i in inst.stage_id_list for j in inst.job_id_list}
    assert present == expected


def test_reconstruct_active_equals_build_plus_idle() -> None:
    """The wrapper is exactly ``build_active_from_reference`` + insert_idle_time
    (no ``make_semi_active``: the build is already semi-active)."""
    inst = _instance()
    ref = _crowded_reference(inst)

    final = reconstruct_active_coarse_schedule(ref, inst)

    built = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)
    built.insert_idle_time(
        inst.job_2_due_window_map,
        inst.job_2_ewt_map,
        inst.job_2_twt_map,
    )

    assert final.get_jik_2_start_time_map() == built.get_jik_2_start_time_map()
    assert final.get_jik_2_end_time_map() == built.get_jik_2_end_time_map()


# --- active_but_last_semi reconstruction -----------------------------------


def test_build_active_except_last_preserves_last_stage_machine_assignment() -> None:
    """Last stage keeps the coarse machine assignment verbatim."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )

    last_stage = inst.stage_id_list[-1]
    for j in inst.job_id_list:
        assert _ji_machines(active)[j, last_stage] == _ji_machines(ref)[j, last_stage]


def test_build_active_except_last_preserves_last_stage_per_machine_order() -> None:
    """Last stage preserves the coarse per-machine job order."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )

    last_stage = inst.stage_id_list[-1]
    active_orders = _machine_orders(active, inst)
    ref_orders = _machine_orders(ref, inst)
    for mc in inst.stage_2_machines_map[last_stage]:
        assert active_orders[last_stage, mc] == ref_orders[last_stage, mc]


def test_build_active_except_last_reassigns_prior_stage_machines() -> None:
    """Prior stages reassign machines (active behavior), unlike last stage."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )

    prior_stage = inst.stage_id_list[0]
    assert _ji_machines(ref)["j1", prior_stage] == "i0_0"
    assert _ji_machines(active)["j1", prior_stage] == "i0_1"


def test_build_active_except_last_is_feasible() -> None:
    """Result honors durations and precedence, same as other builders."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )

    validate_reconstructed_schedule(active, inst)


def test_build_active_except_last_prior_stage_matches_build_active() -> None:
    """In a 2-stage instance, stage 0 matches build_active_from_reference's stage 0."""
    inst = _instance()
    ref = _crowded_reference(inst)

    hybrid = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )
    full_active = build_active_from_reference(ref, inst, inst.stage_2_job_2_p_map)

    prior_stage = inst.stage_id_list[0]
    hybrid_starts = {
        (j, mc): s
        for (j, i, mc), s in hybrid.get_jik_2_start_time_map().items()
        if i == prior_stage
    }
    active_starts = {
        (j, mc): s
        for (j, i, mc), s in full_active.get_jik_2_start_time_map().items()
        if i == prior_stage
    }
    assert hybrid_starts == active_starts


def test_build_active_except_last_is_semi_active() -> None:
    """Like build_active, the hybrid is already semi-active (make_semi_active no-op)."""
    inst = _instance()
    ref = _crowded_reference(inst)

    active = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )
    before = active.get_jik_2_start_time_map()
    active.make_semi_active(inst.stage_2_job_2_p_map)

    assert active.get_jik_2_start_time_map() == before


def test_reconstruct_active_except_last_covers_every_operation() -> None:
    inst = _instance()
    ref = _crowded_reference(inst)

    final = reconstruct_active_except_last_coarse_schedule(ref, inst)

    present = {(j, i) for (j, i, _mc) in final.get_jik_2_start_time_map()}
    expected = {(j, i) for i in inst.stage_id_list for j in inst.job_id_list}
    assert present == expected


def test_reconstruct_active_except_last_equals_build_plus_idle() -> None:
    """The wrapper = build + insert_idle_time (no make_semi_active)."""
    inst = _instance()
    ref = _crowded_reference(inst)

    final = reconstruct_active_except_last_coarse_schedule(ref, inst)

    built = build_active_except_last_from_reference(ref, inst, inst.stage_2_job_2_p_map)
    built.insert_idle_time(
        inst.job_2_due_window_map,
        inst.job_2_ewt_map,
        inst.job_2_twt_map,
    )

    assert final.get_jik_2_start_time_map() == built.get_jik_2_start_time_map()
    assert final.get_jik_2_end_time_map() == built.get_jik_2_end_time_map()


# --- single-stage guard ---------------------------------------------------


def _single_stage_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="single_stage_recon",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"]},
        p_manager=JobStageProcessingTimeManager(
            name="ss_p", df=pd.DataFrame([[10], [10]])
        ),
        job_2_due_window_map={"j0": (10, 20), "j1": (20, 30)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
    )


def test_single_stage_active_except_last_equals_semi() -> None:
    """With one stage, active_but_last_semi == semi (last stage is the only stage)."""
    inst = _single_stage_instance()
    ref = FFcSchedule(
        jobs=inst.job_id_list,
        stages=inst.stage_id_list,
        machines_per_stage=inst.stage_2_machines_map,
    )
    ref.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 10)
    ref.add_ops_times_2_mc("i0", "i0_1", "j1", 5, 15)

    hybrid_raw = build_active_except_last_from_reference(
        ref, inst, inst.stage_2_job_2_p_map
    )
    semi_raw = reconstruct_raw_coarse_schedule(ref, inst, 1)

    assert _ji_machines(hybrid_raw) == _ji_machines(semi_raw)
    assert _machine_orders(hybrid_raw, inst) == _machine_orders(semi_raw, inst)


def test_validate_reconstructed_schedule_rejects_missing_ops() -> None:
    """Missing operation must raise AssertionError."""
    inst = _instance()
    sched = FFcSchedule(
        jobs=inst.job_id_list,
        stages=inst.stage_id_list,
        machines_per_stage=inst.stage_2_machines_map,
    )
    sched.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 10)
    sched.add_ops_times_2_mc("i0", "i0_1", "j1", 0, 10)
    # j2@i0, j0@i1, j1@i1, j2@i1 intentionally missing

    with pytest.raises(AssertionError, match="Missing"):
        validate_reconstructed_schedule(sched, inst)
