"""Coarsen-Solve-Reconstruct adapter for the FFc-DDW problem.

Coarsens a ``FFcDDWParameters`` instance by ``factor``, applies a warm-start
hint from an EDD-based dispatch seed before solving the coarsened model with
the base CP-SAT (``BaseModelBuilder``), then reconstructs the raw coarse
start/end times back to the original scale:

    reconstructed_start[j,i] = coarse_start[j,i] * factor
    reconstructed_end[j,i]   = reconstructed_start[j,i] + original_p[j,i]

The dispatch seed strategy is selected via ``seed_dispatch`` (``"job_wise"``
or ``"mixed"``). Post-processing (``make_semi_active`` → ``insert_idle_time``)
and objective evaluation (``compute_weighted_earliness_tardiness``) are done
against the **original** instance, so metrics reflect the original problem scale.

Time budgeting follows the same pattern as ``CpsatAdapter``:
``CoarsenSolveReconstructOption.timelimit_sec`` is the single pre-resolved
cap; model-build elapsed is subtracted before setting
``CpSolver.parameters.max_time_in_seconds``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from ..solution.objectives import compute_weighted_earliness_tardiness
from ..solution.schedule_build import (
    build_schedule_from_op_starts,
    reconstruct_coarse_schedule,
    reconstruct_raw_coarse_schedule,
)
from .base.alg_option import AlgOption
from .base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    WorkStatus,
)
from .base.alg_spec import AlgSpec
from .cpsat_callbacks.obj_bound_recorder import ObjectiveBoundRecorder
from .cpsat_callbacks.obj_value_recorder import ObjectiveValueRecorder
from .cpsat_callbacks.progress_log_builder import build_progress_log
from .cpsat_solver_options import CpsatSolverOptions, get_solver
from .cumulative import BaseModelBuilder
from .dispatcher.base import BaseDispatcher
from .dispatcher.mixed import MixedDispatcher
from .dispatcher.paired import (
    build_v3_paired_dispatch_schedule,
    build_v4_paired_dispatch_schedule,
)
from .dispatcher.utils import dispatch_job_sequence_by_stages

__all__ = [
    "CoarsenSolveReconstructAdapter",
    "CoarsenSolveReconstructOption",
    "CoarsenSolveReconstructTrace",
    "run_coarsen_solve_reconstruct",
]


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
    seed_dispatch: Literal["job_wise", "mixed", "v3", "v4"] = "mixed"


@dataclass(frozen=True, slots=True, kw_only=True)
class CoarsenSolveReconstructTrace:
    """Immutable trace from one coarsen-solve-reconstruct pipeline run.

    Carries the final schedule, intermediate snapshots, and coarsened-scale
    CP trajectory.  All three schedule fields are **distinct objects**: the
    pipeline builds them separately so postprocess on ``final_schedule``
    cannot mutate ``reconstructed_raw_schedule``.
    """

    work_status: WorkStatus
    termination_reason: TerminationReason
    error: str | None

    # Schedules (None when no solution)
    final_schedule: FFcSchedule | None
    """Post-processed schedule on original scale (make_semi_active + insert_idle_time)."""
    coarse_schedule: FFcSchedule | None
    """Coarsened-scale schedule as returned by the CP-SAT solver."""
    reconstructed_raw_schedule: FFcSchedule | None
    """Inflated-start + original-p_ij duration schedule, BEFORE postprocess."""

    # CP trajectory in coarsened-scale objective units
    cp_progress_log: tuple[ProgressLogEntry, ...]
    """Merged UB/LB trajectory (coarsened scale). Empty when no solution."""

    # Original-scale final objective (None when no solution)
    obj_value: float | None

    # Same metrics dict as the adapter already emits
    metrics: dict


def _dispatch_seed_job_sequence(
    coarsened: FFcDDWParameters,
) -> list[str]:
    """Return jobs sorted by (d^+_j asc, w^+_j desc, given index asc)."""
    dw_ub = coarsened.job_2_dw_ub_map
    twt = coarsened.job_2_twt_map
    given_index = {j: idx for idx, j in enumerate(coarsened.job_id_list)}
    return sorted(
        coarsened.job_id_list,
        key=lambda j: (dw_ub[j], -twt[j], given_index[j]),
    )


def _build_dispatch_seed_schedule(
    coarsened: FFcDDWParameters,
    strategy: Literal["job_wise", "mixed", "v3", "v4"],
) -> FFcSchedule:
    """Build a seed schedule via dispatch + idle insertion on coarsened scale.

    * ``job_wise``: single job-wise dispatch using the EDD-derived sequence.
    * ``mixed``: enumerate all np-list candidates, insert idle, pick the one
      with minimum coarsened wET.
    * ``v3``: v3 paired-dispatch pool on coarsened instance → min-wET.
    """
    if strategy == "v3":
        seed, _obj, _label = build_v3_paired_dispatch_schedule(coarsened)
        return seed
    if strategy == "v4":
        seed, _obj, _label = build_v4_paired_dispatch_schedule(coarsened)
        return seed

    seq = _dispatch_seed_job_sequence(coarsened)
    dw = coarsened.job_2_due_window_map
    ewt = coarsened.job_2_ewt_map
    twt = coarsened.job_2_twt_map

    if strategy == "job_wise":
        schedule = BaseDispatcher(coarsened)._create_empty_schedule(coarsened)
        dispatch_job_sequence_by_stages(schedule, seq, coarsened.job_2_stage_2_p_map)
        schedule.insert_idle_time(dw, ewt, twt)
        return schedule

    # strategy == "mixed": pick candidate with minimum coarsened wET
    dispatcher = MixedDispatcher(coarsened)
    best_obj: float | None = None
    best_sch: FFcSchedule | None = None
    for cand in dispatcher.iter_mixed_schedules_by_sequence(seq):
        cand.insert_idle_time(dw, ewt, twt)
        sum_e, sum_t = compute_weighted_earliness_tardiness(cand, coarsened)
        obj = sum_e + sum_t
        if best_obj is None or obj < best_obj:
            best_obj, best_sch = obj, cand
    if best_sch is None:
        raise RuntimeError(
            f"_build_dispatch_seed_schedule(mixed): no feasible candidate "
            f"for {coarsened.name}."
        )
    return best_sch


def _solve_coarsened_model(
    coarsened_instance: FFcDDWParameters,
    *,
    timelimit_sec: float | None,
    solver_thread_cnt: int,
    log_search_progress: bool,
    build_start: float,
    seed_dispatch: Literal["job_wise", "mixed", "v3", "v4"] = "mixed",
) -> tuple[
    str,
    dict[tuple[str, str], int] | None,
    dict[tuple[str, str], int] | None,
    float | None,
    float | None,
    float,
    tuple[ProgressLogEntry, ...],
    float | None,
]:
    """Build, solve, and extract raw op starts/ends for the coarsened instance.

    Returns a tuple:
        (status_name, j_i_2_start, j_i_2_end, cp_obj_value, cp_obj_bound,
         elapsed_sec, cp_progress_log, dispatch_seed_obj)

    ``j_i_2_start`` and ``j_i_2_end`` are ``None`` when no solution exists.
    ``cp_obj_value`` and ``cp_obj_bound`` are ``None`` when no solution exists.
    ``dispatch_seed_obj`` is the coarsened wET of the dispatch seed (None when
    no solution). ``elapsed_sec`` is measured from ``build_start`` to after
    ``solver.solve``. ``cp_progress_log`` is the merged UB/LB trajectory
    (coarsened scale).
    """
    params = BaseModelBuilder.make_params(coarsened_instance)
    horizon = sum(params.p.values())
    builder = BaseModelBuilder()
    mdl, params, op_vars, et_vars = builder.build(coarsened_instance, horizon=horizon)

    # Apply dispatch seed as warm-start hint before solving.
    seed_schedule = _build_dispatch_seed_schedule(coarsened_instance, seed_dispatch)
    BaseModelBuilder.apply_hints_from_schedule(
        mdl, params, op_vars, et_vars, seed_schedule
    )
    seed_sum_e, seed_sum_t = compute_weighted_earliness_tardiness(
        seed_schedule, coarsened_instance
    )
    dispatch_seed_obj: float | None = float(seed_sum_e + seed_sum_t)

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

    value_recorder = ObjectiveValueRecorder()
    bound_recorder = ObjectiveBoundRecorder()
    solver.best_bound_callback = bound_recorder

    status = solver.solve(mdl, solution_callback=value_recorder)
    elapsed = time.monotonic() - build_start

    cp_progress_log = build_progress_log(
        value_recorder=value_recorder,
        bound_recorder=bound_recorder,
    )

    status_name = solver.status_name(status)
    has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    if not has_solution:
        return (
            status_name,
            None,
            None,
            None,
            None,
            elapsed,
            cp_progress_log,
            dispatch_seed_obj,
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
    cp_obj_value = float(solver.objective_value)
    cp_obj_bound = float(solver.best_objective_bound)
    return (
        status_name,
        j_i_2_start,
        j_i_2_end,
        cp_obj_value,
        cp_obj_bound,
        elapsed,
        cp_progress_log,
        dispatch_seed_obj,
    )


def run_coarsen_solve_reconstruct(
    instance: FFcDDWParameters,
    option: CoarsenSolveReconstructOption,
    logger: logging.Logger,
) -> CoarsenSolveReconstructTrace:
    """Pure pipeline: coarsen → solve → reconstruct → postprocess.

    Returns a ``CoarsenSolveReconstructTrace`` with:

    - three distinct schedule snapshots (coarse, raw-reconstructed, final),
    - the coarsened-scale CP trajectory,
    - original-scale ``obj_value``, and
    - the same ``metrics`` dict that ``CoarsenSolveReconstructAdapter.run`` emits.

    Callers that only need the ``AlgRecord`` should use
    ``CoarsenSolveReconstructAdapter.run`` which delegates here.
    """
    build_start = time.monotonic()

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, option.factor)

    logger.info(
        "run_coarsen_solve_reconstruct: instance=%s, coarsened=%s, factor=%d, "
        "timelimit_sec=%s, num_workers=%d",
        instance.name,
        coarsened.name,
        option.factor,
        f"{option.timelimit_sec:.3f}s" if option.timelimit_sec is not None else "None",
        option.solver_thread_cnt,
    )

    (
        coarsened_status_name,
        coarse_j_i_2_start,
        coarse_j_i_2_end,
        coarsened_obj_value,
        coarsened_obj_bound,
        coarsened_elapsed,
        cp_progress_log,
        dispatch_seed_obj,
    ) = _solve_coarsened_model(
        coarsened,
        timelimit_sec=option.timelimit_sec,
        solver_thread_cnt=option.solver_thread_cnt,
        log_search_progress=option.log_search_progress,
        build_start=build_start,
        seed_dispatch=option.seed_dispatch,
    )

    has_solution = coarse_j_i_2_start is not None

    if not has_solution:
        coarsened_status_int = {
            "INFEASIBLE": cp_model.INFEASIBLE,
        }.get(coarsened_status_name, -1)
        is_infeasible = coarsened_status_int == cp_model.INFEASIBLE
        if is_infeasible and option.error_if_infeasible:
            raise RuntimeError(
                f"run_coarsen_solve_reconstruct: coarsened CP proved INFEASIBLE "
                f"for {coarsened.name}."
            )
        logger.warning(
            "run_coarsen_solve_reconstruct: no feasible solution on coarsened "
            "instance (status=%s)",
            coarsened_status_name,
        )
        metrics: dict = {
            "factor": option.factor,
            "coarsened_instance_name": coarsened.name,
            "coarsened_status": coarsened_status_name,
            "coarsened_obj_value": None,
            "coarsened_obj_bound": None,
            "coarsened_elapsed": coarsened_elapsed,
            "reconstructed_obj_value": None,
            "reconstructed_makespan": None,
            "seed_dispatch": option.seed_dispatch,
            "dispatch_seed_coarsened_obj": dispatch_seed_obj,
        }
        return CoarsenSolveReconstructTrace(
            work_status=WorkStatus.INFEASIBLE if is_infeasible else WorkStatus.ERROR,
            termination_reason=(
                TerminationReason.COMPLETED
                if is_infeasible
                else TerminationReason.ERROR
            ),
            error=(
                None if is_infeasible else f"coarsened_status={coarsened_status_name}"
            ),
            final_schedule=None,
            coarse_schedule=None,
            reconstructed_raw_schedule=None,
            cp_progress_log=cp_progress_log,
            obj_value=None,
            metrics=metrics,
        )

    # --- Build coarse-scale schedule snapshot ---
    coarse_schedule = build_schedule_from_op_starts(
        coarsened, coarse_j_i_2_start, coarse_j_i_2_end
    )

    # --- Reconstruct to original scale ---
    # Raw snapshot BEFORE any postprocess, and the ET-aligned final schedule.
    # Built by separate calls so the final's in-place postprocess cannot mutate
    # the raw snapshot. Both share the reconstruct logic in schedule_build.
    factor = option.factor
    reconstructed_raw_schedule = reconstruct_raw_coarse_schedule(
        coarse_schedule, instance, factor
    )
    final_schedule = reconstruct_coarse_schedule(coarse_schedule, instance, factor)

    sum_e, sum_t = compute_weighted_earliness_tardiness(final_schedule, instance)
    obj_value = float(sum_e + sum_t)

    coarsened_status_code_is_optimal = coarsened_status_name == "OPTIMAL"

    metrics = {
        "factor": option.factor,
        "coarsened_instance_name": coarsened.name,
        "coarsened_status": coarsened_status_name,
        "coarsened_obj_value": coarsened_obj_value,
        "coarsened_obj_bound": coarsened_obj_bound,
        "coarsened_elapsed": coarsened_elapsed,
        "reconstructed_obj_value": obj_value,
        "reconstructed_makespan": final_schedule.makespan,
        "seed_dispatch": option.seed_dispatch,
        "dispatch_seed_coarsened_obj": dispatch_seed_obj,
    }

    return CoarsenSolveReconstructTrace(
        work_status=(
            WorkStatus.OPTIMAL
            if coarsened_status_code_is_optimal
            else WorkStatus.FEASIBLE
        ),
        termination_reason=(
            TerminationReason.COMPLETED
            if coarsened_status_code_is_optimal
            else TerminationReason.TIME_LIMIT
        ),
        error=None,
        final_schedule=final_schedule,
        coarse_schedule=coarse_schedule,
        reconstructed_raw_schedule=reconstructed_raw_schedule,
        cp_progress_log=cp_progress_log,
        obj_value=obj_value,
        metrics=metrics,
    )


class CoarsenSolveReconstructAdapter:
    """Solve FFc-DDW via coarsen → CP-SAT → reconstruct to original scale."""

    algorithm_id = "coarsen_solve_reconstruct"

    def run(self, spec: AlgSpec) -> AlgRecord:  # noqa: PLR0911
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger if spec.logger is not None else logging.getLogger(__name__)

        trace = run_coarsen_solve_reconstruct(instance, option, logger)

        if trace.final_schedule is None:
            # No-solution path: check error_if_infeasible (already raised in pipeline
            # if applicable; we arrive here only for the non-raising case)
            return AlgRecord(
                work_status=trace.work_status,
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=None,
                    obj_value=None,
                    obj_bound=None,
                    metrics=trace.metrics,
                ),
                progress_log=trace.cp_progress_log if trace.cp_progress_log else None,
                termination_reason=trace.termination_reason,
                error=trace.error,
            )

        return AlgRecord(
            work_status=trace.work_status,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=trace.final_schedule,
                obj_value=trace.obj_value,
                obj_bound=None,
                metrics=trace.metrics,
            ),
            progress_log=trace.cp_progress_log,
            termination_reason=trace.termination_reason,
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
