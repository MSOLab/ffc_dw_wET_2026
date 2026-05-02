"""Phase 3 of the MCF-LB pipeline.

Reverse-dispatch with the last stage pinned as seed, then unflip back
to the original instance. Produces the full dispatched schedule that
Phase 4 will warm-start its profile-fix CP-SAT model from.

The reverse-dispatch core is exposed as :func:`reverse_dispatch_full_schedule`
so other subroutines (e.g. ``build_full_sch_from_last_stage_only_sch``) can
build a feasible full schedule from any last-stage-only ``FFcSchedule``
without going through Phase 1 / Phase 2 first.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..dispatcher import MixedDispatcher
from .diagnostic import MCFLBDiagnostic
from .phase1_mcf import Phase1State
from .phase2_last_stage import Phase2State

__all__ = ["Phase3State", "reverse_dispatch_full_schedule", "run_phase3"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase3State:
    """Outputs of Phase 3 consumed by Phase 4 (and by controller sidecar)."""

    full_sch_from_ls_only_sch: FFcSchedule
    dispatched_obj: float

    # Reversed-instance intermediates (None when single-stage short-circuit fires).
    ls_only_sch_delayed: FFcSchedule | None = None
    ls_only_sch_flipped: FFcSchedule | None = None
    full_sch_before_unflip: FFcSchedule | None = None


def reverse_dispatch_full_schedule(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    last_stage_id: str | None = None,
    job_2_pos: dict[str, int] | None = None,
    machine_then_job: bool = False,
    logger: logging.Logger | None = None,
) -> Phase3State | None:
    """Build the full dispatched schedule via reverse-dispatch + unflip.

    Pure helper — no diagnostic side effects. The caller mutates a
    :class:`MCFLBDiagnostic` if it needs to record timing/objective there.

    When ``instance.stage_count == 1`` the last-stage schedule is already
    the full schedule; the reverse-dispatch is skipped and the delayed /
    flipped / before-unflipping sidecar slots stay ``None``.

    For ``instance.stage_count > 1``, the input last-stage schedule is
    first delayed via :meth:`FFcSchedule.delay_job_latest_leq_obj_contrib`
    so each operation ends as late as possible without increasing its
    per-job objective contribution. The delayed schedule's makespan is
    then used as the flip horizon.

    Args:
        instance: Original (non-reversed) FFcDDW instance.
        last_stage_only_schedule: Schedule whose last stage is fully
            populated (other stages may be empty). Treated as immutable —
            the delay step operates on a deep copy.
        last_stage_id: Defaults to ``instance.stage_id_list[-1]``.
        job_2_pos: Tie-break order for the reverse job sequence.
            Defaults to instance-native order
            (``{j: i for i, j in enumerate(instance.job_id_list)}``).
        machine_then_job: Forwarded to
            :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`.
        logger: Optional logger; warnings are emitted on dispatcher failure.

    Returns ``None`` if the reversed ``MixedDispatcher`` fails to produce
    a schedule (a warning is logged via ``logger`` when supplied).
    """
    if last_stage_id is None:
        last_stage_id = instance.stage_id_list[-1]
    if job_2_pos is None:
        job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}

    ls_only_sch_delayed: FFcSchedule | None = None
    ls_only_sch_flipped: FFcSchedule | None = None
    full_sch_before_unflip: FFcSchedule | None = None

    if instance.stage_count == 1:
        full_sch_from_ls_only_sch = last_stage_only_schedule
    else:
        # Delay last-stage operations to the latest end time that does not
        # worsen per-job ET contribution. Operates on a copy so the caller's
        # input schedule stays untouched.
        ls_only_sch_delayed = last_stage_only_schedule.deepcopy()
        ls_only_sch_delayed.delay_job_latest_leq_obj_contrib(
            instance.job_2_dw_ub_map
        )
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
        reversed_full = rev_dispatcher.get_best_mixed_schedule_by_sequence(
            rev_job_sequence,
            schedule=reversed_seed,
            from_stage=reversed_instance.stage_id_list[1],
            machine_then_job=machine_then_job,
            criteria="makespan",
        )
        if reversed_full is None:
            if logger is not None:
                logger.warning(
                    "reverse_dispatch_full_schedule: reversed MixedDispatcher "
                    "produced no schedule"
                )
            return None

        full_sch_before_unflip = reversed_full
        full_sch_from_ls_only_sch = reversed_full.as_reversed()
        # Push left to a semi-active form, then insert idle time on the last
        # stage so the unflipped operations land at ET-optimal positions
        # before scoring (mirrors `_dispatch_by_reversed_sequence_with_iit`).
        full_sch_from_ls_only_sch.make_semi_active(instance.stage_2_job_2_p_map)
        full_sch_from_ls_only_sch.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )

    sum_e, sum_t = compute_weighted_earliness_tardiness(full_sch_from_ls_only_sch, instance)
    dispatched_obj = float(sum_e + sum_t)

    return Phase3State(
        full_sch_from_ls_only_sch=full_sch_from_ls_only_sch,
        dispatched_obj=dispatched_obj,
        ls_only_sch_delayed=ls_only_sch_delayed,
        ls_only_sch_flipped=ls_only_sch_flipped,
        full_sch_before_unflip=full_sch_before_unflip,
    )


def run_phase3(
    phase1: Phase1State,
    phase2: Phase2State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    logger: logging.Logger | None = None,
    machine_then_job: bool = False,
) -> Phase3State | None:
    """Phase-3 wrapper around :func:`reverse_dispatch_full_schedule`.

    Mutates ``diagnostic``: ``single_stage``, ``dispatch_sec``,
    ``dispatched_obj``, advances ``reached_phase`` to ``"dispatched"``.
    """
    diagnostic.single_stage = instance.stage_count == 1
    t_disp = time.monotonic()

    state = reverse_dispatch_full_schedule(
        instance,
        phase2.last_stage_only_schedule,
        last_stage_id=phase1.last_stage_id,
        job_2_pos=phase1.job_2_pos,
        machine_then_job=machine_then_job,
        logger=logger,
    )
    if state is None:
        return None

    diagnostic.dispatch_sec = time.monotonic() - t_disp
    diagnostic.dispatched_obj = state.dispatched_obj
    diagnostic.reached_phase = "dispatched"

    return state
