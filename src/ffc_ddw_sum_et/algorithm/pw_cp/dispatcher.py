"""PwCpDispatcher: sliding-window CP refinement of an incumbent FFcDDW schedule."""

from __future__ import annotations

import logging
import math
import time

from ortools.sat.python import cp_model

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    WorkStatus,
)
from ..base.alg_spec import AlgSpec
from ..cpsat_callbacks.obj_value_recorder import ObjectiveValueRecorder
from ..step_tl_resolver import resolve_per_step_tl
from ..utils import trunc4
from .cp_model import PwCpModelBuilder
from .option import PwCpOption
from .partition import (
    build_operation_partition,
    build_stage_2_batch_list,
    validate_and_get_batch_count,
)
from .step_log import PwCpStepEntry

__all__ = ["PwCpDispatcher"]


class PwCpDispatcher:
    """Refine a feasible FFcDDW incumbent via sliding-window CP-SAT."""

    algorithm_id = "pw_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        if spec.ref_solution is None:
            raise ValueError(
                "PwCpDispatcher requires spec.ref_solution (a feasible "
                "incumbent FFcSchedule) — chain it after a seeding "
                "subroutine such as calc_mcf_lb_and_derive_full_sch."
            )
        option = self._resolve_option(spec)
        logger = spec.logger or logging.getLogger(__name__)

        incumbent = spec.ref_solution.deepcopy()
        incumbent.make_semi_active(instance.stage_2_job_2_p_map)
        incumbent.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )

        horizon = max(
            1,
            int(math.ceil(incumbent.makespan * option.horizon_makespan_multiplier)),
        )

        initial_batches = build_stage_2_batch_list(incumbent, option.batch_size)
        max_batch_cnt = validate_and_get_batch_count(initial_batches)
        if max_batch_cnt < option.unfixed_batch_count:
            logger.info(
                "pw_cp: max_batch_cnt=%d < unfixed_batch_count=%d; nothing to do.",
                max_batch_cnt,
                option.unfixed_batch_count,
            )
            return self._make_completed_record(
                instance,
                option,
                incumbent,
                step_entries=(),
                progress_entries=(),
                step_schedules=(),
            )

        iteration_idxs = list(
            range(0, max_batch_cnt - option.unfixed_batch_count + 1, option.step_size)
        )
        per_step_tl = resolve_per_step_tl(
            cp_tl_from_arg=option.cp_tl_seconds,
            total_seconds=option.total_timelimit_seconds,
            num_batches=None,
            batch_count=len(iteration_idxs),
            batch_tl_mode=option.batch_tl_mode,
            batch_tl_offset_seconds=option.batch_tl_offset_seconds,
            logger=logger,
        )

        logger.info(
            "pw_cp: incumbent E+T=%.0f, batches=%d, iterations=%d, "
            "unfixed=%d, lpf=%d, rpf=%d, pf_method=%s, wall_clock_deadline=%s",
            self._full_obj(incumbent, instance),
            max_batch_cnt,
            len(iteration_idxs),
            option.unfixed_batch_count,
            option.left_profile_fixed_batch_count,
            option.right_profile_fixed_batch_count,
            option.pf_method,
            f"{option.wall_clock_deadline_sec:.3f}"
            if option.wall_clock_deadline_sec is not None
            else "None",
        )

        builder = PwCpModelBuilder()
        step_entries: list[PwCpStepEntry] = []
        progress_entries: list[ProgressLogEntry] = []
        step_schedules: list[tuple[int, FFcSchedule, FFcSchedule | None]] = []
        prev_elapsed_seconds = 0.0
        stopped_early = False

        start_elapsed = time.monotonic()

        for step, unfixed_start in enumerate(iteration_idxs):
            stage_2_batch = build_stage_2_batch_list(incumbent, option.batch_size)
            current_batch_cnt = validate_and_get_batch_count(stage_2_batch)
            if current_batch_cnt != max_batch_cnt:
                raise AssertionError(
                    "pw_cp: batch count changed during run "
                    f"(initial={max_batch_cnt}, current={current_batch_cnt})."
                )

            stage_2_partition = {
                i: build_operation_partition(
                    stage_2_batch[i],
                    unfixed_batch_start_idx=unfixed_start,
                    unfixed_batch_count=option.unfixed_batch_count,
                    left_profile_fixed_batch_count=option.left_profile_fixed_batch_count,
                    right_profile_fixed_batch_count=option.right_profile_fixed_batch_count,
                )
                for i in instance.stage_id_list
            }
            if option.enable_promotion_profile_fixed:
                unfixed_jobs = frozenset(
                    j for p in stage_2_partition.values() for j in p.unfixed_jobs
                )
                stage_2_partition = {
                    i: p.promote_job_contained_ops(unfixed_jobs)
                    for i, p in stage_2_partition.items()
                }

            sub_jobs = {
                j for p in stage_2_partition.values() for j, _ in p.non_time_fixed
            }
            if not sub_jobs:
                logger.debug("pw_cp step %d: empty non-time-fixed set; skipping.", step)
                continue

            rj_schedule = incumbent.deepcopy()
            rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(
                instance.job_2_dw_ub_map
            )

            sub_instance = FFcDDWParameters.create_instance_of_job_subset(
                instance, sub_jobs
            )

            build_result = builder.build(
                sub_instance,
                rj_schedule,
                stage_2_partition,
                horizon=horizon,
                pf_method=option.pf_method,
            )

            solver = cp_model.CpSolver()
            applied_tl_seconds = self._apply_tl_and_deadline(
                solver,
                option,
                per_step_tl[step] if per_step_tl is not None else None,
                start_elapsed,
                step,
                len(iteration_idxs),
                logger,
            )
            if applied_tl_seconds is False:
                stopped_early = True
                break
            solver.parameters.num_workers = option.solver_thread_cnt

            log_this_step = option.log_search_progress and (
                option.log_search_progress_max_steps is None
                or step < option.log_search_progress_max_steps
            )
            if log_this_step:
                solver.parameters.log_search_progress = True
                solver.parameters.log_to_response = True
                solver.parameters.log_to_stdout = False

            value_recorder = ObjectiveValueRecorder()
            status = solver.solve(build_result.mdl, solution_callback=value_recorder)
            if log_this_step:
                solve_log = solver.response_proto.solve_log
                if solve_log:
                    for line in solve_log.splitlines():
                        logger.info("[cp_sat step %d] %s", step, line)
            if value_recorder.entries:
                offset_sec = value_recorder.time_started - start_elapsed
                for t_rec, vb in value_recorder.entries:
                    progress_entries.append(
                        ProgressLogEntry(
                            elapsed_sec=t_rec + offset_sec,
                            obj_value=float(vb.value)
                            + float(build_result.et_offset_partial),
                            obj_bound=None,
                        )
                    )

            cand: FFcSchedule | None = None
            cand_obj: float | None = None
            cp_divergence_count = 0
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                cand, cp_divergence_count = builder.build_full_schedule_from_cp(
                    instance,
                    rj_schedule,
                    stage_2_partition,
                    build_result.sub_params,
                    build_result.op_vars,
                    solver,
                )
                cand.make_semi_active(instance.stage_2_job_2_p_map)
                cand.insert_idle_time(
                    instance.job_2_due_window_map,
                    instance.job_2_ewt_map,
                    instance.job_2_twt_map,
                )
                cand_obj = self._full_obj(cand, instance)
                if cp_divergence_count:
                    logger.debug(
                        "pw_cp step %d: %d ops realised at later end-time than CP "
                        "(cumulative auto-assignment couldn't honour exact CP times). "
                        "Schedule is still feasible; objective recomputed from "
                        "realised positions.",
                        step,
                        cp_divergence_count,
                    )

            incumbent_obj_before = self._full_obj(incumbent, instance)
            accepted = (
                cand is not None
                and cand_obj is not None
                and cand_obj < incumbent_obj_before
            )
            if accepted:
                incumbent = cand  # type: ignore[assignment]
            incumbent_obj_after = self._full_obj(incumbent, instance)

            if option.keep_step_schedules:
                step_schedules.append(
                    (
                        step,
                        incumbent.deepcopy(),
                        cand.deepcopy() if cand is not None else None,
                    )
                )

            step_elapsed_seconds = float(time.monotonic() - start_elapsed)
            step_delta = step_elapsed_seconds - prev_elapsed_seconds
            elapsed_portion = (
                step_delta / applied_tl_seconds
                if applied_tl_seconds is not None and applied_tl_seconds > 0
                else None
            )
            step_entries.append(
                PwCpStepEntry(
                    step=step,
                    elapsed_time=trunc4(step_elapsed_seconds),
                    TL=trunc4(applied_tl_seconds),
                    elapsed_portion=trunc4(elapsed_portion),
                    unfixed_batch_start_idx=unfixed_start,
                    non_time_fixed_op_count=sum(
                        len(p.non_time_fixed) for p in stage_2_partition.values()
                    ),
                    sub_job_count=len(sub_jobs),
                    incumbent_obj_before=incumbent_obj_before,
                    cp_obj=cand_obj,
                    incumbent_obj_after=incumbent_obj_after,
                    accepted=accepted,
                    status=solver.StatusName(status),
                    wall_seconds=float(solver.wall_time),
                    cp_divergence_count=cp_divergence_count,
                )
            )
            prev_elapsed_seconds = step_elapsed_seconds

            logger.info(
                "pw_cp step %d: unfixed[%d:%d) ntf_ops=%d sub_jobs=%d "
                "status=%s cp_obj=%s incumbent=%.0f→%.0f accepted=%s "
                "wall=%.3fs TL=%s",
                step,
                unfixed_start,
                unfixed_start + option.unfixed_batch_count,
                sum(len(p.non_time_fixed) for p in stage_2_partition.values()),
                len(sub_jobs),
                solver.StatusName(status),
                f"{cand_obj:.0f}" if cand_obj is not None else "None",
                incumbent_obj_before,
                incumbent_obj_after,
                accepted,
                solver.wall_time,
                f"{applied_tl_seconds:.3f}"
                if applied_tl_seconds is not None
                else "None",
            )

            if spec.stop_predicate is not None and spec.stop_predicate():
                logger.info(
                    "pw_cp: stop_predicate fired after step %d/%d; stopping.",
                    step + 1,
                    len(iteration_idxs),
                )
                stopped_early = True
                break
            if option.wall_clock_deadline_sec is not None:
                remaining = option.wall_clock_deadline_sec - time.monotonic()
                if remaining <= 0:
                    logger.info(
                        "pw_cp: wall_clock_deadline exceeded after step %d/%d; "
                        "stopping.",
                        step + 1,
                        len(iteration_idxs),
                    )
                    stopped_early = True
                    break

        termination_reason = (
            TerminationReason.STOP_REQUESTED
            if stopped_early
            else TerminationReason.COMPLETED
        )
        if termination_reason == TerminationReason.STOP_REQUESTED:
            return self._make_stopped_record(
                instance,
                option,
                incumbent,
                tuple(step_entries),
                tuple(progress_entries),
                tuple(step_schedules),
                start_elapsed,
            )
        return self._make_completed_record(
            instance,
            option,
            incumbent,
            step_entries=tuple(step_entries),
            progress_entries=tuple(progress_entries),
            step_schedules=tuple(step_schedules),
        )

    # ---- helpers ----

    @staticmethod
    def _full_obj(schedule: FFcSchedule, instance: FFcDDWParameters) -> float:
        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
        return float(sum_e + sum_t)

    @staticmethod
    def _apply_tl_and_deadline(
        solver: cp_model.CpSolver,
        option: PwCpOption,
        per_step_tl: float | None,
        start_elapsed: float,
        step: int,
        total_steps: int,
        logger: logging.Logger,
    ) -> float | None | bool:
        """Mirror neh_cp's TL+deadline plumbing.

        Returns the applied TL in seconds, or ``False`` when the deadline
        was already exceeded before this step's solve.
        """
        applied: float | None = None
        if per_step_tl is not None:
            if option.apply_cumulative_tl:
                # mirror neh_cp's cumulative-tl branch (rare)
                solver.parameters.max_time_in_seconds = per_step_tl
                applied = per_step_tl
            else:
                solver.parameters.max_time_in_seconds = per_step_tl
                applied = per_step_tl
        if option.wall_clock_deadline_sec is not None:
            remaining = option.wall_clock_deadline_sec - time.monotonic()
            if remaining <= 0:
                logger.info(
                    "pw_cp: wall_clock_deadline_sec exceeded before step "
                    "%d/%d solve; stopping.",
                    step + 1,
                    total_steps,
                )
                return False
            if applied is None or remaining < applied:
                solver.parameters.max_time_in_seconds = remaining
                applied = remaining
        return applied

    def _make_completed_record(
        self,
        instance: FFcDDWParameters,
        option: PwCpOption,
        incumbent: FFcSchedule,
        *,
        step_entries,
        progress_entries,
        step_schedules,
    ) -> AlgRecord:
        sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent, instance)
        obj_value = float(sum_e + sum_t)
        metrics: dict = {
            "sum_earliness": sum_e,
            "sum_tardiness": sum_t,
            "makespan": incumbent.makespan,
            "step_log": tuple(step_entries),
        }
        if option.keep_step_schedules:
            metrics["step_schedules"] = tuple(step_schedules)
        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=incumbent,
                obj_value=obj_value,
                obj_bound=None,
                metrics=metrics,
            ),
            progress_log=tuple(progress_entries),
            termination_reason=TerminationReason.COMPLETED,
        )

    def _make_stopped_record(
        self,
        instance: FFcDDWParameters,
        option: PwCpOption,
        incumbent: FFcSchedule,
        step_entries,
        progress_entries,
        step_schedules,
        start_elapsed: float,
    ) -> AlgRecord:
        sum_e, sum_t = compute_weighted_earliness_tardiness(incumbent, instance)
        obj_value = float(sum_e + sum_t)
        progress = tuple(progress_entries) + (
            ProgressLogEntry(
                elapsed_sec=float(time.monotonic() - start_elapsed),
                obj_value=obj_value,
                obj_bound=None,
            ),
        )
        metrics: dict = {
            "sum_earliness": sum_e,
            "sum_tardiness": sum_t,
            "makespan": incumbent.makespan,
            "step_log": tuple(step_entries),
            "stopped_after_step": (step_entries[-1].step if step_entries else -1),
        }
        if option.keep_step_schedules:
            metrics["step_schedules"] = tuple(step_schedules)
        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=incumbent,
                obj_value=obj_value,
                obj_bound=None,
                metrics=metrics,
            ),
            progress_log=progress,
            termination_reason=TerminationReason.STOP_REQUESTED,
        )

    def _validate_instance(self, spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "PwCpDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    def _resolve_option(self, spec: AlgSpec) -> PwCpOption:
        if spec.option is None:
            return PwCpOption()
        if not isinstance(spec.option, PwCpOption):
            raise TypeError("PwCpDispatcher requires PwCpOption as spec.option.")
        return spec.option
