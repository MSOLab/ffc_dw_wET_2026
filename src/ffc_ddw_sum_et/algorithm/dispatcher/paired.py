"""Pure dispatch helpers for v3 paired-dispatch seed scheduling.

These functions decode a job sequence via the sd (forward) or rd (reversed)
pipeline and return a feasible schedule with its weighted-ET score.

The ``build_v3_paired_dispatch_schedule`` function enumerates the v3 pool
(priority × {sd, rd}) and returns the best candidate.

Imported by both ``orchestration.controller`` (refactored thin wrappers) and
``algorithm.coarsen_solve_reconstruct`` (``v3`` seed strategy).
"""

from __future__ import annotations

import logging
from typing import Literal, Sequence

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.sorter import (
    V3_PRIORITY_SET,
    V4_PRIORITY_SET,
    dispatch_seq_job_sequence,
)
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

from .mixed import MixedDispatcher

__all__ = [
    "dispatch_forward_with_iit",
    "dispatch_reversed_with_iit",
    "build_v3_paired_dispatch_schedule",
    "build_v4_paired_dispatch_schedule",
]


def dispatch_forward_with_iit(
    instance: FFcDDWParameters,
    job_sequence: Sequence[str],
    logger: logging.Logger | None = None,
    *,
    time_factor: int = 1,
    idle_mode: Literal["flooring", "ceiling", "lookahead"] = "flooring",
) -> tuple[FFcSchedule, float]:
    """sd pipeline: forward job-centric decode + semi-active + IIT + wET."""
    log = logger or logging.getLogger(__name__)
    log.debug(
        "dispatch_forward_with_iit: instance=%s, jobs=%d",
        instance.name,
        len(job_sequence),
    )
    dispatcher = MixedDispatcher(instance, logger=log)
    schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
    schedule.make_semi_active(instance.stage_2_job_2_p_map)  # TODO: remove
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
        time_factor=time_factor,
        idle_mode=idle_mode,
    )
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        schedule, instance, time_factor=time_factor
    )
    return schedule, float(sum_e + sum_t)


def dispatch_reversed_with_iit(
    instance: FFcDDWParameters,
    job_sequence: Sequence[str],
    logger: logging.Logger | None = None,
    *,
    time_factor: int = 1,
    idle_mode: Literal["flooring", "ceiling", "lookahead"] = "flooring",
) -> tuple[FFcSchedule, float]:
    """rd pipeline: stage-reverse + reversed mixed(makespan) + unflip + IIT + wET."""
    log = logger or logging.getLogger(__name__)
    log.debug(
        "dispatch_reversed_with_iit: instance=%s, jobs=%d",
        instance.name,
        len(job_sequence),
    )
    reversed_instance = FFcDDWParameters.reverse_stages(instance)
    rev_seq = list(reversed(job_sequence))

    rev_dispatcher = MixedDispatcher(reversed_instance, logger=log)
    reversed_full_1 = rev_dispatcher.get_best_mixed_schedule_by_sequence(
        rev_seq,
        machine_then_job=True,
        criteria="makespan",
    )
    reversed_full_2 = rev_dispatcher.get_best_mixed_schedule_by_sequence(
        rev_seq,
        machine_then_job=False,
        criteria="makespan",
    )
    if reversed_full_1 is None and reversed_full_2 is None:
        raise RuntimeError(
            f"dispatch_reversed_with_iit: MixedDispatcher "
            f"produced no schedule for {instance.name}"
        )
    if reversed_full_1 is not None:
        schedule_1 = reversed_full_1.as_reversed()
    else:
        schedule_1 = None
    if reversed_full_2 is not None:
        schedule_2 = reversed_full_2.as_reversed()
    else:
        schedule_2 = None

    if schedule_1 is not None and schedule_2 is not None:
        sum_e_1, sum_t_1 = compute_weighted_earliness_tardiness(
            schedule_1, instance, time_factor=time_factor
        )
        obj_1 = float(sum_e_1 + sum_t_1)

        sum_e_2, sum_t_2 = compute_weighted_earliness_tardiness(
            schedule_2, instance, time_factor=time_factor
        )
        obj_2 = float(sum_e_2 + sum_t_2)

        if obj_1 <= obj_2:
            schedule = schedule_1
            log.info(
                "dispatch_reversed_with_iit: "
                "machine_then_job=True better (obj=%s) than "
                "machine_then_job=False (obj=%s)",
                obj_1,
                obj_2,
            )
        else:
            schedule = schedule_2
            log.info(
                "dispatch_reversed_with_iit: "
                "machine_then_job=False better (obj=%s) than "
                "machine_then_job=True (obj=%s)",
                obj_2,
                obj_1,
            )
    else:
        schedule = schedule_1 or schedule_2

    if schedule is None:
        raise RuntimeError(
            f"dispatch_reversed_with_iit: no schedule after "
            f"unflipping for {instance.name}"
        )
    schedule.make_semi_active(instance.stage_2_job_2_p_map)
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
        time_factor=time_factor,
        idle_mode=idle_mode,
    )
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        schedule, instance, time_factor=time_factor
    )
    return schedule, float(sum_e + sum_t)


def build_v3_paired_dispatch_schedule(
    instance: FFcDDWParameters,
    priorities: Sequence[str] = V3_PRIORITY_SET,
    logger: logging.Logger | None = None,
    *,
    factor: int = 1,
    idle_mode: Literal["flooring", "ceiling", "lookahead"] = "flooring",
) -> tuple[FFcSchedule, float, str]:
    """v3 paired pool: priority×{sd,rd} candidates → min-wET (schedule, obj, label).

    When ``factor > 1`` (CSR pipeline), the wET evaluation uses
    ``factor * C^c`` against the instance's (original-scale) due window
    so the seed is consistent with the CSR CP model's objective.
    """
    log = logger or logging.getLogger(__name__)
    candidates: list[tuple[float, str, FFcSchedule]] = []
    for p in priorities:
        seq = dispatch_seq_job_sequence(instance, p)
        # ``time_factor=factor`` makes both the idle insertion *and* the wET
        # ranking inside the helpers use the CSR objective (factor*C^c against
        # the instance's original window), so candidates are built and scored
        # consistently with the CSR CP model.
        sd_sch, sd_obj = dispatch_forward_with_iit(
            instance, seq, log, time_factor=factor, idle_mode=idle_mode
        )
        rd_sch, rd_obj = dispatch_reversed_with_iit(
            instance, seq, log, time_factor=factor, idle_mode=idle_mode
        )
        candidates.append((sd_obj, f"sd:{p}", sd_sch))
        candidates.append((rd_obj, f"rd:{p}", rd_sch))
    best_obj, best_label, best_sch = min(candidates, key=lambda c: c[0])
    log.info(
        "build_v3_paired_dispatch_schedule: best=%s obj=%s of %d candidates [%s]",
        best_label,
        best_obj,
        len(candidates),
        ", ".join(f"{lab}={obj:.0f}" for obj, lab, _ in candidates),
    )
    return best_sch, best_obj, best_label


def build_v4_paired_dispatch_schedule(
    instance: FFcDDWParameters,
    priorities: Sequence[str] = V4_PRIORITY_SET,
    logger: logging.Logger | None = None,
    *,
    factor: int = 1,
    idle_mode: Literal["flooring", "ceiling", "lookahead"] = "flooring",
) -> tuple[FFcSchedule, float, str]:
    """v4 paired pool: priority×{sd,rd} candidates → min-wET (schedule, obj, label).

    When ``factor > 1`` (CSR pipeline), the wET evaluation uses
    ``factor * C^c`` against the instance's (original-scale) due window
    so the seed is consistent with the CSR CP model's objective.
    """
    log = logger or logging.getLogger(__name__)
    candidates: list[tuple[float, str, FFcSchedule]] = []
    for p in priorities:
        seq = dispatch_seq_job_sequence(instance, p)
        # ``time_factor=factor`` makes both the idle insertion *and* the wET
        # ranking inside the helpers use the CSR objective (factor*C^c against
        # the instance's original window), so candidates are built and scored
        # consistently with the CSR CP model.
        sd_sch, sd_obj = dispatch_forward_with_iit(
            instance, seq, log, time_factor=factor, idle_mode=idle_mode
        )
        rd_sch, rd_obj = dispatch_reversed_with_iit(
            instance, seq, log, time_factor=factor, idle_mode=idle_mode
        )
        candidates.append((sd_obj, f"sd:{p}", sd_sch))
        candidates.append((rd_obj, f"rd:{p}", rd_sch))
    best_obj, best_label, best_sch = min(candidates, key=lambda c: c[0])
    log.info(
        "build_v4_paired_dispatch_schedule: best=%s obj=%s of %d candidates [%s]",
        best_label,
        best_obj,
        len(candidates),
        ", ".join(f"{lab}={obj:.0f}" for obj, lab, _ in candidates),
    )
    return best_sch, best_obj, best_label
