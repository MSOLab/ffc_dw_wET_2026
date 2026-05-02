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

    dispatched_schedule: FFcSchedule
    dispatched_obj: float

    # Reversed-instance intermediates (None when single-stage short-circuit fires).
    last_stage_only_schedule_flipped: FFcSchedule | None = None
    dispatched_schedule_before_unflipping: FFcSchedule | None = None


def reverse_dispatch_full_schedule(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    last_stage_id: str | None = None,
    last_stage_only_makespan: int | None = None,
    job_2_pos: dict[str, int] | None = None,
    machine_then_job: bool = False,
    logger: logging.Logger | None = None,
) -> Phase3State | None:
    """Build the full dispatched schedule via reverse-dispatch + unflip.

    Pure helper — no diagnostic side effects. The caller mutates a
    :class:`MCFLBDiagnostic` if it needs to record timing/objective there.

    When ``instance.stage_count == 1`` the last-stage schedule is already
    the full schedule; the reverse-dispatch is skipped and the flipped /
    before-unflipping sidecar slots stay ``None``.

    Args:
        instance: Original (non-reversed) FFcDDW instance.
        last_stage_only_schedule: Schedule whose last stage is fully
            populated (other stages may be empty). The (job, last_stage)
            end times are read directly from this schedule.
        last_stage_id: Defaults to ``instance.stage_id_list[-1]``.
        last_stage_only_makespan: Max end on the last stage. Defaults to
            ``last_stage_only_schedule.makespan`` (which itself returns
            the max end on the schedule's last stage).
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
    if last_stage_only_makespan is None:
        last_stage_only_makespan = last_stage_only_schedule.makespan

    last_stage_only_schedule_flipped: FFcSchedule | None = None
    dispatched_schedule_before_unflipping: FFcSchedule | None = None

    if instance.stage_count == 1:
        dispatched_schedule = last_stage_only_schedule
    else:
        last_stage_end_map: dict[str, int] = {}
        for _, _, end_time, job_id in last_stage_only_schedule.iter_operations_on_stage(
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
        for mc_id, s, e, j in last_stage_only_schedule.iter_operations_on_stage(
            last_stage_id
        ):
            reversed_seed.add_ops_times_2_mc(
                stage_id=last_stage_id,
                mc_id=mc_id,
                job_id=j,
                start_time=last_stage_only_makespan - e,
                end_time=last_stage_only_makespan - s,
            )
        last_stage_only_schedule_flipped = reversed_seed

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

        dispatched_schedule_before_unflipping = reversed_full
        dispatched_schedule = reversed_full.as_reversed()

    sum_e, sum_t = compute_weighted_earliness_tardiness(dispatched_schedule, instance)
    dispatched_obj = float(sum_e + sum_t)

    return Phase3State(
        dispatched_schedule=dispatched_schedule,
        dispatched_obj=dispatched_obj,
        last_stage_only_schedule_flipped=last_stage_only_schedule_flipped,
        dispatched_schedule_before_unflipping=dispatched_schedule_before_unflipping,
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
        last_stage_only_makespan=phase2.last_stage_only_schedule_makespan,
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
