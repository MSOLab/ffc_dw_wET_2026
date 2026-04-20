"""Phase 2 of the MCF-LB pipeline.

Warm-starts the last-stage-only CP-SAT model from the Phase 1 dispatch
seed and solves it. Returns the partial last-stage schedule used by
Phase 3 as the pinned seed, or ``None`` when no feasible solution is
found within the time budget.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.schedule_build import build_schedule_from_op_starts
from ..cumulative import BaseModelBuilder
from .diagnostic import MCFLBDiagnostic
from .phase1_mcf import Phase1State

__all__ = ["Phase2State", "run_phase2"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase2State:
    """Outputs of Phase 2 consumed by Phase 3."""

    last_stage_only_schedule: FFcSchedule
    last_stage_only_schedule_makespan: int
    last_stage_only_obj: float
    ls_j_i_2_end: dict[tuple[str, str], int]


def run_phase2(
    phase1: Phase1State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    last_stage_only_timelimit: float | str | None = None,
    solver_thread_cnt: int = 1,
    logger: logging.Logger | None = None,
) -> Phase2State | None:
    """Apply Phase 1 hints and solve the last-stage-only CP-SAT model.

    Mutates ``diagnostic``: sets ``last_stage_cp_sat_sec``, ``ls_status``,
    and on feasibility ``last_stage_only_obj``, ``last_stage_only_bound``,
    advancing ``reached_phase`` to ``"last_stage"``.

    Returns ``None`` when the solver does not produce OPTIMAL or FEASIBLE
    within the budget; a warning is logged via ``logger`` if supplied.
    """
    last_stage_id = phase1.last_stage_id

    BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
        phase1.ls_mdl,
        phase1.ls_params,
        phase1.ls_ops_vars,
        phase1.last_stage_only_init_schedule,
        profile_fix_by_machine=True,
        machine_precedence_stride=1,
    )
    BaseModelBuilder.apply_start_hints_from_start_time_map(
        phase1.ls_mdl,
        phase1.ls_params,
        phase1.ls_ops_vars,
        phase1.last_stage_only_init_schedule.get_jik_2_start_time_map(),
    )
    BaseModelBuilder.apply_end_hints_from_end_time_map(
        phase1.ls_mdl,
        phase1.ls_params,
        phase1.ls_ops_vars,
        phase1.last_stage_only_init_schedule.get_jik_2_end_time_map(),
    )

    ls_budget = _parse_nc_timelimit(
        last_stage_only_timelimit, instance.job_count, instance.stage_count
    )
    ls_solver = cp_model.CpSolver()
    if ls_budget is not None:
        ls_solver.parameters.max_time_in_seconds = float(ls_budget)
    ls_solver.parameters.num_search_workers = int(solver_thread_cnt)
    # TODO: remove
    # ls_solver.parameters.log_search_progress = True

    t_ls = time.monotonic()
    ls_status = ls_solver.Solve(phase1.ls_mdl)
    diagnostic.last_stage_cp_sat_sec = time.monotonic() - t_ls
    diagnostic.ls_status = ls_solver.StatusName(ls_status)

    if ls_status == cp_model.INFEASIBLE:
        raise RuntimeError("Last-stage CP-SAT model is infeasible; check MCF LB solution validity")
    if ls_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if logger is not None:
            logger.warning(
                "run_mcf_lb phase 2: last-stage CP-SAT no feasible solution "
                "(status=%s)",
                ls_solver.StatusName(ls_status),
            )
        return None

    ls_ops_vars = phase1.ls_ops_vars
    ls_params = phase1.ls_params

    ls_j_i_2_start = {
        (j, last_stage_id): int(ls_solver.Value(ls_ops_vars.op_start[j, last_stage_id]))
        for j in ls_params.j_list
    }
    ls_j_i_2_end = {
        (j, last_stage_id): int(ls_solver.Value(ls_ops_vars.op_end[j, last_stage_id]))
        for j in ls_params.j_list
    }
    last_stage_only_schedule = build_schedule_from_op_starts(
        instance, ls_j_i_2_start, ls_j_i_2_end, stages=[last_stage_id]
    )
    last_stage_only_schedule_makespan = max(ls_j_i_2_end.values())

    diagnostic.last_stage_only_obj = float(ls_solver.objective_value)
    diagnostic.last_stage_only_bound = float(ls_solver.best_objective_bound)
    diagnostic.reached_phase = "last_stage"

    return Phase2State(
        last_stage_only_schedule=last_stage_only_schedule,
        last_stage_only_schedule_makespan=last_stage_only_schedule_makespan,
        last_stage_only_obj=float(ls_solver.objective_value),
        ls_j_i_2_end=ls_j_i_2_end,
    )


def _parse_nc_timelimit(value: float | str | None, n: int, c: int) -> float | None:
    """Parse a timelimit spec into seconds.

    - ``None`` -> ``None`` (no limit).
    - ``float``/``int`` -> seconds as-is.
    - ``"<x>nc"`` -> ``float(x) * n * c`` seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = value.strip()
    if s.endswith("nc"):
        return float(s[:-2]) * n * c
    raise ValueError(
        f"Invalid timelimit spec: {value!r}; expected float or '<x>nc' string"
    )
