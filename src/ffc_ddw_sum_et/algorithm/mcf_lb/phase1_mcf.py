"""Phase 1 of the MCF-LB pipeline.

Computes the MCF preemptive LB and derives one last-stage dispatch seed
per MCF-derived priority map (avg time, start time, completion time).
Phase 2 picks up the seed list and builds an independent CP-SAT model
per seed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..parallel_mc_pmtn import ParallelMachinePreemptionMcf
from .diagnostic import MCFLBDiagnostic

__all__ = [
    "LastStageSeed",
    "McfLbResult",
    "Phase1State",
    "SeedTag",
    "run_phase1",
    "solve_mcf_lb",
]


SeedTag = Literal[
    "avg_time",
    "avg_time_minus_half_p",
    "start_time",
    "completion_time",
    "completion_time_minus_p",
    "due_date_lb",
    "due_date_lb_minus_p",
    "due_date_ub",
    "due_date_ub_minus_p",
    "due_date_star",
    "due_date_star_minus_p",
    "due_date_star_minus_half_p",
    "due_date_star_plus_half_p",
    "due_date_star_plus_p",
    "due2-weight-pos",
]

# Fixed emission order across artifacts (diagnostic, gantt, CSV).


@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageSeed:
    """One last-stage-only dispatch seed derived from an MCF priority map."""

    tag: SeedTag
    job_sequence: list[str]
    init_schedule: FFcSchedule


@dataclass(frozen=True, slots=True, kw_only=True)
class McfLbResult:
    """Bare result of solving the MCF relaxation: bound + preemptive schedule.

    Used by ``run_phase1`` to seed the full 4-phase pipeline, and by
    LB-only subroutines that report a global lower bound with no schedule.
    The ``mcf`` handle is retained so callers can extract MCF-derived
    priority maps without re-solving.
    """

    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    mcf: ParallelMachinePreemptionMcf


def solve_mcf_lb(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
) -> McfLbResult:
    """Solve the MCF relaxation and record the bound on ``diagnostic``.

    Mutates ``diagnostic`` in place: sets ``mcf_solve_sec``, ``mcf_lb``,
    and advances ``reached_phase`` to ``"mcf"``.

    Raises:
        RuntimeError: if the MCF flow is not optimal for ``instance``.
    """
    last_stage_id = instance.stage_id_list[-1]

    t_mcf = time.monotonic()
    mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    mcf.solve()
    if not mcf.is_optimal():
        raise RuntimeError(f"MCF not optimal for instance {instance.name}")
    mcf_lb = float(mcf.get_obj_value())
    diagnostic.mcf_solve_sec = time.monotonic() - t_mcf
    diagnostic.mcf_lb = mcf_lb
    diagnostic.reached_phase = "mcf"

    mcf_preemptive_schedule = MCFPreemptiveSchedule.from_flow_dict(
        mcf.get_variable_value_dict(),
        stage_id=last_stage_id,
        machines=instance.stage_2_machines_map[last_stage_id],
    )
    return McfLbResult(
        mcf_lb=mcf_lb,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        mcf=mcf,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase1State:
    """Outputs of Phase 1 consumed by subsequent phases."""

    mcf_lb: float
    last_stage_id: str
    job_2_pos: dict[str, int]
    job_2_release_map: dict[str, int]
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    last_stage_seeds: list[LastStageSeed]


def run_phase1(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    logger: logging.Logger | None = None,
    last_stage_only_priority_tags: Sequence[SeedTag] | None = None,
) -> Phase1State:
    """Run MCF LB + build one last-stage dispatch seed per MCF priority map.

    Mutates ``diagnostic`` in place: sets ``mcf_solve_sec``, ``mcf_lb``,
    and advances ``reached_phase`` to ``"mcf"`` once the MCF step
    finishes.

    Raises:
        RuntimeError: if the MCF flow is not optimal for ``instance``.
    """
    del logger  # reserved for future use

    last_stage_id = instance.stage_id_list[-1]

    mcf_result = solve_mcf_lb(instance, diagnostic)
    mcf = mcf_result.mcf
    mcf_lb = mcf_result.mcf_lb
    mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule

    job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)

    priority_map_by_tag: dict[SeedTag, Mapping[str, float | int | None]] = {
        "avg_time": mcf.get_job_priority_by_avg_time(),
        "avg_time_minus_half_p": mcf.get_job_priority_by_avg_time_minus_half_p(),
        "start_time": mcf.get_job_2_start_time_map(),
        "completion_time": mcf.get_job_2_completion_time_map(),
        "completion_time_minus_p": mcf.get_job_2_completion_time_minus_p_map(),
        "due_date_lb": instance.get_job_2_due_date_lb_map(),
        "due_date_lb_minus_p": instance.get_job_2_due_date_lb_minus_p_map(),
        "due_date_ub": instance.get_job_2_due_date_ub_map(),
        "due_date_ub_minus_p": instance.get_job_2_due_date_ub_minus_p_map(),
        "due_date_star": instance.get_job_2_due_date_star_map(),
        "due_date_star_minus_p": instance.get_job_2_due_date_star_minus_p_map(),
        "due_date_star_minus_half_p": instance.get_job_2_due_date_star_minus_half_p_map(),
        "due_date_star_plus_half_p": instance.get_job_2_due_date_star_plus_half_p_map(),
        "due_date_star_plus_p": instance.get_job_2_due_date_star_plus_p_map(),
    }
    if last_stage_only_priority_tags is None:
        last_stage_only_priority_tags = ["avg_time"]
    last_stage_seeds = [
        _build_seed(
            tag=tag,
            job_sequence=_resolve_job_sequence(
                tag=tag,
                instance=instance,
                priority_map_by_tag=priority_map_by_tag,
                job_2_pos=job_2_pos,
            ),
            instance=instance,
            last_stage_id=last_stage_id,
            job_2_release=job_2_release_map,
            duration_map=duration_map,
        )
        for tag in last_stage_only_priority_tags
    ]

    return Phase1State(
        mcf_lb=mcf_lb,
        last_stage_id=last_stage_id,
        job_2_pos=job_2_pos,
        job_2_release_map=job_2_release_map,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        last_stage_seeds=last_stage_seeds,
    )


def _resolve_job_sequence(
    *,
    tag: SeedTag,
    instance: FFcDDWParameters,
    priority_map_by_tag: Mapping[SeedTag, Mapping[str, float | int | None]],
    job_2_pos: dict[str, int],
) -> list[str]:
    """Resolve the job dispatch order for a seed tag.

    Most tags derive the order from a per-job priority score (None-last,
    ties broken by instance order). The ``due2-weight-pos`` tag uses the
    composite key defined on the instance directly.
    """
    if tag == "due2-weight-pos":
        return instance.get_due2_weight_pos_job_sequence()
    priority_map = priority_map_by_tag[tag]
    return sorted(
        instance.job_id_list,
        key=lambda j: (
            priority_map[j] is None,
            priority_map[j] if priority_map[j] is not None else 0,
            job_2_pos[j],
        ),
    )


def _build_seed(
    *,
    tag: SeedTag,
    job_sequence: list[str],
    instance: FFcDDWParameters,
    last_stage_id: str,
    job_2_release: dict[str, int],
    duration_map: dict[str, int],
) -> LastStageSeed:
    """Dispatch ``job_sequence`` onto the last stage under the MCF release times."""
    init_schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    init_schedule.dispatch_stage_by_jobs(
        last_stage_id,
        job_sequence,
        duration_map,
        job_2_release=job_2_release,
        force_job_id_seq_as_priority=True,
    )
    return LastStageSeed(
        tag=tag, job_sequence=job_sequence, init_schedule=init_schedule
    )
