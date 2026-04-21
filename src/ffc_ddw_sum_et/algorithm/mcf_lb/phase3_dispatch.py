"""Phase 3 of the MCF-LB pipeline.

Reverse-dispatch with the last stage pinned as seed, then unflip back
to the original instance. Produces the full dispatched schedule that
Phase 4 will warm-start its profile-fix CP-SAT model from.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_window_et
from ..dispatcher import MixedDispatcher
from .diagnostic import MCFLBDiagnostic
from .phase1_mcf import Phase1State
from .phase2_last_stage import Phase2State

__all__ = ["Phase3State", "run_phase3"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase3State:
    """Outputs of Phase 3 consumed by Phase 4 (and by controller sidecar)."""

    dispatched_schedule: FFcSchedule
    dispatched_obj: float

    # Reversed-instance intermediates (None when single-stage short-circuit fires).
    last_stage_only_schedule_flipped: FFcSchedule | None = None
    dispatched_schedule_before_unflipping: FFcSchedule | None = None


def run_phase3(
    phase1: Phase1State,
    phase2: Phase2State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    logger: logging.Logger | None = None,
    machine_then_job: bool = False,
) -> Phase3State | None:
    """Build the full dispatched schedule via reverse-dispatch + unflip.

    When ``instance.stage_count == 1`` the last-stage schedule is already
    the full schedule; the reverse-dispatch is skipped and the flipped /
    before-unflipping sidecar slots stay ``None``.

    Mutates ``diagnostic``: ``single_stage``, ``dispatch_sec``,
    ``dispatched_obj``, advances ``reached_phase`` to ``"dispatched"``.

    Returns ``None`` if the reversed ``MixedDispatcher`` fails to produce
    a schedule (a warning is logged via ``logger`` when supplied).
    """
    last_stage_id = phase1.last_stage_id
    last_stage_only_schedule = phase2.last_stage_only_schedule
    last_stage_only_schedule_makespan = phase2.last_stage_only_schedule_makespan

    t_disp = time.monotonic()
    c = instance.stage_count
    diagnostic.single_stage = c == 1

    last_stage_only_schedule_flipped: FFcSchedule | None = None
    dispatched_schedule_before_unflipping: FFcSchedule | None = None

    if c == 1:
        dispatched_schedule = last_stage_only_schedule
    else:
        last_stage_end_map = {
            j: phase2.ls_j_i_2_end[j, last_stage_id] for j in instance.job_id_list
        }
        rev_job_sequence = sorted(
            instance.job_id_list,
            key=lambda j: (-last_stage_end_map[j], phase1.job_2_pos[j]),
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
                start_time=last_stage_only_schedule_makespan - e,
                end_time=last_stage_only_schedule_makespan - s,
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
                    "run_mcf_lb phase 3: reversed MixedDispatcher produced no schedule"
                )
            return None

        dispatched_schedule_before_unflipping = reversed_full
        dispatched_schedule = reversed_full.as_reversed()

    sum_e, sum_t = compute_window_et(dispatched_schedule, instance)
    dispatched_obj = float(sum_e + sum_t)

    diagnostic.dispatch_sec = time.monotonic() - t_disp
    diagnostic.dispatched_obj = dispatched_obj
    diagnostic.reached_phase = "dispatched"

    return Phase3State(
        dispatched_schedule=dispatched_schedule,
        dispatched_obj=dispatched_obj,
        last_stage_only_schedule_flipped=last_stage_only_schedule_flipped,
        dispatched_schedule_before_unflipping=dispatched_schedule_before_unflipping,
    )
