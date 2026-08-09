"""Job-contribution D&C CP-SAT dispatcher.

Destruct incumbent's top-contributing jobs, profile-fix the remaining
jobs' relative order, give a complete hint, and let CP-SAT re-insert
the removed jobs.
"""

from __future__ import annotations

import logging
import math
import time

from ortools.sat.python import cp_model

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import (
    compute_job_2_obj_contrib_map,
    compute_weighted_earliness_tardiness,
)
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
from ..cpsat_search_log import write_cpsat_search_log
from ..cpsat_solver_options import CpsatSolverOptions, get_solver
from ..cumulative import BaseModelBuilder, decode_pf_method
from .option import JobContribCpOption
from .selection import select_jd_jobs

__all__ = ["JobContribCpDispatcher"]

_OBJ_IMPROVEMENT_TOLERANCE = 1e-6


class JobContribCpDispatcher:
    """D&C: destroy top-contributing jobs, profile-fix the rest, CP-SAT re-inserts."""

    algorithm_id = "job_contrib_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger if spec.logger is not None else logging.getLogger(__name__)

        incumbent = spec.ref_solution
        if incumbent is None:
            raise RuntimeError(
                "JobContribCpDispatcher requires spec.ref_solution (incumbent "
                "schedule); chain it after a step that registers an incumbent."
            )

        start = time.monotonic()

        job_2_contrib = compute_job_2_obj_contrib_map(
            incumbent, instance, time_factor=option.time_factor
        )
        incumbent_obj = float(sum(job_2_contrib.values()))
        positive_jobs = [j for j, v in job_2_contrib.items() if v > 0]

        instance_job_set = set(instance.job_id_list)
        destroy_selection: str
        if option.destroy_job_ids is not None:
            destroy_selection = "explicit"
            selected = []
            for j in option.destroy_job_ids:
                if j not in instance_job_set:
                    raise ValueError(
                        f"destroy_job_ids contains job '{j}' not in instance "
                        f"(instance has {len(instance_job_set)} jobs)"
                    )
                selected.append(j)
            jd_count_eff = len(selected)
        else:
            destroy_selection = "contribution"
            selected = select_jd_jobs(
                incumbent,
                instance,
                option.jd_count_target,
                time_factor=option.time_factor,
            )
            jd_count_eff = len(selected)

        if jd_count_eff == 0:
            logger.info(
                "job_contrib_cp: no job with positive objective contribution; "
                "returning the incumbent unchanged (obj=%.1f).",
                incumbent_obj,
            )
            final_elapsed = time.monotonic() - start
            return AlgRecord(
                work_status=WorkStatus.OPTIMAL,
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=incumbent,
                    obj_value=incumbent_obj,
                    obj_bound=incumbent_obj,
                    metrics={
                        "jd_count_target": option.jd_count_target,
                        "jd_count_eff": 0,
                        "destroyed_op_count": 0,
                        "positive_contrib_job_count": 0,
                        "incumbent_obj": incumbent_obj,
                        "selected_jobs": [],
                        "destroy_selection": destroy_selection,
                    },
                ),
                progress_log=(
                    ProgressLogEntry(
                        elapsed_sec=final_elapsed,
                        obj_value=incumbent_obj,
                        obj_bound=incumbent_obj,
                    ),
                ),
                termination_reason=TerminationReason.COMPLETED,
            )

        jd_eff_contrib_sum = sum(job_2_contrib[j] for j in selected)
        logger.info(
            "job_contrib_cp: destroy_selection=%s, jd_count_target=%s, "
            "jd_count_eff=%d (positive-contrib jobs=%d, n=%d, incumbent obj=%.1f, "
            "jd eff contrib sum=%d)",
            destroy_selection,
            option.jd_count_target,
            jd_count_eff,
            len(positive_jobs),
            instance.job_count,
            incumbent_obj,
            jd_eff_contrib_sum,
        )

        selected_set = set(selected)
        pf_schedule = incumbent.deepcopy()
        pf_schedule.remove_jobs(selected_set)

        raw_horizon = max(
            incumbent.makespan,
            max(
                (
                    instance.job_2_dw_ub_map[j] // option.time_factor
                    if option.time_factor > 1
                    else instance.job_2_dw_ub_map[j]
                )
                for j in instance.job_id_list
            ),
        )
        horizon = math.ceil(raw_horizon * option.horizon_multiplier)

        builder = BaseModelBuilder()
        mdl, params, op_vars, et_vars = builder.build(
            instance, horizon=horizon, time_factor=option.time_factor
        )

        by_machine, stride_set = decode_pf_method(option.pf_method)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            pf_schedule,
            profile_fix_by_machine=by_machine,
            machine_precedence_stride_set=stride_set,
        )

        start_map = incumbent.get_jik_2_start_time_map()
        end_map = incumbent.get_jik_2_end_time_map()
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, start_map
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, end_map
        )
        if et_vars is not None:
            BaseModelBuilder.apply_et_hints_from_ref_schedule(
                mdl, params, et_vars, incumbent
            )

        # The CP time limit is the tighter of (a) this step's own ``cp_tl``
        # budget minus the setup already spent and (b) the controller's
        # remaining wall-clock budget for the whole run. Honouring (b) is what
        # keeps an inserted job_contrib_cp step inside a fixed total-TL
        # experiment budget.
        now = time.monotonic()
        tl_bounds: dict[str, float] = {}
        destroyed_op_count = jd_count_eff * instance.stage_count
        effective_cp_tl: float | None = None
        if option.cp_tl_mode == "proportional":
            effective_cp_tl = option.destroyed_op_tl_multiplier * destroyed_op_count
            tl_bounds["cp_tl"] = effective_cp_tl - (now - start)
        elif option.cp_tl_seconds is not None:
            effective_cp_tl = option.cp_tl_seconds
            tl_bounds["cp_tl"] = option.cp_tl_seconds - (now - start)
        if option.wall_clock_deadline_sec is not None:
            tl_bounds["wall_clock_deadline"] = option.wall_clock_deadline_sec - now

        eff_tl: float | None = min(tl_bounds.values()) if tl_bounds else None
        if eff_tl is not None and eff_tl <= 0.0:
            binding = min(tl_bounds, key=lambda k: tl_bounds[k])
            logger.warning(
                "JobContribCpDispatcher: time budget exhausted before solver "
                "start (binding=%s, eff_tl=%.3fs), falling back to incumbent",
                binding,
                eff_tl,
            )
            return self._incumbent_fallback_record(
                instance,
                option,
                incumbent,
                incumbent_obj=incumbent_obj,
                jd_count_eff=jd_count_eff,
                destroyed_op_count=destroyed_op_count,
                positive_count=len(positive_jobs),
                cpsat_status=f"budget_exhausted_before_solve:{binding}",
                selected=selected,
                destroy_selection=destroy_selection,
                setup_seconds=now - start,
            )

        solver_cfg = CpsatSolverOptions(
            max_time_in_seconds=eff_tl,
            num_workers=option.solver_thread_cnt,
            log_search_progress=option.log_search_progress,
            log_to_stdout=False if option.log_search_progress else None,
            log_to_response=True if option.log_search_progress else None,
        )
        solver = get_solver(solver_cfg)

        logger.info(
            "JobContribCpDispatcher: instance=%s, horizon=%d, "
            "selected=%s, eff_tl=%s (bounds=%s), num_workers=%d, "
            "log_search_progress=%s",
            instance.name,
            horizon,
            [str(j) for j in selected],
            f"{eff_tl:.3f}s" if eff_tl is not None else "None",
            {k: round(v, 3) for k, v in tl_bounds.items()} or "none",
            option.solver_thread_cnt,
            option.log_search_progress,
        )

        # When the search log is requested, capture it so the hint-completeness
        # invariant (§1-(3) of the plan) is self-checked instead of only being
        # asserted in tests.
        search_log_lines: list[str] = []
        if option.log_search_progress:
            solver.log_callback = search_log_lines.append

        recorder = ObjectiveValueRecorder()
        setup_seconds = time.monotonic() - start
        status = solver.solve(mdl, solution_callback=recorder)
        status_name = solver.status_name(status)

        if option.log_search_progress:
            write_cpsat_search_log(
                solver.response_proto.solve_log,
                option.solver_log_path_getter,
                "_job_contrib_cp_search.log",
                logger=logger,
            )

        if option.log_search_progress and any(
            "solution hint is incomplete" in line.lower() for line in search_log_lines
        ):
            logger.warning(
                "JobContribCpDispatcher: CP-SAT reports an INCOMPLETE solution "
                "hint; every op_start/op_end/E/T is expected to be hinted from "
                "the incumbent. This is a bug signal."
            )

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        if not has_solution:
            if status == cp_model.MODEL_INVALID:
                logger.error(
                    "JobContribCpDispatcher: CP-SAT returned MODEL_INVALID; "
                    "this is a model construction error.",
                )
                raise RuntimeError(
                    f"JobContribCpDispatcher: CP-SAT returned status={status_name} "
                    "(MODEL_INVALID); this is a model construction error."
                )
            if status == cp_model.UNKNOWN:
                logger.warning(
                    "JobContribCpDispatcher: no feasible solution (status=%s), "
                    "falling back to incumbent",
                    status_name,
                )
                return self._incumbent_fallback_record(
                    instance,
                    option,
                    incumbent,
                    incumbent_obj=incumbent_obj,
                    jd_count_eff=jd_count_eff,
                    destroyed_op_count=destroyed_op_count,
                    positive_count=len(positive_jobs),
                    cpsat_status=status_name,
                    selected=selected,
                    destroy_selection=destroy_selection,
                    setup_seconds=setup_seconds,
                )
            # INFEASIBLE
            if option.error_if_infeasible:
                logger.error(
                    "JobContribCpDispatcher: infeasible solution (status=%s) "
                    "despite complete hint",
                    status_name,
                )
                raise RuntimeError(
                    f"JobContribCpDispatcher: CP-SAT returned status={status_name} "
                    "(INFEASIBLE) despite complete hint; this is a bug signal."
                )
            logger.warning(
                "JobContribCpDispatcher: infeasible solution (status=%s), "
                "falling back to incumbent",
                status_name,
            )
            return self._incumbent_fallback_record(
                instance,
                option,
                incumbent,
                incumbent_obj=incumbent_obj,
                jd_count_eff=jd_count_eff,
                destroyed_op_count=destroyed_op_count,
                positive_count=len(positive_jobs),
                cpsat_status=status_name,
                selected=selected,
                destroy_selection=destroy_selection,
                setup_seconds=setup_seconds,
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
        schedule.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
            time_factor=option.time_factor,
        )

        sum_e, sum_t = compute_weighted_earliness_tardiness(
            schedule, instance, time_factor=option.time_factor
        )
        post_obj = float(sum_e + sum_t)

        cp_obj = float(solver.objective_value)
        if post_obj > cp_obj + _OBJ_IMPROVEMENT_TOLERANCE:
            logger.warning(
                "JobContribCpDispatcher: post-process objective (%.3f) > "
                "CP objective (%.3f); post_obj used for registration",
                post_obj,
                cp_obj,
            )

        final_elapsed = time.monotonic() - start
        progress_entries: list[ProgressLogEntry] = []
        cp_progress: list[dict] = []
        if recorder.entries:
            offset_sec = recorder.time_started - start
            for t_rec, vb in recorder.entries:
                t_from_start = t_rec + offset_sec
                progress_entries.append(
                    ProgressLogEntry(
                        elapsed_sec=t_from_start,
                        obj_value=float(vb.value),
                        obj_bound=None,
                    )
                )
                cp_progress.append(
                    {
                        "t": t_from_start,
                        "obj_value": float(vb.value),
                        "obj_bound": float(vb.bound),
                    }
                )
        progress_entries.append(
            ProgressLogEntry(
                elapsed_sec=final_elapsed,
                obj_value=post_obj,
                obj_bound=None,
            ),
        )
        cp_progress.append(
            {
                "t": final_elapsed,
                "obj_value": post_obj,
                "obj_bound": None,
            }
        )
        progress_log = tuple(progress_entries)

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
                obj_value=post_obj,
                obj_bound=None,
                metrics={
                    "jd_count_target": option.jd_count_target,
                    "jd_count_eff": jd_count_eff,
                    "destroyed_op_count": destroyed_op_count,
                    "cp_tl_seconds": effective_cp_tl,
                    "positive_contrib_job_count": len(positive_jobs),
                    "incumbent_obj": incumbent_obj,
                    "cpsat_status": status_name,
                    "cpsat_obj": cp_obj,
                    "horizon": float(horizon),
                    "sum_earliness": float(sum_e),
                    "sum_tardiness": float(sum_t),
                    "selected_jobs": [str(j) for j in selected],
                    "cp_progress": cp_progress,
                    "destroy_selection": destroy_selection,
                    "setup_seconds": setup_seconds,
                    # int(): FFcSchedule.makespan can be a numpy scalar, which
                    # the controller's dump_yaml cannot represent.
                    "makespan": int(schedule.makespan),
                },
            ),
            progress_log=progress_log,
            termination_reason=(
                TerminationReason.COMPLETED
                if status == cp_model.OPTIMAL
                else TerminationReason.TIME_LIMIT
            ),
        )

    @staticmethod
    def _incumbent_fallback_record(
        instance: FFcDDWParameters,
        option: JobContribCpOption,
        incumbent: FFcSchedule,
        *,
        incumbent_obj: float,
        jd_count_eff: int,
        destroyed_op_count: int,
        positive_count: int,
        cpsat_status: str,
        selected: list[str],
        destroy_selection: str,
        setup_seconds: float | None,
    ) -> AlgRecord:
        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=JobContribCpDispatcher.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=incumbent,
                obj_value=incumbent_obj,
                obj_bound=None,
                metrics={
                    "jd_count_target": option.jd_count_target,
                    "jd_count_eff": jd_count_eff,
                    "destroyed_op_count": destroyed_op_count,
                    "positive_contrib_job_count": positive_count,
                    "incumbent_obj": incumbent_obj,
                    "cpsat_status": cpsat_status,
                    "fallback": "incumbent",
                    "selected_jobs": [str(j) for j in selected],
                    "destroy_selection": destroy_selection,
                    "setup_seconds": setup_seconds,
                },
            ),
            progress_log=(),
            termination_reason=TerminationReason.TIME_LIMIT,
        )

    @staticmethod
    def _validate_instance(spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "JobContribCpDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    @staticmethod
    def _resolve_option(spec: AlgSpec) -> JobContribCpOption:
        if spec.option is None:
            return JobContribCpOption(jd_count_target=1)
        if not isinstance(spec.option, JobContribCpOption):
            raise TypeError(
                "JobContribCpDispatcher requires JobContribCpOption as spec.option."
            )
        return spec.option
