"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from routix.dynamic_data_object import DynamicDataObject
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria
from routix.subroutine_controller import SubroutineController

from ..algorithm.base.alg_record import WorkStatus
from ..parameters.ffc_ddw_params import FFcDDWParameters
from .solution_manager import FFcDDWSolution, FFcDDWSolutionManager


def _to_ddo(data: Any) -> Any:
    """Convert raw dicts/lists from YAML to DynamicDataObject."""
    if isinstance(data, dict):
        return DynamicDataObject(data)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [
            DynamicDataObject(item) if isinstance(item, dict) else item for item in data
        ]
    return data


class FFcDDWSubroutineControllerCore(
    SubroutineController[StoppingCriteria, SubroutineReport]
):
    """Routix subroutine controller for Flexible Flow Shop with Due Date Windows"""

    def __init__(
        self,
        instance: FFcDDWParameters,
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
        self.solution_manager = FFcDDWSolutionManager()

    def is_stopping_condition(self, **kwargs: Any) -> bool:
        """Stop when the timelimit is exceeded."""
        return self.timer.time_over(self.stopping_criteria.timelimit)

    def post_run_process(self) -> None:
        """Nothing to do at the controller level — the runner handles file I/O."""

    @property
    def best_solution(self) -> FFcDDWSolution | None:
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
