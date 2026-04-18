"""Single-instance runner for FAM experiment orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from routix.runner.single_instance_runner import (
    SingleInstanceRunner,
)
from routix.type_defs import RunMode

from ..parameters.ffc_ddw_params import FFcDueDateWindowParameters
from .controller import FAMSubroutineController
from .solution_manager import FAMSolution

logger = logging.getLogger(__name__)


def _json_default(obj):
    """Handle numpy/pandas types for JSON serialization."""
    import numpy as np

    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceResult:
    """Aggregated result for a single instance run."""

    instance_name: str
    elapsed_time: float
    obj_value: float | None
    obj_bound: float | None
    work_status: str | None
    solution_path: str | None = None
    has_incumbent: bool = False
    method_call_counts: dict[str, int] = field(default_factory=dict)
    report_count: int = 0


class FAMSingleInstanceRunner(
    SingleInstanceRunner[FFcDueDateWindowParameters, FAMSubroutineController]
):
    """Runs FAM on one instance and saves results."""

    def run(self):
        try:
            if self.mode == RunMode.FULL_RUN:
                self.ctrlr = self.get_controller()
                self.ctrlr.set_working_dir(self.working_dir)
                self.ctrlr.run()
        except Exception:
            logger.exception("Error running instance %s", self.ins_name)
        finally:
            return self.post_run_process()

    def get_controller(self) -> FAMSubroutineController:
        return FAMSubroutineController(
            instance=self.instance,
            subroutine_flow=self.subroutine_flow,
            stopping_criteria=self.stopping_criteria,
        )

    def post_run_process(self) -> InstanceResult:
        try:
            return self._post_run_process_inner()
        except Exception:
            logger.exception("Error in post_run_process for %s", self.ins_name)
            return InstanceResult(
                instance_name=self.ins_name,
                elapsed_time=0.0,
                obj_value=None,
                obj_bound=None,
                work_status=None,
            )

    def _post_run_process_inner(self) -> InstanceResult:
        controller = self.ctrlr
        solution_manager = controller.solution_manager

        last_report = solution_manager.get_last_report()
        incumbent = solution_manager.get_incumbent()

        elapsed_time = float(last_report.elapsed_time) if last_report else 0.0
        obj_value = (
            float(incumbent.obj_value)
            if incumbent and incumbent.obj_value is not None
            else (
                float(last_report.obj_value)
                if last_report and last_report.obj_value is not None
                else None
            )
        )
        obj_bound = (
            float(incumbent.obj_bound)
            if incumbent and incumbent.obj_bound is not None
            else (
                float(last_report.obj_bound)
                if last_report and last_report.obj_bound is not None
                else None
            )
        )

        # Save best solution
        solution_path = None
        if incumbent is not None and self.working_dir is not None:
            try:
                solution_path = self._save_solution(incumbent)
            except Exception:
                logger.exception("Error saving solution for %s", self.ins_name)

        # Save objective log
        if self.working_dir is not None and last_report is not None:
            try:
                self._save_obj_log(solution_manager.history)
            except Exception:
                logger.exception("Error saving obj_log for %s", self.ins_name)

        return InstanceResult(
            instance_name=self.ins_name,
            elapsed_time=elapsed_time,
            obj_value=obj_value,
            obj_bound=obj_bound,
            work_status=controller.work_status.value
            if controller.work_status
            else None,
            solution_path=solution_path,
            has_incumbent=incumbent is not None,
            method_call_counts={
                k: int(v) for k, v in controller.method_call_counts.items()
            },
            report_count=len(solution_manager.history),
        )

    def _save_solution(self, solution: FAMSolution) -> str:
        """Save best solution as JSON."""
        schedule = solution.schedule
        data = {
            "instance_name": self.ins_name,
            "obj_value": solution.obj_value,
            "obj_bound": solution.obj_bound,
            "jobs": list(schedule.jobs),
            "stages": list(schedule.stages),
            "machines_per_stage": {
                stage: list(mcs) for stage, mcs in schedule.machines_per_stage.items()
            },
            "operations": self._extract_operations(schedule),
        }
        path = self.working_dir / f"{self.ins_name}_solution.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=_json_default)
        return str(path)

    def _extract_operations(self, schedule) -> list[dict]:
        """Extract operation-level data from schedule for JSON serialization."""
        start_map = schedule.get_jik_2_start_time_map()
        end_map = schedule.get_jik_2_end_time_map()
        operations = []
        for (job_id, stage_id, mc_id), start in sorted(start_map.items()):
            operations.append(
                {
                    "stage": stage_id,
                    "machine": mc_id,
                    "job": job_id,
                    "start": start,
                    "end": end_map.get((job_id, stage_id, mc_id)),
                }
            )
        return operations

    def _save_obj_log(self, history) -> None:
        """Save objective value trajectory as YAML."""
        from routix.io import dump_yaml

        entries = []
        for i, record in enumerate(history):
            if record.report is not None:
                entries.append(
                    {
                        "step": i,
                        "elapsed_time": float(record.report.elapsed_time),
                        "obj_value": float(record.report.obj_value)
                        if record.report.obj_value is not None
                        else None,
                        "obj_bound": float(record.report.obj_bound)
                        if record.report.obj_bound is not None
                        else None,
                    }
                )
        if entries:
            dump_yaml(entries, self.working_dir / f"{self.ins_name}_obj_log.yaml")
