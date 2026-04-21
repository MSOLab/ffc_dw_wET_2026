"""Reusable solve routines built on top of BaseModelBuilder."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from ..solution.schedule_build import build_schedule_from_op_starts
from .cumulative import BaseModelBuilder

__all__ = [
    "FullCpSolveResult",
    "LastStageSolveResult",
    "solve_full_cp_with_profile_fix",
    "solve_last_stage_with_profile_fix",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageSolveResult:
    """Raw CP-SAT output for one last-stage-only solve."""

    status_name: str
    schedule: FFcSchedule
    objective: float
    bound: float
    j_i_2_end: dict[tuple[str, str], int]
    makespan: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FullCpSolveResult:
    """Raw CP-SAT output for one full-model profile-fix solve."""

    status_name: str
    schedule: FFcSchedule
    objective: float
    bound: float
    j_i_2_start: dict[tuple[str, str], int]
    j_i_2_end: dict[tuple[str, str], int]


def solve_last_stage_with_profile_fix(
    reference_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    last_stage_id: str,
    job_2_release: dict[str, int],
    obj_lb: float,
    *,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
    solver_thread_cnt: int = 1,
    repeat_while_improving: bool = False,
) -> tuple[LastStageSolveResult | None, float, str]:
    """Build and solve a last-stage-only CP-SAT model, optionally looping.

    Args:
        reference_schedule (FFcSchedule): Schedule whose operation ordering
            defines the profile-fix precedence arcs and warm-start hints.
            When ``repeat_while_improving=True`` this is only the initial
            reference; later iterations use the previously-solved schedule.
        instance (FFcDDWParameters): The FFc DDW instance to model.
        last_stage_id (str): Stage id whose operations are the CP-SAT
            decision variables. Earlier-stage operations are abstracted away
            via ``job_2_release``.
        job_2_release (dict[str, int]): Per-job earliest start at the last
            stage, applied as ``op_start`` lower bounds (typically the MCF
            preemptive completion times of stages 1..c-1).
        obj_lb (float): Lower bound on the CP-SAT objective (e.g., the MCF
            LB), passed into the model for pruning.
        profile_fix_by_machine (bool, optional): If True, profile-fix
            precedence is enforced per-machine within each stage instead of
            per-stage. Defaults to False.
        machine_precedence_stride (int, optional): Stride ``k`` for the
            per-machine precedence chain; only operations ``k`` positions
            apart are constrained. ``1`` = full chain. Defaults to 1.
        solver_thread_cnt (int, optional): Value passed to
            ``CpSolver.parameters.num_search_workers``. Defaults to 1.
        repeat_while_improving (bool, optional): If True, after each
            feasible solve the resulting schedule becomes the new profile-fix
            reference and the model is rebuilt and re-solved. The loop stops
            once the new objective fails to strictly improve
            (``new_obj >= prev_obj``); the returned ``result`` is the last
            strictly-improving iteration. Defaults to False.

    Raises:
        RuntimeError: The CP-SAT solver returned ``INFEASIBLE``, which
            indicates the MCF LB is inconsistent with the last-stage-only
            model and should not happen in practice.

    Returns:
        tuple[LastStageSolveResult | None, float, str]: A 3-tuple of
        ``(result, total_solve_sec, last_status_name)``:

        * ``result`` — summary of the best feasible solve, or ``None`` if
          no iteration returned ``OPTIMAL``/``FEASIBLE``.
        * ``total_solve_sec`` — cumulative wall-clock seconds across every
          CP-SAT solve attempt, including non-feasible terminations.
        * ``last_status_name`` — ``CpSolver.status_name`` of the final
          solve attempt (the one that ended the loop).
    """
    current_schedule = reference_schedule
    total_solve_sec = 0.0
    best_result: LastStageSolveResult | None = None
    best_status: str | None = None
    prev_obj = float("inf")

    while True:
        horizon = int(current_schedule.makespan * 2)

        ls_builder = BaseModelBuilder()
        ls_mdl, ls_params, ls_ops_vars, _ = ls_builder.build(
            instance=instance,
            horizon=horizon,
            last_stage_only=True,
            job_2_release=job_2_release,
            obj_lb=obj_lb,
        )
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            ls_mdl,
            ls_params,
            ls_ops_vars,
            current_schedule,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
        )
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            ls_mdl,
            ls_params,
            ls_ops_vars,
            current_schedule.get_jik_2_start_time_map(),
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            ls_mdl,
            ls_params,
            ls_ops_vars,
            current_schedule.get_jik_2_end_time_map(),
        )

        ls_solver = cp_model.CpSolver()
        ls_solver.parameters.num_search_workers = int(solver_thread_cnt)

        t0 = time.monotonic()
        status = ls_solver.solve(ls_mdl)
        status_name = ls_solver.status_name(status)
        total_solve_sec += time.monotonic() - t0

        if status == cp_model.INFEASIBLE:
            raise RuntimeError(
                "Last-stage CP-SAT model is infeasible; check MCF LB solution validity"
            )
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break

        ls_j_i_2_start = {
            (j, last_stage_id): int(
                ls_solver.Value(ls_ops_vars.op_start[j, last_stage_id])
            )
            for j in ls_params.j_list
        }
        ls_j_i_2_end = {
            (j, last_stage_id): int(
                ls_solver.Value(ls_ops_vars.op_end[j, last_stage_id])
            )
            for j in ls_params.j_list
        }
        new_schedule = build_schedule_from_op_starts(
            instance, ls_j_i_2_start, ls_j_i_2_end, stages=[last_stage_id]
        )
        new_obj = float(ls_solver.objective_value)

        result = LastStageSolveResult(
            status_name=status_name,
            schedule=new_schedule,
            objective=new_obj,
            bound=float(ls_solver.best_objective_bound),
            j_i_2_end=ls_j_i_2_end,
            makespan=max(ls_j_i_2_end.values()),
        )

        if not repeat_while_improving:
            best_result = result
            best_status = status_name
            break

        if new_obj >= prev_obj:
            break

        best_result = result
        best_status = status_name
        prev_obj = new_obj
        current_schedule = result.schedule

    return best_result, total_solve_sec, best_status or status_name


def solve_full_cp_with_profile_fix(
    reference_schedule: FFcSchedule,
    instance: FFcDDWParameters,
    *,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
    solver_thread_cnt: int = 1,
    repeat_while_improving: bool = False,
) -> tuple[FullCpSolveResult | None, float, str]:
    """Build and solve the full profile-fix CP-SAT model, optionally looping.

    The full model includes all stages (no ``last_stage_only``,
    ``job_2_release`` or ``obj_lb`` shortcuts); the input schedule is used
    only to drive the profile-fix precedence arcs and the warm-start hints.

    Args:
        reference_schedule (FFcSchedule): Schedule whose operation ordering
            defines the profile-fix precedence arcs and warm-start hints.
            When ``repeat_while_improving=True`` this is only the initial
            reference; later iterations use the previously-solved schedule.
        instance (FFcDDWParameters): The FFc DDW instance to model.
        profile_fix_by_machine (bool, optional): If True, profile-fix
            precedence is enforced per-machine within each stage instead of
            per-stage. Defaults to False.
        machine_precedence_stride (int, optional): Stride ``k`` for the
            per-machine precedence chain; only operations ``k`` positions
            apart are constrained. ``1`` = full chain. Defaults to 1.
        solver_thread_cnt (int, optional): Value passed to
            ``CpSolver.parameters.num_search_workers``. Defaults to 1.
        repeat_while_improving (bool, optional): If True, after each
            feasible solve the resulting schedule becomes the new profile-fix
            reference and the model is rebuilt and re-solved. The loop stops
            once the new objective fails to strictly improve
            (``new_obj >= prev_obj``); the returned ``result`` is the last
            strictly-improving iteration. Defaults to False.

    Returns:
        tuple[FullCpSolveResult | None, float, str]: A 3-tuple of
        ``(result, total_solve_sec, last_status_name)``:

        * ``result`` — summary of the best feasible solve, or ``None`` if
          no iteration returned ``OPTIMAL``/``FEASIBLE``. Unlike the
          last-stage routine, ``INFEASIBLE`` is treated like any other
          non-feasible termination (loop exits, no exception raised) so
          the caller can fall back to its incumbent.
        * ``total_solve_sec`` — cumulative wall-clock seconds across every
          CP-SAT solve attempt, including non-feasible terminations.
        * ``last_status_name`` — ``CpSolver.status_name`` of the final
          solve attempt (the one that ended the loop).
    """
    current_schedule = reference_schedule
    total_solve_sec = 0.0
    best_result: FullCpSolveResult | None = None
    best_status: str | None = None
    prev_obj = float("inf")

    while True:
        horizon = int(current_schedule.makespan * 2)

        pf_builder = BaseModelBuilder()
        pf_mdl, pf_params, pf_op_vars, _ = pf_builder.build(instance, horizon=horizon)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            pf_mdl,
            pf_params,
            pf_op_vars,
            current_schedule,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
        )
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            pf_mdl,
            pf_params,
            pf_op_vars,
            current_schedule.get_jik_2_start_time_map(),
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            pf_mdl,
            pf_params,
            pf_op_vars,
            current_schedule.get_jik_2_end_time_map(),
        )

        pf_solver = cp_model.CpSolver()
        pf_solver.parameters.num_search_workers = int(solver_thread_cnt)

        t0 = time.monotonic()
        status = pf_solver.solve(pf_mdl)
        status_name = pf_solver.status_name(status)
        total_solve_sec += time.monotonic() - t0

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break

        pf_j_i_2_start = {
            (j, i): int(pf_solver.Value(pf_op_vars.op_start[j, i]))
            for j in pf_params.j_list
            for i in pf_params.i_list
        }
        pf_j_i_2_end = {
            (j, i): int(pf_solver.Value(pf_op_vars.op_end[j, i]))
            for j in pf_params.j_list
            for i in pf_params.i_list
        }
        new_schedule = build_schedule_from_op_starts(
            instance, pf_j_i_2_start, pf_j_i_2_end
        )
        new_obj = float(pf_solver.objective_value)

        result = FullCpSolveResult(
            status_name=status_name,
            schedule=new_schedule,
            objective=new_obj,
            bound=float(pf_solver.best_objective_bound),
            j_i_2_start=pf_j_i_2_start,
            j_i_2_end=pf_j_i_2_end,
        )

        if not repeat_while_improving:
            best_result = result
            best_status = status_name
            break

        if new_obj >= prev_obj:
            break

        best_result = result
        best_status = status_name
        prev_obj = new_obj
        current_schedule = result.schedule

    return best_result, total_solve_sec, best_status or status_name
