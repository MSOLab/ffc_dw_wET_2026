"""Single-instance runner for FFcDWwET experiment orchestration."""

from __future__ import annotations

import csv
import dataclasses
import datetime
import json
import logging
import os
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
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

from ..io import dump_preemptive_schedule_json, dump_solution_json
from ..logging_setup import get_logging_args, setup_logging
from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..solution.objectives import (
    compute_phase_obj_value,
    compute_weighted_earliness_tardiness,
)
from .controller import FFcDDWSubroutineController
from .controller_core import RESUME_SEED_STEP_NAME
from .solution_manager import FFcDDWSolution
from .subroutine_report import FFcDDWSubroutineReport
from .value_resolver import resolve_value_expr

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
    # Per-entry-point diagnostic dicts. Each is populated only when the
    # matching controller method was invoked as a top-level subroutine
    # flow step (composite invocations record on
    # ``calc_mcf_lb_and_derive_full_sch_diagnostic`` instead).
    mcf_lb_diagnostic: dict[str, Any] | None = None
    heuristic_last_stage_only_diagnostic: dict[str, Any] | None = None
    build_full_sch_diagnostic: dict[str, Any] | None = None
    calc_mcf_lb_and_derive_full_sch_diagnostic: dict[str, Any] | None = None
    makespan: float | None = None

    last_stage_only_obj: float | None = None
    """
    Weighted E+T of ``self.last_stage_only_sol`` when the controller
    produced a last-stage-only schedule
    (``heuristic_last_stage_only_sch_from_mcf_lb`` or the equivalent
    sub-call inside ``calc_mcf_lb_and_derive_full_sch``).
    ``None`` otherwise.
    """


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


def _fold_history_into_obj_log_dicts(
    history: list[Any],
    value_data: dict[str, float],
    value_notes: dict[str, str],
    bound_data: dict[str, float],
    bound_notes: dict[str, str],
    *,
    merged_base: bool = False,
) -> None:
    """Fold a history of ``FFcDDWSubroutineReport`` into mutable obj_log dicts.

    Caller owns dict creation and post-processing (RESUME base merge, file write).
    First-writer-wins on duplicate timestamps via ``setdefault``.
    """
    for record in history:
        report = record.report
        if not isinstance(report, FFcDDWSubroutineReport):
            continue

        for entry in report.progress_log:
            t_global = report.start_time + entry.elapsed_sec
            key = repr(t_global)
            if entry.obj_value is not None:
                value_data.setdefault(key, float(entry.obj_value))
            if entry.obj_bound is not None:
                bound_data.setdefault(key, float(entry.obj_bound))

        end_global = report.start_time + report.elapsed_time
        end_key = repr(end_global)
        label = report.step_label or ""
        if merged_base and label.rsplit("-", 1)[-1] == RESUME_SEED_STEP_NAME:
            label = ""
        if report.obj_value is not None:
            value_data.setdefault(end_key, float(report.obj_value))
            if label:
                value_notes[end_key] = label
        if report.obj_bound is not None:
            bound_data.setdefault(end_key, float(report.obj_bound))
            if label:
                bound_notes[end_key] = label


def _write_obj_log_payload(
    out_path: Path,
    value_data: dict[str, float],
    value_notes: dict[str, str],
    bound_data: dict[str, float],
    bound_notes: dict[str, str],
) -> None:
    """Write the shared obj_log JSON shape to ``out_path``."""
    payload = {
        "obj_value": {
            "name": "obj_value",
            "data": value_data,
            "notes": value_notes,
        },
        "obj_bound": {
            "name": "obj_bound",
            "data": bound_data,
            "notes": bound_notes,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)


class FFcDDWSingleInstanceRunner(
    SingleInstanceRunner[FFcDDWParameters, FFcDDWSubroutineController]
):
    """Runs on one FFcDDW instance and saves results."""

    # RunMode.RESUME: injected by FFcDDWMultiInstanceRunner._load_resume_data
    # from the base run's per-instance artifacts. `resume_solution` carries the
    # restored incumbent schedule plus obj_value/obj_bound taken from the base
    # `instance_result.yaml` (the solution JSON's objBound is usually None; the
    # global LB lives in the manifest). `flow_resume_idx` is inherited from the
    # routix base runner and set via set_flow_resume_idx.
    resume_solution: FFcDDWSolution | None = None
    resume_elapsed_time: float | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._setup_logging_args = kwargs.pop("setup_logging_args", None)
        scenario_name = kwargs.pop("scenario_name", None)
        if scenario_name is None:
            raise ValueError(
                "FFcDDWSingleInstanceRunner requires a scenario_name. "
                "It is forwarded by FFcDDWMultiInstanceRunner."
            )
        self._scenario_name: str = scenario_name
        super().__init__(*args, **kwargs)
        if self.ins_name is None:
            raise ValueError(
                "FFcDDWSingleInstanceRunner requires an instance_name. "
                "It is forwarded by FFcDDWMultiInstanceRunner."
            )
        self._ins_name: str = self.ins_name
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
        self._layout = self.layout
        self._resolve_stopping_timelimit_expr()

    def _resolve_stopping_timelimit_expr(self) -> None:
        # The scenario-level stopping_criteria dict is a single object shared
        # by reference across every SIR in the scenario (see
        # ffcddw_multi_instance_runner._init_single_instance_runners). Replace
        # with a fresh dict so per-instance resolution never mutates the
        # shared one.
        sc = self.stopping_criteria
        if not isinstance(sc, dict):
            return
        raw_tl = sc.get("timelimit")
        if not isinstance(raw_tl, str):
            return
        n = self.instance.job_count
        c = self.instance.stage_count
        m = self.instance.last_stage_mc_count
        raw_resolved = resolve_value_expr(raw_tl, n, c, m)
        if raw_resolved is None:
            raise ValueError(
                f"Scenario timelimit expression {raw_tl!r} resolved to None "
                f"(n={n}, c={c}, m={m}); expected a numeric value."
            )
        resolved = float(raw_resolved)
        self.stopping_criteria = {**sc, "timelimit": resolved}
        self.logger.debug(
            "Resolved scenario timelimit '%s' for %s (n=%d, c=%d, m=%d) -> %.3fs",
            raw_tl,
            self._ins_name,
            n,
            c,
            m,
            resolved,
        )

    def _init_working_dir(self) -> None:
        """Resolve working_dir through the layout when one is bound, else fall
        back to the routix base behavior.

        The layout call is gated so that test fixtures constructing a runner
        directly without a layout can still rely on the legacy
        `output_dir / ins_name` directory.
        """
        if self.layout is None:
            super()._init_working_dir()
            return
        if self.ins_name is None:
            raise ValueError("instance_name is required for FFcDDWSingleInstanceRunner")
        self.working_dir = self.layout.instance_dir(self._scenario_name, self.ins_name)

    def run(self):
        self._run_error: str | None = None
        previous_logging_args = get_logging_args()
        restore_logging = False
        sc_logger_name = f"{_SC_LOGGER_PREFIX}.{self._ins_name}"

        runs_controller = self.mode in (RunMode.FULL_RUN, RunMode.RESUME)
        if runs_controller and self._setup_logging_args is not None:
            _, quiet, verbose = self._setup_logging_args
            sir_log_path = self._layout.log_path(
                "single_instance_runner",
                scenario_name=self._scenario_name,
                instance_name=self._ins_name,
            )
            setup_logging(sir_log_path, quiet, verbose)
            self._attach_sc_filter_to_root()
            restore_logging = True

        try:
            if runs_controller:
                sc_log_path = self._layout.log_path(
                    "subroutine_controller",
                    scenario_name=self._scenario_name,
                    instance_name=self._ins_name,
                )
                attach_fh_to_logger(sc_logger_name, sc_log_path)
                try:
                    self.ctrlr = self.get_controller()
                    self.ctrlr.set_artifact_layout(
                        self._layout,
                        scenario_name=self._scenario_name,
                        instance_name=self._ins_name,
                    )
                    self.ctrlr.set_working_dir(self.working_dir)
                    if self.mode == RunMode.RESUME:
                        self._apply_resume()
                        self.ctrlr.run(flow_resume_idx=self.flow_resume_idx)
                    else:
                        self.ctrlr.run()
                finally:
                    detach_fh_from_logger(sc_logger_name)
        except Exception:
            self._run_error = traceback.format_exc()
            self.logger.exception("Error running instance %s", self._ins_name)
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

    def _apply_resume(self) -> None:
        """Seed the controller from the base run's restored incumbent, then
        back-date the controller clock so the ``timelimit`` stopping criterion
        charges the base prefix's wall time (the tail gets
        ``timelimit - prefix_elapsed``). Called before
        ``ctrlr.run(flow_resume_idx=...)`` on RunMode.RESUME.
        """
        if self.resume_solution is None:
            raise RuntimeError(
                f"RESUME: no base incumbent loaded for instance "
                f"{self._ins_name!r}; _load_resume_data did not inject it "
                "(check resume_root and base artifacts)."
            )
        elapsed = float(self.resume_elapsed_time or 0.0)
        # Back-date the clock: timer.elapsed_sec ≈ elapsed at register time.
        self.ctrlr.timer.set_start_time(
            datetime.datetime.now() - datetime.timedelta(seconds=elapsed)
        )
        # Structural feasibility guard on the restored schedule.
        self.ctrlr.check_feasibility(
            self.resume_solution.schedule.get_jik_2_start_time_map()
        )
        # Register the restored incumbent: obj_value drives the incumbent, and
        # obj_bound (base global MCF LB) seeds solution_manager.best_obj_bound.
        self.ctrlr.seed_resume_incumbent(
            self.resume_solution,
            obj_value=self.resume_solution.obj_value,
            obj_bound=self.resume_solution.obj_bound,
        )
        self.logger.info(
            "RESUME: seeded %s from base incumbent obj_value=%s obj_bound=%s "
            "elapsed=%.3fs, resuming flow at idx %d",
            self._ins_name,
            self.resume_solution.obj_value,
            self.resume_solution.obj_bound,
            elapsed,
            self.flow_resume_idx,
        )

    def post_run_process(self) -> InstanceResult:
        try:
            if self.mode in (RunMode.FULL_RUN, RunMode.RESUME) and getattr(
                self, "ctrlr", None
            ):
                return self._persist_run_artifacts(self.ctrlr)
            return self._load_instance_result()
        except Exception:
            post_error = traceback.format_exc()
            self.logger.exception("Error in post_run_process for %s", self._ins_name)
            combined_error = (
                f"{self._run_error}\n---\npost_run_process:\n{post_error}"
                if getattr(self, "_run_error", None)
                else post_error
            )
            return InstanceResult(
                instance_name=self._ins_name,
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
        layout: ArtifactLayout = self._layout
        scope: dict[str, str] = {
            "scenario_name": self._scenario_name,
            "instance_name": self._ins_name,
        }

        solution_manager = controller.solution_manager

        history = solution_manager.history
        last_report = solution_manager.get_last_report()
        incumbent = solution_manager.get_incumbent()

        # elapsedTime: outer controller wall-clock (set by core.run() override).
        elapsed_time = float(controller.total_elapsed_time)

        # bestObj: SSOT recompute from incumbent schedule.
        obj_value: float | None = None
        if incumbent is not None:
            sum_e, sum_t = compute_weighted_earliness_tardiness(
                incumbent.schedule, self.instance
            )
            obj_value = float(sum_e + sum_t)

        # bestBound: max over all registered reports' obj_bound; 0.0 when none.
        bound_values = [
            float(r.report.obj_bound)
            for r in history
            if r.report is not None and r.report.obj_bound is not None
        ]
        obj_bound: float = max(bound_values) if bound_values else 0.0

        # initObj: recompute from the FIRST registered solution's schedule.
        first_obj_value: float | None = None
        for record in history:
            if record.solution is not None:
                sum_e, sum_t = compute_weighted_earliness_tardiness(
                    record.solution.schedule, self.instance
                )
                first_obj_value = float(sum_e + sum_t)
                break

        # initBound: first non-None obj_bound across history; 0.0 when none.
        first_obj_bound: float = 0.0
        for record in history:
            if record.report is not None and record.report.obj_bound is not None:
                first_obj_bound = float(record.report.obj_bound)
                break

        solution_path = None
        if incumbent is not None:
            try:
                solution_path = self._save_solution(incumbent)
            except Exception:
                self.logger.exception("Error saving solution for %s", self.ins_name)

        phase_schedules = getattr(controller, "mcf_lb_phase_schedules", None) or []
        for name, sched in phase_schedules:
            if sched is None:
                continue
            json_path = layout.artifact_path(
                "mcf_lb_phase_schedule", phase_name=name, **scope
            )
            try:
                phase_obj = compute_phase_obj_value(sched, self.instance)
                if isinstance(sched, MCFPreemptiveSchedule):
                    dump_preemptive_schedule_json(
                        json_path,
                        instance_name=self.ins_name,
                        stage_id=sched.stage_id,
                        machines=sched.machines,
                        jobs=self.instance.job_id_list,
                        segments=sched.to_gantt_segments(),
                        all_jobs=self.instance.job_id_list,
                        obj_value=phase_obj,
                        compact=True,
                    )
                else:
                    dump_solution_json(
                        sched,
                        json_path,
                        instance_name=self.ins_name,
                        obj_value=phase_obj,
                        compact=True,
                    )
            except Exception:
                self.logger.exception(
                    "Error saving %s json for %s", name, self.ins_name
                )

        self._emit_csr_artifacts(controller, layout, scope)

        def _diag_to_dict(attr_name: str) -> dict[str, Any] | None:
            d = getattr(controller, attr_name, None)
            return asdict(d) if d is not None else None

        diag_dict = _diag_to_dict("mcf_lb_diagnostic")
        heuristic_diag_dict = _diag_to_dict("heuristic_last_stage_only_diagnostic")
        build_full_diag_dict = _diag_to_dict("build_full_sch_diagnostic")
        calc_diag_dict = _diag_to_dict("calc_mcf_lb_and_derive_full_sch_diagnostic")
        ls_only_sol = getattr(controller, "last_stage_only_sol", None)
        last_stage_only_obj = (
            float(ls_only_sol.obj_value)
            if ls_only_sol is not None and ls_only_sol.obj_value is not None
            else None
        )
        if any(
            d is not None
            for d in (
                diag_dict,
                heuristic_diag_dict,
                build_full_diag_dict,
                calc_diag_dict,
            )
        ):
            try:
                dump_yaml(
                    {
                        "mcf_lb_diagnostic": diag_dict,
                        "heuristic_last_stage_only_diagnostic": heuristic_diag_dict,
                        "build_full_sch_diagnostic": build_full_diag_dict,
                        "calc_mcf_lb_and_derive_full_sch_diagnostic": calc_diag_dict,
                    },
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
            instance_name=self._ins_name,
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
            heuristic_last_stage_only_diagnostic=heuristic_diag_dict,
            build_full_sch_diagnostic=build_full_diag_dict,
            calc_mcf_lb_and_derive_full_sch_diagnostic=calc_diag_dict,
            makespan=makespan,
            last_stage_only_obj=last_stage_only_obj,
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
        payload = {
            "_schema_version": _MANIFEST_SCHEMA_VERSION,
            **_to_serializable(asdict(result)),
        }
        final = self._layout.artifact_path(
            "instance_result_manifest",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        tmp = final.with_suffix(".yaml.tmp")
        dump_yaml(payload, tmp)
        os.replace(tmp, final)

    def _load_instance_result(self) -> InstanceResult:
        """Load manifest and project to current ``InstanceResult`` schema."""
        path = self._layout.artifact_path(
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
        path = self._layout.artifact_path(
            "solution_json",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        dump_solution_json(
            solution.schedule,
            path,
            instance_name=self._ins_name,
            obj_value=solution.obj_value,
            obj_bound=solution.obj_bound,
        )
        return str(path)

    # ------------------------------------------------------------------
    # shared: obj_log aggregation from a history of FFcDDWSubroutineReport
    # ------------------------------------------------------------------

    def _save_obj_log(self, history) -> None:
        """Aggregate per-step ``progress_log`` into a single-line, compact
        JSON file matching hybridflowshop's yaml mapping shape.

        Layout (one line, no whitespace):
            {"obj_value":{"name":"obj_value","data":{<t_str>:v,...},"notes":{<t_str>:label,...}},
             "obj_bound":{...}}

        Timestamps are controller-frame elapsed seconds, formatted via
        ``repr(float)`` to preserve full precision (matches hybridflowshop).
        First-writer-wins on duplicate timestamps.
        """
        value_data: dict[str, float] = {}
        value_notes: dict[str, str] = {}
        bound_data: dict[str, float] = {}
        bound_notes: dict[str, str] = {}

        # RESUME: prepend the base run's full prefix trajectory so the resume
        # obj_log (and its progress plot) shows the whole run from t=0, mirroring
        # hybridflowshop (which installs the base obj_store before the tail).
        # Base controller-frame timestamps live in [0, prefix_elapsed] and the
        # tail (back-dated clock) continues from prefix_elapsed, so the two
        # series concatenate into one continuous trajectory.
        merged_base = False
        if self.mode == RunMode.RESUME:
            merged_base = self._merge_base_obj_log(
                value_data, value_notes, bound_data, bound_notes
            )

        _fold_history_into_obj_log_dicts(
            history,
            value_data,
            value_notes,
            bound_data,
            bound_notes,
            merged_base=merged_base,
        )

        if not (value_data or bound_data):
            return

        out_path = self._layout.artifact_path(
            "obj_log_json",
            scenario_name=self._scenario_name,
            instance_name=self.ins_name,
        )
        _write_obj_log_payload(
            out_path, value_data, value_notes, bound_data, bound_notes
        )

    def _merge_base_obj_log(
        self,
        value_data: dict[str, float],
        value_notes: dict[str, str],
        bound_data: dict[str, float],
        bound_notes: dict[str, str],
    ) -> bool:
        """RESUME: merge the base run's ``<ins>_obj_log.json`` (the prefix
        trajectory) into the accumulating obj_log maps. Returns True on success.

        Best-effort: a missing/unreadable base obj_log logs a warning and returns
        False (the resume run then keeps just its own tail + the resume_seed
        marker). Uses ``setdefault`` so any resume point wins on a key collision;
        in practice base (``t <= prefix_elapsed``) and tail (``t >=
        prefix_elapsed``) timestamps are disjoint.
        """
        resume_root = self.output_metadata.get("resume_root")
        if not resume_root:
            return False
        base_path = (
            Path(resume_root) / self._ins_name / f"{self._ins_name}_obj_log.json"
        )
        if not base_path.is_file():
            self.logger.warning(
                "RESUME: base obj_log not found for %s: %s (plot will start at "
                "the resume seed)",
                self._ins_name,
                base_path,
            )
            return False
        try:
            with open(base_path, encoding="utf-8") as f:
                base = json.load(f)
        except Exception:
            self.logger.exception("RESUME: failed to read base obj_log %s", base_path)
            return False

        for series_key, data_dict, notes_dict in (
            ("obj_value", value_data, value_notes),
            ("obj_bound", bound_data, bound_notes),
        ):
            block = base.get(series_key) or {}
            for k, v in (block.get("data") or {}).items():
                if v is not None:
                    data_dict.setdefault(k, float(v))
            for k, lbl in (block.get("notes") or {}).items():
                notes_dict.setdefault(k, str(lbl))
        return True

    def _emit_csr_artifacts(
        self,
        controller: Any,
        layout: ArtifactLayout,
        scope: dict[str, str],
    ) -> None:
        """Emit CSR (coarsen_solve_reconstruct) phase schedule JSONs and the
        CP trajectory JSON if the controller captured them.

        Mirrors the mcf_lb phase loop: each emission is wrapped in its own
        try/except so one failure does not block other artifacts.
        """
        # --- CSR phase schedules (3 snapshots) ---
        csr_phases = getattr(controller, "csr_phase_schedules", None) or []
        for name, sched in csr_phases:
            if sched is None:
                continue
            try:
                json_path = layout.artifact_path(
                    "csr_phase_schedule", phase_name=name, **scope
                )
                # The coarse_solver_result snapshot is on the coarsened time
                # scale — an original-instance objective is meaningless there.
                # Pass obj_value=None so only the makespan is shown in the title.
                if name.endswith("1_coarse_solver_result"):
                    phase_obj = None
                else:
                    phase_obj = compute_phase_obj_value(sched, self.instance)
                dump_solution_json(
                    sched,
                    json_path,
                    instance_name=self.ins_name,
                    obj_value=phase_obj,
                    compact=True,
                )
            except Exception:
                self.logger.exception(
                    "Error saving CSR phase schedule %s json for %s",
                    name,
                    self.ins_name,
                )

        # --- CSR solve_flow candidates CSV (one row per candidate) ---
        candidate_rows = getattr(controller, "csr_candidate_rows", None) or []
        if candidate_rows:
            try:
                csv_path = layout.artifact_path("csr_candidates_csv", **scope)
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                fieldnames = [
                    "source",
                    "coarse_obj",
                    "coarse_bound",
                    "restored_obj",
                    "valid",
                    "sec_elapsed_step",
                    "sec_elapsed_recon",
                ]
                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in candidate_rows:
                        writer.writerow({k: row.get(k) for k in fieldnames})
            except Exception:
                self.logger.exception(
                    "Error saving CSR candidates csv for %s", self.ins_name
                )

        # --- CSR inner-solve coarse-scale obj_log (child controller history) ---
        child_history = getattr(controller, "csr_child_history", None)
        if child_history:
            try:
                value_data: dict[str, float] = {}
                value_notes: dict[str, str] = {}
                bound_data: dict[str, float] = {}
                bound_notes: dict[str, str] = {}
                _fold_history_into_obj_log_dicts(
                    child_history,
                    value_data,
                    value_notes,
                    bound_data,
                    bound_notes,
                )
                if value_data or bound_data:
                    inner_path = layout.artifact_path("csr_inner_obj_log_json", **scope)
                    _write_obj_log_payload(
                        inner_path, value_data, value_notes, bound_data, bound_notes
                    )
            except Exception:
                self.logger.exception(
                    "Error saving CSR inner obj_log for %s", self.ins_name
                )
