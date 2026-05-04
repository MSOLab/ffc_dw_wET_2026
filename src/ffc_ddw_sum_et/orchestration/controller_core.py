"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Sequence

from routix.dynamic_data_object import DynamicDataObject
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria
from routix.subroutine_controller import SubroutineController

from ..algorithm.base.alg_record import WorkStatus
from ..algorithm.mcf_lb.diagnostic import MCFLBDiagnostic
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
        self._define_states()

    def _define_states(self) -> None:
        """Define all state attributes used across subroutine phases."""
        self.mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None
        self.mcf_lb_diagnostic: MCFLBDiagnostic | None = None
        self.last_stage_only_sol: FFcDDWSolution | None = None
        # `p_increment` value used by the producing step; ``None`` until the
        # step has run. When non-zero, the recorded MCF preemptive schedule
        # / last-stage-only solution belong to an *augmented* problem (last
        # stage processing times inflated by ``p_increment``) and downstream
        # consumers must treat them as such (e.g. MCF LB is not a global LB
        # for the original problem).
        self.mcf_preemptive_sch_p_increment: int | None = None
        self.last_stage_only_sol_p_increment: int | None = None
        # Ordered (name, schedule) pairs per MCF-LB phase, used by the
        # runner to emit numbered progress artifacts (1_mcf_preemptive,
        # 2_last_stage_only_init, ..., 7_final). Only populated entries
        # are appended so early returns retain partial progress.
        self.mcf_lb_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []
        # Outer wall-clock around `run()`. Set in the run() override below;
        # the runner reads this for the per-instance summary `elapsedTime`.
        self.total_elapsed_time: float = 0.0  # TODO: apply to routix

    def is_stopping_condition(self, **kwargs: Any) -> bool:
        """Stop when the timelimit is exceeded."""
        return self.timer.time_over(self.stopping_criteria.timelimit)

    def get_file_path_for_subroutine(self, filename_suffix: str) -> Path:
        """Override: when an `ArtifactLayout` is bound, route per-call-context
        paths into the instance's `progress/` zone instead of the working
        directory. Keeps dynamically-named per-step artifacts (step_log,
        cp-sat solver logs) out of the SSOT-protected `final` zone.
        """
        layout = self._artifact_layout
        if (
            layout is not None
            and self._artifact_scenario_name is not None
            and self._artifact_instance_name is not None
        ):
            filename = self._get_call_context_of_current_method() + filename_suffix
            zone_dir = layout.zone_dir(
                "progress",
                scenario_name=self._artifact_scenario_name,
                instance_name=self._artifact_instance_name,
            )
            return zone_dir / filename
        return super().get_file_path_for_subroutine(filename_suffix)

    def try_get_file_path_for_subroutine(self, suffix: str) -> Path | None:
        """Like ``get_file_path_for_subroutine`` but returns ``None`` instead
        of raising when no working directory is configured.

        Use for optional artifact emission (e.g. ``_step_log.yaml``) that
        should be silently skipped in tests or scripted runs without a
        working directory or layout.
        """
        if self._artifact_layout is None and self._working_dir_path is None:
            return None
        return self.get_file_path_for_subroutine(suffix)

    def run(self) -> None:
        """Wrap routix's run loop with an outer wall-clock measurement."""
        start = time.monotonic()
        try:
            super().run()
        finally:  # TODO: apply to routix
            self.total_elapsed_time = time.monotonic() - start

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
        if last_record.report is None or last_record.solution is None:
            return None
        return WorkStatus.FEASIBLE
