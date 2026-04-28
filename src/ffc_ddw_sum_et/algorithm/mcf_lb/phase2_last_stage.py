"""Phase 2 of the MCF-LB pipeline.

Builds and solves an independent last-stage-only CP-SAT model for each
Phase 1 seed. Returns the best-obj feasible candidate (plus the full
candidate list) consumed by Phase 3. Returns ``None`` when no seed
yields a feasible solution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ffc_ddw_sum_et.algorithm.cumulative import PFMethod
from ffc_ddw_sum_et.algorithm.cumulative_heuristic import (
    solve_last_stage_by_cumulative_heuristic,
)
from ffc_ddw_sum_et.algorithm.cumulative_routine import (
    solve_last_stage_with_profile_fix,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

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
    logger: logging.Logger | None = None,
    pf_method: PFMethod | None = None,
    solver_thread_cnt: int = 1,
    repeat_last_stage_only_cp_while_improving: bool = False,
    tl_seconds: float | None = None,
    log_search_progress: bool = False,
    solver_log_path_getter: Callable[[str], Path] | None = None,
    use_heuristic: bool = False,
    heuristic_first_improvement_restart: bool = False,
    heuristic_insert_radius: int | None = None,
    use_mcf_window: bool = False,
) -> Phase2State | None:
    """Solve the last-stage CP-SAT model per seed, pick the best.

    Each seed gets its own model (horizon, profile-fix constraints, start
    and end hints are all seed-specific). Seeds that return INFEASIBLE
    raise; seeds that return UNKNOWN/time-limit are skipped with a
    warning; feasible seeds contribute a ``LastStageCandidate``.

    When ``repeat_last_stage_only_cp_while_improving=True``, each seed's solve
    is repeated with the new schedule fed back as the reference until
    the CP-SAT objective stops improving; per-seed ``solve_sec`` accumulates
    across iterations and the candidate reflects the last (best) iteration.

    Mutates ``diagnostic``: accumulates ``last_stage_cp_sat_sec`` across
    seeds; records per-seed ``ls_status_per_seed`` /
    ``last_stage_obj_per_seed``; populates aggregate
    ``last_stage_only_obj`` / ``last_stage_only_bound`` / ``ls_status``
    from the chosen candidate; sets ``chosen_seed_tag`` and advances
    ``reached_phase`` to ``"last_stage"``.

    When ``use_heuristic=True``, the seed whose ``init_schedule`` has the
    lowest weighted E+T is selected from Phase 1, and the cumulative
    reinsertion heuristic is run on that single seed's schedule. All other
    seeds and all CP-SAT-related kwargs are ignored on that path.

    When ``use_mcf_window=True``, every seed's CP-SAT solve tightens the
    last-stage interval-variable domains to ``phase1.mcf_window_per_job``
    and skips all warm-start hints (start/end/E/T). Mutually exclusive
    with ``use_heuristic``. ``ValueError`` is raised at model-build time
    if any job's MCF window cannot fit a contiguous interval of length
    ``p``; ``RuntimeError`` is raised if CP-SAT proves the tightened
    model infeasible.

    Returns ``None`` when no seed produces a feasible solution.
    """
    if use_heuristic and use_mcf_window:
        raise ValueError(
            "use_heuristic and use_mcf_window are mutually exclusive: the MCF "
            "window tightening only applies to the CP-SAT path."
        )

    candidates: list[LastStageCandidate] = []
    total_solve_sec = 0.0

    if use_heuristic:
        seeds_to_run = [
            min(
                phase1.last_stage_seeds,
                key=lambda s: sum(
                    compute_weighted_earliness_tardiness(s.init_schedule, instance)
                ),
            )
        ]
    else:
        seeds_to_run = phase1.last_stage_seeds

    for seed in seeds_to_run:
        candidate, solve_sec, status_name = _solve_last_stage_for_seed(
            seed,
            phase1,
            instance,
            diagnostic,
            logger=logger,
            pf_method=pf_method,
            solver_thread_cnt=solver_thread_cnt,
            repeat_cp_while_improving=repeat_last_stage_only_cp_while_improving,
            tl_seconds=tl_seconds,
            log_search_progress=log_search_progress,
            solver_log_path_getter=solver_log_path_getter,
            use_heuristic=use_heuristic,
            heuristic_first_improvement_restart=heuristic_first_improvement_restart,
            heuristic_insert_radius=heuristic_insert_radius,
            use_mcf_window=use_mcf_window,
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
    diagnostic: MCFLBDiagnostic,
    *,
    logger: logging.Logger | None = None,
    pf_method: PFMethod | None,
    solver_thread_cnt: int,
    repeat_cp_while_improving: bool = False,
    tl_seconds: float | None = None,
    log_search_progress: bool = False,
    solver_log_path_getter: Callable[[str], Path] | None = None,
    use_heuristic: bool = False,
    heuristic_first_improvement_restart: bool = False,
    heuristic_insert_radius: int | None = None,
    use_mcf_window: bool = False,
) -> tuple[LastStageCandidate | None, float, str]:
    """Solve the last-stage-only model for one seed (CP-SAT or heuristic).

    Returns ``(candidate, solve_sec, status_name)``. On the CP-SAT path,
    ``candidate`` is ``None`` when the solver returns neither OPTIMAL nor
    FEASIBLE; ``RuntimeError`` is raised on INFEASIBLE (the MCF LB should be
    consistent with the last-stage-only model). The heuristic path always
    yields a candidate.
    """
    if use_heuristic:
        result, solve_sec, status_name, progress, scan_stats = (
            solve_last_stage_by_cumulative_heuristic(
                seed.init_schedule,
                instance,
                phase1.last_stage_id,
                phase1.job_2_release_map,
                phase1.mcf_lb,
                logger=logger,
                time_limit_seconds=tl_seconds,
                first_improvement_restart=heuristic_first_improvement_restart,
                insert_radius=heuristic_insert_radius,
            )
        )
        diagnostic.heuristic_progress_per_seed[seed.tag] = progress
        diagnostic.heuristic_scan_stats_per_seed[seed.tag] = scan_stats
        candidate = LastStageCandidate(
            tag=seed.tag,
            last_stage_only_schedule=result.schedule,
            last_stage_only_schedule_makespan=result.makespan,
            last_stage_only_obj=result.objective,
            last_stage_only_bound=result.bound,
            ls_status=result.status_name,
            ls_j_i_2_end=result.j_i_2_end,
        )
        return candidate, solve_sec, status_name

    result, solve_sec, status_name = solve_last_stage_with_profile_fix(
        seed.init_schedule,
        instance,
        phase1.last_stage_id,
        phase1.job_2_release_map,
        phase1.mcf_lb,
        logger=logger,
        pf_method=pf_method,
        solver_thread_cnt=solver_thread_cnt,
        repeat_while_improving=repeat_cp_while_improving,
        max_time_in_seconds=tl_seconds,
        log_search_progress=log_search_progress,
        solver_log_path_getter=solver_log_path_getter,
        mcf_window_per_job=phase1.mcf_window_per_job if use_mcf_window else None,
    )
    if result is None:
        return None, solve_sec, status_name
    candidate = LastStageCandidate(
        tag=seed.tag,
        last_stage_only_schedule=result.schedule,
        last_stage_only_schedule_makespan=result.makespan,
        last_stage_only_obj=result.objective,
        last_stage_only_bound=result.bound,
        ls_status=result.status_name,
        ls_j_i_2_end=result.j_i_2_end,
    )
    return candidate, solve_sec, status_name
