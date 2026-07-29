"""Coarsen-Solve-Reconstruct adapter for the FFc-DDW problem.

Coarsens a ``FFcDDWParameters`` instance by ``factor``, applies a warm-start
hint from an EDD-based dispatch seed before solving the coarsened model with
the base CP-SAT (``BaseModelBuilder``), then reconstructs the coarse solution
back to the original scale by carrying its **machine assignment and per-machine
job order** and re-deriving times from the original processing times:

    start[j,i] = max(end[j,i-1], machine_end[k])   # k = coarse assignment
    end[j,i]   = start[j,i] + original_p[j,i]

See ``solution.schedule_build.reconstruct_raw_coarse_schedule`` for why the
coarse *times* are not carried.

The dispatch seed strategy is selected via ``seed_dispatch`` (``"job_wise"``,
``"mixed"``, ``"v3"``, or ``"v4"``). Post-processing (``insert_idle_time``)
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
from typing import Any, Literal, Sequence

from ortools.sat.python import cp_model

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from ..solution.objectives import compute_weighted_earliness_tardiness
from ..solution.schedule_build import (
    build_active_except_last_from_reference,
    build_active_from_reference,
    build_schedule_from_op_starts,
    reconstruct_active_coarse_schedule,
    reconstruct_active_except_last_coarse_schedule,
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
    "CsrCandidate",
    "dedup_candidates",
    "run_coarsen_solve_reconstruct",
    "schedule_sequence_signature",
]

DEFAULT_COARSEN_FACTOR: int = 50


@dataclass(frozen=True, slots=True, kw_only=True)
class CsrCandidate:
    """One coarse-scale candidate schedule harvested from a CSR ``solve_flow``.

    ``source`` is the child controller's per-registration step label / call
    context (e.g. ``"4-neh_cp"``). ``coarse_schedule`` lives on the coarsened
    time grid; ``coarse_obj`` / ``coarse_bound`` are the child's reported
    objective / bound in coarse-scale units (both may be ``None``).

    ``sec_elapsed_step`` is the child controller's wall-clock time (seconds)
    at registration — measured from the start of the CSR subroutine.
    """

    source: str
    coarse_schedule: FFcSchedule
    coarse_obj: float | None
    coarse_bound: float | None
    sec_elapsed_step: float | None = None


def schedule_sequence_signature(schedule: FFcSchedule) -> tuple[tuple[Any, ...], ...]:
    """Structural signature of a schedule for candidate de-duplication.

    Port of hybridflowshop ``hfs_cp_lns._schedule_sequence_signature`` adapted
    to :class:`FFcSchedule`. Two schedules with the same per-machine job
    sequences AND the same per-stage time-ordered job order share a signature.
    The signature ignores absolute timing (only ordering matters), so two
    reconstructions that place the same operations in the same relative order
    collapse to one candidate.

    ``FFcSchedule.get_job_sequence(stage, machine)`` returns
    ``(job_id, start, end)`` tuples (note the ordering differs from
    hybridflowshop's ``(start, end, job_id)``).
    """
    machine_parts: list[tuple[Any, ...]] = []
    stage_parts: list[tuple[Any, ...]] = []
    job_index = {job_id: idx for idx, job_id in enumerate(schedule.jobs)}

    for stage_id in schedule.stages:
        stage_ops: list[tuple[int, int, int, Any]] = []
        for machine_id in schedule.machines_per_stage[stage_id]:
            seq = schedule.get_job_sequence(stage_id, machine_id)
            machine_seq = tuple(job_id for job_id, _s, _e in seq)
            machine_parts.append(("m", stage_id, machine_id, machine_seq))
            for job_id, start_time, end_time in seq:
                stage_ops.append(
                    (
                        int(start_time),
                        int(end_time),
                        job_index[job_id],
                        job_id,
                    )
                )
        stage_parts.append(
            (
                "s",
                stage_id,
                tuple(job_id for *_unused, job_id in sorted(stage_ops)),
            )
        )

    return tuple(machine_parts + stage_parts)


def dedup_candidates(candidates: Sequence[CsrCandidate]) -> list[CsrCandidate]:
    """Return candidates with duplicate structural signatures collapsed.

    Iterates in order and keeps the FIRST candidate for each signature
    (earlier registrations win). This makes the downstream winner tie-break
    ("earlier candidate wins") deterministic and stable.
    """
    seen: set[tuple[tuple[Any, ...], ...]] = set()
    out: list[CsrCandidate] = []
    for cand in candidates:
        sig = schedule_sequence_signature(cand.coarse_schedule)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(cand)
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class CoarsenSolveReconstructOption(AlgOption):
    """Option payload for ``CoarsenSolveReconstructAdapter``.

    ``timelimit_sec`` is the single, fully pre-resolved time cap (same
    convention as ``CpsatOption``): the controller takes the strict-min of
    any per-call cap and the remaining global time before constructing this
    option. ``None`` means "no time cap from the caller".

    ``factor`` is the coarsening divisor applied to all processing times.
    ``coarsen_mode`` selects the rounding rule (see
    ``FFcDDWParameters.coarsen_processing_times``). Due-window bounds are
    **preserved at original scale** and must be read together with
    ``time_factor=factor``.
    """

    factor: int = DEFAULT_COARSEN_FACTOR
    coarsen_mode: Literal["ceil", "round", "floor", "cumulative"] = "ceil"
    reconstruct_mode: Literal["semi_active", "active", "active_but_last_semi"] = (
        "semi_active"
    )
    """How the coarse solution is reconstructed onto the original scale.

    ``"semi_active"`` (default, prior behavior): carry the coarse machine
    assignment and per-machine order verbatim, re-derive times
    (:func:`reconstruct_coarse_schedule`). ``"active"``: keep only the coarse
    per-stage operation start-order and re-assign machines by earliest start
    (:func:`reconstruct_active_coarse_schedule`). ``"active_but_last_semi"``:
    active rebuild for all stages except the last, which preserves the coarse
    machine assignment and per-machine order but reads previous-stage end times
    from the actively rebuilt stages
    (:func:`reconstruct_active_except_last_coarse_schedule`).
    """
    timelimit_sec: float | None = None
    solver_thread_cnt: int = 1
    log_search_progress: bool = False
    error_if_infeasible: bool = False
    seed_dispatch: Literal["job_wise", "mixed", "v3", "v4"] = "mixed"
    solve: bool = True
    """When ``False``, skip CP-SAT solve and use the dispatch seed directly.

    This produces a deterministic seed-only schedule (no CP noise), useful for
    A/B testing seed quality changes. The ``coarse_schedule`` becomes the seed
    itself; ``cp_progress_log`` is empty; ``coarsened_status`` is ``"SEED_ONLY"``.
    """

    def __post_init__(self) -> None:
        valid_dispatch = {"job_wise", "mixed", "v3", "v4"}
        if self.seed_dispatch not in valid_dispatch:
            raise ValueError(
                f"seed_dispatch must be one of {valid_dispatch}, "
                f"got {self.seed_dispatch!r}"
            )
        valid_modes = {"ceil", "round", "floor", "cumulative"}
        if self.coarsen_mode not in valid_modes:
            raise ValueError(
                f"coarsen_mode must be one of {valid_modes}, got {self.coarsen_mode!r}"
            )
        valid_reconstruct = {"semi_active", "active", "active_but_last_semi"}
        if self.reconstruct_mode not in valid_reconstruct:
            raise ValueError(
                f"reconstruct_mode must be one of {valid_reconstruct}, "
                f"got {self.reconstruct_mode!r}"
            )


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
    """Post-processed schedule on original scale (raw reconstruction + insert_idle_time)."""
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


def _build_dispatch_seed_schedule(
    coarsened: FFcDDWParameters,
    factor: int,
    strategy: Literal["job_wise", "mixed", "v3", "v4"],
) -> FFcSchedule:
    """Build a seed schedule via dispatch + idle insertion on coarsened scale.

    * ``job_wise``: single job-wise dispatch using the EDD-derived sequence.
    * ``mixed``: enumerate all np-list candidates, insert idle, pick the one
      with minimum coarsened wET.
    * ``v3``: v3 paired-dispatch pool on coarsened instance → min-wET.

    The wET evaluation uses the new objective: ``factor * C^c`` compared
    against the **original** due window (preserved on the coarsened instance),
    so the seed is consistent with the CP model's objective function.

    Idle insertion on the coarse grid uses the CSR path
    (``tau > 1`` → :func:`_iit_csr_shift`).
    """
    if strategy not in {"job_wise", "mixed", "v3", "v4"}:
        raise ValueError(f"Unknown seed_dispatch strategy: {strategy!r}")
    if strategy == "v3":
        seed, _obj, _label = build_v3_paired_dispatch_schedule(coarsened, factor=factor)
        return seed
    if strategy == "v4":
        seed, _obj, _label = build_v4_paired_dispatch_schedule(coarsened, factor=factor)
        return seed

    seq = coarsened.get_eddub_twt_job_sequence()
    dw = coarsened.job_2_due_window_map
    ewt = coarsened.job_2_ewt_map
    twt = coarsened.job_2_twt_map

    if strategy == "job_wise":
        schedule = BaseDispatcher(coarsened)._create_empty_schedule(coarsened)
        dispatch_job_sequence_by_stages(schedule, seq, coarsened.job_2_stage_2_p_map)
        schedule.insert_idle_time(dw, ewt, twt, time_factor=factor)
        return schedule

    # strategy == "mixed": pick the candidate with minimum wET under the CSR
    # objective (factor-scaled completion vs the original due window), so the
    # seed ranking matches the CP model. Idle insertion still positions ops on
    # the coarse grid against the effective coarse window.
    dispatcher = MixedDispatcher(coarsened)
    best_obj: float | None = None
    best_sch: FFcSchedule | None = None
    for cand in dispatcher.iter_mixed_schedules_by_sequence(seq):
        cand.insert_idle_time(dw, ewt, twt, time_factor=factor)
        sum_e, sum_t = compute_weighted_earliness_tardiness(
            cand, coarsened, time_factor=factor
        )
        obj = sum_e + sum_t
        if best_obj is None or obj < best_obj:
            best_obj, best_sch = obj, cand
    if best_sch is None:
        raise RuntimeError(
            f"_build_dispatch_seed_schedule(mixed): no feasible candidate "
            f"for {coarsened.name}."
        )
    return best_sch


def _seed_and_obj(
    coarsened: FFcDDWParameters,
    factor: int,
    strategy: Literal["job_wise", "mixed", "v3", "v4"],
) -> tuple[FFcSchedule, float]:
    """Build a dispatch seed schedule and evaluate its coarsened wET.

    Returns ``(seed_schedule, dispatch_seed_obj)`` where ``dispatch_seed_obj``
    is the weighted earliness+tardiness computed under the CSR objective
    (``factor * C^c`` vs the original due window).
    """
    seed_schedule = _build_dispatch_seed_schedule(coarsened, factor, strategy)
    seed_sum_e, seed_sum_t = compute_weighted_earliness_tardiness(
        seed_schedule, coarsened, time_factor=factor
    )
    dispatch_seed_obj: float = float(seed_sum_e + seed_sum_t)
    return seed_schedule, dispatch_seed_obj


def _solve_coarsened_model(
    coarsened_instance: FFcDDWParameters,
    factor: int,
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
    builder = BaseModelBuilder()
    mdl, params, op_vars, et_vars = builder.build(
        coarsened_instance,
        horizon=sum(BaseModelBuilder.make_params(coarsened_instance).p.values()),
        time_factor=factor,
    )

    # Build dispatch seed and its CSR-objective wET via the shared helper
    # (single source of truth with the seed-only path), then apply it as a
    # warm-start hint before solving.
    seed_schedule, dispatch_seed_obj = _seed_and_obj(
        coarsened_instance, factor, seed_dispatch
    )
    BaseModelBuilder.apply_hints_from_schedule(
        mdl, params, op_vars, et_vars, seed_schedule
    )

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

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, option.factor, mode=option.coarsen_mode
    )

    logger.info(
        "run_coarsen_solve_reconstruct: instance=%s, coarsened=%s, factor=%d, "
        "timelimit_sec=%s, num_workers=%d",
        instance.name,
        coarsened.name,
        option.factor,
        f"{option.timelimit_sec:.3f}s" if option.timelimit_sec is not None else "None",
        option.solver_thread_cnt,
    )

    if option.solve:
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
            option.factor,
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
                work_status=WorkStatus.INFEASIBLE
                if is_infeasible
                else WorkStatus.ERROR,
                termination_reason=(
                    TerminationReason.COMPLETED
                    if is_infeasible
                    else TerminationReason.ERROR
                ),
                error=(
                    None
                    if is_infeasible
                    else f"coarsened_status={coarsened_status_name}"
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
    else:
        # Seed-only deterministic mode: skip CP-SAT, use dispatch seed directly.
        seed_schedule, dispatch_seed_obj = _seed_and_obj(
            coarsened, option.factor, option.seed_dispatch
        )
        coarse_schedule = seed_schedule
        coarsened_status_name = "SEED_ONLY"
        coarsened_obj_value: float | None = dispatch_seed_obj
        coarsened_obj_bound: float | None = None
        coarsened_elapsed = time.monotonic() - build_start
        cp_progress_log: tuple[ProgressLogEntry, ...] = ()

    # --- Reconstruct to original scale ---
    # Raw snapshot BEFORE any postprocess, and the ET-aligned final schedule.
    # Built by separate calls so the final's in-place postprocess cannot mutate
    # the raw snapshot. Both share the reconstruct logic in schedule_build.
    # reconstruct_mode selects semi-active (carry coarse machine assignment),
    # active (keep only the coarse start-order, re-assign machines), or
    # active_but_last_semi (active for all but the last stage).
    factor = option.factor
    if option.reconstruct_mode == "active":
        reconstructed_raw_schedule = build_active_from_reference(
            coarse_schedule, instance, instance.stage_2_job_2_p_map
        )
        final_schedule = reconstruct_active_coarse_schedule(coarse_schedule, instance)
    elif option.reconstruct_mode == "active_but_last_semi":
        reconstructed_raw_schedule = build_active_except_last_from_reference(
            coarse_schedule, instance, instance.stage_2_job_2_p_map
        )
        final_schedule = reconstruct_active_except_last_coarse_schedule(
            coarse_schedule, instance
        )
    else:
        reconstructed_raw_schedule = reconstruct_raw_coarse_schedule(
            coarse_schedule, instance, factor
        )
        final_schedule = reconstruct_coarse_schedule(coarse_schedule, instance, factor)

    sum_e, sum_t = compute_weighted_earliness_tardiness(final_schedule, instance)
    obj_value = float(sum_e + sum_t)

    coarsened_status_code_is_optimal = coarsened_status_name == "OPTIMAL"
    # Seed-only mode finishes deterministically, not against a time cap.
    is_seed_only = coarsened_status_name == "SEED_ONLY"

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
            if coarsened_status_code_is_optimal or is_seed_only
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
