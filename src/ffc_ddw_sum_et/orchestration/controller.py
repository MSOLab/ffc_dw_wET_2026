"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ortools.sat.python import cp_model
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cumulative import (
    BaseModelBuilder,
    PFMethod,
    decode_pf_method,
)
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.mcf_lb import MCFLBDiagnostic
from ffc_ddw_sum_et.algorithm.mcf_lb.phase1_mcf import SeedTag, run_phase1
from ffc_ddw_sum_et.algorithm.mcf_lb.phase2_last_stage import run_phase2
from ffc_ddw_sum_et.algorithm.mcf_lb.phase3_dispatch import run_phase3
from ffc_ddw_sum_et.algorithm.mcf_lb.phase4_profile_fix import run_phase4
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .controller_core import FFcDDWSubroutineControllerCore
from .solution_manager import FFcDDWSolution

__all__ = ["FFcDDWSubroutineController", "MCFLBDiagnostic"]


def _resolve_cp_tl(
    tl_raw: float | str | None,
    job_count: int,
    stage_count: int,
) -> float | None:
    """Resolve a raw CP-SAT time-limit value to ``float | None``.

    * ``None``  → ``None`` (no limit)
    * ``float`` → used as-is (seconds)
    * ``str`` ending with ``"nc"`` with a numeric prefix → ``number * job_count * stage_count``
    * other ``str`` → ``float(value)``; raises ``ValueError`` if the cast fails
    """
    if tl_raw is None:
        return None
    if isinstance(tl_raw, (int, float)):
        return float(tl_raw)
    # str branch
    s = tl_raw.strip()
    if s.endswith("nc"):
        prefix = s[:-2]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{tl_raw}' ends with 'nc' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * job_count * stage_count
    try:
        return float(s)
    except ValueError:
        raise ValueError(
            f"cp_tl string '{tl_raw}' cannot be interpreted as a float "
            "and does not match the '<number>nc' pattern"
        )


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

    def run_mcf_lb_4(
        self,
        last_stage_only_priority_tags: Sequence[SeedTag] | None = None,
        last_stage_only_cp_pf_method: PFMethod | None = None,
        last_stage_only_cp_solver_thread_cnt: int = 1,
        last_stage_only_cp_tl: float | str | None = None,
        repeat_last_stage_only_cp_while_improving: bool = False,
        log_last_stage_only_cp_search_progress: bool = False,
        machine_then_job: bool = False,
        full_cp_pf_method: PFMethod | None = None,
        full_cp_solver_thread_cnt: int = 1,
        full_cp_tl: float | str | None = None,
        repeat_full_cp_while_improving: bool = False,
        log_full_cp_search_progress: bool = False,
    ) -> SubroutineReport:
        """Run the 4-phase MCF-LB algorithm and register the best incumbent.

        Phase 1 solves the MCF relaxation and dispatches one last-stage seed
        per priority map. Phase 2 runs a CP-SAT last-stage-only solve for each
        seed and picks the best. Phase 3 reverse-dispatches the best last-stage
        solution to a full schedule. Phase 4 runs a full CP-SAT profile-fix
        solve warm-started from the Phase 3 incumbent.

        Args:
            last_stage_only_priority_tags: Priority tags used in Phase 1 to
                generate dispatch seeds. ``None`` uses all available tags.
            last_stage_only_cp_pf_method: Profile-fix precedence policy for the
                Phase 2 last-stage CP-SAT solve. ``None`` (default) skips the
                precedence-arc pass entirely while keeping warm-start / ET
                hints. Previously the implicit default was ``"PF0"``
                (stage-level time-based selection); set explicitly to restore
                that behaviour.
            full_cp_pf_method: Same policy for the Phase 4 full CP-SAT solve.
                Same ``None`` / ``"PF0"`` distinction applies.
            full_cp_solver_thread_cnt: Number of CP-SAT solver threads for the
                Phase 4 full CP-SAT solve.
            repeat_last_stage_only_cp_while_improving: If ``True``, Phase 2
                re-solves with the updated profile until no improvement.
            repeat_full_cp_while_improving: If ``True``, Phase 4 re-solves
                with the updated profile until no improvement.
            machine_then_job: Passed to Phase 3 reverse-dispatch ordering.
            last_stage_only_cp_tl: Per-solve time limit (seconds) for the
                Phase 2 last-stage-only CP-SAT model. Accepts a ``float``,
                a ``"<n>nc"`` string (resolves to ``n * job_count *
                stage_count``), or ``None`` for no limit.
            full_cp_tl: Same for the Phase 4 full CP-SAT model.

        Returns:
            SubroutineReport with ``obj_bound`` = MCF LB and ``obj_value`` =
            Phase 4 objective (or Phase 3 dispatched objective if Phase 4 is
            infeasible).
        """
        start_elapsed = self.timer.elapsed_sec
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag
        instance = self.instance
        last_stage_only_cp_tl_seconds = _resolve_cp_tl(
            last_stage_only_cp_tl, instance.job_count, instance.stage_count
        )
        full_cp_tl_seconds = _resolve_cp_tl(
            full_cp_tl, instance.job_count, instance.stage_count
        )

        # Phase 1: MCF LB + one last-stage dispatch seed per MCF priority map.
        phase1 = run_phase1(
            instance,
            diag,
            logger=self.logger,
            last_stage_only_priority_tags=last_stage_only_priority_tags,
        )
        mcf_lb = phase1.mcf_lb
        self.mcf_preemptive_schedule = phase1.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules.clear()
        self.mcf_lb_phase_schedules.append(
            ("1_mcf_preemptive_schedule", phase1.mcf_preemptive_schedule)
        )
        for seed in phase1.last_stage_seeds:
            self.mcf_lb_phase_schedules.append(
                (f"2_last_stage_only_init_schedule__{seed.tag}", seed.init_schedule)
            )

        # Phase 2: solve the last-stage CP-SAT model for each seed, pick best.
        self.logger.info(
            "Phase 1 MCF LB: %d; preparing Phase 2 last-stage-only CP-SAT solves "
            "with time limit %.2f seconds",
            int(mcf_lb),
            last_stage_only_cp_tl_seconds,
        )
        phase2 = run_phase2(
            phase1,
            instance,
            diag,
            logger=self.logger,
            pf_method=last_stage_only_cp_pf_method,
            solver_thread_cnt=last_stage_only_cp_solver_thread_cnt,
            repeat_pf_cp_while_improving=repeat_last_stage_only_cp_while_improving,
            cp_tl_seconds=last_stage_only_cp_tl_seconds,
            log_search_progress=log_last_stage_only_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
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
        for candidate in phase2.candidates:
            self.mcf_lb_phase_schedules.append(
                (
                    f"3_last_stage_only_schedule__{candidate.tag}",
                    candidate.last_stage_only_schedule,
                )
            )
        self.mcf_lb_phase_schedules.append(
            ("3_last_stage_only_schedule_chosen", phase2.last_stage_only_schedule)
        )

        # Phase 3: reverse-dispatch + unflip.
        phase3 = run_phase3(
            phase1,
            phase2,
            instance,
            diag,
            logger=self.logger,
            machine_then_job=machine_then_job,
        )
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
        self.logger.info(
            "Phase 3 dispatched objective: %d; preparing Phase 4 full CP-SAT solve with time limit %.2f seconds",
            int(phase3.dispatched_obj),
            full_cp_tl_seconds,
        )
        phase4 = run_phase4(
            phase1,
            phase3,
            instance,
            diag,
            pf_method=full_cp_pf_method,
            solver_thread_cnt=full_cp_solver_thread_cnt,
            logger=self.logger,
            repeat_pf_cp_while_improving=repeat_full_cp_while_improving,
            cp_tl_seconds=full_cp_tl_seconds,
            log_search_progress=log_full_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
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
        pm_mdl, pm_params, pm_ops_vars, pm_et_vars = builder.build(
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
        BaseModelBuilder.apply_et_hints_from_ref_schedule(
            pm_mdl, pm_params, pm_et_vars, init_schedule
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(0.01 * n * c)
        solver.parameters.num_search_workers = int(solver_thread_cnt)
        status = solver.Solve(pm_mdl)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        obj_value: float | None = solver.objective_value
        obj_bound: float | None = None
        # is a valid global LB since no profile-fixing is applied in this model
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
            sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
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
        pf_method: PFMethod = "PF0",
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
        mdl, params, op_vars, et_vars = builder.build(instance, horizon=horizon)

        by_machine, stride = decode_pf_method(pf_method)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            incumbent.schedule,
            profile_fix_by_machine=by_machine,
            machine_precedence_stride=stride,
        )
        start_map = incumbent.schedule.get_jik_2_start_time_map()
        end_map = incumbent.schedule.get_jik_2_end_time_map()
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, start_map
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, end_map
        )
        BaseModelBuilder.apply_et_hints_from_ref_schedule(
            mdl, params, et_vars, incumbent.schedule
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

        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
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
