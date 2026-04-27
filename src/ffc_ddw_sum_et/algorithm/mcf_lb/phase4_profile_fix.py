"""Phase 4 of the MCF-LB pipeline.

Profile-fix CP-SAT full solve warm-started from the Phase 3 dispatched
schedule. Produces the final schedule and an ``obj_bound_final =
max(mcf_lb, pf_bound)``; if the solver returns neither OPTIMAL nor
FEASIBLE, ``final_schedule``/``final_obj`` stay ``None`` and the
caller falls back to the Phase 3 incumbent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..cumulative import PFMethod
from ..cumulative_routine import solve_full_cp_with_profile_fix
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
    logger: logging.Logger | None = None,
    pf_method: PFMethod | None = None,
    solver_thread_cnt: int = 1,
    repeat_full_cp_while_improving: bool = False,
    cp_tl_seconds: float | None = None,
    log_search_progress: bool = False,
    solver_log_path_getter: Callable[[str], Path] | None = None,
) -> Phase4State:
    """Build and solve the profile-fix full CP-SAT model.

    Mutates ``diagnostic``: ``profile_fix_cp_sat_sec``, ``pf_status``,
    ``profile_fix_bound`` always; on feasibility also ``profile_fix_obj``
    and advances ``reached_phase`` to ``"profile_fix"``.

    When ``repeat_full_cp_while_improving=True``, the solve is repeated with
    the new schedule fed back as the dispatched-schedule reference until
    the CP-SAT objective stops improving. ``profile_fix_cp_sat_sec``
    accumulates across iterations; ``pf_status``, ``profile_fix_bound``,
    ``profile_fix_obj`` reflect the last (best) iteration.

    Always returns a ``Phase4State``. When the solver is infeasible the
    ``final_schedule`` / ``final_obj`` slots stay ``None`` and the caller
    should fall back to the Phase 3 dispatched schedule as the reported
    incumbent, using ``obj_bound_final`` as the bound.
    """
    result, total_solve_sec, last_status_name = solve_full_cp_with_profile_fix(
        phase3.dispatched_schedule,
        instance,
        pf_method=pf_method,
        solver_thread_cnt=solver_thread_cnt,
        repeat_while_improving=repeat_full_cp_while_improving,
        obj_lb=phase1.mcf_lb,
        max_time_in_seconds=cp_tl_seconds,
        log_search_progress=log_search_progress,
        solver_log_path_getter=solver_log_path_getter,
    )
    diagnostic.profile_fix_cp_sat_sec = total_solve_sec
    diagnostic.pf_status = last_status_name

    if result is None:
        pf_bound = phase1.mcf_lb
        diagnostic.profile_fix_bound = pf_bound
        obj_bound_final = max(phase1.mcf_lb, pf_bound)
        if logger is not None:
            logger.warning(
                "run_mcf_lb phase 4: profile-fix CP-SAT no feasible solution "
                "(status=%s); falling back to phase-3 incumbent",
                last_status_name,
            )
        return Phase4State(obj_bound_final=obj_bound_final)

    pf_bound = result.bound
    diagnostic.profile_fix_bound = pf_bound
    obj_bound_final = max(phase1.mcf_lb, pf_bound)

    final_schedule = result.schedule
    sum_e, sum_t = compute_weighted_earliness_tardiness(final_schedule, instance)
    final_obj = float(sum_e + sum_t)
    cp_obj = result.objective
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
