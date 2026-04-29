"""Single-instance runner for FAM experiment orchestration."""

from __future__ import annotations

import dataclasses
import logging
import os
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any

from routix.io import ArtifactLayout, dump_yaml, load_yaml
from routix.logging import (
    PrefixLevelFilter,
    attach_fh_to_logger,
    detach_fh_from_logger,
)
from routix.runner.single_instance_runner import (
    SingleInstanceRunner,
)
from routix.type_defs import RunMode

from ..io import dump_preemptive_schedule_yaml, dump_schedule_yaml, dump_solution_json
from ..logging_setup import get_logging_args, setup_logging
from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from .controller import FFcDDWSubroutineController
from .solution_manager import FFcDDWSolution

logger = logging.getLogger(__name__)

_MANIFEST_SCHEMA_VERSION = 1
_SC_LOGGER_PREFIX = "ffc_ddw_sum_et.orchestration.controller"


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
        self._scenario_name: str | None = kwargs.pop("scenario_name", None)
        super().__init__(*args, **kwargs)
        if kwargs.get("logger") is None:
            self.logger = logging.getLogger(
                "ffc_ddw_sum_et.orchestration.FFcDDWSingleInstanceRunner."
                f"{self.ins_name}"
            )
        if self.layout is None:
            raise ValueError(
                "FFcDDWSingleInstanceRunner requires a non-None ArtifactLayout. "
                "It is forwarded by FFcDDWMultiInstanceRunner."
            )
        if self._scenario_name is None:
            raise ValueError(
                "FFcDDWSingleInstanceRunner requires a scenario_name. "
                "It is forwarded by FFcDDWMultiInstanceRunner."
            )

    def _init_working_dir(self) -> None:
        """Resolve working_dir through the layout when one is bound, else fall
        back to the routix base behavior.

        The layout call is gated so that test fixtures constructing a runner
        directly without a layout can still rely on the legacy
        `output_dir / ins_name` directory.
        """
        if getattr(self, "layout", None) is None or self._scenario_name is None:
            super()._init_working_dir()
            return
        layout: ArtifactLayout = self.layout
        self.working_dir = layout.instance_dir(self._scenario_name, self.ins_name)

    def run(self):
        self._run_error: str | None = None
        previous_logging_args = get_logging_args()
        restore_logging = False
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        sc_logger_name = f"{_SC_LOGGER_PREFIX}.{self.ins_name}"

        if self.mode == RunMode.FULL_RUN and self._setup_logging_args is not None:
            _, quiet, verbose = self._setup_logging_args
            sir_log_path = layout.log_path(
                "single_instance_runner",
                scenario_name=self._scenario_name,
                instance_name=self.ins_name,
            )
            setup_logging(sir_log_path, quiet, verbose)
            self._attach_sc_filter_to_root()
            restore_logging = True

        try:
            if self.mode == RunMode.FULL_RUN:
                sc_log_path = layout.log_path(
                    "subroutine_controller",
                    scenario_name=self._scenario_name,
                    instance_name=self.ins_name,
                )
                attach_fh_to_logger(sc_logger_name, sc_log_path)
                try:
                    self.ctrlr = self.get_controller()
                    self.ctrlr.set_artifact_layout(
                        layout,
                        scenario_name=self._scenario_name,
                        instance_name=self.ins_name,
                    )
                    self.ctrlr.set_working_dir(self.working_dir)
                    self.ctrlr.run()
                finally:
                    detach_fh_from_logger(sc_logger_name)
        except Exception:
            self._run_error = traceback.format_exc()
            self.logger.exception("Error running instance %s", self.ins_name)
        finally:
            try:
                return self.post_run_process()
            finally:
                if restore_logging:
                    setup_logging(*previous_logging_args)

    def _attach_sc_filter_to_root(self) -> None:
        """Attach a `PrefixLevelFilter` for the SC namespace to every managed
        file handler on the root logger that lacks one.

        Idempotent across SIR.run calls in the same process: each handler is
        tagged after the filter is attached, and we skip handlers already
        carrying our filter. Stream handlers are left untouched so console
        output keeps SC INFO/DEBUG records.
        """
        root = logging.getLogger()
        for h in root.handlers:
            if not isinstance(h, logging.FileHandler):
                continue
            if any(
                isinstance(f, PrefixLevelFilter)
                and getattr(f, "_prefix", None) == _SC_LOGGER_PREFIX
                for f in h.filters
            ):
                continue
            h.addFilter(PrefixLevelFilter(_SC_LOGGER_PREFIX))

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
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        scope: dict[str, str] = {
            "scenario_name": self._scenario_name,  # type: ignore[dict-item]
            "instance_name": self.ins_name,
        }

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
        if incumbent is not None:
            try:
                solution_path = self._save_solution(incumbent)
            except Exception:
                self.logger.exception("Error saving solution for %s", self.ins_name)

        last_stage_cp_sat = getattr(controller, "last_stage_cp_sat_solution", None)
        if last_stage_cp_sat is not None:
            try:
                dump_schedule_yaml(
                    last_stage_cp_sat.schedule,
                    layout.artifact_path("last_stage_cp_sat_schedule", **scope),
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
        for name, sched in phase_schedules:
            if sched is None:
                continue
            yaml_path = layout.artifact_path(
                "mcf_lb_phase_schedule", phase_name=name, **scope
            )
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
        if diag_dict is not None:
            try:
                dump_yaml(
                    diag_dict,
                    layout.artifact_path("mcf_lb_diagnostic", **scope),
                )
            except Exception:
                self.logger.exception(
                    "Error saving mcf_lb_diagnostic yaml for %s", self.ins_name
                )

        makespan = int(incumbent.schedule.makespan) if incumbent is not None else None

        if last_report is not None:
            try:
                self._save_obj_log(solution_manager.history)
            except Exception:
                self.logger.exception("Error saving obj_log for %s", self.ins_name)

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
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        payload = {
            "_schema_version": _MANIFEST_SCHEMA_VERSION,
            **_to_serializable(asdict(result)),
        }
        final = layout.artifact_path(
            "instance_result_manifest",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        tmp = final.with_suffix(".yaml.tmp")
        dump_yaml(payload, tmp)
        os.replace(tmp, final)

    def _load_instance_result(self) -> InstanceResult:
        """Load manifest and project to current ``InstanceResult`` schema."""
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        path = layout.artifact_path(
            "instance_result_manifest",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        raw = load_yaml(path)
        if not isinstance(raw, dict):
            raise RuntimeError(f"manifest is not a mapping: {path}")
        raw.pop("_schema_version", None)
        valid = {f.name for f in dataclasses.fields(InstanceResult)}
        projected = {k: v for k, v in raw.items() if k in valid}
        return InstanceResult(**projected)

    def _save_solution(self, solution: FFcDDWSolution) -> str:
        """Save best solution as JSON."""
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        path = layout.artifact_path(
            "solution_json",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        dump_solution_json(
            solution.schedule,
            path,
            instance_name=self.ins_name,
            obj_value=solution.obj_value,
            obj_bound=solution.obj_bound,
        )
        return str(path)

    def _save_obj_log(self, history) -> None:
        """Save objective value trajectory as YAML."""
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
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
            dump_yaml(
                entries,
                layout.artifact_path(
                    "obj_log",
                    scenario_name=self._scenario_name,
                    instance_name=self.ins_name,
                ),
            )
