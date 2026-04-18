"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from routix.dynamic_data_object import DynamicDataObject
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria
from routix.subroutine_controller import SubroutineController

from ..algorithm.fam import FAMDispatcher, FAMOption
from ..algorithm.base.alg_record import WorkStatus
from ..algorithm.base.alg_spec import AlgSpec
from ..parameters.ffc_ddw_params import FFcDueDateWindowParameters
from .solution_manager import FAMSolution, FAMSolutionManager


def _to_ddo(data: Any) -> Any:
    """Convert raw dicts/lists from YAML to DynamicDataObject."""
    if isinstance(data, dict):
        return DynamicDataObject(data)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [
            DynamicDataObject(item) if isinstance(item, dict) else item for item in data
        ]
    return data


class FAMSubroutineController(SubroutineController[StoppingCriteria, SubroutineReport]):
    """Runs the FAM decoder as a routix subroutine controller."""

    def __init__(
        self,
        instance: FFcDueDateWindowParameters,
        subroutine_flow: Sequence[DynamicDataObject]
        | DynamicDataObject
        | Sequence[dict]
        | dict,
        stopping_criteria: StoppingCriteria | dict,
    ):
        self._instance_name = instance.name
        converted_flow = _to_ddo(subroutine_flow)
        if isinstance(stopping_criteria, dict):
            converted_stopping = StoppingCriteria(stopping_criteria)
        else:
            converted_stopping = stopping_criteria
        super().__init__(
            name=instance.name,
            subroutine_flow=converted_flow,
            stopping_criteria=converted_stopping,
        )
        self.instance = instance
        self.solution_manager = FAMSolutionManager()

    def run_fam(self, job_sequence: str | None = None) -> SubroutineReport:
        """Step method: run FAMDispatcher and return a SubroutineReport."""
        start_elapsed = self.timer.elapsed_sec

        if job_sequence is not None:
            option = FAMOption(job_sequence=(job_sequence,))
        else:
            option = FAMOption()

        spec = AlgSpec(
            instance=self.instance,
            option=option,
            logger=logging.getLogger(f"ffc_ddw_sum_et.{self._instance_name}"),
        )

        record = FAMDispatcher().run(spec)
        elapsed = self.timer.elapsed_sec - start_elapsed

        result = record.result
        obj_value = result.obj_value if result else None
        obj_bound = result.obj_bound if result else None

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )

        if result is not None and result.schedule is not None:
            fam_solution = FAMSolution(
                schedule=result.schedule,
                obj_value=obj_value,
                obj_bound=obj_bound,
            )
            self.solution_manager.register(report, fam_solution)

        return report

    def is_stopping_condition(self, **kwargs: Any) -> bool:
        """Stop when the timelimit is exceeded."""
        return self.timer.time_over(self.stopping_criteria.timelimit)

    def post_run_process(self) -> None:
        """Nothing to do at the controller level — the runner handles file I/O."""

    @property
    def best_solution(self) -> FAMSolution | None:
        return self.solution_manager.get_incumbent()

    @property
    def best_obj_value(self) -> float | None:
        return self.solution_manager.best_obj_value

    @property
    def work_status(self) -> WorkStatus | None:
        if not self.solution_manager.history:
            return None
        last_record = self.solution_manager.history[-1]
        if last_record.report is None:
            return None
        return WorkStatus.FEASIBLE
