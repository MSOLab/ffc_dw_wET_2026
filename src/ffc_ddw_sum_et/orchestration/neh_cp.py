"""NEH-CP constructor: incremental batched CP-SAT schedule construction.

Extracted from ``FFcDDWSubroutineController.neh_cp``. Mirrors the
two-stage lexicographic optimize structure in
``hybridflowshop/controller/neh_cp.py``: primary minimizes weighted E/T,
optional secondary minimizes makespan subject to the primary's E/T ceiling.
"""

from __future__ import annotations

import logging
import math
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
from .value_resolver import resolve_value_expr

__all__ = [
    "NehCpBatchTlMode",
    "NehCpConstructor",
    "NehCpContext",
    "NehCpJobPriority",
]

NehCpJobPriority = Literal[
    "weight-due-pos", "due-weight-pos", "due*-weight-pos", "due2-weight-pos"
]
NehCpBatchTlMode = Literal["constant", "linear"]


def _neh_cp_job_sequence(
    instance: FFcDDWParameters, job_priority: NehCpJobPriority = "weight-due-pos"
) -> list[str]:
    if job_priority == "weight-due-pos":
        return instance.get_weight_due_pos_job_sequence()
    if job_priority == "due-weight-pos":
        return instance.get_due_weight_pos_job_sequence()
    if job_priority == "due*-weight-pos":
        return instance.get_due_star_weight_pos_job_sequence()
    if job_priority == "due2-weight-pos":
        return instance.due2_weight_pos_job_sequence()
    raise ValueError(f"Unknown job_priority: {job_priority!r}")


def _resolve_per_step_tl(
    *,
    cp_tl_from_arg: float | None,
    total_seconds: float | None,
    num_batches: int | None,
    batch_count: int,
    batch_tl_mode: NehCpBatchTlMode,
    batch_tl_offset_seconds: float,
    logger: logging.Logger,
) -> list[float] | None:
    """Build the per-batch CP-SAT time-limit list (or None for no limit).

    When ``total_seconds`` is None, this is a flat ``[cp_tl_from_arg] * B``
    (or None if ``cp_tl_from_arg`` is also None).  When ``total_seconds`` is
    set, ``cp_tl_from_arg`` is ignored — the per-batch limit is derived from
    ``batch_tl_mode``: ``"constant"`` yields a flat ``total_seconds /
    divisor`` per batch (``divisor`` is ``num_batches`` when set, else
    ``batch_count``); ``"linear"`` yields ``offset + i * x`` per batch with
    ``x`` chosen so the limits sum to ``total_seconds``, falling back to
    constant ``total_seconds / batch_count`` when the offset would consume
    the whole budget.
    """
    if total_seconds is None:
        if cp_tl_from_arg is None:
            return None
        return [cp_tl_from_arg] * batch_count

    if cp_tl_from_arg is not None:
        logger.info(
            "neh_cp: cp_tl=%s ignored because total_timelimit is set; "
            "per-batch limit governed by batch_tl_mode=%r.",
            cp_tl_from_arg,
            batch_tl_mode,
        )

    if batch_tl_mode == "constant":
        divisor = num_batches if num_batches is not None else batch_count
        return [total_seconds / divisor] * batch_count

    if batch_tl_mode == "linear":
        offset = batch_tl_offset_seconds
        x = (
            2.0
            * (total_seconds - batch_count * offset)
            / (batch_count * (batch_count + 1))
        )
        if x <= 0:
            logger.warning(
                "neh_cp: batch_tl_offset_seconds=%.4f * B=%d exceeds "
                "total_timelimit=%.2f; falling back to constant schedule.",
                offset,
                batch_count,
                total_seconds,
            )
            return [total_seconds / batch_count] * batch_count
        return [offset + (i + 1) * x for i in range(batch_count)]

    raise ValueError(f"Unknown batch_tl_mode: {batch_tl_mode!r}")


class NehCpContext(Protocol):
    """Minimal interface `NehCpConstructor` requires from its calling context."""

    instance: FFcDDWParameters
    logger: logging.Logger
    solution_manager: FFcDDWSolutionManager

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
        job_priority: NehCpJobPriority = "weight-due-pos",
        solver_thread_cnt: int = 1,
        added_batch_size: int = 1,
        cp_tl: float | str | None = None,
        total_timelimit: float | str | None = None,
        num_batches: int | None = None,
        batch_tl_mode: NehCpBatchTlMode = "constant",
        batch_tl_offset_seconds: float = 0.01,
        apply_cumulative_tl: bool = False,
        pf_method: PFMethod = "PF1",
        skip_pf_below_obj: str | float | None = None,
        make_semi_active_after_cp: bool = False,
        minimize_makespan_lex: bool = False,
        cp_tl_2nd_obj: float | str | None = None,
        error_if_infeasible: bool = False,
    ) -> SubroutineReport:
        """Build a schedule by incrementally adding job batches and refining via CP-SAT.

        Jobs are ordered by ``_neh_cp_job_sequence`` and added in batches.
        The first batch is ``max(added_batch_size, max_machines_per_stage * 2)``;
        each subsequent batch adds ``added_batch_size`` jobs.  After each batch
        a CP-SAT model is solved; the best of the CP and dispatch solutions
        carries forward as the warm-start for the next step.

        Args:
            job_priority (NehCpJobPriority, optional): Rule used to order jobs for
                the incremental batches. ``"weight-due-pos"`` (default) sorts by
                ``(max(w⁻, w⁺) desc, w⁻+w⁺ desc, due-window width asc, position
                asc)``. ``"due-weight-pos"`` sorts by
                ``(max(0, d⁺-p_last) asc, d⁺ asc, d⁻ asc, w⁻+w⁺ asc, position
                asc)``. Defaults to ``"weight-due-pos"``.
            solver_thread_cnt (int, optional): CP-SAT solver threads per batch solve.
                Defaults to 1.
            added_batch_size (int, optional): Jobs added per incremental step after
                the first batch. Defaults to 1.
            cp_tl (float | str | None, optional): Time limit per batch solve.
                A float is interpreted as seconds; a string such as ``"0.006nc"``
                is evaluated as an expression where ``n`` = job count and ``c`` =
                stage count; ``None`` means no limit. ``cp_tl`` is **ignored**
                whenever ``total_timelimit`` is set — the per-batch limit is
                then governed entirely by ``total_timelimit`` /
                ``num_batches`` / ``batch_tl_mode`` /
                ``batch_tl_offset_seconds``. Defaults to None.
            total_timelimit (float | str | None, optional): Total CP-SAT budget
                across all batches, using the same grammar as ``cp_tl``
                (e.g. ``"0.024nc"``). When set, the per-batch limit is derived
                from ``batch_tl_mode``: ``"constant"`` (default) gives every
                batch the same limit (``total_timelimit / num_batches`` when
                ``num_batches`` is set, else ``total_timelimit /
                len(batches)``); ``"linear"`` gives batch *i*
                ``offset + i * x`` seconds (see ``batch_tl_mode`` /
                ``batch_tl_offset_seconds``). Setting ``total_timelimit``
                causes ``cp_tl`` to be ignored. Defaults to None.
            num_batches (int | None, optional): When set, overrides the
                incremental batching layout: ``added_batch_size`` is replaced
                with ``ceil(n / num_batches)`` so the run completes in
                roughly ``num_batches`` CP solves, and ``num_batches`` is
                used as the divisor for the per-batch time limit
                (``total_timelimit / num_batches`` under
                ``batch_tl_mode="constant"``). Must be castable to ``int``
                — a non-integer value raises ``TypeError``. Pass ``None``
                (default) to keep the explicit ``added_batch_size`` and let
                the per-batch divisor be the actual batch count
                ``len(batches)``. Defaults to None.
            batch_tl_mode (NehCpBatchTlMode, optional): How the resolved
                ``total_timelimit`` budget is distributed across batches.
                ``"constant"`` (default) gives every batch the same per-batch
                limit and reproduces prior behavior. ``"linear"`` gives batch
                *i* (1-indexed) ``batch_tl_offset_seconds + i * x`` seconds,
                with ``x = 2 * (total_timelimit - B * offset) / (B * (B + 1))``
                where ``B`` is the actual batch count (``num_batches`` if
                set, else ``len(batches)``). When ``B * offset`` exceeds
                ``total_timelimit`` the slope is non-positive; the schedule
                logs a warning and falls back to constant
                ``total_timelimit / B``. Has no effect when
                ``total_timelimit`` is None. Defaults to ``"constant"``.
            batch_tl_offset_seconds (float, optional): Per-batch CP build /
                CP-SAT initialization offset used by ``batch_tl_mode="linear"``
                (the constant in ``offset + i * x``). Has no effect under
                ``"constant"`` or when ``total_timelimit`` is None.
                Defaults to 0.01.
            apply_cumulative_tl (bool, optional): When True and a per-batch
                limit is in effect, each batch receives the remaining
                cumulative budget (``sum(per_step_limits[: step + 1]) -
                elapsed``) rather than a fixed per-batch limit. If that
                remainder is less than this batch's own limit, the batch's
                limit is used as a minimum floor. Under ``batch_tl_mode=
                "constant"`` this matches the prior ``cp_tl_seconds * (step
                + 1) - elapsed`` form; under ``"linear"`` the cumulative
                budget grows quadratically because per-batch limits grow.
                Defaults to False.
            pf_method (PFMethod, optional): Partial-fix strategy used to inject the
                previous step's solution as precedence constraints into the next model.
                Defaults to "PF1".
            skip_pf_below_obj (str | float | None, optional): When set, suppresses
                partial-fix for any batch where the previous step's weighted E+T is
                at or below this threshold.  ``"makespan"`` uses the previous
                solution's makespan as the threshold; a float is used directly.
                ``None`` always applies partial-fix. Defaults to None.
            make_semi_active_after_cp (bool, optional): When True, the stage-1
                CP solution is post-processed with ``make_semi_active`` and
                ``insert_idle_time`` before it is compared against the
                dispatched warm-start. The tidied schedule replaces the raw
                CP decode only when its weighted E/T is strictly lower.
                Has no effect on the dispatched fallback (already tidied) or
                on the stage-2 solve. Defaults to False.
            minimize_makespan_lex (bool, optional): When True, run a
                lexicographic secondary CP solve after each batch's primary
                (weighted E/T) solve. The secondary model constrains
                ``weighted_E_T <= primary_obj`` and minimizes makespan,
                refining the warm-start handed to the next batch. Defaults to
                False.
            cp_tl_2nd_obj (float | str | None, optional): Time limit for the
                secondary makespan solve, using the same grammar as ``cp_tl``.
                Only applies when ``minimize_makespan_lex=True``. If None,
                falls back to ``cp_tl``. Defaults to None.
            error_if_infeasible (bool, optional): Raise ``RuntimeError`` instead of
                returning a dispatched fallback when no feasible solution is found.
                Defaults to False.

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
        last_stage_mc_count = instance.last_stage_mc_count
        if n == 0:
            raise RuntimeError("neh_cp requires at least one job in the instance.")

        if num_batches is not None:
            try:
                num_batches = int(num_batches)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"num_batches must be an integer, got {num_batches!r}"
                ) from exc
            added_batch_size = math.ceil(n / num_batches)

        cp_tl_from_arg = resolve_value_expr(cp_tl, n, stage_count, last_stage_mc_count)
        total_seconds = (
            resolve_value_expr(total_timelimit, n, stage_count, last_stage_mc_count)
            if total_timelimit is not None
            else None
        )
        cp_tl_2nd_obj_seconds = (
            resolve_value_expr(
                cp_tl_2nd_obj if cp_tl_2nd_obj is not None else cp_tl,
                n,
                stage_count,
                last_stage_mc_count,
            )
            if minimize_makespan_lex
            else None
        )
        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

        job_sequence = _neh_cp_job_sequence(instance, job_priority=job_priority)
        max_m = max(instance.machine_count_per_stage)
        first_batch_size = max(added_batch_size, max_m * 2)

        batches: list[list[str]] = [job_sequence[:first_batch_size]]
        tail = job_sequence[first_batch_size:]
        for start in range(0, len(tail), added_batch_size):
            batches.append(tail[start : start + added_batch_size])

        cp_tl_seconds_per_step: list[float] | None = _resolve_per_step_tl(
            cp_tl_from_arg=cp_tl_from_arg,
            total_seconds=total_seconds,
            num_batches=num_batches,
            batch_count=len(batches),
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            logger=ctx.logger,
        )

        partial_sol: FFcSchedule | None = None
        current_jobs: list[str] = []
        sub_step_log: list[dict] = []

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
            if cp_tl_seconds_per_step is not None:
                step_tl = cp_tl_seconds_per_step[step]
                if apply_cumulative_tl:
                    elapsed_so_far = time.monotonic() - start_elapsed
                    cumulative_tl = sum(cp_tl_seconds_per_step[: step + 1])
                    applied_tl = cumulative_tl - elapsed_so_far
                    if applied_tl < step_tl:
                        ctx.logger.warning(
                            "neh_cp step %d: remaining cumulative budget %.2f seconds "
                            "is below per-batch floor %.2f seconds "
                            "(cumulative_tl=%.2f, elapsed=%.2f); using floor.",
                            step,
                            applied_tl,
                            step_tl,
                            cumulative_tl,
                            elapsed_so_far,
                        )
                        applied_tl = step_tl
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
                    solver.parameters.max_time_in_seconds = step_tl
            solver.parameters.num_workers = solver_thread_cnt
            status = solver.solve(mdl)

            raw_lb = (
                solver.best_objective_bound
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
                else None
            )
            sub_obj_lb = float(raw_lb) if raw_lb is not None else 0.0

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
                if make_semi_active_after_cp:
                    cp_sch.make_semi_active(stage_2_job_2_p)
                    cp_sch.insert_idle_time(due_window_map, ewt_map, twt_map)
                    se_tidy, st_tidy = compute_weighted_earliness_tardiness(
                        cp_sch, sub_instance
                    )
                    if (se_tidy + st_tidy) < (se_new + st_new):
                        ctx.logger.info(
                            "neh_cp step %d: making CP solution semi-active reduced E/T "
                            "from %d to %d; using semi-active solution.",
                            step,
                            se_new + st_new,
                            se_tidy + st_tidy,
                        )
                        se_new, st_new = se_tidy, st_tidy
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

            se_stage1, st_stage1 = compute_weighted_earliness_tardiness(
                partial_sol, sub_instance
            )
            stage1_obj = se_stage1 + st_stage1

            ran_2nd_obj = False
            if minimize_makespan_lex and partial_sol.makespan > 0:
                horizon_2 = int(partial_sol.makespan)
                mdl_2, params_2, op_vars_2, et_vars_2 = builder.build(
                    sub_instance,
                    horizon=horizon_2,
                    minimize_makespan_lex=True,
                    et_ub=stage1_obj,
                )
                by_machine, stride = decode_pf_method(pf_method)
                BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
                    mdl_2,
                    params_2,
                    op_vars_2,
                    partial_sol,
                    profile_fix_by_machine=by_machine,
                    machine_precedence_stride=stride,
                )
                BaseModelBuilder.apply_hints_from_schedule(
                    mdl_2, params_2, op_vars_2, et_vars_2, partial_sol
                )

                solver_2 = cp_model.CpSolver()
                if cp_tl_2nd_obj_seconds is not None:
                    solver_2.parameters.max_time_in_seconds = cp_tl_2nd_obj_seconds
                solver_2.parameters.num_workers = solver_thread_cnt
                status_2 = solver_2.solve(mdl_2)
                if status_2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    j_i_2_start_2 = {
                        (j, i): int(solver_2.Value(op_vars_2.op_start[j, i]))
                        for j in params_2.j_list
                        for i in params_2.i_list
                    }
                    j_i_2_end_2 = {
                        (j, i): int(solver_2.Value(op_vars_2.op_end[j, i]))
                        for j in params_2.j_list
                        for i in params_2.i_list
                    }
                    cp_sch_2 = build_schedule_from_op_starts(
                        instance, j_i_2_start_2, j_i_2_end_2, jobs=current_jobs
                    )
                    ctx.logger.info(
                        "neh_cp step %d: stage-2 makespan solve: %d -> %d (E/T <= %d).",
                        step,
                        horizon_2,
                        int(cp_sch_2.makespan),
                        int(stage1_obj),
                    )
                    partial_sol = cp_sch_2
                    ran_2nd_obj = True
                else:
                    ctx.logger.info(
                        "neh_cp step %d: stage-2 makespan solve infeasible "
                        "(status=%s); keeping stage-1 schedule.",
                        step,
                        solver_2.StatusName(status_2),
                    )

            se, st = compute_weighted_earliness_tardiness(partial_sol, sub_instance)
            ub = float(se + st)
            if sub_obj_lb == 0:
                gap: float | None = 0.0 if ub == 0 else None
            else:
                gap = ub / sub_obj_lb
            sub_step_log.append(
                {
                    "step": step,
                    "elapsed_time": float(time.monotonic() - start_elapsed),
                    "sub_obj": ub,
                    "sub_obj_lb": sub_obj_lb,
                    "gap": gap,
                    "job_count": len(current_jobs),
                    "makespan": int(partial_sol.makespan),
                    "ran_2nd_obj": ran_2nd_obj,
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
            log_path = ctx.get_file_path_for_subroutine("_step_log.yaml")
        except AttributeError:
            log_path = None
        if log_path is not None:
            dump_yaml(sub_step_log, log_path)

        return report
