"""Single-instance runner for FAM experiment orchestration."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any

from routix.io import dump_yaml, load_yaml
from routix.report import SubroutineReportStatistics
from routix.runner.single_instance_runner import (
    SingleInstanceRunner,
)
from routix.type_defs import RunMode

from ..logging_setup import get_logging_args, setup_logging
from ..io import dump_preemptive_schedule_yaml, dump_schedule_yaml
from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from .controller import FFcDDWSubroutineController
from .solution_manager import FFcDDWSolution

logger = logging.getLogger(__name__)

_MANIFEST_SCHEMA_VERSION = 1


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
    first_obj_value: float | None = None
    first_obj_bound: float | None = None
    error: str | None = None
    job_count: int | None = None
    stage_count: int | None = None
    machines_per_stage: int | None = None
    timelimit: float | None = None
    mcf_lb_diagnostic: dict[str, Any] | None = None
    makespan: float | None = None


def _to_serializable(value: Any) -> Any:
    """Recursively coerce enums to ``.value`` for safe YAML dump."""
    import enum

    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_serializable(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_serializable(v) for v in value)
    return value


class FFcDDWSingleInstanceRunner(
    SingleInstanceRunner[FFcDDWParameters, FFcDDWSubroutineController]
):
    """Runs on one FFcDDW instance and saves results."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._setup_logging_args = kwargs.pop("setup_logging_args", None)
        super().__init__(*args, **kwargs)
        if kwargs.get("logger") is None:
            self.logger = logging.getLogger(
                "ffc_ddw_sum_et.orchestration.FFcDDWSingleInstanceRunner."
                f"{self.ins_name}"
            )

    def run(self):
        self._run_error: str | None = None
        previous_logging_args = get_logging_args()
        restore_logging = False

        if self.mode == RunMode.FULL_RUN and self._setup_logging_args is not None:
            _, quiet, verbose = self._setup_logging_args
            setup_logging(
                self.working_dir / f"{self.ins_name}_solve.log",
                quiet,
                verbose,
            )
            restore_logging = True

        try:
            if self.mode == RunMode.FULL_RUN:
                self.ctrlr = self.get_controller()
                self.ctrlr.set_working_dir(self.working_dir)
                self.ctrlr.run()
        except Exception:
            self._run_error = traceback.format_exc()
            self.logger.exception("Error running instance %s", self.ins_name)
        finally:
            try:
                return self.post_run_process()
            finally:
                if restore_logging:
                    setup_logging(*previous_logging_args)

    def get_controller(self) -> FFcDDWSubroutineController:
        return FFcDDWSubroutineController(
            instance=self.instance,
            subroutine_flow=self.subroutine_flow,
            stopping_criteria=self.stopping_criteria,
        )

    def post_run_process(self) -> InstanceResult:
        try:
            if self.mode == RunMode.FULL_RUN and getattr(self, "ctrlr", None):
                return self._persist_run_artifacts(self.ctrlr)
            return self._load_instance_result()
        except Exception:
            post_error = traceback.format_exc()
            self.logger.exception("Error in post_run_process for %s", self.ins_name)
            combined_error = (
                f"{self._run_error}\n---\npost_run_process:\n{post_error}"
                if getattr(self, "_run_error", None)
                else post_error
            )
            return InstanceResult(
                instance_name=self.ins_name,
                elapsed_time=0.0,
                obj_value=None,
                obj_bound=None,
                work_status=None,
                error=combined_error,
            )

    def _persist_run_artifacts(
        self, controller: FFcDDWSubroutineController
    ) -> InstanceResult:
        """Write every per-instance file then build + atomically write the
        manifest. Returns the same ``InstanceResult`` that's saved to disk.
        """
        solution_manager = controller.solution_manager

        first_report = (
            solution_manager.history[0].report if solution_manager.history else None
        )
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
        first_obj_value = (
            float(first_report.obj_value)
            if first_report and first_report.obj_value is not None
            else None
        )
        first_obj_bound = (
            float(first_report.obj_bound)
            if first_report and first_report.obj_bound is not None
            else None
        )

        solution_path = None
        if incumbent is not None and self.working_dir is not None:
            try:
                solution_path = self._save_solution(incumbent)
            except Exception:
                self.logger.exception("Error saving solution for %s", self.ins_name)
            try:
                dump_schedule_yaml(
                    incumbent.schedule,
                    self.working_dir / f"{self.ins_name}_schedule.yaml",
                    instance_name=self.ins_name,
                    obj_value=incumbent.obj_value,
                    obj_bound=incumbent.obj_bound,
                )
            except Exception:
                self.logger.exception(
                    "Error saving schedule yaml for %s", self.ins_name
                )

        last_stage_cp_sat = getattr(controller, "last_stage_cp_sat_solution", None)
        if last_stage_cp_sat is not None and self.working_dir is not None:
            try:
                dump_schedule_yaml(
                    last_stage_cp_sat.schedule,
                    self.working_dir
                    / f"{self.ins_name}_last_stage_cp_sat_schedule.yaml",
                    instance_name=f"{self.ins_name}_last_stage_cp_sat",
                    obj_value=last_stage_cp_sat.obj_value,
                    obj_bound=last_stage_cp_sat.obj_bound,
                )
            except Exception:
                self.logger.exception(
                    "Error saving last_stage_cp_sat schedule yaml for %s",
                    self.ins_name,
                )

        phase_schedules = getattr(controller, "mcf_lb_phase_schedules", None) or []
        if phase_schedules and self.working_dir is not None:
            for name, sched in phase_schedules:
                if sched is None:
                    continue
                yaml_path = self.working_dir / f"{self.ins_name}_{name}.yaml"
                try:
                    if isinstance(sched, MCFPreemptiveSchedule):
                        dump_preemptive_schedule_yaml(
                            yaml_path,
                            instance_name=f"{self.ins_name}_{name}",
                            stage_id=sched.stage_id,
                            machines=sched.machines,
                            jobs=self.instance.job_id_list,
                            segments=sched.to_gantt_segments(),
                            all_jobs=self.instance.job_id_list,
                        )
                    else:
                        dump_schedule_yaml(
                            sched,
                            yaml_path,
                            instance_name=f"{self.ins_name}_{name}",
                        )
                except Exception:
                    self.logger.exception(
                        "Error saving %s yaml for %s", name, self.ins_name
                    )

        diag = getattr(controller, "mcf_lb_diagnostic", None)
        diag_dict: dict[str, Any] | None = asdict(diag) if diag is not None else None
        if diag_dict is not None and self.working_dir is not None:
            try:
                dump_yaml(
                    diag_dict,
                    self.working_dir / f"{self.ins_name}_mcf_lb_diagnostic.yaml",
                )
            except Exception:
                self.logger.exception(
                    "Error saving mcf_lb_diagnostic yaml for %s", self.ins_name
                )

        makespan = int(incumbent.schedule.makespan) if incumbent is not None else None

        if self.working_dir is not None and last_report is not None:
            try:
                self._save_obj_log(solution_manager.history)
            except Exception:
                self.logger.exception("Error saving obj_log for %s", self.ins_name)
            try:
                self._save_statistics(
                    controller.method_call_counts, solution_manager.history
                )
            except Exception:
                self.logger.exception("Error saving statistics for %s", self.ins_name)

        machines = self.instance.stage_2_machines_map
        first_stage = (
            self.instance.stage_id_list[0] if self.instance.stage_id_list else None
        )
        mps = len(machines[first_stage]) if first_stage is not None else 0
        stopping = getattr(self, "stopping_criteria", None) or {}
        timelimit = float(stopping["timelimit"]) if "timelimit" in stopping else None

        result = InstanceResult(
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
            first_obj_value=first_obj_value,
            first_obj_bound=first_obj_bound,
            error=getattr(self, "_run_error", None),
            job_count=len(self.instance.job_id_list),
            stage_count=len(self.instance.stage_id_list),
            machines_per_stage=mps,
            timelimit=timelimit,
            mcf_lb_diagnostic=diag_dict,
            makespan=makespan,
        )

        if self.working_dir is not None:
            try:
                self._write_instance_result_manifest(result)
            except Exception:
                self.logger.exception(
                    "Error writing instance_result manifest for %s", self.ins_name
                )

        return result

    def _write_instance_result_manifest(self, result: InstanceResult) -> None:
        """Atomic-write ``<ins_name>_instance_result.yaml``.

        Written last in ``_persist_run_artifacts`` so its presence implies
        every other per-instance artifact has been written.
        """
        payload = {
            "_schema_version": _MANIFEST_SCHEMA_VERSION,
            **_to_serializable(asdict(result)),
        }
        final = self.working_dir / f"{self.ins_name}_instance_result.yaml"
        tmp = final.with_suffix(".yaml.tmp")
        dump_yaml(payload, tmp)
        os.replace(tmp, final)

    def _load_instance_result(self) -> InstanceResult:
        """Load manifest and project to current ``InstanceResult`` schema."""
        if self.working_dir is None:
            raise RuntimeError("working_dir is None")
        path = self.working_dir / f"{self.ins_name}_instance_result.yaml"
        raw = load_yaml(path)
        if not isinstance(raw, dict):
            raise RuntimeError(f"manifest is not a mapping: {path}")
        raw.pop("_schema_version", None)
        valid = {f.name for f in dataclasses.fields(InstanceResult)}
        projected = {k: v for k, v in raw.items() if k in valid}
        return InstanceResult(**projected)

    def _save_solution(self, solution: FFcDDWSolution) -> str:
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

    def _save_statistics(self, method_call_counts, history) -> None:
        """Save per-instance subroutine-flow statistics as JSON and YAML.

        Aggregates the per-step ``SubroutineReport`` entries from this instance's
        trajectory. ``SubroutineReportStatistics`` is designed for a single
        instance's history; using one object per instance keeps
        ``improvementRatio`` semantically correct (best vs first in THIS instance).
        """
        reports = [r.report for r in history if r.report is not None]
        if not reports:
            return
        stats = SubroutineReportStatistics(
            name=self.ins_name,
            reports=reports,
            method_call_counts=dict(method_call_counts),
        )
        stats.to_yaml(
            self.working_dir / f"{self.ins_name}_statistics.yaml", is_maximize=False
        )
        stats.to_json(
            self.working_dir / f"{self.ins_name}_statistics.json", is_maximize=False
        )

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
