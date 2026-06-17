"""Intermediate-stage seed schedule construction from an MCF preemptive LB.

Pure algorithm module — no controller / orchestration dependency. Given a
tardiness-only MCF preemptive schedule on an intermediate stage, build a
stage anchor and derive two full-schedule candidates from it, then keep the
lower-wET one.

Single algorithm entry point:

  - :func:`build_stage_seed_full_sch`: from the stage-``stage_id`` MCF
    preemptive window, build a fresh anchor schedule (each job placed at the
    midpoint of its MCF window via :func:`insert_jobs_at_desired_starts`,
    mirroring the last-stage builder), then derive two full schedules:

    * **two_way** — extend the anchor across all stages via
      :meth:`BN2DDispatcher.get_full_schedule_from_anchor`;
    * **seq_both_ways** — order the anchor jobs by their stage-``stage_id``
      end time, then dispatch that sequence forward
      (:meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`) and
      reversed (reverse-instance + IIT pipeline), keeping the lower-wET.

    Return the global min-wET schedule across both candidates.
"""

import logging
from dataclasses import dataclass
from typing import Literal, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..dispatcher import BN2DDispatcher, MixedDispatcher
from .utils import (
    insert_jobs_at_desired_starts,
    pm_pmtn_sort_job_sequence_with_log,
    window_map_from_preemptive_schedule,
)

__all__ = [
    "StageSeedResult",
    "build_stage_seed_full_sch",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class StageSeedResult:
    """Aggregate result of one intermediate-stage seed construction."""

    schedule: FFcSchedule

    obj_value: float
    """Weighted earliness + tardiness of :attr:`schedule`."""

    best_candidate: Literal["two_way", "seq_both_ways"]


def build_stage_seed_full_sch(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    stage_id: str,
    *,
    logger: logging.Logger | None = None,
) -> StageSeedResult:
    """Build a full schedule seed anchored on intermediate stage ``stage_id``.

    From the MCF preemptive window on stage ``stage_id`` (a tardiness-only
    lower-bound relaxation), build a fresh anchor schedule and derive two
    full-schedule candidates, returning the lower-wET one.
    """
    log = logger or logging.getLogger(__name__)

    anchor_sch = _build_anchor_schedule(
        instance, mcf_preemptive_schedule, stage_id, log
    )

    two_way_sch = BN2DDispatcher().get_full_schedule_from_anchor(
        instance, anchor_sch, stage_id, logger=log
    )
    two_way_obj = _weighted_et(two_way_sch, instance)

    seq_both_sch, seq_both_obj = _seq_both_ways(instance, anchor_sch, stage_id, log)

    if two_way_obj <= seq_both_obj:
        return StageSeedResult(
            schedule=two_way_sch,
            obj_value=two_way_obj,
            best_candidate="two_way",
        )
    return StageSeedResult(
        schedule=seq_both_sch,
        obj_value=seq_both_obj,
        best_candidate="seq_both_ways",
    )


def _build_anchor_schedule(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    stage_id: str,
    logger: logging.Logger,
) -> FFcSchedule:
    """Build a fresh stage-``stage_id``-only anchor from the MCF window.

    Mirrors :func:`heuristic_last_stage_only_from_mcf_lb` (last-stage
    builder): order jobs by the preemptive-window priority sort, then place
    each at the midpoint of its MCF window on stage ``stage_id`` via
    :func:`insert_jobs_at_desired_starts` (with upstream release times = sum
    of processing before the stage). Unlike the last-stage seed, the anchor
    keeps the raw midpoint placement — no ``make_semi_active`` /
    ``insert_idle_time`` — so the placement the downstream extension anchors
    on faithfully reflects where the LB wants each job.
    """
    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    duration_map = instance.get_job_2_p_map_for_stage(stage_id)
    job_2_release = instance.get_job_2_p_sum_before_stage(stage_id)

    job_sequence = pm_pmtn_sort_job_sequence_with_log(
        window_map, duration_map, instance, logger=logger
    )

    return insert_jobs_at_desired_starts(
        None,
        instance,
        stage_id=stage_id,
        job_2_release=job_2_release,
        duration_map=duration_map,
        window_map=window_map,
        appended=job_sequence,
    )


def _seq_both_ways(
    instance: FFcDDWParameters,
    anchor_sch: FFcSchedule,
    stage_id: str,
    logger: logging.Logger,
) -> tuple[FFcSchedule, float]:
    """Forward + reversed dispatch of the anchor end-time order; min wET.

    Sequence = anchor jobs sorted by stage-``stage_id`` end time ascending.
    Forward uses :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`;
    reversed uses the reverse-instance + IIT pipeline.
    """
    job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    sequence = sorted(
        instance.job_id_list,
        key=lambda j: (anchor_sch.get_job_end_time(stage_id, j), job_2_pos[j]),
    )

    forward_sch = MixedDispatcher(
        instance, logger=logger
    ).get_best_mixed_schedule_by_sequence(sequence, criteria="weighted_et")
    if forward_sch is None:
        raise RuntimeError(
            f"build_stage_seed_full_sch: MixedDispatcher produced no forward "
            f"schedule for {instance.name} (stage {stage_id})"
        )
    forward_obj = _weighted_et(forward_sch, instance)

    reversed_sch, reversed_obj = _dispatch_by_reversed_sequence_with_iit(
        instance, sequence, logger
    )

    if forward_obj <= reversed_obj:
        return forward_sch, forward_obj
    return reversed_sch, reversed_obj


def _dispatch_by_reversed_sequence_with_iit(
    instance: FFcDDWParameters,
    job_sequence: Sequence[str],
    logger: logging.Logger,
) -> tuple[FFcSchedule, float]:
    """Pure port of the controller reverse-instance + IIT dispatch pipeline.

    Steps: stage-reverse the instance, dispatch ``reversed(job_sequence)``
    twice (min-makespan, both machine-then-job orders), unflip with
    :meth:`FFcSchedule.as_reversed`, push left to semi-active form, then
    insert idle time. Keep the lower-wET of the two unflipped candidates.
    """
    reversed_instance = FFcDDWParameters.reverse_stages(instance)
    rev_seq = list(reversed(job_sequence))

    rev_dispatcher = MixedDispatcher(reversed_instance, logger=logger)
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
            f"_dispatch_by_reversed_sequence_with_iit: MixedDispatcher "
            f"produced no schedule for {instance.name}"
        )
    schedule_1 = reversed_full_1.as_reversed() if reversed_full_1 is not None else None
    schedule_2 = reversed_full_2.as_reversed() if reversed_full_2 is not None else None

    if schedule_1 is not None and schedule_2 is not None:
        obj_1 = _weighted_et(schedule_1, instance)
        obj_2 = _weighted_et(schedule_2, instance)
        schedule = schedule_1 if obj_1 <= obj_2 else schedule_2
    else:
        schedule = schedule_1 or schedule_2

    if schedule is None:
        raise RuntimeError(
            f"_dispatch_by_reversed_sequence_with_iit: no schedule after "
            f"unflipping for {instance.name}"
        )
    schedule.make_semi_active(instance.stage_2_job_2_p_map)
    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return schedule, _weighted_et(schedule, instance)


def _weighted_et(schedule: FFcSchedule, instance: FFcDDWParameters) -> float:
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    return float(sum_e + sum_t)
