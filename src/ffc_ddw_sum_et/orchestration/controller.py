"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cumulative import BaseModelBuilder
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_window_et

from .controller_core import FFcDDWSubroutineControllerCore
from .solution_manager import FFcDDWSolution


@dataclass(slots=True)
class MCFLBDiagnostic:
    """Per-phase value/time diagnostic for ``run_mcf_lb``.

    Populated incrementally as the pipeline progresses, so partial data
    survives an early return on infeasibility.
    """

    mcf_lb: float | None = None
    last_stage_only_obj: float | None = None
    last_stage_only_bound: float | None = None
    dispatched_obj: float | None = None
    profile_fix_obj: float | None = None
    profile_fix_bound: float | None = None
    mcf_solve_sec: float | None = None
    last_stage_cp_sat_sec: float | None = None
    dispatch_sec: float | None = None
    profile_fix_cp_sat_sec: float | None = None
    reached_phase: str = "init"
    ls_status: str | None = None
    pf_status: str | None = None
    single_stage: bool = False


class FFcDDWSubroutineController(FFcDDWSubroutineControllerCore):
    def run_fam(self, job_sequence: Sequence[str] | None = None) -> SubroutineReport:
        """Step method: run FAMDispatcher and return a SubroutineReport.

        Args:
            job_sequence: Full permutation of instance job IDs. Must include every
                instance job exactly once. When omitted, the instance's native
                ``job_id_list`` order is used.
        """
        start_elapsed = self.timer.elapsed_sec

        if job_sequence is None:
            option = FAMOption()
        else:
            option = FAMOption(job_sequence=tuple(job_sequence))

        spec = AlgSpec(
            instance=self.instance,
            option=option,
            logger=self.logger,
        )

        record = FAMDispatcher().run(spec)
        elapsed = self.timer.elapsed_sec - start_elapsed

        result = record.result
        obj_value = (
            float(result.obj_value) if result and result.obj_value is not None else None
        )
        obj_bound = (
            float(result.obj_bound) if result and result.obj_bound is not None else None
        )

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )

        if result is not None and result.schedule is not None:
            fam_solution = FFcDDWSolution(
                schedule=result.schedule,
                obj_value=obj_value,
                obj_bound=obj_bound,
            )
            self.solution_manager.register(report, fam_solution)

        return report

    def run_mcf_lb(
        self,
        last_stage_only_timelimit: float | str | None = None,
        profile_fix_by_machine: bool = False,
        machine_precedence_stride: int = 1,
    ) -> SubroutineReport:
        """Step method: full MCF-LB pipeline (step 1 + step 2).

        1) MCF preemptive LB + last-stage-only CP-SAT warm-started from MCF.
        2-1) Reverse-dispatch with last-stage pinned as seed: flip the
             last-stage CP-SAT schedule around its makespan, insert it into
             a reversed-instance schedule, then fill earlier reversed stages
             with ``MixedDispatcher(reversed).get_best_mixed_schedule_by_sequence``
             under ``criteria="makespan"``; unflip via ``as_reversed``.
        2-2) Align last stage back to CP-SAT times via ``right_shift``, then
             right-shift again if any op now starts before 0.
        2-3) Profile-fix CP-SAT full solve warm-started from step 2-2.

        Args:
            last_stage_only_timelimit: Time limit for the last-stage-only
                CP-SAT solver (step 1). Accepts either a float (seconds) or
                a string of the form ``"<x>nc"`` which is parsed as
                ``x * n * c`` seconds. ``None`` leaves the solver unbounded.
                Only applies to ``ls_solver``; the profile-fix solver
                (step 2-3) runs without an explicit time limit.
        """
        start_elapsed = self.timer.elapsed_sec
        diag = MCFLBDiagnostic()
        # Expose up-front so early returns retain whatever has been filled so far.
        self.mcf_lb_diagnostic = diag
        instance = self.instance
        n = instance.job_count
        c = instance.stage_count
        last_stage_id = instance.stage_id_list[-1]
        solver_thread_cnt = 1

        # ----- Step 1: MCF LB + last-stage-only CP-SAT -----
        # Step 1-1: Priority score from min cost flow
        t_mcf = self.timer.elapsed_sec
        mcf = ParallelMachinePreemptionMcf.from_instance(instance)
        mcf.solve()
        if not mcf.is_optimal():
            raise RuntimeError(f"MCF not optimal for instance {instance.name}")
        mcf_lb = float(mcf.get_obj_value())
        diag.mcf_solve_sec = self.timer.elapsed_sec - t_mcf
        diag.mcf_lb = mcf_lb
        diag.reached_phase = "mcf"
        # Step 1-1 done
        job_2_priority_score_map = (
            mcf.get_job_2_avg_time_minus_half_processing_time_sum_map()
        )

        # Step 1-2: Initialize last-stage only schedule by dispatching jobs with priority score from MCF
        job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
        mcf_job_sequence = sorted(
            instance.job_id_list,
            key=lambda j: (
                job_2_priority_score_map[j] is None,
                job_2_priority_score_map[j]
                if job_2_priority_score_map[j] is not None
                else 0,
                job_2_pos[j],
            ),
        )
        job_2_release_map = instance.get_job_2_p_sum_except_last_stage()
        duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)

        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

        ls_builder = BaseModelBuilder()
        ls_mdl, ls_params, ls_ops_vars, _ls_obj_vars = ls_builder.build(
            instance=instance,
            horizon=horizon,
            last_stage_only=True,
            job_2_release=job_2_release_map,
            obj_lb=mcf_lb,
        )

        ls_init_schedule = FFcSchedule(
            jobs=instance.job_id_list,
            stages=instance.stage_id_list,
            machines_per_stage=instance.stage_2_machines_map,
        )
        # Step 1-2 done
        ls_init_schedule.dispatch_stage_by_jobs(
            last_stage_id,
            mcf_job_sequence,
            duration_map,
            job_2_release=job_2_release_map,
        )
        # Step 1-3: CP-SAT last stage only, warm-started from dispatch
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            ls_mdl,
            ls_params,
            ls_ops_vars,
            ls_init_schedule.get_jik_2_start_time_map(),
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            ls_mdl, ls_params, ls_ops_vars, ls_init_schedule.get_jik_2_end_time_map()
        )

        ls_budget = _parse_nc_timelimit(last_stage_only_timelimit, n, c)
        ls_solver = cp_model.CpSolver()
        if ls_budget is not None:
            ls_solver.parameters.max_time_in_seconds = float(ls_budget)
        ls_solver.parameters.num_search_workers = int(solver_thread_cnt)
        t_ls = self.timer.elapsed_sec
        ls_status = ls_solver.Solve(ls_mdl)
        diag.last_stage_cp_sat_sec = self.timer.elapsed_sec - t_ls
        diag.ls_status = ls_solver.StatusName(ls_status)

        if ls_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.logger.warning(
                "run_mcf_lb step 1c: last-stage CP-SAT no feasible solution "
                "(status=%s)",
                ls_solver.StatusName(ls_status),
            )
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
            )

        ls_j_i_2_start = {
            (j, last_stage_id): int(
                ls_solver.Value(ls_ops_vars.op_start[j, last_stage_id])
            )
            for j in ls_params.j_list
        }
        ls_j_i_2_end = {
            (j, last_stage_id): int(
                ls_solver.Value(ls_ops_vars.op_end[j, last_stage_id])
            )
            for j in ls_params.j_list
        }
        last_stage_schedule = _build_schedule_from_op_starts(
            instance, ls_j_i_2_start, ls_j_i_2_end, stages=[last_stage_id]
        )
        last_stage_only_schedule_makespan = max(ls_j_i_2_end.values())
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=last_stage_schedule,
            obj_value=float(ls_solver.objective_value),
            obj_bound=mcf_lb,
        )
        diag.last_stage_only_obj = float(ls_solver.objective_value)
        diag.last_stage_only_bound = float(ls_solver.best_objective_bound)
        diag.reached_phase = "last_stage"
        # Step 1-3 done

        # ----- Step 2-1 + 2-2: reverse-dispatch, unflip, align, right-shift -----
        t_disp = self.timer.elapsed_sec
        diag.single_stage = c == 1
        if c == 1:
            # Single-stage instance: last_stage_schedule is already the full
            # schedule; no reverse-dispatch needed.
            dispatched_schedule = last_stage_schedule
        else:
            # Step 2-1: reverse-dispatch with last-stage pinned as seed
            # Sort jobs by last-stage end time (latest first)
            last_stage_end_map = {
                j: ls_j_i_2_end[j, last_stage_id] for j in instance.job_id_list
            }
            rev_job_sequence = sorted(
                instance.job_id_list,
                key=lambda j: (-last_stage_end_map[j], job_2_pos[j]),
            )

            reversed_instance = FFcDDWParameters.reverse_stages(instance)
            reversed_seed = FFcSchedule(
                jobs=reversed_instance.job_id_list,
                stages=reversed_instance.stage_id_list,
                machines_per_stage=reversed_instance.stage_2_machines_map,
            )
            for mc_id, s, e, j in last_stage_schedule.iter_operations_on_stage(
                last_stage_id
            ):
                reversed_seed.add_ops_times_2_mc(
                    stage_id=last_stage_id,
                    mc_id=mc_id,
                    job_id=j,
                    start_time=last_stage_only_schedule_makespan - e,
                    end_time=last_stage_only_schedule_makespan - s,
                )

            rev_dispatcher = MixedDispatcher(reversed_instance)
            reversed_full = rev_dispatcher.get_best_mixed_schedule_by_sequence(
                rev_job_sequence,
                schedule=reversed_seed,
                from_stage=reversed_instance.stage_id_list[1],
                criteria="makespan",
            )
            if reversed_full is None:
                self.logger.warning(
                    "run_mcf_lb step 2-1: reversed MixedDispatcher produced no schedule"
                )
                elapsed = self.timer.elapsed_sec - start_elapsed
                return SubroutineReport(
                    elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
                )

            # Step 2-1 done
            dispatched_schedule = reversed_full.as_reversed()

        sum_e, sum_t = compute_window_et(dispatched_schedule, instance)
        step2_obj = float(sum_e + sum_t)
        diag.dispatch_sec = self.timer.elapsed_sec - t_disp
        diag.dispatched_obj = step2_obj
        diag.reached_phase = "dispatched"
        self.solution_manager.register(
            SubroutineReport(
                elapsed_time=self.timer.elapsed_sec - start_elapsed,
                obj_value=step2_obj,
                obj_bound=mcf_lb,
            ),
            FFcDDWSolution(
                schedule=dispatched_schedule, obj_value=step2_obj, obj_bound=mcf_lb
            ),
        )

        # ----- Step 2-3: profile-fix CP-SAT full solve -----
        pf_builder = BaseModelBuilder()
        pf_mdl, pf_params, pf_op_vars, _pf_et_vars = pf_builder.build(
            instance, horizon=horizon
        )
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            pf_mdl,
            pf_params,
            pf_op_vars,
            dispatched_schedule,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
        )
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            pf_mdl,
            pf_params,
            pf_op_vars,
            dispatched_schedule.get_jik_2_start_time_map(),
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            pf_mdl, pf_params, pf_op_vars, dispatched_schedule.get_jik_2_end_time_map()
        )

        pf_solver = cp_model.CpSolver()
        pf_solver.parameters.num_search_workers = int(solver_thread_cnt)
        t_pf = self.timer.elapsed_sec
        pf_status = pf_solver.Solve(pf_mdl)
        diag.profile_fix_cp_sat_sec = self.timer.elapsed_sec - t_pf
        diag.pf_status = pf_solver.StatusName(pf_status)

        try:
            pf_bound = float(pf_solver.best_objective_bound)
        except Exception:
            pf_bound = mcf_lb
        diag.profile_fix_bound = pf_bound
        obj_bound_final = max(mcf_lb, pf_bound)

        if pf_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.logger.warning(
                "run_mcf_lb step 2-2: profile-fix CP-SAT no feasible solution "
                "(status=%s); returning step-2-2 incumbent",
                pf_solver.StatusName(pf_status),
            )
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=step2_obj, obj_bound=obj_bound_final
            )

        final_j_i_2_start = {
            (j, i): int(pf_solver.Value(pf_op_vars.op_start[j, i]))
            for j in pf_params.j_list
            for i in pf_params.i_list
        }
        final_j_i_2_end = {
            (j, i): int(pf_solver.Value(pf_op_vars.op_end[j, i]))
            for j in pf_params.j_list
            for i in pf_params.i_list
        }
        final_schedule = _build_schedule_from_op_starts(
            instance, final_j_i_2_start, final_j_i_2_end
        )

        sum_e, sum_t = compute_window_et(final_schedule, instance)
        final_obj = float(sum_e + sum_t)
        cp_obj = float(pf_solver.objective_value)
        if final_obj != cp_obj:
            self.logger.warning(
                "run_mcf_lb step 2-3: post-build objective %.3f != CP-SAT "
                "objective %.3f",
                final_obj,
                cp_obj,
            )
        diag.profile_fix_obj = final_obj
        diag.reached_phase = "profile_fix"

        elapsed = self.timer.elapsed_sec - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=final_obj,
            obj_bound=obj_bound_final,
        )
        self.solution_manager.register(
            report,
            FFcDDWSolution(
                schedule=final_schedule,
                obj_value=final_obj,
                obj_bound=obj_bound_final,
            ),
        )
        return report

    def run_last_stage_cp_sat_lb(
        self,
        solver_thread_cnt: int = 1,
    ) -> SubroutineReport:
        """Step method: build a last-stage-only CP-SAT schedule tight against
        the MCF preemptive LB.

        MCF provides preemptive start times used twice: as the job-release
        map for an initial single-stage dispatch, and indirectly as warm-start
        hints into the CP-SAT model. True release bounds ``r_j`` (sum of
        processing times on stages 1..c-1) are also enforced as domain lower
        bounds in the CP-SAT model via ``job_2_release``.

        Solves under a time budget of ``0.01 * n * c`` seconds. The resulting
        partial schedule (only the last stage is filled) is stored on
        ``self.last_stage_cp_sat_solution`` for downstream subroutines; it is
        NOT registered with the incumbent manager (a partial schedule is not
        a full incumbent).
        """
        start_elapsed = self.timer.elapsed_sec

        mcf = ParallelMachinePreemptionMcf.from_instance(self.instance)
        mcf.solve()
        if not mcf.is_optimal():
            raise RuntimeError(f"MCF not optimal for instance {self.instance.name}")
        mcf_start_map = mcf.get_job_2_start_time_map()
        mcf_lb = float(mcf.get_obj_value())

        last_stage_id = self.instance.stage_id_list[-1]
        r_j_map = self.instance.get_job_2_p_sum_except_last_stage()
        duration_map = self.instance.get_job_2_p_map_for_stage(last_stage_id)
        n = self.instance.job_count
        c = self.instance.stage_count

        params_for_horizon = BaseModelBuilder.make_params(self.instance)
        horizon = sum(params_for_horizon.p.values())

        builder = BaseModelBuilder()
        pm_mdl, pm_params, pm_ops_vars, _pm_obj_vars = builder.build(
            instance=self.instance,
            horizon=horizon,
            last_stage_only=True,
            job_2_release=r_j_map,
            obj_lb=mcf_lb,
        )

        job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
        job_sequence = sorted(
            self.instance.job_id_list,
            key=lambda j: (
                mcf_start_map[j] is None,
                mcf_start_map[j] if mcf_start_map[j] is not None else 0,
                job_2_pos[j],
            ),
        )
        job_2_release_for_dispatch: dict[str, int] = {}
        for j in self.instance.job_id_list:
            mcf_start = mcf_start_map[j]
            job_2_release_for_dispatch[j] = (
                mcf_start if mcf_start is not None else r_j_map[j]
            )

        init_schedule = FFcSchedule(
            jobs=self.instance.job_id_list,
            stages=self.instance.stage_id_list,
            machines_per_stage=self.instance.stage_2_machines_map,
        )
        init_schedule.dispatch_stage_by_jobs(
            last_stage_id,
            job_sequence,
            duration_map,
            job_2_release=job_2_release_for_dispatch,
        )

        BaseModelBuilder.apply_start_hints_from_start_time_map(
            pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_start_time_map()
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_end_time_map()
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(0.01 * n * c)
        solver.parameters.num_search_workers = int(solver_thread_cnt)
        status = solver.Solve(pm_mdl)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        obj_value: float | None = solver.objective_value
        obj_bound: float | None = None
        try:
            obj_bound = float(solver.best_objective_bound)
            self.logger.info(
                "run_last_stage_cp_sat_lb: UB=%d, LB=%d (MCF LB=%d)",
                obj_value,
                obj_bound,
                mcf_lb,
            )
        except Exception:
            obj_bound = None
            self.logger.warning(
                "run_last_stage_cp_sat_lb: UB=%d, CP-SAT LB unknown (MCF LB=%d)",
                obj_value,
                mcf_lb,
            )

        if not has_solution:
            elapsed = self.timer.elapsed_sec - start_elapsed
            self.logger.warning(
                "run_last_stage_cp_sat_lb: no feasible solution (status=%s)",
                solver.StatusName(status),
            )
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=mcf_lb,
            )

        j_i_2_start = {
            (j, last_stage_id): int(
                solver.Value(pm_ops_vars.op_start[j, last_stage_id])
            )
            for j in pm_params.j_list
        }
        j_i_2_end = {
            (j, last_stage_id): int(solver.Value(pm_ops_vars.op_end[j, last_stage_id]))
            for j in pm_params.j_list
        }
        out_schedule = _build_schedule_from_op_starts(
            self.instance, j_i_2_start, j_i_2_end, stages=[last_stage_id]
        )

        cp_obj = float(solver.objective_value)
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=out_schedule, obj_value=cp_obj, obj_bound=mcf_lb
        )

        elapsed = self.timer.elapsed_sec - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=cp_obj,
            obj_bound=obj_bound if obj_bound is not None else mcf_lb,
        )
        return report

    def initialize_by_edd(
        self,
        dispatcher: Literal["mixed", "fam"] = "mixed",
        dispatching_criteria: Literal["weighted_et", "makespan"] = "weighted_et",
    ) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in EDD order.

        With due-date windows ``[d^-_j, d^+_j]``, EDD uses ``d^+_j`` (the
        latest on-time moment) so tight deadlines go first and slack jobs
        drop to the tail. Ties on ``d^+`` break by native ``job_id_list``
        order for determinism, matching the tie-break rule used by
        :meth:`run_mcf_lb`.

        ``dispatcher`` selects the decoder that turns the EDD permutation
        into a schedule: ``"mixed"`` uses :class:`MixedDispatcher` (with
        ``dispatching_criteria`` for its internal selection rule); ``"fam"`` uses
        :class:`FAMDispatcher` and ignores ``dispatching_criteria``.
        """
        start_elapsed = self.timer.elapsed_sec

        due_window_map = self.instance.job_2_due_window_map
        job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
        job_sequence = sorted(
            self.instance.job_id_list,
            key=lambda j: (due_window_map[j][1], job_2_pos[j]),
        )

        if dispatcher == "mixed":
            mixed = MixedDispatcher(self.instance)
            schedule = mixed.get_best_mixed_schedule_by_sequence(
                job_sequence, criteria=dispatching_criteria
            )
            if schedule is None:
                raise RuntimeError(
                    f"MixedDispatcher produced no schedule for {self.instance.name}"
                )
            sum_e, sum_t = compute_window_et(schedule, self.instance)
            obj_value = float(sum_e + sum_t)
        elif dispatcher == "fam":
            spec = AlgSpec(
                instance=self.instance,
                option=FAMOption(job_sequence=tuple(job_sequence)),
                logger=self.logger,
            )
            record = FAMDispatcher().run(spec)
            result = record.result
            if result is None or result.schedule is None:
                raise RuntimeError(
                    f"FAMDispatcher produced no schedule for {self.instance.name}"
                )
            schedule = result.schedule
            obj_value = (
                float(result.obj_value) if result.obj_value is not None else None
            )
        else:
            raise ValueError(
                f"Unknown dispatcher {dispatcher!r}; expected 'mixed' or 'fam'."
            )

        elapsed = self.timer.elapsed_sec - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        self.solution_manager.register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        return report

    def run_profile_fixed_ns(
        self,
        computational_time: float,
        solver_thread_cnt: int = 1,
        profile_fix_by_machine: bool = False,
        machine_precedence_stride: int = 1,
    ) -> SubroutineReport:
        """Step method: warm-start CP-SAT from the incumbent by fixing its
        dispatch profile (precedence arcs derived from the incumbent's
        operation ordering), then solve under a time budget.
        """
        start_elapsed = self.timer.elapsed_sec

        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "run_profile_fixed_ns requires an incumbent schedule; "
                "chain it after a seeding subroutine such as run_mcf_lb."
            )

        instance = self.instance
        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

        builder = BaseModelBuilder()
        mdl, params, op_vars, _et_vars = builder.build(instance, horizon=horizon)

        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            incumbent.schedule,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
        )
        start_map = incumbent.schedule.get_jik_2_start_time_map()
        end_map = incumbent.schedule.get_jik_2_end_time_map()
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, start_map
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, end_map
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(computational_time)
        solver.parameters.num_search_workers = int(solver_thread_cnt)
        status = solver.Solve(mdl)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        obj_bound: float | None = None
        try:
            obj_bound = float(solver.best_objective_bound)
        except Exception:
            obj_bound = None

        if not has_solution:
            elapsed = self.timer.elapsed_sec - start_elapsed
            self.logger.warning(
                "run_profile_fixed_ns: no feasible solution (status=%s)",
                solver.StatusName(status),
            )
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=obj_bound,
            )

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
        schedule = _build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)

        sum_e, sum_t = compute_window_et(schedule, instance)
        obj_value = float(sum_e + sum_t)
        cp_obj = float(solver.objective_value)
        if obj_value != cp_obj:
            self.logger.warning(
                "run_profile_fixed_ns: post-build objective %.3f != CP-SAT "
                "objective %.3f",
                obj_value,
                cp_obj,
            )

        elapsed = self.timer.elapsed_sec - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )
        self.solution_manager.register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=obj_bound),
        )
        return report


def _parse_nc_timelimit(value: float | str | None, n: int, c: int) -> float | None:
    """Parse a timelimit spec into seconds.

    - ``None`` -> ``None`` (no limit).
    - ``float``/``int`` -> seconds as-is.
    - ``"<x>nc"`` -> ``float(x) * n * c`` seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = value.strip()
    if s.endswith("nc"):
        return float(s[:-2]) * n * c
    raise ValueError(
        f"Invalid timelimit spec: {value!r}; expected float or '<x>nc' string"
    )


def _build_schedule_from_op_starts(
    instance: FFcDDWParameters,
    j_i_2_start: dict[tuple[str, str], int],
    j_i_2_end: dict[tuple[str, str], int],
    stages: Sequence[str] | None = None,
) -> FFcSchedule:
    """Greedy interval-graph coloring to assign machines from CP-SAT starts.

    The cumulative constraint at each stage caps concurrent intervals at
    ``|M_i|``, so a free machine is always available at any operation's
    start time. ``stages`` restricts the loop to a subset of stages; other
    stages remain empty in the returned schedule.
    """
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for i in stages if stages is not None else instance.stage_id_list:
        machines = list(instance.stage_2_machines_map[i])
        machine_end: dict[str, int] = {k: 0 for k in machines}
        ordered_jobs = sorted(
            instance.job_id_list,
            key=lambda j: (j_i_2_start[j, i], j_i_2_end[j, i], j),
        )
        for j in ordered_jobs:
            s = j_i_2_start[j, i]
            e = j_i_2_end[j, i]
            picked = next((k for k in machines if machine_end[k] <= s), None)
            if picked is None:
                raise RuntimeError(
                    f"No free machine at stage {i} for job {j} start={s}"
                )
            schedule.add_ops_times_2_mc(i, picked, j, s, e)
            machine_end[picked] = e
    return schedule
