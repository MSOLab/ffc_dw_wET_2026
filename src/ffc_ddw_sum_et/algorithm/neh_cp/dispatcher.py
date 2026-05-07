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
    WorkStatus,
)
from ..base.alg_spec import AlgSpec
from ..cpsat_callbacks.obj_value_recorder import ObjectiveValueRecorder
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

        if option.custom_job_sequence is not None:
            self._validate_custom_sequence(option.custom_job_sequence, instance)
            job_sequence = list(option.custom_job_sequence)
        else:
            job_sequence = neh_cp_job_sequence(
                instance, job_priority=option.job_priority
            )
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

        logger.info(
            "neh_cp: %d jobs split into %d batches (sizes=%s); "
            "objective_lower_bound=%s, wall_clock_deadline_sec=%s",
            n,
            len(batches),
            [len(b) for b in batches],
            f"{option.objective_lower_bound:.2f}"
            if option.objective_lower_bound is not None
            else "None",
            f"{option.wall_clock_deadline_sec:.3f}"
            if option.wall_clock_deadline_sec is not None
            else "None",
        )

        partial_sol: FFcSchedule | None = None
        current_jobs: list[str] = []
        step_entries: list[NehCpStepEntry] = []
        progress_entries: list[ProgressLogEntry] = []
        step_schedules: list[
            tuple[int, FFcSchedule, FFcSchedule | None, FFcSchedule | None]
        ] = []

        mixed = MixedDispatcher(instance, logger=logger)
        first_stage_id = instance.stage_id_list[0]
        job_2_stage_2_p = instance.job_2_stage_2_p_map
        stage_2_job_2_p = instance.stage_2_job_2_p_map
        due_window_map = instance.job_2_due_window_map
        ewt_map = instance.job_2_ewt_map
        twt_map = instance.job_2_twt_map

        last_obj_value: float | int = 0
        prev_elapsed_seconds = 0.0
        stopped_early = False
        scheduled_job_set: set[str] = set()

        start_elapsed = time.monotonic()

        for step, batch in enumerate(batches):
            current_jobs.extend(batch)
            sub_instance = FFcDDWParameters.create_instance_of_job_subset(
                instance, set(current_jobs)
            )

            builder = BaseModelBuilder()
            is_last_batch = step == len(batches) - 1
            obj_lb_for_build = (
                option.objective_lower_bound
                if is_last_batch and option.objective_lower_bound is not None
                else None
            )
            if obj_lb_for_build is not None:
                logger.info(
                    "neh_cp step %d: last batch — passing obj_lb=%.2f to CP-SAT",
                    step,
                    obj_lb_for_build,
                )
            mdl, params, op_vars, et_vars = builder.build(
                sub_instance, horizon=horizon, obj_lb=obj_lb_for_build
            )
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
                by_machine, stride_set = decode_pf_method(option.pf_method)
                BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
                    mdl,
                    params,
                    op_vars,
                    partial_sol,
                    profile_fix_by_machine=by_machine,
                    machine_precedence_stride_set=stride_set,
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
            se_dis, st_dis = compute_weighted_earliness_tardiness(
                dispatched, sub_instance
            )
            dispatched_obj = float(se_dis + st_dis)
            dispatched_snapshot = (
                dispatched.deepcopy() if option.keep_step_schedules else None
            )
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
            if option.wall_clock_deadline_sec is not None:
                remaining_deadline = option.wall_clock_deadline_sec - time.monotonic()
                if remaining_deadline <= 0:
                    logger.info(
                        "neh_cp: wall_clock_deadline_sec exceeded before batch "
                        "%d/%d primary CP-SAT solve; stopping.",
                        step + 1,
                        len(batches),
                    )
                    stopped_early = True
                    break
                if (
                    applied_tl_seconds is None
                    or remaining_deadline < applied_tl_seconds
                ):
                    solver.parameters.max_time_in_seconds = remaining_deadline
                    applied_tl_seconds = remaining_deadline
            solver.parameters.num_workers = option.solver_thread_cnt
            value_recorder: ObjectiveValueRecorder | None = None
            if is_last_batch:
                value_recorder = ObjectiveValueRecorder()
                status = solver.solve(mdl, solution_callback=value_recorder)
            else:
                status = solver.solve(mdl)

            if value_recorder is not None:
                offset_sec = value_recorder.time_started - start_elapsed
                for t_rec, vb in value_recorder.entries:
                    progress_entries.append(
                        ProgressLogEntry(
                            elapsed_sec=t_rec + offset_sec,
                            obj_value=float(vb.value),
                            obj_bound=None,
                        )
                    )

            raw_lb = (
                solver.best_objective_bound
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
                else None
            )
            sub_obj_lb = float(raw_lb) if raw_lb is not None else 0.0

            logger.info(
                "neh_cp step %d: primary CP-SAT status=%s, obj=%s, "
                "bound=%.2f, wall=%.3fs, applied_tl=%s",
                step,
                solver.StatusName(status),
                f"{int(solver.ObjectiveValue())}"
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
                else "None",
                sub_obj_lb,
                solver.wall_time,
                f"{applied_tl_seconds:.3f}"
                if applied_tl_seconds is not None
                else "None",
            )

            cp_obj: float | None = None
            semi_active_obj: float | None = None
            cp_raw_snapshot: FFcSchedule | None = None
            semi_active_snapshot: FFcSchedule | None = None
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
                cp_obj = float(se_new + st_new)
                if option.keep_step_schedules:
                    cp_raw_snapshot = cp_sch.deepcopy()
                threshold = option.make_semi_active_after_cp_obj_threshold
                if threshold >= 0:
                    apply_semi_active = (se_new + st_new) >= threshold
                else:
                    apply_semi_active = option.make_semi_active_after_cp
                if apply_semi_active:
                    cp_sch.make_semi_active(stage_2_job_2_p)
                    cp_sch.insert_idle_time(due_window_map, ewt_map, twt_map)
                    se_tidy, st_tidy = compute_weighted_earliness_tardiness(
                        cp_sch, sub_instance
                    )
                    semi_active_obj = float(se_tidy + st_tidy)
                    if option.keep_step_schedules:
                        semi_active_snapshot = cp_sch.deepcopy()
                    if (se_tidy + st_tidy) < (se_new + st_new):
                        logger.info(
                            "neh_cp step %d: making CP solution semi-active reduced E/T "
                            "from %d to %d; using semi-active solution.",
                            step,
                            se_new + st_new,
                            se_tidy + st_tidy,
                        )
                        se_new, st_new = se_tidy, st_tidy
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
            scheduled_job_set = set(current_jobs)

            if option.keep_step_schedules:
                step_schedules.append(
                    (
                        step,
                        dispatched_snapshot
                        if dispatched_snapshot is not None
                        else dispatched.deepcopy(),
                        cp_raw_snapshot,
                        semi_active_snapshot,
                    )
                )

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
                by_machine, stride_set = decode_pf_method(option.pf_method)
                BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
                    mdl_2,
                    params_2,
                    op_vars_2,
                    partial_sol,
                    profile_fix_by_machine=by_machine,
                    machine_precedence_stride_set=stride_set,
                )
                BaseModelBuilder.apply_hints_from_schedule(
                    mdl_2, params_2, op_vars_2, et_vars_2, partial_sol
                )

                solver_2 = cp_model.CpSolver()
                if cp_tl_2nd_obj_seconds is not None:
                    solver_2.parameters.max_time_in_seconds = cp_tl_2nd_obj_seconds
                solver_2.parameters.num_workers = option.solver_thread_cnt
                if option.wall_clock_deadline_sec is not None:
                    remaining_deadline_2 = (
                        option.wall_clock_deadline_sec - time.monotonic()
                    )
                    if remaining_deadline_2 <= 0:
                        logger.info(
                            "neh_cp: wall_clock_deadline_sec exceeded before "
                            "stage-2 makespan solve at batch %d/%d; stopping.",
                            step + 1,
                            len(batches),
                        )
                        stopped_early = True
                        break
                    if (
                        cp_tl_2nd_obj_seconds is None
                        or remaining_deadline_2 < cp_tl_2nd_obj_seconds
                    ):
                        solver_2.parameters.max_time_in_seconds = remaining_deadline_2
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
                    dispatched_obj=dispatched_obj,
                    cp_obj=cp_obj,
                    semi_active_obj=semi_active_obj,
                )
            )
            prev_elapsed_seconds = step_elapsed_seconds
            last_obj_value = se + st

            if spec.stop_predicate is not None and spec.stop_predicate():
                logger.info(
                    "neh_cp: stop_predicate fired after batch %d/%d; stopping.",
                    step + 1,
                    len(batches),
                )
                stopped_early = True
                break
            if option.wall_clock_deadline_sec is not None:
                remaining_post = option.wall_clock_deadline_sec - time.monotonic()
                if remaining_post <= 0:
                    logger.info(
                        "neh_cp: wall_clock_deadline_sec exceeded after batch "
                        "%d/%d; stopping.",
                        step + 1,
                        len(batches),
                    )
                    stopped_early = True
                    break

        if stopped_early:
            remaining_jobs = [j for j in job_sequence if j not in scheduled_job_set]
            logger.info(
                "neh_cp: recovery dispatch — %d/%d remaining jobs (scheduled=%d).",
                len(remaining_jobs),
                n,
                len(scheduled_job_set),
            )
            if remaining_jobs:
                if partial_sol is None:
                    partial_sol = FFcSchedule(
                        jobs=instance.job_id_list,
                        stages=instance.stage_id_list,
                        machines_per_stage=instance.stage_2_machines_map,
                    )
                for j in remaining_jobs:
                    partial_sol.dispatch_job_by_stages(j, job_2_stage_2_p[j])
                partial_sol.make_semi_active(stage_2_job_2_p)
                partial_sol.insert_idle_time(due_window_map, ewt_map, twt_map)

            assert partial_sol is not None
            sum_e_stop, sum_t_stop = compute_weighted_earliness_tardiness(
                partial_sol, instance
            )
            obj_value_stop = float(sum_e_stop + sum_t_stop)
            progress_entries.append(
                ProgressLogEntry(
                    elapsed_sec=float(time.monotonic() - start_elapsed),
                    obj_value=obj_value_stop,
                    obj_bound=None,
                )
            )
            metrics_stopped: dict = {
                "sum_earliness": sum_e_stop,
                "sum_tardiness": sum_t_stop,
                "makespan": partial_sol.makespan,
                "step_log": tuple(step_entries),
                "stopped_after_batch": step,
                "recovered_jobs": tuple(remaining_jobs),
            }
            if option.keep_step_schedules:
                metrics_stopped["step_schedules"] = tuple(step_schedules)
            return AlgRecord(
                work_status=WorkStatus.FEASIBLE,
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=partial_sol,
                    obj_value=obj_value_stop,
                    obj_bound=None,
                    metrics=metrics_stopped,
                ),
                progress_log=tuple(progress_entries),
                termination_reason=TerminationReason.STOP_REQUESTED,
            )

        assert partial_sol is not None, (
            "partial_sol should not be None if we didn't stop early."
        )

        final = partial_sol
        sum_e, sum_t = compute_weighted_earliness_tardiness(final, instance)
        obj_value = float(sum_e + sum_t)
        progress_entries.append(
            ProgressLogEntry(
                elapsed_sec=float(time.monotonic() - start_elapsed),
                obj_value=obj_value,
                obj_bound=None,
            )
        )
        logger.info(
            "neh_cp: completed all %d batches naturally; obj=%.0f, makespan=%d.",
            len(batches),
            obj_value,
            int(final.makespan),
        )
        metrics_feasible: dict = {
            "sum_earliness": sum_e,
            "sum_tardiness": sum_t,
            "makespan": final.makespan,
            "step_log": tuple(step_entries),
        }
        if option.keep_step_schedules:
            metrics_feasible["step_schedules"] = tuple(step_schedules)
        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=final,
                obj_value=obj_value,
                obj_bound=None,
                metrics=metrics_feasible,
            ),
            progress_log=tuple(progress_entries),
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

    @staticmethod
    def _validate_custom_sequence(
        sequence: tuple[str, ...], instance: FFcDDWParameters
    ) -> None:
        expected = set(instance.job_id_list)
        provided = set(sequence)
        if len(sequence) != len(expected) or provided != expected:
            missing = sorted(expected - provided)
            extra = sorted(provided - expected)
            raise ValueError(
                "NehCpOption.custom_job_sequence must be a permutation of "
                f"instance.job_id_list ({len(expected)} jobs); "
                f"got {len(sequence)} entries (missing={missing[:5]}, "
                f"extra={extra[:5]})."
            )
