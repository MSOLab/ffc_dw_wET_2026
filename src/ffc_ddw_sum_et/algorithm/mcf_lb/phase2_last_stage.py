"""Phase 2 of the MCF-LB pipeline.

Builds and solves an independent last-stage-only CP-SAT model for each
Phase 1 seed. Returns the best-obj feasible candidate (plus the full
candidate list) consumed by Phase 3. Returns ``None`` when no seed
yields a feasible solution.
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
from .phase1_mcf import LastStageSeed, Phase1State, SeedTag

__all__ = ["LastStageCandidate", "Phase2State", "run_phase2"]


@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageCandidate:
    """One feasible last-stage CP-SAT solution, tagged by its seed."""

    tag: SeedTag
    last_stage_only_schedule: FFcSchedule
    last_stage_only_schedule_makespan: int
    last_stage_only_obj: float
    # CP-SAT best_objective_bound from the last-stage-only model.
    # Valid as a global LB only if not profile-fixed by any means.
    # At the moment, the bound is not a global LB since profile-fixing is applied
    last_stage_only_bound: float
    ls_status: str
    ls_j_i_2_end: dict[tuple[str, str], int]


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase2State:
    """Outputs of Phase 2 consumed by Phase 3."""

    chosen: LastStageCandidate
    candidates: list[LastStageCandidate]

    # Backward-compatible shortcuts mirroring ``chosen``.
    last_stage_only_schedule: FFcSchedule
    last_stage_only_schedule_makespan: int
    last_stage_only_obj: float
    ls_j_i_2_end: dict[tuple[str, str], int]


def run_phase2(
    phase1: Phase1State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    profile_fix_by_machine: bool,
    machine_precedence_stride: int,
    solver_thread_cnt: int = 1,
    logger: logging.Logger | None = None,
) -> Phase2State | None:
    """Solve the last-stage CP-SAT model once per seed, pick the best.

    Each seed gets its own model (horizon, profile-fix constraints, start
    and end hints are all seed-specific). Seeds that return INFEASIBLE
    raise; seeds that return UNKNOWN/time-limit are skipped with a
    warning; feasible seeds contribute a ``LastStageCandidate``.

    Mutates ``diagnostic``: accumulates ``last_stage_cp_sat_sec`` across
    seeds; records per-seed ``ls_status_per_seed`` /
    ``last_stage_obj_per_seed``; populates aggregate
    ``last_stage_only_obj`` / ``last_stage_only_bound`` / ``ls_status``
    from the chosen candidate; sets ``chosen_seed_tag`` and advances
    ``reached_phase`` to ``"last_stage"``.

    Returns ``None`` when no seed produces a feasible solution.
    """
    candidates: list[LastStageCandidate] = []
    total_solve_sec = 0.0

    for seed in phase1.last_stage_seeds:
        candidate, solve_sec, status_name = _solve_last_stage_for_seed(
            seed,
            phase1,
            instance,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
            solver_thread_cnt=solver_thread_cnt,
        )
        total_solve_sec += solve_sec
        diagnostic.ls_status_per_seed[seed.tag] = status_name
        if candidate is None:
            if logger is not None:
                logger.warning(
                    "run_mcf_lb phase 2: last-stage CP-SAT no feasible solution "
                    "for seed=%s (status=%s)",
                    seed.tag,
                    status_name,
                )
            continue
        diagnostic.last_stage_obj_per_seed[seed.tag] = candidate.last_stage_only_obj
        candidates.append(candidate)

    diagnostic.last_stage_cp_sat_sec = total_solve_sec

    if not candidates:
        if logger is not None:
            logger.warning(
                "run_mcf_lb phase 2: no seed produced a feasible last-stage solution"
            )
        return None

    chosen = min(candidates, key=lambda c: c.last_stage_only_obj)

    diagnostic.last_stage_only_obj = chosen.last_stage_only_obj
    diagnostic.last_stage_only_bound = chosen.last_stage_only_bound
    diagnostic.ls_status = chosen.ls_status
    diagnostic.chosen_seed_tag = chosen.tag
    diagnostic.reached_phase = "last_stage"

    return Phase2State(
        chosen=chosen,
        candidates=candidates,
        last_stage_only_schedule=chosen.last_stage_only_schedule,
        last_stage_only_schedule_makespan=chosen.last_stage_only_schedule_makespan,
        last_stage_only_obj=chosen.last_stage_only_obj,
        ls_j_i_2_end=chosen.ls_j_i_2_end,
    )


def _solve_last_stage_for_seed(
    seed: LastStageSeed,
    phase1: Phase1State,
    instance: FFcDDWParameters,
    *,
    profile_fix_by_machine: bool,
    machine_precedence_stride: int,
    solver_thread_cnt: int,
) -> tuple[LastStageCandidate | None, float, str]:
    """Build and solve a last-stage-only CP-SAT model for one seed.

    Returns ``(candidate, solve_sec, status_name)``. ``candidate`` is
    ``None`` when the solver returns neither OPTIMAL nor FEASIBLE.
    Raises ``RuntimeError`` on INFEASIBLE (the MCF LB should be
    consistent with the last-stage-only model).
    """
    last_stage_id = phase1.last_stage_id
    horizon = int(seed.init_schedule.makespan * 2)

    ls_builder = BaseModelBuilder()
    ls_mdl, ls_params, ls_ops_vars, _ls_obj_vars = ls_builder.build(
        instance=instance,
        horizon=horizon,
        last_stage_only=True,
        job_2_release=phase1.job_2_release_map,
        obj_lb=phase1.mcf_lb,
    )

    BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
        ls_mdl,
        ls_params,
        ls_ops_vars,
        seed.init_schedule,
        profile_fix_by_machine=profile_fix_by_machine,
        machine_precedence_stride=machine_precedence_stride,
    )
    BaseModelBuilder.apply_start_hints_from_start_time_map(
        ls_mdl,
        ls_params,
        ls_ops_vars,
        seed.init_schedule.get_jik_2_start_time_map(),
    )
    BaseModelBuilder.apply_end_hints_from_end_time_map(
        ls_mdl,
        ls_params,
        ls_ops_vars,
        seed.init_schedule.get_jik_2_end_time_map(),
    )

    ls_solver = cp_model.CpSolver()
    ls_solver.parameters.num_search_workers = int(solver_thread_cnt)

    t_ls = time.monotonic()
    ls_status = ls_solver.Solve(ls_mdl)
    solve_sec = time.monotonic() - t_ls
    status_name = ls_solver.StatusName(ls_status)

    if ls_status == cp_model.INFEASIBLE:
        raise RuntimeError(
            f"Last-stage CP-SAT model is infeasible for seed={seed.tag}; "
            "check MCF LB solution validity"
        )
    if ls_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, solve_sec, status_name

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

    candidate = LastStageCandidate(
        tag=seed.tag,
        last_stage_only_schedule=last_stage_only_schedule,
        last_stage_only_schedule_makespan=last_stage_only_schedule_makespan,
        last_stage_only_obj=float(ls_solver.objective_value),
        last_stage_only_bound=float(ls_solver.best_objective_bound),
        ls_status=status_name,
        ls_j_i_2_end=ls_j_i_2_end,
    )
    return candidate, solve_sec, status_name
