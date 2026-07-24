"""Build an ``FFcSchedule`` from CP-SAT operation start/end times."""

from __future__ import annotations

from typing import Mapping, Sequence

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffc_schedule import FFcSchedule

__all__ = [
    "build_active_except_last_from_reference",
    "build_active_from_reference",
    "build_schedule_from_op_starts",
    "reconstruct_active_coarse_schedule",
    "reconstruct_active_except_last_coarse_schedule",
    "reconstruct_coarse_schedule",
    "reconstruct_raw_coarse_schedule",
    "validate_reconstructed_schedule",
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


def build_active_from_reference(
    reference: FFcSchedule,
    instance: FFcDDWParameters,
    stage_2_job_2_duration: Mapping[str, Mapping[str, int]],
) -> FFcSchedule:
    """Rebuild an **active** schedule that preserves ``reference``'s per-stage
    operation start-order but re-assigns machines freely.

    The sibling of :func:`reconstruct_raw_coarse_schedule`. Both keep what the
    reference solution actually *decided* — the order operations run in — and
    re-derive times on the original scale. They differ in what else they carry:

    * ``reconstruct_raw_coarse_schedule`` freezes the reference's **machine
      assignment** (each machine keeps its own job order, only times move).
    * this function frees the assignment. It keeps only the reference's
      per-stage operation **start order** (machine-agnostic: which machine an
      operation sat on is discarded) and dispatches that order onto the
      earliest-available machine, so an operation may land on a different
      machine than in ``reference``. The result is an active schedule — no
      operation can start earlier without reordering or delaying another.

    Processing is front-to-back over stages. Within a stage the dispatch order
    is ``(reference_start_time, due2-weight-pos position)``: the reference start
    time is the primary key; ``instance.get_due2_weight_pos_job_sequence()``
    breaks ties between operations the reference started simultaneously. Only
    the *order* is taken from ``reference`` — actual start times are recomputed
    by :meth:`FFcSchedule.dispatch_stage_by_jobs` (earliest-start machine
    selection, precedence enforced against already-placed prior stages), so the
    reference's absolute time scale (coarse or fine) is irrelevant.

    Args:
        reference: Schedule supplying the per-stage operation start order. Must
            share ``instance``'s job/stage/machine layout and cover every
            ``(job, stage)`` pair.
        instance: The original-scale instance supplying the due2-weight-pos
            tie-break order and the machine layout of the new schedule.
        stage_2_job_2_duration: Processing durations indexed ``[stage][job]``
            (the axis order :meth:`FFcSchedule.dispatch_stage_by_jobs` expects;
            note it is the transpose of ``instance.job_2_stage_2_p_map``). Pass
            ``instance.stage_2_job_2_p_map`` for an original-scale rebuild.

    Raises:
        ValueError: If ``reference`` is missing any ``(job, stage)`` operation.
            A missing operation would be absent from the dispatch order and
            vanish silently, scoring a truncated schedule as complete — the
            same hazard :func:`reconstruct_raw_coarse_schedule` guards against.
    """
    _validate_coarse_schedule_covers_instance(reference, instance)
    tie_breaker = {
        job_id: pos
        for pos, job_id in enumerate(instance.get_due2_weight_pos_job_sequence())
    }
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for stage_id in instance.stage_id_list:
        ref_start: dict[str, int] = {}
        for mc_id in instance.stage_2_machines_map[stage_id]:
            for job_id, start_time, _end in reference.get_job_sequence(stage_id, mc_id):
                ref_start[job_id] = start_time
        order = sorted(ref_start, key=lambda j: (ref_start[j], tie_breaker[j]))
        schedule.dispatch_stage_by_jobs(
            stage_id,
            order,
            stage_2_job_2_duration[stage_id],
            force_job_id_seq_as_priority=True,
        )
    return schedule


def reconstruct_active_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
) -> FFcSchedule:
    """Active-reconstruct a coarse-scale schedule onto the original scale.

    Sibling of :func:`reconstruct_coarse_schedule`: instead of carrying the
    coarse machine assignment (semi-active), it preserves only the coarse
    per-stage operation start-order and re-assigns machines by earliest start
    (:func:`build_active_from_reference`), then runs ``insert_idle_time`` to
    land operations at ET-optimal positions.

    There is no ``make_semi_active`` call: the active build already places every
    operation as early as feasible, so any semi-active left-shift would be a
    no-op (``build_active_from_reference`` yields an active — hence semi-active —
    schedule). Unlike :func:`reconstruct_coarse_schedule` there is no ``factor``
    argument: times are re-derived from the original processing times, so the
    coarse scale is never read.
    """
    schedule = build_active_from_reference(
        coarse_schedule, instance, instance.stage_2_job_2_p_map
    )
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return schedule


def build_active_except_last_from_reference(
    reference: FFcSchedule,
    instance: FFcDDWParameters,
    stage_2_job_2_duration: Mapping[str, Mapping[str, int]],
) -> FFcSchedule:
    """Build an active schedule that preserves ``reference``'s per-stage
    start-order for all but the last stage, where the coarse machine assignment
    and per-machine job order are carried verbatim.

    The sibling of :func:`build_active_from_reference` and
    :func:`reconstruct_raw_coarse_schedule`. This hybrid:

    * For every stage **except the last**: dispatches actively — the reference's
      per-stage start order is preserved (machine-agnostic), and machines are
      re-assigned by earliest start via
      :meth:`FFcSchedule.dispatch_stage_by_jobs`.
    * For the **last stage**: freezes the reference's machine assignment and
      per-machine job order (same as
      :func:`reconstruct_raw_coarse_schedule`), but reads the previous-stage
      end times from the **actively rebuilt** earlier stages rather than the
      coarse schedule's times.

    The result is active in all but the last stage and left-shifted everywhere.

    Processing is front-to-back over stages. The tie-break for active dispatch
    is ``(reference_start_time, due2-weight-pos position)``, identical to
    :func:`build_active_from_reference`.

    When ``len(stages) == 1``, the last stage is the only stage and this
    behaves identically to semi-active reconstruction — the guard delegates
    to a left-shifted projection of ``reference``'s assignment and order
    (equivalent to ``reconstruct_raw_coarse_schedule`` without ET
    postprocessing).

    Args:
        reference: Schedule supplying per-stage order and last-stage assignment.
        instance: The original-scale instance.
        stage_2_job_2_duration: Processing durations indexed ``[stage][job]``.
    """
    _validate_coarse_schedule_covers_instance(reference, instance)
    stages = instance.stage_id_list

    if len(stages) == 1:
        schedule = FFcSchedule(
            jobs=instance.job_id_list,
            stages=stages,
            machines_per_stage=instance.stage_2_machines_map,
        )
        mc_stg_id = stages[0]
        p_map = instance.job_2_stage_2_p_map
        for mc_id in instance.stage_2_machines_map[mc_stg_id]:
            machine_end = 0
            for j, _cs, _ce in reference.get_job_sequence(mc_stg_id, mc_id):
                start = machine_end
                end = start + p_map[j][mc_stg_id]
                schedule.add_ops_times_2_mc(mc_stg_id, mc_id, j, start, end)
                machine_end = end
        return schedule

    tie_breaker = {
        job_id: pos
        for pos, job_id in enumerate(instance.get_due2_weight_pos_job_sequence())
    }
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=stages,
        machines_per_stage=instance.stage_2_machines_map,
    )

    for stage_id in stages[:-1]:
        ref_start: dict[str, int] = {}
        for mc_id in instance.stage_2_machines_map[stage_id]:
            for job_id, start_time, _end in reference.get_job_sequence(stage_id, mc_id):
                ref_start[job_id] = start_time
        order = sorted(ref_start, key=lambda j: (ref_start[j], tie_breaker[j]))
        schedule.dispatch_stage_by_jobs(
            stage_id,
            order,
            stage_2_job_2_duration[stage_id],
            force_job_id_seq_as_priority=True,
        )

    last_stage = stages[-1]
    prev_stage = stages[-2]
    original_p = instance.job_2_stage_2_p_map
    for mc_id in instance.stage_2_machines_map[last_stage]:
        machine_end = 0
        for j, _coarse_start, _coarse_end in reference.get_job_sequence(
            last_stage, mc_id
        ):
            prev_end = schedule.get_job_end_time(prev_stage, j)
            start = max(prev_end, machine_end)
            end = start + original_p[j][last_stage]
            schedule.add_ops_times_2_mc(last_stage, mc_id, j, start, end)
            machine_end = end

    return schedule


def reconstruct_active_except_last_coarse_schedule(
    coarse_schedule: FFcSchedule,
    instance: FFcDDWParameters,
) -> FFcSchedule:
    """Active-except-last reconstruct a coarse schedule onto the original scale.

    Sibling of :func:`reconstruct_coarse_schedule` and
    :func:`reconstruct_active_coarse_schedule`: builds with
    :func:`build_active_except_last_from_reference`, then runs
    ``insert_idle_time`` on the original-scale instance to land operations at
    ET-optimal positions.

    Like :func:`reconstruct_active_coarse_schedule`, there is no ``factor``
    argument: times are re-derived from the original processing times.
    """
    schedule = build_active_except_last_from_reference(
        coarse_schedule, instance, instance.stage_2_job_2_p_map
    )
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return schedule


def validate_reconstructed_schedule(
    schedule: FFcSchedule,
    instance: FFcDDWParameters,
) -> None:
    """Assert a reconstructed schedule is structurally feasible.

    Checks:
    1. Every ``(job, stage)`` pair present.
    2. Every start >= 0.
    3. Duration matches ``instance.job_2_stage_2_p_map``.
    4. Stage precedence: each op starts at or after the previous stage's end.
    5. No machine overlap.

    Raises ``AssertionError`` on violation. Intended as a shared post-reconstruct
    safety net for both the controller and offline replay scripts.
    """
    ops: dict[tuple[str, str, str], tuple[int, int]] = {}
    for (j, i, mc), s in schedule.get_jik_2_start_time_map().items():
        e = schedule.get_jik_2_end_time_map()[(j, i, mc)]
        ops[(j, i, mc)] = (s, e)

    present = {(j, i) for (j, i, _mc) in ops}
    expected = {(j, i) for i in instance.stage_id_list for j in instance.job_id_list}
    missing = expected - present
    assert not missing, f"Missing operations: {missing}"

    stage_list = instance.stage_id_list
    original_p = instance.job_2_stage_2_p_map

    for (j, i, mc), (s, e) in ops.items():
        assert s >= 0, f"Negative start: ({j},{i},{mc}) start={s}"
        p_ij = original_p[j][i]
        assert e - s == p_ij, (
            f"Duration mismatch: ({j},{i},{mc}) start={s} end={e} p={p_ij}"
        )
        if i != stage_list[0]:
            prev_i = stage_list[stage_list.index(i) - 1]
            prev_ends = [
                oe for (jj, ii, _mc), (_, oe) in ops.items() if jj == j and ii == prev_i
            ]
            if prev_ends:
                assert s >= max(prev_ends), (
                    f"Precedence violation: ({j},{i}) start={s}"
                    f" < prev_end={max(prev_ends)}"
                )
        for (jj, ii, mmc), (os, oe) in ops.items():
            if ii == i and mmc == mc and jj != j:
                assert not (s < oe and e > os), (
                    f"Overlap: ({j},{i},{mc}) [{s},{e}]"
                    f" vs ({jj},{ii},{mmc}) [{os},{oe}]"
                )
