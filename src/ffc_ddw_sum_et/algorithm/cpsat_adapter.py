"""CP-SAT adapter solving the FFc-DDW base model.

Algorithm Protocol implementation that builds a fresh base CP model for
the full instance, optionally warm-starts it from ``spec.ref_solution``,
solves via CP-SAT under a single pre-resolved time cap, and returns the
post-CP semi-active schedule.

Time budgeting is the controller's responsibility: ``CpsatOption.timelimit_sec``
is the strict-min of any user-specified per-call cap and the controller's
remaining global time. The adapter only subtracts model-build elapsed
from it to derive ``CpSolver.parameters.max_time_in_seconds``.
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
    TimingInfo,
    WorkStatus,
)
from .base.alg_spec import AlgSpec
from .cpsat_solver_options import CpsatSolverOptions, get_solver
from .cumulative import BaseModelBuilder

__all__ = ["CpsatAdapter", "CpsatOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CpsatOption(AlgOption):
    """Option payload for ``CpsatAdapter``.

    ``timelimit_sec`` is the single, fully pre-resolved time cap. The
    controller is responsible for taking the strict-min of any per-call
    cap and remaining global time before constructing this option.
    ``None`` means "no time cap from the caller" — CP-SAT runs until
    OPTIMAL / INFEASIBLE / external interruption.
    """

    timelimit_sec: float | None = None
    solver_thread_cnt: int = 1
    log_search_progress: bool = False
    error_if_infeasible: bool = False
    draw_gantt: bool = False


class CpsatAdapter:
    """Solve the full-instance FFc-DDW base CP model via CP-SAT."""

    algorithm_id = "cpsat_base_model"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger if spec.logger is not None else logging.getLogger(__name__)

        start = time.monotonic()

        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())
        builder = BaseModelBuilder()
        mdl, params, op_vars, et_vars = builder.build(instance, horizon=horizon)

        ref_schedule = spec.ref_solution
        if ref_schedule is not None:
            BaseModelBuilder.apply_hints_from_schedule(
                mdl, params, op_vars, et_vars, ref_schedule
            )

        eff_tl: float | None
        if option.timelimit_sec is None:
            eff_tl = None
        else:
            eff_tl = max(0.0, option.timelimit_sec - (time.monotonic() - start))

        solver_cfg = CpsatSolverOptions(
            max_time_in_seconds=eff_tl,
            num_workers=option.solver_thread_cnt,
            log_search_progress=option.log_search_progress,
        )
        solver = get_solver(solver_cfg)

        logger.info(
            "CpsatAdapter: instance=%s, ref_solution=%s, eff_tl=%s, num_workers=%d",
            instance.name,
            "given" if ref_schedule is not None else "None",
            f"{eff_tl:.3f}s" if eff_tl is not None else "None",
            option.solver_thread_cnt,
        )

        status = solver.solve(mdl)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        status_name = solver.status_name(status)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

        if not has_solution:
            if status == cp_model.INFEASIBLE and option.error_if_infeasible:
                raise RuntimeError(
                    f"CpsatAdapter: CP-SAT proved INFEASIBLE for {instance.name}."
                )
            logger.warning(
                "CpsatAdapter: no feasible solution (status=%s)", status_name
            )
            return AlgRecord(
                work_status=(
                    WorkStatus.INFEASIBLE
                    if status == cp_model.INFEASIBLE
                    else WorkStatus.ERROR
                ),
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=None,
                    obj_value=None,
                    obj_bound=None,
                    metrics={"cpsat_status": status_name},
                ),
                timing=TimingInfo(wall_ms=elapsed_ms),
                termination_reason=(
                    TerminationReason.COMPLETED
                    if status == cp_model.INFEASIBLE
                    else TerminationReason.ERROR
                ),
                error=None
                if status == cp_model.INFEASIBLE
                else f"status={status_name}",
            )

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
        schedule = build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)
        schedule.make_semi_active(instance.stage_2_job_2_p_map)

        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
        obj_value = float(sum_e + sum_t)
        cp_obj = float(solver.objective_value)
        if obj_value != cp_obj:
            logger.warning(
                "CpsatAdapter: post-semi-active objective %.3f != CP-SAT objective %.3f",
                obj_value,
                cp_obj,
            )
        obj_bound = float(solver.best_objective_bound)

        if option.draw_gantt:
            logger.info(
                "CpsatAdapter: draw_gantt=True requested but Gantt rendering "
                "is not yet implemented at the algorithm layer; skipping."
            )

        return AlgRecord(
            work_status=(
                WorkStatus.OPTIMAL
                if status == cp_model.OPTIMAL
                else WorkStatus.FEASIBLE
            ),
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=schedule,
                obj_value=obj_value,
                obj_bound=obj_bound,
                metrics={
                    "cpsat_status": status_name,
                    "cpsat_obj_value": cp_obj,
                    "sum_earliness": float(sum_e),
                    "sum_tardiness": float(sum_t),
                    "makespan": schedule.makespan,
                },
            ),
            timing=TimingInfo(wall_ms=elapsed_ms),
            termination_reason=(
                TerminationReason.COMPLETED
                if status == cp_model.OPTIMAL
                else TerminationReason.TIME_LIMIT
            ),
        )

    @staticmethod
    def _validate_instance(spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError("CpsatAdapter requires FFcDDWParameters as spec.instance.")
        return spec.instance

    @staticmethod
    def _resolve_option(spec: AlgSpec) -> CpsatOption:
        if spec.option is None:
            return CpsatOption()
        if not isinstance(spec.option, CpsatOption):
            raise TypeError("CpsatAdapter requires CpsatOption as spec.option.")
        return spec.option
