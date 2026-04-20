"""Phase 4 of the MCF-LB pipeline.

Profile-fix CP-SAT full solve warm-started from the Phase 3 dispatched
schedule. Produces the final schedule and an ``obj_bound_final =
max(mcf_lb, pf_bound)``; if the solver returns neither OPTIMAL nor
FEASIBLE, ``final_schedule``/``final_obj`` stay ``None`` and the
caller falls back to the Phase 3 incumbent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_window_et
from ...solution.schedule_build import build_schedule_from_op_starts
from ..cumulative import BaseModelBuilder
from .diagnostic import MCFLBDiagnostic
from .phase1_mcf import Phase1State
from .phase3_dispatch import Phase3State

__all__ = ["Phase4State", "run_phase4"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase4State:
    """Outputs of Phase 4."""

    obj_bound_final: float
    final_schedule: FFcSchedule | None = None
    final_obj: float | None = None


def run_phase4(
    phase1: Phase1State,
    phase3: Phase3State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
    solver_thread_cnt: int = 1,
    logger: logging.Logger | None = None,
) -> Phase4State:
    """Build and solve the profile-fix full CP-SAT model.

    Mutates ``diagnostic``: ``profile_fix_cp_sat_sec``, ``pf_status``,
    ``profile_fix_bound`` always; on feasibility also ``profile_fix_obj``
    and advances ``reached_phase`` to ``"profile_fix"``.

    Always returns a ``Phase4State``. When the solver is infeasible the
    ``final_schedule`` / ``final_obj`` slots stay ``None`` and the caller
    should fall back to the Phase 3 dispatched schedule as the reported
    incumbent, using ``obj_bound_final`` as the bound.
    """
    dispatched_schedule = phase3.dispatched_schedule

    pf_builder = BaseModelBuilder()
    pf_mdl, pf_params, pf_op_vars, _pf_et_vars = pf_builder.build(
        instance, horizon=phase1.horizon
    )
    BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
        pf_mdl,
        pf_params,
        pf_op_vars,
        dispatched_schedule,
        profile_fix_by_machine=profile_fix_by_machine,
        machine_precedence_stride=machine_precedence_stride,
    )
    BaseModelBuilder.apply_start_hints_from_start_time_map(
        pf_mdl,
        pf_params,
        pf_op_vars,
        dispatched_schedule.get_jik_2_start_time_map(),
    )
    BaseModelBuilder.apply_end_hints_from_end_time_map(
        pf_mdl,
        pf_params,
        pf_op_vars,
        dispatched_schedule.get_jik_2_end_time_map(),
    )

    pf_solver = cp_model.CpSolver()
    pf_solver.parameters.num_search_workers = int(solver_thread_cnt)
    t_pf = time.monotonic()
    pf_status = pf_solver.Solve(pf_mdl)
    diagnostic.profile_fix_cp_sat_sec = time.monotonic() - t_pf
    diagnostic.pf_status = pf_solver.StatusName(pf_status)

    try:
        pf_bound = float(pf_solver.best_objective_bound)
    except Exception:
        pf_bound = phase1.mcf_lb
    diagnostic.profile_fix_bound = pf_bound
    obj_bound_final = max(phase1.mcf_lb, pf_bound)

    if pf_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if logger is not None:
            logger.warning(
                "run_mcf_lb phase 4: profile-fix CP-SAT no feasible solution "
                "(status=%s); falling back to phase-3 incumbent",
                pf_solver.StatusName(pf_status),
            )
        return Phase4State(obj_bound_final=obj_bound_final)

    final_j_i_2_start = {
        (j, i): int(pf_solver.Value(pf_op_vars.op_start[j, i]))
        for j in pf_params.j_list
        for i in pf_params.i_list
    }
    final_j_i_2_end = {
        (j, i): int(pf_solver.Value(pf_op_vars.op_end[j, i]))
        for j in pf_params.j_list
        for i in pf_params.i_list
    }
    final_schedule = build_schedule_from_op_starts(
        instance, final_j_i_2_start, final_j_i_2_end
    )

    sum_e, sum_t = compute_window_et(final_schedule, instance)
    final_obj = float(sum_e + sum_t)
    cp_obj = float(pf_solver.objective_value)
    if final_obj != cp_obj and logger is not None:
        logger.warning(
            "run_mcf_lb phase 4: post-build objective %.3f != CP-SAT objective %.3f",
            final_obj,
            cp_obj,
        )
    diagnostic.profile_fix_obj = final_obj
    diagnostic.reached_phase = "profile_fix"

    return Phase4State(
        obj_bound_final=obj_bound_final,
        final_schedule=final_schedule,
        final_obj=final_obj,
    )
