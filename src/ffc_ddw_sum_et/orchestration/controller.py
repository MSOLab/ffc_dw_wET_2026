"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ortools.sat.python import cp_model
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cumulative import BaseModelBuilder
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.mcf_lb import MCFLBDiagnostic
from ffc_ddw_sum_et.algorithm.mcf_lb.phase1_mcf import run_phase1
from ffc_ddw_sum_et.algorithm.mcf_lb.phase2_last_stage import run_phase2
from ffc_ddw_sum_et.algorithm.mcf_lb.phase3_dispatch import run_phase3
from ffc_ddw_sum_et.algorithm.mcf_lb.phase4_profile_fix import run_phase4
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_window_et
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .controller_core import FFcDDWSubroutineControllerCore
from .solution_manager import FFcDDWSolution

__all__ = ["FFcDDWSubroutineController", "MCFLBDiagnostic"]


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

    # TODO: remove; use run_mcf_lb_4 instead
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
        solver_thread_cnt = 1

        # ----- Phase 1: MCF LB + last-stage-only CP-SAT model build -----
        phase1 = run_phase1(instance, diag, logger=self.logger)
        mcf_lb = phase1.mcf_lb
        horizon = phase1.horizon
        self.mcf_preemptive_schedule = phase1.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules = [
            ("1_mcf_preemptive_schedule", phase1.mcf_preemptive_schedule),
            ("2_last_stage_only_init_schedule", phase1.last_stage_only_init_schedule),
        ]

        # ----- Phase 2: last-stage-only CP-SAT warm-start + solve -----
        phase2 = run_phase2(
            phase1,
            instance,
            diag,
            last_stage_only_timelimit=last_stage_only_timelimit,
            solver_thread_cnt=solver_thread_cnt,
            logger=self.logger,
        )
        if phase2 is None:
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
            )

        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=phase2.last_stage_only_schedule,
            obj_value=phase2.last_stage_only_obj,
            obj_bound=mcf_lb,
        )
        self.mcf_lb_phase_schedules.append(
            ("3_last_stage_only_schedule", phase2.last_stage_only_schedule)
        )

        # ----- Phase 3: reverse-dispatch + unflip -----
        phase3 = run_phase3(phase1, phase2, instance, diag, logger=self.logger)
        if phase3 is None:
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
            )
        dispatched_schedule = phase3.dispatched_schedule
        step2_obj = phase3.dispatched_obj
        if phase3.last_stage_only_schedule_flipped is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "4_last_stage_only_schedule_flipped",
                    phase3.last_stage_only_schedule_flipped,
                )
            )
        if phase3.dispatched_schedule_before_unflipping is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "5_dispatched_schedule_before_unflipping",
                    phase3.dispatched_schedule_before_unflipping,
                )
            )
        self.mcf_lb_phase_schedules.append(
            ("6_dispatched_schedule", phase3.dispatched_schedule)
        )
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
        final_schedule = build_schedule_from_op_starts(
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
        self.mcf_lb_phase_schedules.append(("7_final_schedule", final_schedule))

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

    def run_mcf_lb_4(
        self,
        last_stage_only_timelimit: float | str | None = None,
        profile_fix_by_machine: bool = False,
        machine_precedence_stride: int = 1,
    ) -> SubroutineReport:
        """Step method: full MCF-LB pipeline composed of four extracted phases.

        Behavior-equivalent alternative to :meth:`run_mcf_lb`, built as a thin
        wrapper around ``run_phase1`` / ``run_phase2`` / ``run_phase3`` /
        ``run_phase4`` in :mod:`ffc_ddw_sum_et.algorithm.mcf_lb`. Kept
        side-by-side with ``run_mcf_lb`` for parity verification before the
        inline version is retired.
        """
        start_elapsed = self.timer.elapsed_sec
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag
        instance = self.instance
        solver_thread_cnt = 1

        # Phase 1: MCF LB + last-stage-only CP-SAT model build.
        phase1 = run_phase1(instance, diag, logger=self.logger)
        mcf_lb = phase1.mcf_lb
        self.mcf_preemptive_schedule = phase1.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules = [
            ("1_mcf_preemptive_schedule", phase1.mcf_preemptive_schedule),
            ("2_last_stage_only_init_schedule", phase1.last_stage_only_init_schedule),
        ]

        # Phase 2: last-stage-only CP-SAT warm-start + solve.
        phase2 = run_phase2(
            phase1,
            instance,
            diag,
            last_stage_only_timelimit=last_stage_only_timelimit,
            solver_thread_cnt=solver_thread_cnt,
            logger=self.logger,
        )
        if phase2 is None:
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
            )
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=phase2.last_stage_only_schedule,
            obj_value=phase2.last_stage_only_obj,
            obj_bound=mcf_lb,
        )
        self.mcf_lb_phase_schedules.append(
            ("3_last_stage_only_schedule", phase2.last_stage_only_schedule)
        )

        # Phase 3: reverse-dispatch + unflip.
        phase3 = run_phase3(phase1, phase2, instance, diag, logger=self.logger)
        if phase3 is None:
            elapsed = self.timer.elapsed_sec - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=mcf_lb
            )
        if phase3.last_stage_only_schedule_flipped is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "4_last_stage_only_schedule_flipped",
                    phase3.last_stage_only_schedule_flipped,
                )
            )
        if phase3.dispatched_schedule_before_unflipping is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "5_dispatched_schedule_before_unflipping",
                    phase3.dispatched_schedule_before_unflipping,
                )
            )
        self.mcf_lb_phase_schedules.append(
            ("6_dispatched_schedule", phase3.dispatched_schedule)
        )
        self.solution_manager.register(
            SubroutineReport(
                elapsed_time=self.timer.elapsed_sec - start_elapsed,
                obj_value=phase3.dispatched_obj,
                obj_bound=mcf_lb,
            ),
            FFcDDWSolution(
                schedule=phase3.dispatched_schedule,
                obj_value=phase3.dispatched_obj,
                obj_bound=mcf_lb,
            ),
        )

        # Phase 4: profile-fix CP-SAT full solve.
        phase4 = run_phase4(
            phase1,
            phase3,
            instance,
            diag,
            profile_fix_by_machine=profile_fix_by_machine,
            machine_precedence_stride=machine_precedence_stride,
            solver_thread_cnt=solver_thread_cnt,
            logger=self.logger,
        )

        elapsed = self.timer.elapsed_sec - start_elapsed
        if phase4.final_schedule is None:
            # Infeasible profile-fix: keep the phase-3 incumbent, bound upgraded.
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=phase3.dispatched_obj,
                obj_bound=phase4.obj_bound_final,
            )
        self.mcf_lb_phase_schedules.append(("7_final_schedule", phase4.final_schedule))

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=phase4.final_obj,
            obj_bound=phase4.obj_bound_final,
        )
        self.solution_manager.register(
            report,
            FFcDDWSolution(
                schedule=phase4.final_schedule,
                obj_value=phase4.final_obj,
                obj_bound=phase4.obj_bound_final,
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
        out_schedule = build_schedule_from_op_starts(
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
        schedule = build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)

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
