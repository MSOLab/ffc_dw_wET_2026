"""NEH-CP constructor: incremental batched CP-SAT schedule construction.

Extracted from ``FFcDDWSubroutineController.neh_cp``. The two-stage
optimize port (modeled on
``hybridflowshop/controller/neh_cp.py``) is a follow-up task.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, Protocol

from ortools.sat.python import cp_model
from routix.io import dump_yaml
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.cumulative import (
    BaseModelBuilder,
    PFMethod,
    decode_pf_method,
)
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .solution_manager import FFcDDWSolution, FFcDDWSolutionManager
from .tl_resolver import resolve_cp_tl

__all__ = ["NehCpConstructor", "NehCpContext", "NehCpJobPriority"]

NehCpJobPriority = Literal["weight-due-pos", "due-weight-pos"]


class NehCpContext(Protocol):
    """Minimal interface `NehCpConstructor` requires from its calling context."""

    instance: FFcDDWParameters
    logger: logging.Logger
    solution_manager: FFcDDWSolutionManager

    def _neh_cp_job_sequence(
        self, job_priority: NehCpJobPriority = "weight-due-pos"
    ) -> list[str]: ...

    def get_file_path_for_subroutine(self, suffix: str) -> Path: ...


class NehCpConstructor:
    """Builds a schedule incrementally by batching jobs and refining via CP-SAT.

    The public entry point is :meth:`run`.  The constructor takes a context that
    satisfies :class:`NehCpContext` — typically a
    :class:`~ffc_ddw_sum_et.orchestration.controller.FFcDDWSubroutineController`.
    """

    def __init__(self, ctx: NehCpContext) -> None:
        self._ctx = ctx

    def run(
        self,
        solver_thread_cnt: int = 1,
        added_batch_size: int = 1,
        cp_tl: float | str | None = None,
        apply_cumulative_tl: bool = False,
        pf_method: PFMethod = "PF1",
        skip_pf_below_obj: str | float | None = None,
        error_if_infeasible: bool = False,
        job_priority: NehCpJobPriority = "weight-due-pos",
    ) -> SubroutineReport:
        """Build a schedule by incrementally adding job batches and refining via CP-SAT.

        Jobs are ordered by ``ctx._neh_cp_job_sequence`` and added in batches.
        The first batch is ``max(added_batch_size, max_machines_per_stage * 2)``;
        each subsequent batch adds ``added_batch_size`` jobs.  After each batch
        a CP-SAT model is solved; the best of the CP and dispatch solutions
        carries forward as the warm-start for the next step.

        Args:
            solver_thread_cnt (int, optional): CP-SAT solver threads per batch solve.
                Defaults to 1.
            added_batch_size (int, optional): Jobs added per incremental step after
                the first batch. Defaults to 1.
            cp_tl (float | str | None, optional): Time limit per batch solve.
                A float is interpreted as seconds; a string such as ``"0.006nc"``
                is evaluated as an expression where ``n`` = job count and ``c`` =
                stage count; ``None`` means no limit. Defaults to None.
            apply_cumulative_tl (bool, optional): When True and ``cp_tl`` is set,
                each batch receives the remaining cumulative budget
                (``cp_tl_seconds * (step + 1) - elapsed``) rather than a fixed
                per-batch limit.  If the remaining budget is less than
                ``cp_tl_seconds``, the per-batch value is used as a minimum floor.
                Defaults to False.
            pf_method (PFMethod, optional): Partial-fix strategy used to inject the
                previous step's solution as precedence constraints into the next model.
                Defaults to "PF1".
            skip_pf_below_obj (str | float | None, optional): When set, suppresses
                partial-fix for any batch where the previous step's weighted E+T is
                at or below this threshold.  ``"makespan"`` uses the previous
                solution's makespan as the threshold; a float is used directly.
                ``None`` always applies partial-fix. Defaults to None.
            error_if_infeasible (bool, optional): Raise ``RuntimeError`` instead of
                returning a dispatched fallback when no feasible solution is found.
                Defaults to False.
            job_priority (NehCpJobPriority, optional): Rule used to order jobs for
                the incremental batches. ``"weight-due-pos"`` (default) sorts by
                ``(max(w⁻, w⁺) desc, w⁻+w⁺ desc, due-window width asc, position
                asc)``. ``"due-weight-pos"`` sorts by
                ``(max(0, d⁺−p_last) asc, d⁺ asc, d⁻ asc, w⁻+w⁺ asc, position
                asc)``. Defaults to ``"weight-due-pos"``.

        Raises:
            ValueError: If ``skip_pf_below_obj`` is a string other than ``"makespan"``
                that cannot be parsed as a float.
            RuntimeError: If the instance contains no jobs.
            RuntimeError: If ``error_if_infeasible=True`` and no feasible schedule is
                produced.

        Returns:
            SubroutineReport: Final schedule and per-batch objective log.
        """
        ctx = self._ctx

        if skip_pf_below_obj is not None and skip_pf_below_obj != "makespan":
            try:
                skip_pf_below_obj = float(skip_pf_below_obj)
            except ValueError:
                raise ValueError(
                    f"Invalid skip_pf_below_obj value: {skip_pf_below_obj!r}; "
                    "expected 'makespan', a float, or None."
                )

        start_elapsed = time.monotonic()
        instance = ctx.instance
        n = instance.job_count
        stage_count = instance.stage_count
        if n == 0:
            raise RuntimeError("neh_cp requires at least one job in the instance.")

        cp_tl_seconds = resolve_cp_tl(cp_tl, n, stage_count)
        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

        job_sequence = ctx._neh_cp_job_sequence(job_priority=job_priority)
        max_m = max(instance.machine_count_per_stage)
        first_batch_size = max(added_batch_size, max_m * 2)

        batches: list[list[str]] = [job_sequence[:first_batch_size]]
        tail = job_sequence[first_batch_size:]
        for start in range(0, len(tail), added_batch_size):
            batches.append(tail[start : start + added_batch_size])

        partial_sol: FFcSchedule | None = None
        current_jobs: list[str] = []
        sub_obj_log: list[dict] = []

        # For warm-start
        mixed = MixedDispatcher(instance, logger=ctx.logger)
        first_stage_id = instance.stage_id_list[0]
        job_2_stage_2_p = instance.job_2_stage_2_p_map
        stage_2_job_2_p = instance.stage_2_job_2_p_map
        due_window_map = instance.job_2_due_window_map
        ewt_map = instance.job_2_ewt_map
        twt_map = instance.job_2_twt_map

        # State
        last_obj_value = 0

        for step, batch in enumerate(batches):
            current_jobs.extend(batch)
            sub_instance = FFcDDWParameters.create_instance_of_job_subset(
                instance, set(current_jobs)
            )

            # Build a CP-SAT model for the current job subset
            builder = BaseModelBuilder()
            mdl, params, op_vars, et_vars = builder.build(sub_instance, horizon=horizon)
            skip_pf = False
            if skip_pf_below_obj is not None:
                if skip_pf_below_obj == "makespan":
                    criteria_value = (
                        partial_sol.makespan if partial_sol is not None else 0
                    )
                else:
                    criteria_value = float(skip_pf_below_obj)

                if last_obj_value <= criteria_value:
                    skip_pf = True
            if partial_sol is not None and not skip_pf:
                by_machine, stride = decode_pf_method(pf_method)
                BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
                    mdl,
                    params,
                    op_vars,
                    partial_sol,
                    profile_fix_by_machine=by_machine,
                    machine_precedence_stride=stride,
                )

            # Warm-start from a mixed dispatch of the current batch,
            # using the previous step's solution as a base if available
            base = (
                partial_sol.deepcopy()
                if partial_sol is not None
                else FFcSchedule(
                    jobs=instance.job_id_list,
                    stages=instance.stage_id_list,
                    machines_per_stage=instance.stage_2_machines_map,
                )
            )
            dispatched = mixed.get_best_mixed_schedule_by_sequence(
                batch,
                schedule=base,
                from_stage=first_stage_id,
                head_for_all_stages=True,
                criteria="makespan",
            )
            if dispatched is None:
                ctx.logger.warning(
                    "neh_cp step %d: MixedDispatcher returned None; falling back "
                    "to dispatch_job_by_stages.",
                    step,
                )
                dispatched = base
                for j in batch:
                    dispatched.dispatch_job_by_stages(j, job_2_stage_2_p[j])
            dispatched.make_semi_active(stage_2_job_2_p)
            dispatched.insert_idle_time(due_window_map, ewt_map, twt_map)
            BaseModelBuilder.apply_hints_from_schedule(
                mdl, params, op_vars, et_vars, dispatched
            )

            solver = cp_model.CpSolver()
            if cp_tl_seconds is not None:
                if apply_cumulative_tl:
                    elapsed_so_far = time.monotonic() - start_elapsed
                    cumulative_tl = cp_tl_seconds * (step + 1)
                    applied_tl = cumulative_tl - elapsed_so_far
                    if applied_tl < cp_tl_seconds:
                        ctx.logger.warning(
                            "neh_cp step %d: remaining cumulative budget %.2f seconds "
                            "is below per-batch floor %.2f seconds "
                            "(cumulative_tl=%.2f, elapsed=%.2f); using floor.",
                            step,
                            applied_tl,
                            cp_tl_seconds,
                            cumulative_tl,
                            elapsed_so_far,
                        )
                        applied_tl = cp_tl_seconds
                    else:
                        ctx.logger.info(
                            "neh_cp step %d: applying %.2f seconds "
                            "(cumulative_tl=%.2f, elapsed=%.2f).",
                            step,
                            applied_tl,
                            cumulative_tl,
                            elapsed_so_far,
                        )
                    solver.parameters.max_time_in_seconds = applied_tl
                else:
                    solver.parameters.max_time_in_seconds = cp_tl_seconds
            solver.parameters.num_workers = solver_thread_cnt
            status = solver.solve(mdl)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                j_i_2_start = {
                    (j, i): int(solver.Value(op_vars.op_start[j, i]))
                    for j in params.j_list
                    for i in params.i_list
                }
                j_i_2_end = {
                    (j, i): int(solver.Value(op_vars.op_end[j, i]))
                    for j in params.j_list
                    for i in params.i_list
                }
                cp_sch = build_schedule_from_op_starts(
                    instance, j_i_2_start, j_i_2_end, jobs=current_jobs
                )
                se_new, st_new = compute_weighted_earliness_tardiness(
                    cp_sch, sub_instance
                )
                se_dis, st_dis = compute_weighted_earliness_tardiness(
                    dispatched, sub_instance
                )
                partial_sol = (
                    cp_sch if (se_new + st_new) <= (se_dis + st_dis) else dispatched
                )
            else:
                ctx.logger.info(
                    "neh_cp step %d: CP-SAT infeasible (status=%s); keeping dispatched schedule.",
                    step,
                    solver.StatusName(status),
                )
                partial_sol = dispatched

            se, st = compute_weighted_earliness_tardiness(partial_sol, sub_instance)
            sub_obj_log.append(
                {
                    "step": step,
                    "elapsed_time": float(time.monotonic() - start_elapsed),
                    "sub_obj": float(se + st),
                    "job_count": len(current_jobs),
                }
            )
            last_obj_value = se + st

        if partial_sol is None:
            if error_if_infeasible:
                raise RuntimeError(
                    f"neh_cp produced no schedule for instance {instance.name}."
                )
            elapsed = time.monotonic() - start_elapsed
            ctx.logger.warning("neh_cp produced no schedule; returning empty report.")
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=None
            )

        final = partial_sol
        se, st = compute_weighted_earliness_tardiness(final, instance)
        obj_value = float(se + st)
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        ctx.solution_manager.register(
            report,
            FFcDDWSolution(schedule=final, obj_value=obj_value),
        )

        try:
            log_path = ctx.get_file_path_for_subroutine("_obj_log.yaml")
        except AttributeError:
            log_path = None
        if log_path is not None:
            dump_yaml(sub_obj_log, log_path)

        return report
