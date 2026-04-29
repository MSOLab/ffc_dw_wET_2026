"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from routix.dynamic_data_object import DynamicDataObject
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria
from routix.subroutine_controller import SubroutineController

from ..algorithm.base.alg_record import WorkStatus
from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from ..solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from .solution_manager import FFcDDWSolution, FFcDDWSolutionManager

MCFLBPhaseSchedule = FFcSchedule | MCFPreemptiveSchedule


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
        self.logger = logging.getLogger(
            f"ffc_ddw_sum_et.orchestration.controller.{self._instance_name}"
        )
        converted_flow = _to_ddo(subroutine_flow)
        if isinstance(stopping_criteria, dict):
            converted_stopping = StoppingCriteria(stopping_criteria)
        else:
            converted_stopping = stopping_criteria
        super().__init__(
            name=instance.name,
            subroutine_flow=converted_flow,
            stopping_criteria=converted_stopping,
            logger=self.logger,
        )
        self.instance = instance
        self.solution_manager = FFcDDWSolutionManager()
        self.last_stage_cp_sat_solution: FFcDDWSolution | None = None
        self.mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None
        # Ordered (name, schedule) pairs per MCF-LB phase, used by the
        # runner to emit numbered progress artifacts (1_mcf_preemptive,
        # 2_last_stage_only_init, ..., 7_final). Only populated entries
        # are appended so early returns retain partial progress.
        self.mcf_lb_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []

    def is_stopping_condition(self, **kwargs: Any) -> bool:
        """Stop when the timelimit is exceeded."""
        return self.timer.time_over(self.stopping_criteria.timelimit)

    def try_get_file_path_for_subroutine(self, suffix: str) -> Path | None:
        """Like ``get_file_path_for_subroutine`` but returns ``None`` instead
        of raising when no working directory is configured.

        Use for optional artifact emission (e.g. ``_step_log.yaml``) that
        should be silently skipped in tests or scripted runs without a
        working directory.
        """
        if self._working_dir_path is None:
            return None
        return self.get_file_path_for_subroutine(suffix)

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
