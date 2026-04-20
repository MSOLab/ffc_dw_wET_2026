"""Phase 1 of the MCF-LB pipeline.

Computes the MCF preemptive LB, derives the last-stage dispatch seed,
and builds the last-stage-only CP-SAT model that Phase 2 will warm-start
and solve.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ortools.sat.python.cp_model import CpModel

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..cumulative import BaseModelBuilder, OperationVars, Params
from ..parallel_mc_pmtn import ParallelMachinePreemptionMcf
from .diagnostic import MCFLBDiagnostic

__all__ = ["Phase1State", "run_phase1"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase1State:
    """Outputs of Phase 1 consumed by subsequent phases."""

    mcf_lb: float
    horizon: int
    last_stage_id: str
    job_2_pos: dict[str, int]
    job_2_release_map: dict[str, int]

    ls_mdl: CpModel
    ls_params: Params
    ls_ops_vars: OperationVars

    mcf_preemptive_schedule: MCFPreemptiveSchedule
    last_stage_only_init_schedule: FFcSchedule


def run_phase1(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    logger: logging.Logger | None = None,
) -> Phase1State:
    """Run MCF LB + last-stage-only dispatch seed + CP-SAT model build.

    Mutates ``diagnostic`` in place: sets ``mcf_solve_sec``, ``mcf_lb``,
    and advances ``reached_phase`` to ``"mcf"`` once the MCF step
    finishes.

    Raises:
        RuntimeError: if the MCF flow is not optimal for ``instance``.
    """
    del logger  # reserved for future use

    last_stage_id = instance.stage_id_list[-1]

    # Step 1-1: priority score from MCF preemptive LB.
    t_mcf = time.monotonic()
    mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    mcf.solve()
    if not mcf.is_optimal():
        raise RuntimeError(f"MCF not optimal for instance {instance.name}")
    mcf_lb = float(mcf.get_obj_value())
    diagnostic.mcf_solve_sec = time.monotonic() - t_mcf
    diagnostic.mcf_lb = mcf_lb
    diagnostic.reached_phase = "mcf"

    job_2_priority_score_map = mcf.get_job_priority_by_avg_time()
    # job_2_priority_score_map = mcf.get_job_2_start_time_map()
    # job_2_priority_score_map = mcf.get_job_2_completion_time_map()
    mcf_preemptive_schedule = MCFPreemptiveSchedule.from_flow_dict(
        mcf.get_variable_value_dict(),
        stage_id=last_stage_id,
        machines=instance.stage_2_machines_map[last_stage_id],
    )

    # Step 1-2: last-stage-only dispatch seed under the MCF priority order.
    job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    mcf_job_sequence = sorted(
        instance.job_id_list,
        key=lambda j: (
            job_2_priority_score_map[j] is None,
            job_2_priority_score_map[j]
            if job_2_priority_score_map[j] is not None
            else 0,
            job_2_pos[j],
        ),
    )

    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)

    last_stage_only_init_schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    last_stage_only_init_schedule.dispatch_stage_by_jobs(
        last_stage_id,
        mcf_job_sequence,
        duration_map,
        job_2_release=job_2_release_map,
        force_job_id_seq_as_priority=True,
    )
    horizon = int(last_stage_only_init_schedule.makespan * 2)

    ls_builder = BaseModelBuilder()
    ls_mdl, ls_params, ls_ops_vars, _ls_obj_vars = ls_builder.build(
        instance=instance,
        horizon=horizon,
        last_stage_only=True,
        job_2_release=job_2_release_map,
        obj_lb=mcf_lb,
    )

    return Phase1State(
        mcf_lb=mcf_lb,
        horizon=horizon,
        last_stage_id=last_stage_id,
        job_2_pos=job_2_pos,
        job_2_release_map=job_2_release_map,
        ls_mdl=ls_mdl,
        ls_params=ls_params,
        ls_ops_vars=ls_ops_vars,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        last_stage_only_init_schedule=last_stage_only_init_schedule,
    )
