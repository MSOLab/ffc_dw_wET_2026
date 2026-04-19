"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
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
