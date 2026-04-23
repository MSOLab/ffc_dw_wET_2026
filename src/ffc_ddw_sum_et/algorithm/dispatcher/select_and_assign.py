"""CP-SAT based job-selection helper used by the BN2D dispatcher.

Ported from ``hybridflowshop.select_and_assign``; solves a 2-set selection
problem that picks ``K_L`` jobs for the "left cap" set and ``K_R`` jobs for the
"right cap" set while minimising ``sum(r_j x_j + t_j y_j)``, with a secondary
tie-break that prefers lower job indices.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model


def solve_selection_problem(
    jobs: Sequence[str],
    r: Mapping[str, int],
    t: Mapping[str, int],
    K_L: int,
    K_R: int,
    solver_thread_cnt: int = 1,
) -> dict[str, Any]:
    """Select disjoint ``L`` and ``R`` subsets of ``jobs`` minimising r/t cost.

    Parameters:
        jobs: Job IDs.
        r: ``{job_id: r_j}`` release-side cost for placing job in ``L``.
        t: ``{job_id: t_j}`` tail-side cost for placing job in ``R``.
        K_L: Exact number of jobs to pick for ``L``.
        K_R: Exact number of jobs to pick for ``R``.

    Returns:
        ``{"status": "OPTIMAL"|"FEASIBLE"|"INFEASIBLE", "L_set": [...],
        "R_set": [...], "total_cost": float, "solve_time": float}``.
    """
    model = cp_model.CpModel()

    x = {j: model.new_bool_var(f"x_{j}") for j in jobs}
    y = {j: model.new_bool_var(f"y_{j}") for j in jobs}

    for j in jobs:
        model.add(x[j] + y[j] <= 1)

    model.add(sum(x[j] for j in jobs) == K_L)
    model.add(sum(y[j] for j in jobs) == K_R)

    primary_obj = sum(r[j] * x[j] + t[j] * y[j] for j in jobs)
    model.minimize(primary_obj)

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = solver_thread_cnt
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": "INFEASIBLE"}

    optimal_cost_int = int(round(solver.objective_value))
    model.add(primary_obj == optimal_cost_int)

    # Secondary objective: prefer lower job indices (1-based).
    j_idx = {j: idx + 1 for idx, j in enumerate(jobs)}
    secondary_obj = sum(j_idx[j] * (x[j] + y[j]) for j in jobs)
    model.minimize(secondary_obj)

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = solver_thread_cnt
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": "INFEASIBLE"}

    L_set = [j for j in jobs if solver.value(x[j]) == 1]
    R_set = [j for j in jobs if solver.value(y[j]) == 1]
    return {
        "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        "L_set": L_set,
        "R_set": R_set,
        "total_cost": solver.objective_value,
        "solve_time": solver.wall_time,
    }
