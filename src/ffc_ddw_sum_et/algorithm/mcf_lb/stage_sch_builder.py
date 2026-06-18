"""Intermediate-stage seed schedule construction from an MCF preemptive LB.

Pure algorithm module — no controller / orchestration dependency. Given an
intermediate-stage MCF preemptive schedule (tardiness-only or full-ET
approximate — the builder only reads the preemptive window, so it is
agnostic to which cost produced it), build two stage anchors and derive two
full-schedule candidates from each, then keep the global lower-wET one.

Single algorithm entry point:

  - :func:`build_stage_seed_full_sch`: from the stage-``stage_id`` MCF
    preemptive window, build two fresh anchor schedules —

    * **midpoint** — each job placed at the midpoint of its MCF window via
      :func:`insert_jobs_at_desired_starts` (mirroring the last-stage
      builder);
    * **simple** — jobs left-packed in ``t_max`` order via
      :func:`build_simple_stage_seed` —

    and from each anchor (see :func:`_candidates_from_anchor`) derive three
    full schedules:

    * **bn2d** — extend the anchor across all stages via
      :meth:`BN2DDispatcher.get_full_schedule_from_anchor`;
    * **mixed_fw** — order the anchor jobs by their stage-``stage_id`` end
      time and dispatch that sequence forward
      (:meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`);
    * **mixed_rv** — dispatch the same sequence reversed (reverse-instance +
      IIT pipeline).

    Return the global min-wET schedule across both anchors' candidates;
    ties favour the ``midpoint`` anchor.
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
    build_simple_stage_seed,
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

    best_candidate: Literal["bn2d", "mixed_fw", "mixed_rv"]

    anchor_method: Literal["simple", "midpoint"]

    candidate_objs: dict[str, float]
    """Per-candidate wET keyed ``{anchor}_{bn2d|mixed_fw|mixed_rv}``.

    Always carries the three ``midpoint_*`` keys; the three ``simple_*`` keys
    are present only when ``seed_compare=True`` (the simple anchor was built).
    """


def build_stage_seed_full_sch(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    stage_id: str,
    *,
    seed_compare: bool = False,
    logger: logging.Logger | None = None,
) -> StageSeedResult:
    """Build a full schedule seed anchored on intermediate stage ``stage_id``.

    From the MCF preemptive window on stage ``stage_id`` (either a
    tardiness-only lower-bound relaxation or a full-ET approximate cost — the
    builder only reads the preemptive window), build the ``midpoint`` anchor
    (each job at its MCF-window midpoint) and derive two full-schedule
    candidates (``two_way`` and ``seq_both_ways``), returning the lower-wET one.

    When ``seed_compare`` is ``True`` a second ``simple`` anchor (jobs
    left-packed in ``t_max`` order) is also built and the global min-wET
    schedule across both anchors is returned; ties favour the ``midpoint``
    anchor so a tie keeps the historical (midpoint-only) output. When
    ``seed_compare`` is ``False`` (default policy) only the midpoint anchor is
    built — byte-identical to the historical single-anchor output, no extra
    build cost.
    """
    log = logger or logging.getLogger(__name__)

    midpoint_anchor = _build_anchor_schedule(
        instance, mcf_preemptive_schedule, stage_id, log
    )
    midpoint_sch, midpoint_obj, midpoint_candidate, midpoint_objs = (
        _candidates_from_anchor(instance, midpoint_anchor, stage_id, log)
    )
    candidate_objs: dict[str, float] = {
        f"midpoint_{k}": v for k, v in midpoint_objs.items()
    }

    if not seed_compare:
        # Comparison disabled: midpoint-only anchor, byte-identical to the
        # historical single-anchor output (no simple anchor built).
        return StageSeedResult(
            schedule=midpoint_sch,
            obj_value=midpoint_obj,
            best_candidate=midpoint_candidate,
            anchor_method="midpoint",
            candidate_objs=candidate_objs,
        )

    window_map = window_map_from_preemptive_schedule(
        mcf_preemptive_schedule, instance.job_id_list
    )
    simple_anchor = build_simple_stage_seed(
        instance,
        window_map,
        stage_id=stage_id,
        duration_map=instance.get_job_2_p_map_for_stage(stage_id),
        job_2_release=instance.get_job_2_p_sum_before_stage(stage_id),
    )
    simple_sch, simple_obj, simple_candidate, simple_objs = _candidates_from_anchor(
        instance, simple_anchor, stage_id, log
    )
    candidate_objs.update({f"simple_{k}": v for k, v in simple_objs.items()})

    # Ties favour the midpoint anchor (strict `<` keeps today's output).
    if simple_obj < midpoint_obj:
        return StageSeedResult(
            schedule=simple_sch,
            obj_value=simple_obj,
            best_candidate=simple_candidate,
            anchor_method="simple",
            candidate_objs=candidate_objs,
        )
    return StageSeedResult(
        schedule=midpoint_sch,
        obj_value=midpoint_obj,
        best_candidate=midpoint_candidate,
        anchor_method="midpoint",
        candidate_objs=candidate_objs,
    )


def _candidates_from_anchor(
    instance: FFcDDWParameters,
    anchor_sch: FFcSchedule,
    stage_id: str,
    log: logging.Logger,
) -> tuple[
    FFcSchedule,
    float,
    Literal["bn2d", "mixed_fw", "mixed_rv"],
    dict[str, float],
]:
    """Derive this anchor's own best full schedule across three candidates.

    From ``anchor_sch`` build ``bn2d``
    (:meth:`BN2DDispatcher.get_full_schedule_from_anchor`), ``mixed_fw``
    (forward :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`), and
    ``mixed_rv`` (reverse-instance + IIT pipeline). Return the lower-wET one
    plus a dict of all three objectives.

    Tie precedence is ``bn2d > mixed_fw > mixed_rv``: ``min`` returns the first
    minimiser, and the candidates are ranked in that order, so the chosen
    schedule is byte-identical to the historical selection (``bn2d`` beat
    ``seq_both_ways`` on ties; the forward arm beat the reverse arm on ties).
    """
    bn2d_sch = BN2DDispatcher().get_full_schedule_from_anchor(
        instance, anchor_sch, stage_id, logger=log
    )
    bn2d_obj = _weighted_et(bn2d_sch, instance)

    mixed_fw_sch, mixed_fw_obj, mixed_rv_sch, mixed_rv_obj = _seq_both_ways(
        instance, anchor_sch, stage_id, log
    )

    objs: dict[str, float] = {
        "bn2d": bn2d_obj,
        "mixed_fw": mixed_fw_obj,
        "mixed_rv": mixed_rv_obj,
    }
    ranked: list[tuple[Literal["bn2d", "mixed_fw", "mixed_rv"], float, FFcSchedule]] = [
        ("bn2d", bn2d_obj, bn2d_sch),
        ("mixed_fw", mixed_fw_obj, mixed_fw_sch),
        ("mixed_rv", mixed_rv_obj, mixed_rv_sch),
    ]
    best_candidate, best_obj, best_sch = min(ranked, key=lambda c: c[1])
    return best_sch, best_obj, best_candidate, objs


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
) -> tuple[FFcSchedule, float, FFcSchedule, float]:
    """Forward + reversed dispatch of the anchor end-time order, kept separate.

    Sequence = anchor jobs sorted by stage-``stage_id`` end time ascending.
    The forward arm (``mixed_fw``) uses
    :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`; the reverse
    arm (``mixed_rv``) uses the reverse-instance + IIT pipeline. Both are
    returned (no ``min`` here); the caller ranks them against ``bn2d``.

    Returns ``(mixed_fw_sch, mixed_fw_obj, mixed_rv_sch, mixed_rv_obj)``.
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

    return forward_sch, forward_obj, reversed_sch, reversed_obj


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
