"""NehCpDispatcher: incremental batched CP-SAT schedule construction.

Mirrors the two-stage lexicographic optimize structure in
``hybridflowshop/controller/neh_cp.py``: primary minimizes weighted E/T,
optional secondary minimizes makespan subject to the primary's E/T ceiling.
"""

from __future__ import annotations

import logging
import math
import time

from ortools.sat.python import cp_model

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ...solution.schedule_build import build_schedule_from_op_starts
from ..base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    TimingInfo,
    WorkStatus,
)
from ..base.alg_spec import AlgSpec
from ..cumulative import BaseModelBuilder, decode_pf_method
from ..dispatcher import MixedDispatcher
from .option import NehCpOption
from .sequence import neh_cp_job_sequence
from .step_log import NehCpStepEntry, trunc4
from .tl_schedule import resolve_per_step_tl

__all__ = ["NehCpDispatcher"]


class NehCpDispatcher:
    """Builds a schedule incrementally by batching jobs and refining via CP-SAT."""

    algorithm_id = "neh_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        if spec.ref_solution is not None:
            raise NotImplementedError(
                "NehCpDispatcher does not support ref_solution yet."
            )

        logger = spec.logger or logging.getLogger(__name__)

        n = instance.job_count
        if n == 0:
            raise RuntimeError("neh_cp requires at least one job in the instance.")

        added_batch_size = option.added_batch_size
        if option.num_batches is not None:
            num_batches = int(option.num_batches)
            added_batch_size = math.ceil(n / num_batches)
        else:
            num_batches = None
            added_batch_size = added_batch_size + option.extra_batch_size_extra

        cp_tl_2nd_obj_seconds = (
            (
                option.cp_tl_2nd_obj_seconds
                if option.cp_tl_2nd_obj_seconds is not None
                else option.cp_tl_seconds
            )
            if option.minimize_makespan_lex
            else None
        )

        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

        job_sequence = neh_cp_job_sequence(instance, job_priority=option.job_priority)
        max_m = max(instance.machine_count_per_stage)
        first_batch_size = max(added_batch_size, max_m * 2)

        batches: list[list[str]] = [job_sequence[:first_batch_size]]
        tail = job_sequence[first_batch_size:]
        for start in range(0, len(tail), added_batch_size):
            batches.append(tail[start : start + added_batch_size])

        cp_tl_seconds_per_step = resolve_per_step_tl(
            cp_tl_from_arg=option.cp_tl_seconds,
            total_seconds=option.total_timelimit_seconds,
            num_batches=num_batches,
            batch_count=len(batches),
            batch_tl_mode=option.batch_tl_mode,
            batch_tl_offset_seconds=option.batch_tl_offset_seconds,
            logger=logger,
        )

        partial_sol: FFcSchedule | None = None
        current_jobs: list[str] = []
        step_entries: list[NehCpStepEntry] = []
        progress_entries: list[ProgressLogEntry] = []

        mixed = MixedDispatcher(instance, logger=logger)
        first_stage_id = instance.stage_id_list[0]
        job_2_stage_2_p = instance.job_2_stage_2_p_map
        stage_2_job_2_p = instance.stage_2_job_2_p_map
        due_window_map = instance.job_2_due_window_map
        ewt_map = instance.job_2_ewt_map
        twt_map = instance.job_2_twt_map

        last_obj_value: float | int = 0
        prev_elapsed_seconds = 0.0

        start_elapsed = time.monotonic()

        for step, batch in enumerate(batches):
            current_jobs.extend(batch)
            sub_instance = FFcDDWParameters.create_instance_of_job_subset(
                instance, set(current_jobs)
            )

            builder = BaseModelBuilder()
            mdl, params, op_vars, et_vars = builder.build(sub_instance, horizon=horizon)
            skip_pf = False
            if option.skip_pf_below_obj is not None:
                if option.skip_pf_below_obj == "makespan":
                    criteria_value = (
                        partial_sol.makespan if partial_sol is not None else 0
                    )
                else:
                    criteria_value = float(option.skip_pf_below_obj)

                if last_obj_value <= criteria_value:
                    skip_pf = True
            if partial_sol is not None and not skip_pf:
                by_machine, stride = decode_pf_method(option.pf_method)
                BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
                    mdl,
                    params,
                    op_vars,
                    partial_sol,
                    profile_fix_by_machine=by_machine,
                    machine_precedence_stride=stride,
                )

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
                logger.warning(
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
            applied_tl_seconds: float | None = None
            if cp_tl_seconds_per_step is not None:
                step_tl = cp_tl_seconds_per_step[step]
                if option.apply_cumulative_tl:
                    elapsed_so_far = time.monotonic() - start_elapsed
                    cumulative_tl = sum(cp_tl_seconds_per_step[: step + 1])
                    applied_tl = cumulative_tl - elapsed_so_far
                    if applied_tl < step_tl:
                        logger.warning(
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
                        logger.info(
                            "neh_cp step %d: applying %.2f seconds "
                            "(cumulative_tl=%.2f, elapsed=%.2f).",
                            step,
                            applied_tl,
                            cumulative_tl,
                            elapsed_so_far,
                        )
                    solver.parameters.max_time_in_seconds = applied_tl
                    applied_tl_seconds = applied_tl
                else:
                    solver.parameters.max_time_in_seconds = step_tl
                    applied_tl_seconds = step_tl
            solver.parameters.num_workers = option.solver_thread_cnt
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
                if option.make_semi_active_after_cp:
                    cp_sch.make_semi_active(stage_2_job_2_p)
                    cp_sch.insert_idle_time(due_window_map, ewt_map, twt_map)
                    se_tidy, st_tidy = compute_weighted_earliness_tardiness(
                        cp_sch, sub_instance
                    )
                    if (se_tidy + st_tidy) < (se_new + st_new):
                        logger.info(
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
                logger.info(
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
            if option.minimize_makespan_lex and partial_sol.makespan > 0:
                horizon_2 = int(partial_sol.makespan)
                mdl_2, params_2, op_vars_2, et_vars_2 = builder.build(
                    sub_instance,
                    horizon=horizon_2,
                    minimize_makespan_lex=True,
                    et_ub=stage1_obj,
                )
                by_machine, stride = decode_pf_method(option.pf_method)
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
                solver_2.parameters.num_workers = option.solver_thread_cnt
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
                    logger.info(
                        "neh_cp step %d: stage-2 makespan solve: %d -> %d (E/T <= %d).",
                        step,
                        horizon_2,
                        int(cp_sch_2.makespan),
                        int(stage1_obj),
                    )
                    partial_sol = cp_sch_2
                    ran_2nd_obj = True
                else:
                    logger.info(
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
            step_elapsed_seconds = float(time.monotonic() - start_elapsed)
            step_delta_seconds = step_elapsed_seconds - prev_elapsed_seconds
            elapsed_portion = (
                step_delta_seconds / applied_tl_seconds
                if applied_tl_seconds is not None and applied_tl_seconds > 0
                else None
            )
            step_entries.append(
                NehCpStepEntry(
                    step=step,
                    elapsed_time=trunc4(step_elapsed_seconds),
                    TL=trunc4(applied_tl_seconds),
                    elapsed_portion=trunc4(elapsed_portion),
                    sub_obj=ub,
                    sub_obj_lb=sub_obj_lb,
                    gap=trunc4(gap),
                    job_count=len(current_jobs),
                    makespan=int(partial_sol.makespan),
                    ran_2nd_obj=ran_2nd_obj,
                )
            )
            progress_entries.append(
                ProgressLogEntry(
                    elapsed_ms=step_elapsed_seconds * 1000.0,
                    obj_value=ub,
                    obj_bound=sub_obj_lb,
                )
            )
            prev_elapsed_seconds = step_elapsed_seconds
            last_obj_value = se + st

        elapsed_seconds = time.monotonic() - start_elapsed
        timing = TimingInfo(wall_ms=elapsed_seconds * 1000.0)

        if partial_sol is None:
            if option.error_if_infeasible:
                raise RuntimeError(
                    f"neh_cp produced no schedule for instance {instance.name}."
                )
            logger.warning("neh_cp produced no schedule; returning empty record.")
            return AlgRecord(
                work_status=WorkStatus.INFEASIBLE,
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=None,
                    obj_value=None,
                    obj_bound=None,
                    metrics={"step_log": tuple(step_entries)},
                ),
                progress_log=tuple(progress_entries),
                timing=timing,
                termination_reason=TerminationReason.COMPLETED,
            )

        final = partial_sol
        sum_e, sum_t = compute_weighted_earliness_tardiness(final, instance)
        obj_value = float(sum_e + sum_t)
        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=final,
                obj_value=obj_value,
                obj_bound=None,
                metrics={
                    "sum_earliness": sum_e,
                    "sum_tardiness": sum_t,
                    "makespan": final.makespan,
                    "step_log": tuple(step_entries),
                },
            ),
            progress_log=tuple(progress_entries),
            timing=timing,
            termination_reason=TerminationReason.COMPLETED,
        )

    # ---- spec validation ----

    def _validate_instance(self, spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "NehCpDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    def _resolve_option(self, spec: AlgSpec) -> NehCpOption:
        if spec.option is None:
            return NehCpOption()
        if not isinstance(spec.option, NehCpOption):
            raise TypeError("NehCpDispatcher requires NehCpOption as spec.option.")
        return spec.option
