"""Build a full schedule from a last-stage-only ``FFcSchedule``.

Two layers live here:

  - ``reverse_dispatch_full_schedule`` is the pure reverse-dispatch + unflip
    core. It runs the reversed dispatcher twice, picks the makespan winner,
    unflips, then post-processes (``make_semi_active`` + ``insert_idle_time``).
    Returns a ``Phase3State`` with intermediate snapshots.

  - ``build_full_sch_from_last_stage_only_sch`` is the algorithm-level
    entry point used by the controller wrapper / composite. It wraps
    ``reverse_dispatch_full_schedule``, measures elapsed wall time, and
    packages outputs (final schedule, dispatched obj, intermediates) into
    a ``BuildFullSchResult`` for the caller to record.
"""

import logging
import time
from dataclasses import dataclass, field

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..dispatcher import MixedDispatcher

__all__ = [
    "BuildFullSchResult",
    "Phase3State",
    "build_full_sch_from_last_stage_only_sch",
    "reverse_dispatch_full_schedule",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase3State:
    """Outputs of Phase 3 consumed by Phase 4 (and by controller sidecar)."""

    full_sch_from_ls_only_sch: FFcSchedule
    dispatched_obj: float

    # Last-stage-only schedule used as input to the reverse-dispatch chain.
    # Populated whenever any reverse-dispatch happens (multi-stage). When
    # ``rebuild_last_stage_with_original_p=True`` it carries the rebuilt
    # original-p variant (same end times, recomputed starts). Otherwise it
    # is a deepcopy of the caller-supplied schedule. ``None`` only on the
    # single-stage short-circuit.
    ls_only_sch_before_delay: FFcSchedule | None = None
    # Reversed-instance intermediates (None when single-stage short-circuit fires).
    ls_only_sch_delayed: FFcSchedule | None = None
    ls_only_sch_flipped: FFcSchedule | None = None
    full_sch_before_unflip: FFcSchedule | None = None
    # Full schedule on the original (un-reversed) instance, captured
    # immediately after ``as_reversed()`` and before the post-process
    # ``make_semi_active`` + ``insert_idle_time`` left-shift / ET-aligning.
    full_sch_after_unflip: FFcSchedule | None = None


def reverse_dispatch_full_schedule(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    last_stage_id: str | None = None,
    job_2_pos: dict[str, int] | None = None,
    rebuild_last_stage_with_original_p: bool = False,
    logger: logging.Logger | None = None,
) -> Phase3State | None:
    """Build the full dispatched schedule via reverse-dispatch + unflip.

    Pure helper -- no diagnostic side effects. The caller mutates a
    :class:`MCFLBDiagnostic` if it needs to record timing/objective there.

    The input last-stage schedule is taken as the starting point (or, when
    ``rebuild_last_stage_with_original_p=True``, the rebuilt schedule
    described below is used as the starting point instead). When
    ``instance.stage_count == 1`` that starting schedule IS the full
    schedule; the reverse-dispatch is skipped and the delayed / flipped /
    before-unflipping sidecar slots stay ``None``.

    For ``instance.stage_count > 1``, the starting schedule is first
    delayed via :meth:`FFcSchedule.delay_job_latest_leq_obj_contrib` so
    each operation ends as late as possible without increasing its
    per-job objective contribution. The delayed schedule's makespan is
    then used as the flip horizon.

    The reversed dispatcher is run twice -- once with
    ``machine_then_job=False`` and once with ``machine_then_job=True`` --
    and the candidate with the shorter ``reversed_full`` makespan is kept;
    only that winner is unflipped via `FFcSchedule.as_reversed`.
    If exactly one branch returns ``None``, the other is used;
    if both return ``None``, this function returns ``None``
    (with a warning logged when ``logger`` is supplied).

    Args:
        instance: Original (non-reversed) FFcDDW instance.
        last_stage_only_schedule: Schedule whose last stage is fully
            populated (other stages may be empty). Treated as immutable -
            the delay step operates on a deep copy.
        last_stage_id: Defaults to ``instance.stage_id_list[-1]``.
        job_2_pos: Tie-break order for the reverse job sequence.
            Defaults to instance-native order
            (``{j: i for i, j in enumerate(instance.job_id_list)}``).
        rebuild_last_stage_with_original_p: When ``True``, rebuild the
            input last-stage schedule using ``instance``'s last-stage
            processing times before any further processing: each
            operation's end time is preserved and its start is recomputed
            as ``end - p_orig_j``. The rebuilt schedule then replaces the
            input as the starting point for both the single-stage
            short-circuit and the multi-stage delay/reverse-dispatch
            path. Use this when the input schedule was produced under
            inflated last-stage durations (e.g. by
            ``heuristic_last_stage_only_sch_from_mcf_lb`` invoked with
            ``p_increment != 0``) so downstream consumers operate on a
            problem-feasible schedule. The rebuilt schedule is exposed
            on ``Phase3State.ls_only_sch_before_delay``.
        logger: Optional logger; warnings are emitted on dispatcher failure.

    Returns ``None`` if both reversed-dispatcher attempts fail (a warning
    is logged via ``logger`` when supplied).
    """
    if last_stage_id is None:
        last_stage_id = instance.stage_id_list[-1]
    if job_2_pos is None:
        job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}

    ls_only_sch_before_delay: FFcSchedule | None = None
    ls_only_sch_delayed: FFcSchedule | None = None
    ls_only_sch_flipped: FFcSchedule | None = None
    full_sch_before_unflip: FFcSchedule | None = None
    full_sch_after_unflip: FFcSchedule | None = None

    if rebuild_last_stage_with_original_p:
        p_map = instance.get_job_2_p_map_for_stage(last_stage_id)
        init_schedule = FFcSchedule(
            jobs=list(instance.job_id_list),
            stages=list(instance.stage_id_list),
            machines_per_stage={
                s: list(instance.stage_2_machines_map[s])
                for s in instance.stage_id_list
            },
        )
        for (
            mc_id,
            _aug_start,
            aug_end,
            job_id,
        ) in last_stage_only_schedule.iter_operations_on_stage(last_stage_id):
            init_schedule.add_ops_times_2_mc(
                stage_id=last_stage_id,
                mc_id=mc_id,
                job_id=job_id,
                start_time=aug_end - p_map[job_id],
                end_time=aug_end,
            )
    else:
        init_schedule = last_stage_only_schedule

    if instance.stage_count == 1:
        full_sch_from_ls_only_sch = init_schedule
    else:
        # Caller-spec snapshot: ``ls_only_sch_before_delay`` is captured
        # for every multi-stage call (deepcopy so subsequent mutations on
        # ``init_schedule`` / its derivatives don't leak back).
        ls_only_sch_before_delay = init_schedule.deepcopy()
        # Delay last-stage operations to the latest end time that does not
        # worsen per-job ET contribution. Operates on a copy so the caller's
        # input schedule stays untouched.
        ls_only_sch_delayed = init_schedule.deepcopy()
        ls_only_sch_delayed.delay_job_latest_leq_obj_contrib(instance.job_2_dw_ub_map)
        delayed_makespan = ls_only_sch_delayed.makespan

        last_stage_end_map: dict[str, int] = {}
        for _, _, end_time, job_id in ls_only_sch_delayed.iter_operations_on_stage(
            last_stage_id
        ):
            last_stage_end_map[job_id] = end_time
        # Every instance job must appear on the last stage; missing keys
        # raise KeyError below (no silent defaulting).
        rev_job_sequence = sorted(
            instance.job_id_list,
            key=lambda j: (-last_stage_end_map[j], job_2_pos[j]),
        )

        reversed_instance = FFcDDWParameters.reverse_stages(instance)
        reversed_seed = FFcSchedule(
            jobs=reversed_instance.job_id_list,
            stages=reversed_instance.stage_id_list,
            machines_per_stage=reversed_instance.stage_2_machines_map,
        )
        for mc_id, s, e, j in ls_only_sch_delayed.iter_operations_on_stage(
            last_stage_id
        ):
            reversed_seed.add_ops_times_2_mc(
                stage_id=last_stage_id,
                mc_id=mc_id,
                job_id=j,
                start_time=delayed_makespan - e,
                end_time=delayed_makespan - s,
            )
        ls_only_sch_flipped = reversed_seed

        rev_dispatcher = MixedDispatcher(reversed_instance)
        reversed_full_jtm = rev_dispatcher.get_best_mixed_schedule_by_sequence(
            rev_job_sequence,
            schedule=reversed_seed,
            from_stage=reversed_instance.stage_id_list[1],
            machine_then_job=False,
            criteria="makespan",
        )
        reversed_full_mtj = rev_dispatcher.get_best_mixed_schedule_by_sequence(
            rev_job_sequence,
            schedule=reversed_seed,
            from_stage=reversed_instance.stage_id_list[1],
            machine_then_job=True,
            criteria="makespan",
        )
        if reversed_full_jtm is None and reversed_full_mtj is None:
            if logger is not None:
                logger.warning(
                    "reverse_dispatch_full_schedule: reversed MixedDispatcher "
                    "produced no schedule for either machine_then_job=False or "
                    "machine_then_job=True"
                )
            return None
        if reversed_full_jtm is None:
            reversed_full = reversed_full_mtj
        elif reversed_full_mtj is None:
            reversed_full = reversed_full_jtm
        elif reversed_full_jtm.makespan <= reversed_full_mtj.makespan:
            reversed_full = reversed_full_jtm
        else:
            reversed_full = reversed_full_mtj

        full_sch_before_unflip = reversed_full
        full_sch_from_ls_only_sch = reversed_full.as_reversed()
        full_sch_after_unflip = full_sch_from_ls_only_sch.deepcopy()
        # Push left to a semi-active form, then insert idle time on the last
        # stage so the unflipped operations land at ET-optimal positions
        # before scoring (mirrors `_dispatch_by_reversed_sequence_with_iit`).
        full_sch_from_ls_only_sch.make_semi_active(instance.stage_2_job_2_p_map)
        full_sch_from_ls_only_sch.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )

    sum_e, sum_t = compute_weighted_earliness_tardiness(
        full_sch_from_ls_only_sch, instance
    )
    dispatched_obj = float(sum_e + sum_t)

    return Phase3State(
        full_sch_from_ls_only_sch=full_sch_from_ls_only_sch,
        dispatched_obj=dispatched_obj,
        ls_only_sch_before_delay=ls_only_sch_before_delay,
        ls_only_sch_delayed=ls_only_sch_delayed,
        ls_only_sch_flipped=ls_only_sch_flipped,
        full_sch_before_unflip=full_sch_before_unflip,
        full_sch_after_unflip=full_sch_after_unflip,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildFullSchResult:
    """Aggregate result of one ``build_full_sch_from_last_stage_only_sch`` call.

    ``schedule`` is the final full schedule on the original (un-reversed)
    instance after the post-process ``make_semi_active`` + ``insert_idle_time``
    pass. It is ``None`` only when both reverse-dispatch attempts fail.

    ``intermediate_schedules`` carries the inner phase snapshots in
    diagnostic order. Labels are unprefixed (callers prepend numbered
    indices when recording into orchestration-side phase lists). Empty
    when ``schedule is None``; for ``stage_count == 1`` instances only
    ``"fullS_after_sa_iti"`` is included, since the reverse-dispatch is
    skipped.
    """

    schedule: FFcSchedule | None
    dispatched_obj: float | None
    full_sch_makespan: int | None
    dispatch_sec: float
    intermediate_schedules: list[tuple[str, FFcSchedule]] = field(default_factory=list)


def build_full_sch_from_last_stage_only_sch(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    rebuild_last_stage_with_original_p: bool = False,
    logger: logging.Logger | None = None,
) -> BuildFullSchResult:
    """Build the full schedule from a last-stage-only schedule.

    Thin algorithm-level wrapper around ``reverse_dispatch_full_schedule``:
    measures elapsed time, packages the resulting ``Phase3State`` into a
    ``BuildFullSchResult`` with an ordered ``intermediate_schedules`` list
    suitable for the caller to record into ``mcf_lb_phase_schedules``.

    Args:
        instance: Original (non-reversed) FFcDDW instance.
        last_stage_only_schedule: Schedule whose last stage is fully
            populated; treated as immutable.
        rebuild_last_stage_with_original_p: When ``True``, rebuild the
            input last-stage schedule using ``instance``'s last-stage
            processing times before any further processing. Use when the
            input was produced under inflated last-stage durations (e.g.
            by ``heuristic_last_stage_only_from_mcf_lb`` invoked with
            ``p_increment != 0``).
        logger: Optional logger; warnings are emitted on dispatcher failure.

    Returns:
        ``BuildFullSchResult``. When both reverse-dispatch attempts fail,
        returns a result with ``schedule=None`` (and ``intermediate_schedules``
        empty); the caller should treat this as a no-op build.
    """
    start_elapsed = time.monotonic()
    state = reverse_dispatch_full_schedule(
        instance,
        last_stage_only_schedule,
        rebuild_last_stage_with_original_p=rebuild_last_stage_with_original_p,
        logger=logger,
    )
    elapsed = time.monotonic() - start_elapsed
    if state is None:
        return BuildFullSchResult(
            schedule=None,
            dispatched_obj=None,
            full_sch_makespan=None,
            dispatch_sec=elapsed,
        )

    intermediates: list[tuple[str, FFcSchedule]] = []
    if state.ls_only_sch_before_delay is not None:
        intermediates.append(("lastS_only_before_rs", state.ls_only_sch_before_delay))
    if state.ls_only_sch_delayed is not None:
        intermediates.append(("lastS_only_after_rs", state.ls_only_sch_delayed))
    if state.ls_only_sch_flipped is not None:
        intermediates.append(("lastS_only_flipped", state.ls_only_sch_flipped))
    if state.full_sch_before_unflip is not None:
        intermediates.append(("fullS_before_unflip", state.full_sch_before_unflip))
    if state.full_sch_after_unflip is not None:
        intermediates.append(("fullS_after_unflip", state.full_sch_after_unflip))
    intermediates.append(("fullS_after_sa_iti", state.full_sch_from_ls_only_sch))

    return BuildFullSchResult(
        schedule=state.full_sch_from_ls_only_sch,
        dispatched_obj=state.dispatched_obj,
        full_sch_makespan=int(state.full_sch_from_ls_only_sch.makespan),
        dispatch_sec=elapsed,
        intermediate_schedules=intermediates,
    )
