"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf

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
        """Step method: compute preemptive last-stage LB via min-cost flow.

        Solves the Pm | r_j, pmtn | sum C_{jt} x_{jt} relaxation as a min-cost
        flow problem; the optimal cost is a valid lower bound on the original
        FFcDDW objective.
        """
        start_elapsed = self.timer.elapsed_sec

        mcf = ParallelMachinePreemptionMcf.from_instance(self.instance)
        mcf.solve()
        if not mcf.is_optimal():
            raise RuntimeError(f"MCF not optimal for instance {self.instance.name}")

        elapsed = self.timer.elapsed_sec - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=None,
            obj_bound=float(mcf.get_obj_value()),
        )
        self.solution_manager.register(report, None)
        return report
