"""Coarsen-Solve-Reconstruct adapter for the FFc-DDW problem.

Coarsens a ``FFcDDWParameters`` instance by ``factor``, solves the
coarsened model with the base CP-SAT (``BaseModelBuilder``), then
reconstructs the raw coarse start/end times back to the original scale:

    reconstructed_start[j,i] = coarse_start[j,i] * factor
    reconstructed_end[j,i]   = reconstructed_start[j,i] + original_p[j,i]

Post-processing (``make_semi_active`` → ``insert_idle_time``) and objective
evaluation (``compute_weighted_earliness_tardiness``) are all done against
the **original** instance, so metrics reflect the original problem scale.

Time budgeting follows the same pattern as ``CpsatAdapter``:
``CoarsenSolveReconstructOption.timelimit_sec`` is the single pre-resolved
cap; model-build elapsed is subtracted before setting
``CpSolver.parameters.max_time_in_seconds``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.objectives import compute_weighted_earliness_tardiness
from ..solution.schedule_build import build_schedule_from_op_starts
from .base.alg_option import AlgOption
from .base.alg_record import (
    AlgRecord,
    AlgResult,
    TerminationReason,
    WorkStatus,
)
from .base.alg_spec import AlgSpec
from .cpsat_solver_options import CpsatSolverOptions, get_solver
from .cumulative import BaseModelBuilder

__all__ = ["CoarsenSolveReconstructAdapter", "CoarsenSolveReconstructOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CoarsenSolveReconstructOption(AlgOption):
    """Option payload for ``CoarsenSolveReconstructAdapter``.

    ``timelimit_sec`` is the single, fully pre-resolved time cap (same
    convention as ``CpsatOption``): the controller takes the strict-min of
    any per-call cap and the remaining global time before constructing this
    option. ``None`` means "no time cap from the caller".

    ``factor`` is the coarsening divisor applied to all processing times and
    due-window bounds via ``ceil(value / factor)``.
    """

    factor: int = 50
    timelimit_sec: float | None = None
    solver_thread_cnt: int = 1
    log_search_progress: bool = False
    error_if_infeasible: bool = False


def _solve_coarsened_model(
    coarsened_instance: FFcDDWParameters,
    *,
    timelimit_sec: float | None,
    solver_thread_cnt: int,
    log_search_progress: bool,
    build_start: float,
) -> tuple[
    str,
    dict[tuple[str, str], int] | None,
    dict[tuple[str, str], int] | None,
    float | None,
    float | None,
    float,
]:
    """Build, solve, and extract raw op starts/ends for the coarsened instance.

    Returns a tuple:
        (status_name, j_i_2_start, j_i_2_end, cp_obj_value, cp_obj_bound,
         elapsed_sec)

    ``j_i_2_start`` and ``j_i_2_end`` are ``None`` when no solution exists.
    ``cp_obj_value`` and ``cp_obj_bound`` are ``None`` when no solution exists.
    ``elapsed_sec`` is measured from ``build_start`` to after ``solver.solve``.

    The caller provides ``build_start`` (``time.monotonic()`` before the
    ``BaseModelBuilder.build`` call) so the effective timelimit can subtract
    model-build overhead before setting ``max_time_in_seconds``.
    """
    params = BaseModelBuilder.make_params(coarsened_instance)
    horizon = sum(params.p.values())
    builder = BaseModelBuilder()
    mdl, params, op_vars, _ = builder.build(coarsened_instance, horizon=horizon)

    eff_tl: float | None
    if timelimit_sec is None:
        eff_tl = None
    else:
        eff_tl = max(0.0, timelimit_sec - (time.monotonic() - build_start))

    solver_cfg = CpsatSolverOptions(
        max_time_in_seconds=eff_tl,
        num_workers=solver_thread_cnt,
        log_search_progress=log_search_progress,
    )
    solver = get_solver(solver_cfg)
    status = solver.solve(mdl)
    elapsed = time.monotonic() - build_start

    status_name = solver.status_name(status)
    has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    if not has_solution:
        return status_name, None, None, None, None, elapsed

    j_i_2_start = {
        (j, i): int(solver.value(op_vars.op_start[j, i]))
        for j in params.j_list
        for i in params.i_list
    }
    j_i_2_end = {
        (j, i): int(solver.value(op_vars.op_end[j, i]))
        for j in params.j_list
        for i in params.i_list
    }
    cp_obj_value = float(solver.objective_value)
    cp_obj_bound = float(solver.best_objective_bound)
    return status_name, j_i_2_start, j_i_2_end, cp_obj_value, cp_obj_bound, elapsed


class CoarsenSolveReconstructAdapter:
    """Solve FFc-DDW via coarsen → CP-SAT → reconstruct to original scale."""

    algorithm_id = "coarsen_solve_reconstruct"

    def run(self, spec: AlgSpec) -> AlgRecord:  # noqa: PLR0911
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger if spec.logger is not None else logging.getLogger(__name__)

        build_start = time.monotonic()

        coarsened = FFcDDWParameters.coarsen_time_resolution(instance, option.factor)

        logger.info(
            "CoarsenSolveReconstructAdapter: instance=%s, coarsened=%s, factor=%d, "
            "timelimit_sec=%s, num_workers=%d",
            instance.name,
            coarsened.name,
            option.factor,
            f"{option.timelimit_sec:.3f}s"
            if option.timelimit_sec is not None
            else "None",
            option.solver_thread_cnt,
        )

        (
            coarsened_status_name,
            coarse_j_i_2_start,
            coarse_j_i_2_end,
            coarsened_obj_value,
            coarsened_obj_bound,
            coarsened_elapsed,
        ) = _solve_coarsened_model(
            coarsened,
            timelimit_sec=option.timelimit_sec,
            solver_thread_cnt=option.solver_thread_cnt,
            log_search_progress=option.log_search_progress,
            build_start=build_start,
        )

        has_solution = coarse_j_i_2_start is not None

        if not has_solution:
            coarsened_status_int = {
                "INFEASIBLE": cp_model.INFEASIBLE,
            }.get(coarsened_status_name, -1)
            is_infeasible = coarsened_status_int == cp_model.INFEASIBLE
            if is_infeasible and option.error_if_infeasible:
                raise RuntimeError(
                    f"CoarsenSolveReconstructAdapter: coarsened CP proved INFEASIBLE "
                    f"for {coarsened.name}."
                )
            logger.warning(
                "CoarsenSolveReconstructAdapter: no feasible solution on coarsened "
                "instance (status=%s)",
                coarsened_status_name,
            )
            return AlgRecord(
                work_status=(
                    WorkStatus.INFEASIBLE if is_infeasible else WorkStatus.ERROR
                ),
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=None,
                    obj_value=None,
                    obj_bound=None,
                    metrics={
                        "factor": option.factor,
                        "coarsened_instance_name": coarsened.name,
                        "coarsened_status": coarsened_status_name,
                        "coarsened_obj_value": None,
                        "coarsened_obj_bound": None,
                        "coarsened_elapsed": coarsened_elapsed,
                        "reconstructed_obj_value": None,
                        "reconstructed_makespan": None,
                    },
                ),
                termination_reason=(
                    TerminationReason.COMPLETED
                    if is_infeasible
                    else TerminationReason.ERROR
                ),
                error=None
                if is_infeasible
                else f"coarsened_status={coarsened_status_name}",
            )

        # --- Reconstruct to original scale ---
        factor = option.factor
        original_p = instance.job_2_stage_2_p_map

        reconstructed_start: dict[tuple[str, str], int] = {
            (j, i): coarse_j_i_2_start[j, i] * factor for (j, i) in coarse_j_i_2_start
        }
        reconstructed_end: dict[tuple[str, str], int] = {
            (j, i): reconstructed_start[j, i] + original_p[j][i]
            for (j, i) in reconstructed_start
        }

        schedule = build_schedule_from_op_starts(
            instance, reconstructed_start, reconstructed_end
        )
        schedule.make_semi_active(instance.stage_2_job_2_p_map)
        schedule.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )

        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
        obj_value = float(sum_e + sum_t)

        coarsened_status_code_is_optimal = coarsened_status_name == "OPTIMAL"

        return AlgRecord(
            work_status=(
                WorkStatus.OPTIMAL
                if coarsened_status_code_is_optimal
                else WorkStatus.FEASIBLE
            ),
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=schedule,
                obj_value=obj_value,
                obj_bound=None,
                metrics={
                    "factor": option.factor,
                    "coarsened_instance_name": coarsened.name,
                    "coarsened_status": coarsened_status_name,
                    "coarsened_obj_value": coarsened_obj_value,
                    "coarsened_obj_bound": coarsened_obj_bound,
                    "coarsened_elapsed": coarsened_elapsed,
                    "reconstructed_obj_value": obj_value,
                    "reconstructed_makespan": schedule.makespan,
                },
            ),
            termination_reason=(
                TerminationReason.COMPLETED
                if coarsened_status_code_is_optimal
                else TerminationReason.TIME_LIMIT
            ),
        )

    @staticmethod
    def _validate_instance(spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "CoarsenSolveReconstructAdapter requires FFcDDWParameters as "
                "spec.instance."
            )
        return spec.instance

    @staticmethod
    def _resolve_option(spec: AlgSpec) -> CoarsenSolveReconstructOption:
        if spec.option is None:
            return CoarsenSolveReconstructOption()
        if not isinstance(spec.option, CoarsenSolveReconstructOption):
            raise TypeError(
                "CoarsenSolveReconstructAdapter requires CoarsenSolveReconstructOption "
                "as spec.option."
            )
        return spec.option
