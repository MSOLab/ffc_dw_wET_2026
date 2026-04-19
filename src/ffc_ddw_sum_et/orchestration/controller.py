"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence

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
            logger=logging.getLogger(f"ffc_ddw_sum_et.{self._instance_name}"),
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

    def run_mcf_lb(self) -> SubroutineReport:
        """Step method: compute preemptive last-stage LB via min-cost flow,
        then seed an incumbent schedule by dispatching jobs in order of
        ascending MCF start time.
        """
        start_elapsed = self.timer.elapsed_sec

        mcf = ParallelMachinePreemptionMcf.from_instance(self.instance)
        mcf.solve()
        if not mcf.is_optimal():
            raise RuntimeError(f"MCF not optimal for instance {self.instance.name}")

        start_map = mcf.get_job_2_start_time_map()
        job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
        # Jobs with no flow fall to the tail in native order; ties on start
        # time break by native order so the sort is deterministic.
        job_sequence = sorted(
            self.instance.job_id_list,
            key=lambda j: (
                start_map[j] is None,
                start_map[j] if start_map[j] is not None else 0,
                job_2_pos[j],
            ),
        )

        dispatcher = MixedDispatcher(self.instance)
        schedule = dispatcher.get_best_mixed_schedule_by_sequence(job_sequence)
        if schedule is None:
            raise RuntimeError(
                f"MixedDispatcher produced no schedule for {self.instance.name}"
            )

        sum_e, sum_t = compute_window_et(schedule, self.instance)
        obj_value = float(sum_e + sum_t)
        obj_bound = float(mcf.get_obj_value())

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
        logger = logging.getLogger(f"ffc_ddw_sum_et.{self._instance_name}")

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
            obj_bound = float(solver.BestObjectiveBound())
        except Exception:
            obj_bound = None

        if not has_solution:
            elapsed = self.timer.elapsed_sec - start_elapsed
            logger.warning(
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
        cp_obj = float(solver.ObjectiveValue())
        if obj_value != cp_obj:
            logger.warning(
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


def _build_schedule_from_op_starts(
    instance: FFcDDWParameters,
    j_i_2_start: dict[tuple[str, str], int],
    j_i_2_end: dict[tuple[str, str], int],
) -> FFcSchedule:
    """Greedy interval-graph coloring to assign machines from CP-SAT starts.

    The cumulative constraint at each stage caps concurrent intervals at
    ``|M_i|``, so a free machine is always available at any operation's
    start time.
    """
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for i in instance.stage_id_list:
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
