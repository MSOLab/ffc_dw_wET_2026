"""Scenario runner and reporting for FAM experiment orchestration."""

import csv
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from routix.io import ArtifactLayout, load_yaml
from routix.runner.multi_instance_concurrent_runner import (
    MultiInstanceConcurrentRunner,
)
from routix.runner.multi_scenario_runner import MultiScenarioRunner
from routix.type_defs import RunMode

from ..io import schedule_keys as K
from ..logging_setup import get_logging_args, setup_logging
from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffcddw_single_instance_runner import FFcDDWSingleInstanceRunner, InstanceResult
from .mcf_lb_phase_labels import MCF_LB_R1_LABEL_ORDER, MCF_LB_R2_LABEL_ORDER
from .summary import FFcDDWInputSummary, FFcDDWOutputSummary, FFcDDWSummary

logger = logging.getLogger(__name__)

# TODO: Consider making this a parameter or deriving it from the observed solve times.
TIMELIMIT_NC_MULTIPLIER = 0.09


def _last_non_empty_line(text: str | None) -> str | None:
    """Return the last non-empty line, or None when text is empty."""
    if not text:
        return None
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_rpdf(obj: float | None, bks: float | None) -> float | None:
    """RPDf = (obj - bks) / ((obj + bks) / 2). None when undefined."""
    if obj is None or bks is None:
        return None
    denom = (obj + bks) / 2
    if denom == 0:
        return 0
    return (obj - bks) / denom


def _format_obj_for_title(value: Any) -> str:
    """Render ``objValue``/``objBound`` for the chart title."""
    if value is None:
        return "N/A"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(f)}" if f.is_integer() else f"{f:.2f}"


def _build_gantt_title(
    data: dict[str, Any],
    png_path: Path,
    *,
    makespan: int,
) -> str:
    """3-line chart title: ``<instance>\\n<file_stem>\\nobj=<o>, makespan=<m>``.

    ``instance`` falls back to the PNG stem if the source carries no
    ``instanceName`` field (defensive). Line 2 uses the PNG's *stem*
    (no ``.png`` suffix) so a reader can match chart -> file at a
    glance without redundant extension noise.
    """
    instance = data.get(K.INSTANCE_NAME) or png_path.stem
    obj = _format_obj_for_title(data.get(K.OBJ_VALUE))
    return f"{instance}\n{png_path.stem}\nobj={obj}, makespan={makespan}"


def _render_gantt_from_solution_json(solution_path: Path, png_path: Path) -> None:
    """Render a Gantt PNG from any ``dump_solution_json``-shaped file.

    Used for the canonical ``<ins>_solution.json`` (final-zone incumbent)
    and any phase JSON whose source schedule was a non-preemptive
    :class:`FFcSchedule` (``operations[]`` shape).

    Module-level so it's picklable by ``ProcessPoolExecutor``. Imports
    matplotlib inside the worker to keep the algorithm process clean.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ..io.gantt import GanttPlotter
    except ImportError:
        logger.warning("matplotlib not available, skipping %s", solution_path)
        return

    try:
        with open(solution_path) as f:
            data = json.load(f)
    except Exception:
        logger.exception("Failed to load solution json %s", solution_path)
        return

    operations = data.get(K.OPERATIONS) or []
    if not operations:
        return

    start_map: dict[tuple[str, str, str], int] = {}
    end_map: dict[tuple[str, str, str], int] = {}
    for op in operations:
        key = (op[K.OP_JOB], op[K.OP_STAGE], op[K.OP_MACHINE])
        start_map[key] = int(op[K.OP_START])
        end_map[key] = int(op[K.OP_END])

    makespan = max(end_map.values()) if end_map else 0
    title = _build_gantt_title(data, png_path, makespan=makespan)

    try:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        GanttPlotter().export(
            png_path,
            start_map,
            end_map,
            job_list=data.get(K.JOBS),
            stage_list=data.get(K.STAGES),
            machine_list_per_stage=data.get(K.MACHINES_PER_STAGE),
            all_job_list=data.get(K.JOBS),
            title=title,
        )
    except Exception:
        logger.exception("Failed to render Gantt for %s", solution_path)


def _render_heatmap_from_yaml(yaml_path: Path, html_path: Path) -> None:
    """Render the signed C-cost HTML heatmap from one heatmap YAML.

    Module-level so it's picklable by ``ProcessPoolExecutor``. plotly is
    imported inside the worker to keep the algorithm process clean.
    """
    try:
        from ..io import (
            heatmap_title,
            load_signed_cost_heatmap_yaml,
            make_figure,
        )
    except ImportError:
        logger.warning("plotly/numpy not available, skipping %s", yaml_path)
        return

    try:
        data = load_signed_cost_heatmap_yaml(yaml_path)
    except Exception:
        logger.exception("Failed to load heatmap yaml %s", yaml_path)
        return

    if not data.y_labels or not data.t_axis or data.Z.size == 0:
        return

    try:
        fig = make_figure(data, title=heatmap_title(data))
        fig.write_html(str(html_path), include_plotlyjs="cdn")
    except Exception:
        logger.exception("Failed to render heatmap for %s", yaml_path)


def _render_phase_gantt_from_json(json_path: Path, png_path: Path) -> None:
    """Render a phase Gantt PNG from a compact-JSON phase schedule.

    Auto-detects regular vs preemptive content from the top-level keys
    (``operations[]`` vs ``segments[]``) and dispatches to the matching
    plotter. Embeds a 3-line chart title:

    1. instance name (from ``instanceName``)
    2. PNG filename
    3. ``obj=<v>, makespan=<m>``

    Module-level so it's picklable by ``ProcessPoolExecutor``.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ..io.gantt import GanttPlotter, PreemptiveGanttPlotter
    except ImportError:
        logger.warning("matplotlib not available, skipping %s", json_path)
        return

    try:
        with open(json_path) as f:
            data = json.load(f)
    except Exception:
        logger.exception("Failed to load phase json %s", json_path)
        return

    is_preemptive = K.SEGMENTS in data

    try:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        if is_preemptive:
            segment_records = data.get(K.SEGMENTS) or []
            if not segment_records:
                return
            segments: list[tuple[str, str, str, int, int]] = [
                (
                    seg[K.OP_JOB],
                    seg[K.OP_STAGE],
                    seg[K.OP_MACHINE],
                    int(seg[K.OP_START]),
                    int(seg[K.OP_END]),
                )
                for seg in segment_records
            ]
            stage_id = data.get(K.STAGE_ID)
            machines_per_stage = data.get(K.MACHINES_PER_STAGE) or {}
            machines = machines_per_stage.get(stage_id, []) if stage_id else []
            jobs = data.get(K.JOBS)
            all_jobs = data.get(K.ALL_JOBS) or jobs
            makespan = max(seg[4] for seg in segments)
            title = _build_gantt_title(data, png_path, makespan=makespan)
            PreemptiveGanttPlotter().export(
                png_path,
                segments,
                stage_id=stage_id,
                machines=machines,
                jobs=jobs,
                all_jobs=all_jobs,
                title=title,
            )
        else:
            operations = data.get(K.OPERATIONS) or []
            if not operations:
                return
            start_map: dict[tuple[str, str, str], int] = {}
            end_map: dict[tuple[str, str, str], int] = {}
            for op in operations:
                key = (op[K.OP_JOB], op[K.OP_STAGE], op[K.OP_MACHINE])
                start_map[key] = int(op[K.OP_START])
                end_map[key] = int(op[K.OP_END])
            makespan = max(end_map.values()) if end_map else 0
            title = _build_gantt_title(data, png_path, makespan=makespan)
            GanttPlotter().export(
                png_path,
                start_map,
                end_map,
                job_list=data.get(K.JOBS),
                stage_list=data.get(K.STAGES),
                machine_list_per_stage=data.get(K.MACHINES_PER_STAGE),
                all_job_list=data.get(K.JOBS),
                title=title,
            )
    except Exception:
        logger.exception("Failed to render Gantt for %s", json_path)


@dataclass
class ScenarioResult:
    """Aggregated result for one scenario."""

    name: str
    instance_results: list[InstanceResult] = field(default_factory=list)
    output_dir: Path | None = None


@dataclass
class FinalResult:
    """Aggregated result for all scenarios."""

    scenario_results: list[ScenarioResult] = field(default_factory=list)


def _is_placeholder_result(ir: InstanceResult) -> bool:
    """Heuristic for the all-None ``InstanceResult`` produced by the failure
    fallback in ``post_run_process``.
    """
    return (
        ir.obj_value is None
        and ir.work_status is None
        and not ir.has_incumbent
        and ir.report_count == 0
    )


class FFcDDWMultiScenarioRunner(
    MultiScenarioRunner[
        FFcDDWParameters,
        FFcDDWSingleInstanceRunner,
        MultiInstanceConcurrentRunner,
    ]
):
    """Runs multiple scenarios, each with a different flow/stopping criteria."""

    def __init__(
        self,
        scenario_names: list[str] | None = None,
        draw_gantt: bool = True,
        painter_thread_cnt: int = 1,
        ins_index_source: Path | None = None,
        bks_table_csv_path: Path | None = None,
        setup_logging_args: tuple | None = None,
        **kwargs: Any,
    ):
        if kwargs.get("logger") is None:
            kwargs["logger"] = logging.getLogger(
                "ffc_ddw_sum_et.orchestration.FFcDDWMultiScenarioRunner"
            )
        if setup_logging_args is not None:
            # Forwarded through self.kwargs -> m_i_runner_class(**self.kwargs)
            kwargs["setup_logging_args"] = setup_logging_args
        if kwargs.get("layout") is None:
            raise ValueError(
                "FFcDDWMultiScenarioRunner requires a non-None ArtifactLayout. "
                "Construct one via init_ffc_artifact_layout() in main.py."
            )
        self.scenario_names = scenario_names or [
            f"scenario_{i + 1}" for i in range(len(kwargs.get("scenario_configs", [])))
        ]
        super().__init__(**kwargs)
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = ins_index_source
        self.bks_table_csv_path = bks_table_csv_path
        self._setup_logging_args = setup_logging_args

    def _init_multi_instance_runners(self) -> None:
        """Override: route scenario directories through the layout and forward
        layout + scenario_name to each MultiInstanceRunner.

        Calling `layout.scenario_dir(name)` here is the stage-2 duplicate check
        (doc § 7.3). The matching stage-1 check runs in `main._validate_
        scenario_uniqueness` before we get this far.
        """
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        self.runners.clear()
        for i, scenario_config in enumerate(self.scenario_configs):
            subroutine_flow = scenario_config.get("subroutine_flow")
            stopping_criteria = scenario_config.get("stopping_criteria")
            if subroutine_flow is None or stopping_criteria is None:
                self.logger.warning(
                    f"Skipping scenario {i + 1} due to missing 'subroutine_flow'"
                    " or 'stopping_criteria'."
                )
                continue
            scenario_name = (
                self.scenario_names[i]
                if i < len(self.scenario_names)
                else f"scenario_{i + 1}"
            )
            scenario_output_dir = layout.scenario_dir(scenario_name)
            multi_instance_runner = self.m_i_runner_class(
                s_i_runner_class=self.s_i_runner_class,
                instances=self.instances,
                shared_param_dict=self.shared_param_dict,
                subroutine_flow=subroutine_flow,
                stopping_criteria=stopping_criteria,
                output_dir=scenario_output_dir,
                output_metadata=self.base_output_metadata.copy(),
                mode=self.mode,
                layout=layout,
                scenario_name=scenario_name,
                **self.kwargs,
            )
            if self.mode == RunMode.RESUME:
                multi_instance_runner.set_flow_resume_idx(
                    scenario_config.get("flow_resume_idx", 0)
                )
            self.runners.append(multi_instance_runner)

    def _scoped_logging_args(self, log_path: Path) -> tuple[Path, bool, int]:
        _, quiet, verbose = self._setup_logging_args or get_logging_args()
        return log_path, quiet, verbose

    def run(self):
        runner_cnt = len(self.runners)
        self.results.clear()
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]
        for i, multi_instance_runner in enumerate(self.runners):
            scenario_name = (
                self.scenario_names[i]
                if i < len(self.scenario_names)
                else f"scenario_{i + 1}"
            )
            setup_logging(
                *self._scoped_logging_args(
                    layout.log_path(
                        "multi_instance_runner", scenario_name=scenario_name
                    )
                )
            )
            logger.info(
                "--- Starting Scenario %d/%d: %s ---", i + 1, runner_cnt, scenario_name
            )
            try:
                result = multi_instance_runner.run()
                self.results.append(result)
            except Exception:
                logger.error(
                    "Error in scenario %d: %s", i + 1, scenario_name, exc_info=True
                )
                self.results.append(None)
            else:
                if isinstance(result, list):
                    n_err = sum(1 for ir in result if getattr(ir, "error", None))
                    if n_err:
                        logger.error(
                            "Scenario %s: %d/%d instances finished with errors",
                            scenario_name,
                            n_err,
                            len(result),
                        )
            logger.info(
                "--- Finished Scenario %d/%d: %s ---", i + 1, runner_cnt, scenario_name
            )
        return self.post_run_process()

    def post_run_process(self) -> FinalResult:
        scenario_results = []
        all_instance_results: list[InstanceResult] = []
        layout: ArtifactLayout = self.layout  # type: ignore[assignment]

        for i, runner in enumerate(self.runners):
            scenario_name = (
                self.scenario_names[i]
                if i < len(self.scenario_names)
                else f"scenario_{i + 1}"
            )
            # The return value from runner.run() is the aggregated instance results
            result = self.results[i] if i < len(self.results) else None
            instance_results = result if isinstance(result, list) else []
            scenario_result = ScenarioResult(
                name=scenario_name,
                instance_results=list(instance_results),
                output_dir=runner.output_dir if hasattr(runner, "output_dir") else None,
            )
            scenario_results.append(scenario_result)
            all_instance_results.extend(instance_results)

        if (
            self.mode == RunMode.POST_PROCESS_ONLY
            and all_instance_results
            and all(_is_placeholder_result(ir) for ir in all_instance_results)
        ):
            raise RuntimeError(
                f"no instance manifests found in {self.output_dir} — "
                "was this dir created before the manifest feature, or is the "
                "path wrong?"
            )

        report_log_path = layout.log_path("multi_scenario_runner")
        report_logging_args = self._scoped_logging_args(report_log_path)
        setup_logging(*report_logging_args)

        FFcDDWReporter(
            self.output_dir,
            scenario_results,
            layout=layout,
            draw_gantt=self.draw_gantt,
            painter_thread_cnt=self.painter_thread_cnt,
            ins_index_source=self.ins_index_source,
            bks_table_csv_path=self.bks_table_csv_path,
            setup_logging_args=report_logging_args,
        ).generate()

        return FinalResult(scenario_results=scenario_results)


class FFcDDWReporter:
    """Generates summary reports: CSV, JSON, YAML, Gantt charts, Excel."""

    def __init__(
        self,
        output_dir: Path | None,
        scenario_results: list[ScenarioResult],
        *,
        layout: ArtifactLayout,
        draw_gantt: bool = True,
        painter_thread_cnt: int = 1,
        ins_index_source: Path | None = None,
        bks_table_csv_path: Path | None = None,
        setup_logging_args: tuple | None = None,
    ):
        self.output_dir = output_dir or Path("output")
        self.scenario_results = scenario_results
        self.layout = layout
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = (
            Path(ins_index_source) if ins_index_source is not None else None
        )
        self.bks_table_csv_path = (
            Path(bks_table_csv_path) if bks_table_csv_path is not None else None
        )
        self._setup_logging_args = setup_logging_args
        self._filename_to_index: dict[str, int] = self._load_filename_to_index()
        self._index_to_meta: dict[int, dict[str, Any]] = self._load_index_to_meta()

    def _load_filename_to_index(self) -> dict[str, int]:
        """Build filename-stem → insIndex map from the hybrid match CSV."""
        if not self.ins_index_source or not self.ins_index_source.exists():
            return {}
        mapping: dict[str, int] = {}
        with open(self.ins_index_source, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("ffc_ddw_sum_et_filename", "").strip()
                if not name:
                    continue
                stem = Path(name).stem
                mapping[stem] = int(row["insIndex"])
        return mapping

    def _load_index_to_meta(self) -> dict[int, dict[str, Any]]:
        """Load insIndex → {n, c, totalMcCount, T, R, W, BKS} from the table CSV."""
        if not self.ins_index_source:
            return {}
        table_path = self.ins_index_source.parent / "pra2017_instance_table.csv"
        if not table_path.exists():
            return {}
        meta: dict[int, dict[str, Any]] = {}
        with open(table_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    idx = int(row["insIndex"])
                except (KeyError, ValueError):
                    continue
                meta[idx] = {
                    "n": _to_int(row.get("n")),
                    "c": _to_int(row.get("c")),
                    "totalMcCount": _to_int(row.get("totalMcCount")),
                    "T": _to_float(row.get("T")),
                    "R": _to_float(row.get("R")),
                    "W": _to_float(row.get("W")),
                    "BKS": _to_float(row.get("BKS")),
                }
        return meta

    def _resolve_ins_index(self, instance_name: str) -> int | None:
        return self._filename_to_index.get(instance_name)

    def generate(self) -> None:
        """Generate all report artifacts."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._write_summary_csv()
        self._write_mcf_preemptive_obj_csv()
        self._write_adjust_params_by_makespan_delta_csv()
        self._write_last_stage_only_obj_csv()
        self._write_mcf_lb_analysis_csv()
        self._write_calc_mcf_lb_phase_metric_summaries()
        self._write_calc_mcf_lb_summary_csv()
        self._write_mcf_lb_pivot_artifacts()
        self._write_mcf_lb_last_stage_only_obj_bks_wintie_pivot()
        self._write_mcf_lb_last_stage_only_obj_bks_wintie_table()
        self._write_statistics_yaml()
        self._write_excel_report()
        self._write_post_run_pivot_artifacts()
        self._write_post_run_subroutine_chart_artifacts()
        self._generate_gantt_charts()

    def _write_post_run_pivot_artifacts(self) -> None:
        """Emit long-format RPDf comparison CSV + 3 PivotTable.js HTML files."""
        if not self.ins_index_source or not self.ins_index_source.exists():
            return
        if not self.bks_table_csv_path or not self.bks_table_csv_path.exists():
            return

        from .post_run_pivot import write_post_run_pivot_artifacts

        summary_csv = self.layout.artifact_path("summary_csv")
        if not summary_csv.exists():
            return
        write_post_run_pivot_artifacts(
            summary_csv=summary_csv,
            layout=self.layout,
            hybrid_match_csv=self.ins_index_source,
            bks_table_csv=self.bks_table_csv_path,
        )

    def _write_post_run_subroutine_chart_artifacts(self) -> None:
        """Emit per-scenario RPDf scatter HTMLs + run-level subroutine flow
        comparison HTML driven by per-instance ``obj_log_json`` files.
        """
        if not self.ins_index_source or not self.ins_index_source.exists():
            return
        if not self.bks_table_csv_path or not self.bks_table_csv_path.exists():
            return
        instance_table_csv = self.ins_index_source.parent / "pra2017_instance_table.csv"
        if not instance_table_csv.exists():
            return

        from ..report import write_post_run_subroutine_chart_artifacts

        write_post_run_subroutine_chart_artifacts(
            layout=self.layout,
            hybrid_match_csv=self.ins_index_source,
            bks_table_csv=self.bks_table_csv_path,
            instance_table_csv=instance_table_csv,
        )

    def _write_summary_csv(self) -> None:
        """Write master summary CSV, one row per (scenario, instance).

        Uses the ``FFcDDWSummary`` append-per-row layout shaped after
        ``hybridflowshop/hfs_summary.py`` so downstream analysis scripts
        line up across projects.

        Atomic: rows are appended to a sibling ``.csv.tmp`` and ``os.replace``
        renames it onto the final path only after every row succeeded. A
        crash mid-loop leaves the prior summary CSV intact.
        """
        path = self.layout.artifact_path("summary_csv")
        tmp = path.with_suffix(".csv.tmp")
        if tmp.exists():
            tmp.unlink()
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                improvement = None
                if (
                    ir.first_obj_value is not None
                    and ir.first_obj_value != 0
                    and ir.obj_value is not None
                ):
                    improvement = (
                        ir.first_obj_value - ir.obj_value
                    ) / ir.first_obj_value
                mcf_extras = self._build_mcf_lb_extras(ir)
                summary = FFcDDWSummary(
                    inputs=FFcDDWInputSummary(
                        name=ir.instance_name,
                        job_count=ir.job_count or 0,
                        stage_count=ir.stage_count or 0,
                        machines_per_stage=ir.machines_per_stage or 0,
                        timelimit=ir.timelimit or 0.0,
                    ),
                    outputs=FFcDDWOutputSummary(
                        scenario_name=sc.name,
                        work_status=ir.work_status,
                        init_obj=ir.first_obj_value,
                        init_bound=ir.first_obj_bound,
                        best_obj=ir.obj_value,
                        best_bound=ir.obj_bound,
                        elapsed_time=float(ir.elapsed_time),
                        improvement_ratio=improvement,
                        has_incumbent=ir.has_incumbent,
                        report_count=ir.report_count,
                        method_call_counts=json.dumps(ir.method_call_counts),
                    ),
                    extra_outputs={
                        **mcf_extras,
                        "error": _last_non_empty_line(ir.error) or "",
                    },
                )
                summary.save(tmp)
        if not tmp.exists():
            logger.warning("No instance results to write for summary CSV at %s", path)
            return
        os.replace(tmp, path)
        logger.info("Summary CSV written to %s", path)

    def _build_mcf_lb_extras(self, ir: InstanceResult) -> dict[str, Any]:
        """Flatten the controller's MCF-LB diagnostics + BKS into summary columns.

        Reads from the per-entry-point diagnostic that was actually
        populated for this run. Standalone ``apply_lb_by_mcf`` populates
        ``mcf_lb_diagnostic``; the composite step populates
        ``calc_mcf_lb_and_derive_full_sch_diagnostic`` with r1/r2
        sub-results as flat fields.
        """
        mcf_diag = ir.mcf_lb_diagnostic or {}
        calc_diag = ir.calc_mcf_lb_and_derive_full_sch_diagnostic or {}
        build_diag = ir.build_full_sch_diagnostic or {}

        ins_index = self._resolve_ins_index(ir.instance_name)
        bks = (
            self._index_to_meta.get(ins_index, {}).get("BKS")
            if ins_index is not None
            else None
        )

        # Composite reports r1's MCF LB as the global LB (always valid);
        # standalone apply_lb_by_mcf reports its own.
        mcf_lb = mcf_diag.get("mcf_lb") or calc_diag.get("r1_mcf_lb")
        mcf_solve_sec = mcf_diag.get("mcf_solve_sec") or calc_diag.get(
            "r1_mcf_solve_sec"
        )
        dispatched_obj = build_diag.get("dispatched_obj") or calc_diag.get("final_obj")

        return {
            "mcfLb": mcf_lb,
            "bks": bks,
            "dispatchedObj": dispatched_obj,
            "reportedObjBound": mcf_lb,
            "mcfSolveSec": mcf_solve_sec,
            "dispatchSec": build_diag.get("dispatch_sec"),
        }

    def _aggregate_scenario(self, sc: ScenarioResult) -> dict[str, Any]:
        """Aggregate across instances within one scenario.

        Intentionally does NOT call ``SubroutineReportStatistics`` on the per-
        instance finals — that class expects one instance's trajectory, so its
        ``improvementRatio`` output is meaningless across independent instances.
        """
        completed = [ir for ir in sc.instance_results if ir.obj_value is not None]
        errored = [ir for ir in sc.instance_results if ir.error is not None]
        obj_values = [float(ir.obj_value) for ir in completed]

        improvement_ratios: list[float] = []
        for ir in completed:
            first = ir.first_obj_value
            if first is None or first == 0:
                continue
            improvement_ratios.append((first - ir.obj_value) / first)

        method_counts: dict[str, int] = defaultdict(int)
        for ir in sc.instance_results:
            for k, v in ir.method_call_counts.items():
                method_counts[k] += v

        return {
            "scenarioName": sc.name,
            "instanceCount": len(sc.instance_results),
            "completedCount": len(completed),
            "erroredCount": len(errored),
            "totalElapsedTime": sum(ir.elapsed_time for ir in sc.instance_results),
            "meanObjValue": sum(obj_values) / len(obj_values) if obj_values else None,
            "minObjValue": min(obj_values) if obj_values else None,
            "maxObjValue": max(obj_values) if obj_values else None,
            "meanImprovementRatio": (
                sum(improvement_ratios) / len(improvement_ratios)
                if improvement_ratios
                else None
            ),
            "methodCallCounts": dict(method_counts),
        }

    _MCF_LB_ANALYSIS_COLUMNS: tuple[str, ...] = (
        "insIndex",
        "error",
        "n",
        "c",
        "totalMcCount",
        "T",
        "R",
        "W",
        "mcfLb",
        "lastStageOnlyObj",
        "bks",
        "dispatchedObj",
        "mcfSolveSec",
        "dispatchSec",
    )

    def _write_calc_mcf_lb_phase_metric_summaries(self) -> None:
        """Per-scenario aggregated wide-format CSVs for
        ``calc_mcf_lb_and_derive_full_sch`` snapshot metrics.

        Reads each instance's ``mcf_lb_phase_obj_csv`` and
        ``mcf_lb_phase_makespan_csv`` (under the per-instance ``progress``
        zone) and collates them into one row per instance with one column
        per ``(round, label)`` snapshot. Instances that did not run the
        composite step (no per-instance CSV) are skipped. When no instance
        in a scenario has either CSV, the summary file is not written.
        Rows are sorted by ``insIndex`` to match other per-scenario tables.
        """
        column_pairs: list[tuple[str, str]] = [
            ("r1", label) for label in MCF_LB_R1_LABEL_ORDER
        ] + [("r2", label) for label in MCF_LB_R2_LABEL_ORDER]
        # Metadata columns prepended on the scenario-aggregated wide CSV.
        # Sourced from the reporter's ``_index_to_meta`` (loaded from
        # the PRA2017 instance table) — distinct from any per-instance
        # CSV (which intentionally carries no metadata to keep it terse).
        meta_columns = ["insIndex", "n", "c", "totalMcCount", "T", "R", "W"]
        column_headers = (
            meta_columns
            + ["instanceName"]
            + [f"{r}_{label}" for r, label in column_pairs]
        )

        for sc in self.scenario_results:
            obj_rows: list[tuple[int | None, str, list[str], list[str]]] = []
            ms_rows: list[tuple[int | None, str, list[str], list[str]]] = []
            for ir in sc.instance_results:
                obj_path = self.layout.artifact_path(
                    "mcf_lb_phase_obj_csv",
                    scenario_name=sc.name,
                    instance_name=ir.instance_name,
                )
                ms_path = self.layout.artifact_path(
                    "mcf_lb_phase_makespan_csv",
                    scenario_name=sc.name,
                    instance_name=ir.instance_name,
                )
                obj_cells = self._read_phase_metric_csv(obj_path, "obj_value")
                ms_cells = self._read_phase_metric_csv(ms_path, "makespan")
                ins_idx = self._resolve_ins_index(ir.instance_name)
                meta = (
                    self._index_to_meta.get(ins_idx, {}) if ins_idx is not None else {}
                )
                meta_cells = ["" if ins_idx is None else str(ins_idx)] + [
                    "" if meta.get(col) is None else str(meta.get(col))
                    for col in meta_columns[1:]
                ]
                if obj_cells is not None:
                    obj_rows.append(
                        (
                            ins_idx,
                            ir.instance_name,
                            meta_cells,
                            [obj_cells.get(p, "") for p in column_pairs],
                        )
                    )
                if ms_cells is not None:
                    ms_rows.append(
                        (
                            ins_idx,
                            ir.instance_name,
                            meta_cells,
                            [ms_cells.get(p, "") for p in column_pairs],
                        )
                    )

            for kind, rows in (
                ("mcf_lb_phase_obj_summary_csv", obj_rows),
                ("mcf_lb_phase_makespan_summary_csv", ms_rows),
            ):
                if not rows:
                    continue
                rows.sort(key=lambda r: (r[0] if r[0] is not None else -1, r[1]))
                path = self.layout.artifact_path(kind, scenario_name=sc.name)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(column_headers)
                    for _, name, meta_cells, value_cells in rows:
                        writer.writerow(meta_cells + [name] + value_cells)
                logger.info("Phase metric summary CSV written to %s", path)

    _CALC_MCF_LB_SUMMARY_R_FIELDS: tuple[str, ...] = (
        "mcfLbObjValue",
        "mcfLbMakespan",
        "lastStageOnlyObjValue",
        "lastStageOnlyMakespan",
        "fullSchObjValue",
        "fullSchMakespan",
        "totalTime",
    )

    def _write_calc_mcf_lb_summary_csv(self) -> None:
        """Per-scenario summary CSV aggregating per-instance r1/r2 sidecars.

        Reads each instance's ``calc_mcf_lb_r1_summary_yaml`` and
        ``calc_mcf_lb_r2_summary_yaml`` (under the per-instance
        ``progress`` zone) and collates them into one row per instance.
        Instances without an r1 sidecar (composite step did not run) are
        skipped. When no instance in a scenario has an r1 sidecar, the
        summary file is not written. Rows are sorted by ``insIndex``.

        Columns: instance metadata, then r1_<field> for each of seven
        stage metrics, then ``makespanDelta``, ``pIncrementAdded``,
        ``rIncrementAdded``, then r2_<field> for the same seven metrics.
        ``mcfLbElapsedTime`` is omitted (covered by ``totalTime``).
        """
        meta_columns = ["insIndex", "n", "c", "totalMcCount", "T", "R", "W"]
        r1_columns = [f"r1_{f}" for f in self._CALC_MCF_LB_SUMMARY_R_FIELDS]
        r2_columns = [f"r2_{f}" for f in self._CALC_MCF_LB_SUMMARY_R_FIELDS]
        delta_columns = ["makespanDelta", "pIncrementAdded", "rIncrementAdded"]
        column_headers = (
            meta_columns + ["instanceName"] + r1_columns + delta_columns + r2_columns
        )

        def _cell(value: Any) -> str:
            return "" if value is None else str(value)

        for sc in self.scenario_results:
            rows: list[tuple[int | None, str, list[str]]] = []
            for ir in sc.instance_results:
                r1_path = self.layout.artifact_path(
                    "calc_mcf_lb_r1_summary_yaml",
                    scenario_name=sc.name,
                    instance_name=ir.instance_name,
                )
                if not r1_path.exists():
                    continue
                r1_data: dict[str, Any] = load_yaml(r1_path) or {}
                r2_path = self.layout.artifact_path(
                    "calc_mcf_lb_r2_summary_yaml",
                    scenario_name=sc.name,
                    instance_name=ir.instance_name,
                )
                r2_data: dict[str, Any] = (
                    load_yaml(r2_path) if r2_path.exists() else {}
                ) or {}

                ins_idx = self._resolve_ins_index(ir.instance_name)
                meta = (
                    self._index_to_meta.get(ins_idx, {}) if ins_idx is not None else {}
                )
                meta_cells = ["" if ins_idx is None else str(ins_idx)] + [
                    _cell(meta.get(col)) for col in meta_columns[1:]
                ]
                r1_cells = [
                    _cell(r1_data.get(f)) for f in self._CALC_MCF_LB_SUMMARY_R_FIELDS
                ]
                delta_cells = [_cell(r1_data.get(c)) for c in delta_columns]
                r2_cells = [
                    _cell(r2_data.get(f)) for f in self._CALC_MCF_LB_SUMMARY_R_FIELDS
                ]
                rows.append(
                    (
                        ins_idx,
                        ir.instance_name,
                        meta_cells
                        + [ir.instance_name]
                        + r1_cells
                        + delta_cells
                        + r2_cells,
                    )
                )

            if not rows:
                continue
            rows.sort(key=lambda r: (r[0] if r[0] is not None else -1, r[1]))
            path = self.layout.artifact_path(
                "calc_mcf_lb_summary_csv", scenario_name=sc.name
            )
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(column_headers)
                for _, _, cells in rows:
                    writer.writerow(cells)
            logger.info("calc_mcf_lb summary CSV written to %s", path)

    @staticmethod
    def _read_phase_metric_csv(
        path: Path, value_column: str
    ) -> dict[tuple[str, str], str] | None:
        """Return a ``{(round, label): cell_value}`` map from a per-instance
        phase-metric CSV. Returns ``None`` if the file does not exist.
        """
        if not path.exists():
            return None
        out: dict[tuple[str, str], str] = {}
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                round_key = row.get("round", "")
                label = row.get("label", "")
                if not round_key or not label:
                    continue
                out[(round_key, label)] = row.get(value_column, "")
        return out

    def _write_mcf_lb_analysis_csv(self) -> None:
        """Per-instance lower-bound analysis table.

        One CSV per scenario that ran ``run_mcf_lb`` at least once. Rows are
        sorted by ``insIndex``; instances not in the PRA2017 instance table
        still appear with empty benchmark-meta columns.
        """
        for sc in self.scenario_results:
            rows = [
                ir
                for ir in sc.instance_results
                if (
                    ir.mcf_lb_diagnostic is not None
                    or ir.calc_mcf_lb_and_derive_full_sch_diagnostic is not None
                )
            ]
            if not rows:
                continue
            path = self.layout.artifact_path("mcf_lb_analysis", scenario_name=sc.name)
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self._MCF_LB_ANALYSIS_COLUMNS)
                rows.sort(
                    key=lambda ir: (
                        self._resolve_ins_index(ir.instance_name)
                        if self._resolve_ins_index(ir.instance_name) is not None
                        else -1
                    )
                )
                for ir in rows:
                    writer.writerow(self._mcf_lb_analysis_row(ir))
            logger.info("MCF-LB analysis CSV written to %s", path)

        self._write_last_stage_only_obj_summary_csv()

    def _write_mcf_preemptive_obj_csv(self) -> None:
        """Run-scoped long-format CSV of MCF-preemptive objective values.

        Columns: ``scenarioName, insIndex, objValue``. One row per
        ``(scenario, instance)`` pair where an MCF LB was produced
        (either by a top-level ``apply_lb_by_mcf`` step or by the
        composite ``calc_mcf_lb_and_derive_full_sch``'s round 1).

        Note: ``MCFPreemptiveSchedule`` does not carry a weighted-E+T
        objective (it is preemptive and stage-disaggregated), so we use the
        MCF lower-bound as the schedule's natural objective.
        """
        rows: list[tuple[str, int | None, float]] = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                mcf_diag = ir.mcf_lb_diagnostic or {}
                calc_diag = ir.calc_mcf_lb_and_derive_full_sch_diagnostic or {}
                mcf_lb = mcf_diag.get("mcf_lb") or calc_diag.get("r1_mcf_lb")
                if mcf_lb is None:
                    continue
                rows.append(
                    (
                        sc.name,
                        self._resolve_ins_index(ir.instance_name),
                        float(mcf_lb),
                    )
                )
        if not rows:
            return
        rows.sort(key=lambda r: (r[0], r[1] if r[1] is not None else -1))
        path = self.layout.artifact_path("mcf_preemptive_obj_csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(("scenarioName", "insIndex", "objValue"))
            for scenario_name, ins_index, obj_value in rows:
                writer.writerow(
                    (
                        scenario_name,
                        "" if ins_index is None else ins_index,
                        obj_value,
                    )
                )
        logger.info("MCF preemptive obj CSV written to %s", path)

    def _write_adjust_params_by_makespan_delta_csv(self) -> None:
        """Run-scoped long-format CSV of makespan-delta-driven param adjusts.

        One row per ``(scenario, instance)`` pair where a
        ``calc_mcf_lb_and_derive_full_sch`` composite ran past round 1
        (its ``makespan_delta`` is non-null), OR where a standalone
        ``heuristic_last_stage_only_sch_from_mcf_lb`` step fired an
        adjust knob.

        Composite rows record the *raw signed* delta
        (``r1_full_sch_makespan − r1_ls_only_pmtn_makespan``) so the
        ``delta <= 0`` skip case is captured rather than dropped. The
        ``pIncrementAdded`` / ``rIncrementAdded`` columns are blank for
        rows where round 2 did not run.

        Columns: ``scenarioName, insIndex, instanceName,
        lastStageOnlyPmtnMakespan, lastStageOnlyMakespan,
        incumbentMakespan, makespanDelta, pIncrementAdded,
        rIncrementAdded``.
        """
        rows: list[
            tuple[
                str,
                int | None,
                str,
                int | None,
                int | None,
                int,
                int,
                int | None,
                int | None,
            ]
        ] = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                # Prefer composite diagnostic when present; fall back
                # to standalone heuristic.
                calc_diag = ir.calc_mcf_lb_and_derive_full_sch_diagnostic
                heuristic_diag = ir.heuristic_last_stage_only_diagnostic
                if (
                    calc_diag is not None
                    and calc_diag.get("makespan_delta") is not None
                ):
                    rows.append(
                        (
                            sc.name,
                            self._resolve_ins_index(ir.instance_name),
                            ir.instance_name,
                            (
                                int(calc_diag["r1_ls_only_pmtn_makespan"])
                                if calc_diag.get("r1_ls_only_pmtn_makespan") is not None
                                else None
                            ),
                            None,
                            int(calc_diag["r1_full_sch_makespan"])
                            if calc_diag.get("r1_full_sch_makespan") is not None
                            else 0,
                            int(calc_diag["makespan_delta"]),
                            (
                                int(calc_diag["r2_p_increment_added"])
                                if calc_diag.get("r2_p_increment_added") is not None
                                else None
                            ),
                            (
                                int(calc_diag["r2_r_increment_added"])
                                if calc_diag.get("r2_r_increment_added") is not None
                                else None
                            ),
                        )
                    )
                elif (
                    heuristic_diag is not None
                    and heuristic_diag.get("makespan_delta") is not None
                ):
                    p_inc_raw = heuristic_diag.get("p_increment_added")
                    r_inc_raw = heuristic_diag.get("r_increment_added")
                    rows.append(
                        (
                            sc.name,
                            self._resolve_ins_index(ir.instance_name),
                            ir.instance_name,
                            (
                                int(heuristic_diag["last_stage_only_pmtn_makespan"])
                                if heuristic_diag.get("last_stage_only_pmtn_makespan")
                                is not None
                                else None
                            ),
                            (
                                int(heuristic_diag["last_stage_only_makespan"])
                                if heuristic_diag.get("last_stage_only_makespan")
                                is not None
                                else None
                            ),
                            int(heuristic_diag["incumbent_makespan"]),
                            int(heuristic_diag["makespan_delta"]),
                            int(p_inc_raw) if p_inc_raw is not None else None,
                            int(r_inc_raw) if r_inc_raw is not None else None,
                        )
                    )
        if not rows:
            return
        rows.sort(key=lambda r: (r[0], r[1] if r[1] is not None else -1))
        path = self.layout.artifact_path("adjust_params_by_makespan_delta_csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                (
                    "scenarioName",
                    "insIndex",
                    "instanceName",
                    "lastStageOnlyPmtnMakespan",
                    "lastStageOnlyMakespan",
                    "incumbentMakespan",
                    "makespanDelta",
                    "pIncrementAdded",
                    "rIncrementAdded",
                )
            )
            for (
                scenario_name,
                ins_index,
                instance_name,
                ls_only_pmtn_makespan,
                ls_only_makespan,
                incumbent_makespan,
                delta,
                p_inc_added,
                r_inc_added,
            ) in rows:
                writer.writerow(
                    (
                        scenario_name,
                        "" if ins_index is None else ins_index,
                        instance_name,
                        "" if ls_only_pmtn_makespan is None else ls_only_pmtn_makespan,
                        "" if ls_only_makespan is None else ls_only_makespan,
                        incumbent_makespan,
                        delta,
                        "" if p_inc_added is None else p_inc_added,
                        "" if r_inc_added is None else r_inc_added,
                    )
                )
        logger.info("adjust_params_by_makespan_delta CSV written to %s", path)

    def _write_last_stage_only_obj_csv(self) -> None:
        """Run-scoped long-format CSV of ``last_stage_only_sol`` objs.

        Columns: ``scenarioName, insIndex, objValue``. One row per
        ``(scenario, instance)`` pair where the controller produced a
        last-stage-only schedule (``heuristic_last_stage_only_sch_from_mcf_lb``
        directly, or via the equivalent sub-call inside
        ``calc_mcf_lb_and_derive_full_sch``). Skipped entirely if no
        scenario produced one.
        """
        rows: list[tuple[str, int | None, float]] = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                if ir.last_stage_only_obj is None:
                    continue
                rows.append(
                    (
                        sc.name,
                        self._resolve_ins_index(ir.instance_name),
                        float(ir.last_stage_only_obj),
                    )
                )
        if not rows:
            return
        rows.sort(key=lambda r: (r[0], r[1] if r[1] is not None else -1))
        path = self.layout.artifact_path("last_stage_only_obj_csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(("scenarioName", "insIndex", "objValue"))
            for scenario_name, ins_index, obj_value in rows:
                writer.writerow(
                    (
                        scenario_name,
                        "" if ins_index is None else ins_index,
                        obj_value,
                    )
                )
        logger.info("last_stage_only obj CSV written to %s", path)

    def _write_last_stage_only_obj_summary_csv(self) -> None:
        """Cross-scenario summary of ``lastStageOnlyObj`` per instance.

        One row per insIndex that appears in any scenario with an MCF-LB
        diagnostic. Columns: insIndex, BKS, one value column per scenario,
        BEST = row-wise max across scenario columns, then one indicator
        column per scenario (1 when the scenario ties BEST, empty otherwise).
        BKS is looked up from the PRA2017 instance table; missing BKS raises.
        """
        scenario_names = [sc.name for sc in self.scenario_results]
        if not scenario_names:
            return

        # (insIndex -> {scenario_name -> last_stage_only_obj})
        per_instance: dict[int, dict[str, float]] = {}
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                if ir.last_stage_only_obj is None:
                    continue
                ins_index = self._resolve_ins_index(ir.instance_name)
                if ins_index is None:
                    continue
                per_instance.setdefault(ins_index, {})[sc.name] = float(
                    ir.last_stage_only_obj
                )

        if not per_instance:
            return

        header = (
            ["insIndex", "BKS"]
            + scenario_names
            + ["BEST"]
            + [f"{name}_is_best" for name in scenario_names]
            + [f"{name}_lt_bks" for name in scenario_names]
        )

        best_counts: dict[str, int] = {name: 0 for name in scenario_names}
        lt_bks_counts: dict[str, int] = {name: 0 for name in scenario_names}

        path = self.layout.artifact_path("mcf_lb_lastStageOnlyObj_summary")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for ins_index in sorted(per_instance.keys()):
                values_by_sc = per_instance[ins_index]
                meta = self._index_to_meta.get(ins_index)
                if meta is None or meta.get("BKS") is None:
                    raise ValueError(
                        f"BKS not found for insIndex={ins_index} in "
                        f"{self.ins_index_source}"
                    )
                bks = meta["BKS"]
                numeric_vals = list(values_by_sc.values())
                best = min(numeric_vals) if numeric_vals else None

                row: list[Any] = [ins_index, bks]
                for name in scenario_names:
                    v = values_by_sc.get(name)
                    row.append("" if v is None else v)
                row.append("" if best is None else best)
                for name in scenario_names:
                    v = values_by_sc.get(name)
                    is_best = v is not None and best is not None and v == best
                    row.append(1 if is_best else "")
                    if is_best:
                        best_counts[name] += 1
                for name in scenario_names:
                    v = values_by_sc.get(name)
                    lt_bks = v is not None and v < bks
                    row.append(1 if lt_bks else "")
                    if lt_bks:
                        lt_bks_counts[name] += 1
                writer.writerow(row)
        logger.info("Last-stage-only-obj summary CSV written to %s", path)

        count_path = self.layout.artifact_path("mcf_lb_lastStageOnlyObj_count")
        with open(count_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["scenarioName", "bestCount", "ltBksCount"])
            for name in scenario_names:
                writer.writerow([name, best_counts[name], lt_bks_counts[name]])
        logger.info("Last-stage-only-obj count CSV written to %s", count_path)

    _MCF_LB_REF_OBJ_COLUMNS: tuple[str, ...] = ("dispatchedObj",)
    _MCF_LB_STEP_SEC_COLUMNS: tuple[str, ...] = (
        "mcfSolveSec",
        "dispatchSec",
    )
    _MCF_LB_REF_BLANK_COLUMNS: tuple[str, ...] = (
        *_MCF_LB_STEP_SEC_COLUMNS,
        "mcfLbSec",
        "time%",
    )

    def _write_mcf_lb_pivot_artifacts(self) -> None:
        """Render an MCF-LB-only PivotTable.js dashboard from the per-scenario
        ``<scenario_name>_mcf_lb_analysis.csv`` files.

        Concatenates each scenario's analysis CSV (with ``scenarioName``
        prepended) and appends two synthetic reference rows per instance —
        ``scenarioName="mcfLb"`` and ``scenarioName="bks"`` — whose
        objective-style columns carry the instance's ``mcfLb``/``bks`` value
        so the heatmap can show LB/BKS reference rows alongside scenarios.
        Time/count columns on the synthetic rows are blanked. Returns
        silently when no MCF-LB analysis CSV exists for this run.
        """
        import pandas as pd

        from .post_run_pivot import DEFAULT_AGGREGATORS_JS, write_pivot_html

        frames: list[pd.DataFrame] = []
        for sc in self.scenario_results:
            csv_path = self.layout.artifact_path(
                "mcf_lb_analysis", scenario_name=sc.name
            )
            if not csv_path.exists():
                continue
            df = pd.read_csv(csv_path)
            df.insert(0, "scenarioName", sc.name)
            frames.append(df)

        if not frames:
            return

        combined = pd.concat(frames, ignore_index=True)
        combined["mcfLbSec"] = combined[list(self._MCF_LB_STEP_SEC_COLUMNS)].sum(
            axis=1, min_count=1
        )
        combined["timelimit"] = combined["n"] * combined["c"] * TIMELIMIT_NC_MULTIPLIER
        combined["time%"] = combined["mcfLbSec"] / combined["timelimit"]
        combined = pd.concat(
            [combined, *self._build_mcf_lb_reference_rows(combined)],
            ignore_index=True,
        )

        initial_state = {
            "rows": ["scenarioName", "R"],
            "cols": ["T"],
            "vals": ["lastStageOnlyObj"],
            "aggregatorName": "Average",
            "rendererName": "Heatmap",
        }

        path = self.layout.artifact_path("mcf_lb_dashboard")
        write_pivot_html(
            combined,
            path,
            initial_state=initial_state,
            aggregators_js=DEFAULT_AGGREGATORS_JS,
            title="MCF-LB Pivot",
        )
        logger.info("MCF-LB pivot dashboard written to %s", path)

    def _build_mcf_lb_reference_rows(self, combined: Any) -> list[Any]:
        """Build synthetic ``scenarioName="mcfLb"`` and ``"bks"`` reference rows."""
        unique = combined.drop_duplicates(subset=["insIndex"]).copy()
        unique["error"] = ""
        nan = float("nan")

        def _ref_rows(name: str, source_col: str):
            df = unique.copy()
            df["scenarioName"] = name
            for col in self._MCF_LB_REF_OBJ_COLUMNS:
                df[col] = df[source_col]
            for col in self._MCF_LB_REF_BLANK_COLUMNS:
                df[col] = nan
            return df

        return [_ref_rows("mcfLb", "mcfLb"), _ref_rows("bks", "bks")]

    def _write_mcf_lb_last_stage_only_obj_bks_wintie_pivot(self) -> None:
        """Render a focused win/tie + RPDf dashboard comparing each scenario's
        ``lastStageOnlyObj`` against ``BKS``.

        Drops every column except scenario/instance dimensions and the five
        comparison metrics: ``lastStageOnlyObj``, ``BKS``, ``RPDf``, ``win``,
        ``tie``. ``win=1`` when ``lastStageOnlyObj < BKS``, ``tie=1`` when
        equal, otherwise both default to 0. Returns silently when no MCF-LB
        analysis CSV exists.
        """
        import pandas as pd

        from .post_run_pivot import WIN_TIE_AGGREGATORS_JS, write_pivot_html

        keep_cols = [
            "scenarioName",
            "insIndex",
            "n",
            "c",
            "totalMcCount",
            "T",
            "R",
            "W",
            "lastStageOnlyObj",
            "BKS",
            "RPDf",
            "win",
            "tie",
            "time%",
        ]

        frames: list[pd.DataFrame] = []
        for sc in self.scenario_results:
            csv_path = self.layout.artifact_path(
                "mcf_lb_analysis", scenario_name=sc.name
            )
            if not csv_path.exists():
                continue
            df = pd.read_csv(csv_path)
            df.insert(0, "scenarioName", sc.name)
            df = df.rename(columns={"bks": "BKS"})
            _denom = df["lastStageOnlyObj"] + df["BKS"]
            df["RPDf"] = (2 * (df["lastStageOnlyObj"] - df["BKS"])).where(
                _denom != 0, 0.0
            ) / _denom.where(_denom != 0, 1.0)
            df["win"] = (df["lastStageOnlyObj"] < df["BKS"]).astype(int)
            df["tie"] = (df["lastStageOnlyObj"] == df["BKS"]).astype(int)
            mcf_lb_sec = df[list(self._MCF_LB_STEP_SEC_COLUMNS)].sum(
                axis=1, min_count=1
            )
            timelimit = df["n"] * df["c"] * TIMELIMIT_NC_MULTIPLIER
            df["time%"] = mcf_lb_sec / timelimit
            frames.append(df[keep_cols])

        if not frames:
            return

        combined = pd.concat(frames, ignore_index=True)

        initial_state = {
            "rows": ["scenarioName", "R"],
            "cols": ["T"],
            "vals": ["win", "tie"],
            "aggregatorName": "Win / Tie sum",
            "rendererName": "Table",
        }

        path = self.layout.artifact_path("mcf_lb_lastStageOnlyObj_BKS_wintie_pivot")
        write_pivot_html(
            combined,
            path,
            initial_state=initial_state,
            aggregators_js=WIN_TIE_AGGREGATORS_JS,
            title="MCF-LB lastStageOnlyObj vs BKS Win/Tie",
        )
        logger.info("MCF-LB lastStageOnlyObj vs BKS win/tie pivot written to %s", path)

    def _write_mcf_lb_last_stage_only_obj_bks_wintie_table(self) -> None:
        """Write a static HTML table with per-scenario ``time%`` average plus
        ``win``/``tie`` counts over all instances. One row per scenario, no
        interactivity. Returns silently when no MCF-LB analysis CSV exists.
        """
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for sc in self.scenario_results:
            csv_path = self.layout.artifact_path(
                "mcf_lb_analysis", scenario_name=sc.name
            )
            if not csv_path.exists():
                continue
            df = pd.read_csv(csv_path)
            mcf_lb_sec = df[list(self._MCF_LB_STEP_SEC_COLUMNS)].sum(
                axis=1, min_count=1
            )
            timelimit = df["n"] * df["c"] * TIMELIMIT_NC_MULTIPLIER
            time_pct = mcf_lb_sec / timelimit
            rows.append(
                {
                    "scenarioName": sc.name,
                    "timePctAverage": float(time_pct.mean()),
                    "winCount": int((df["lastStageOnlyObj"] < df["bks"]).sum()),
                    "tieCount": int((df["lastStageOnlyObj"] == df["bks"]).sum()),
                }
            )

        if not rows:
            return

        summary = pd.DataFrame(rows).set_index("scenarioName")
        table_html = summary.to_html(
            float_format=lambda x: f"{x:.4f}",
            border=0,
        )
        payload = (
            '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n'
            "<title>MCF-LB lastStageOnlyObj vs BKS Win/Tie</title>\n"
            "<style>\n"
            "body { font-family: Verdana, sans-serif; margin: 24px; }\n"
            "table { border-collapse: collapse; }\n"
            "th, td { padding: 6px 12px; border: 1px solid #ccc; "
            "text-align: right; }\n"
            "th { background: #f4f4f4; }\n"
            "td:first-child, th:first-child { text-align: left; }\n"
            "</style>\n</head>\n<body>\n"
            "<h2>MCF-LB lastStageOnlyObj vs BKS — per-scenario summary</h2>\n"
            f"{table_html}\n"
            "</body>\n</html>\n"
        )
        path = self.layout.artifact_path("mcf_lb_lastStageOnlyObj_BKS_wintie_table")
        path.write_text(payload, encoding="utf8")
        logger.info("MCF-LB lastStageOnlyObj vs BKS win/tie table written to %s", path)

    def _mcf_lb_analysis_row(self, ir: InstanceResult) -> list[str]:
        mcf_diag = ir.mcf_lb_diagnostic or {}
        calc_diag = ir.calc_mcf_lb_and_derive_full_sch_diagnostic or {}
        build_diag = ir.build_full_sch_diagnostic or {}
        ins_index = self._resolve_ins_index(ir.instance_name)
        meta = self._index_to_meta.get(ins_index, {}) if ins_index is not None else {}

        def _s(v: Any) -> str:
            return "" if v is None else str(v)

        mcf_lb = mcf_diag.get("mcf_lb") or calc_diag.get("r1_mcf_lb")
        mcf_solve_sec = mcf_diag.get("mcf_solve_sec") or calc_diag.get(
            "r1_mcf_solve_sec"
        )
        dispatched_obj = build_diag.get("dispatched_obj") or calc_diag.get("final_obj")

        values: dict[str, Any] = {
            "insIndex": ins_index,
            "error": _last_non_empty_line(ir.error) or "",
            "n": meta.get("n"),
            "c": meta.get("c"),
            "totalMcCount": meta.get("totalMcCount"),
            "T": meta.get("T"),
            "R": meta.get("R"),
            "W": meta.get("W"),
            "mcfLb": mcf_lb,
            "lastStageOnlyObj": ir.last_stage_only_obj,
            "bks": meta.get("BKS"),
            "dispatchedObj": dispatched_obj,
            "mcfSolveSec": mcf_solve_sec,
            "dispatchSec": build_diag.get("dispatch_sec"),
        }
        return [_s(values[col]) for col in self._MCF_LB_ANALYSIS_COLUMNS]

    def _write_statistics_yaml(self) -> None:
        """Write per-scenario cross-instance aggregates as YAML."""
        try:
            from routix.io import dump_yaml
        except ImportError:
            logger.warning("routix.io.dump_yaml not available, skipping YAML stats")
            return
        for sc in self.scenario_results:
            if not sc.instance_results:
                continue
            data = self._aggregate_scenario(sc)
            path = self.layout.artifact_path(
                "scenario_statistics", scenario_name=sc.name
            )
            dump_yaml(data, path)
            logger.info("Statistics YAML written to %s", path)

    def _generate_gantt_charts(self) -> None:
        """Render Gantt PNGs into the per-instance `report/` zone, plus
        signed C-cost HTML heatmaps next to their YAML sources.

        For each instance: render the main solution Gantt from
        `<ins>_solution.json`, plus one Gantt per phase schedule yaml in
        `progress/`, plus the last_stage_only schedule when present.
        Heatmap YAMLs (``*_C_heatmap.yaml``, written by ``apply_lb_by_mcf``
        when its ``draw_heatmap`` kwarg is True) are also rendered.

        Gated by ``draw_gantt``. Rendering fans out across a
        ``ProcessPoolExecutor`` sized by ``painter_thread_cnt``; matplotlib /
        plotly are imported inside the worker so the algorithm path still
        pays nothing.
        """
        if not self.draw_gantt:
            logger.info("draw_gantt=False; skipping Gantt chart rendering")
            return

        jobs: list[tuple[Any, Path, Path]] = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins = ir.instance_name
                scope: dict[str, str] = {
                    "scenario_name": sc.name,
                    "instance_name": ins,
                }
                solution_json = self.layout.artifact_path("solution_json", **scope)
                if solution_json.exists():
                    jobs.append(
                        (
                            _render_gantt_from_solution_json,
                            solution_json,
                            self.layout.artifact_path("gantt_png", **scope),
                        )
                    )
                for phase_kind in (
                    "mcf_lb_phase_schedule",
                    "flip_makespan_cp_phase_schedule",
                ):
                    for phase_json in self.layout.find_artifacts(
                        phase_kind,
                        scenario_name=sc.name,
                        instance_name=ins,
                    ):
                        # phase_name == file stem (template is "{phase_name}.json")
                        phase_name = phase_json.stem
                        jobs.append(
                            (
                                _render_phase_gantt_from_json,
                                phase_json,
                                self.layout.artifact_path(
                                    "phase_gantt_png",
                                    phase_name=phase_name,
                                    **scope,
                                ),
                            )
                        )
                # Round-nested ``calc_mcf_lb_phase_schedule`` JSONs live under
                # ``progress/<inst>/calc_mcf_lb_and_derive_full_sch/<round>/<n>_<label>.json``.
                # ``find_artifacts`` substitutes ``*`` for the unspecified
                # ``{round}`` / ``{index}`` / ``{label}`` placeholders so the
                # 2-level glob ``calc_mcf_lb_and_derive_full_sch/*/*_*.json``
                # picks them up. Re-derive ``round`` / ``index`` / ``label``
                # from the path so the paired PNG path resolves.
                for phase_json in self.layout.find_artifacts(
                    "calc_mcf_lb_phase_schedule",
                    scenario_name=sc.name,
                    instance_name=ins,
                ):
                    round_part = phase_json.parent.name
                    stem = phase_json.stem
                    sep = stem.find("_")
                    if sep <= 0:
                        continue
                    index_str, label = stem[:sep], stem[sep + 1 :]
                    jobs.append(
                        (
                            _render_phase_gantt_from_json,
                            phase_json,
                            self.layout.artifact_path(
                                "calc_mcf_lb_phase_gantt_png",
                                round=round_part,
                                index=index_str,
                                label=label,
                                **scope,
                            ),
                        )
                    )

        gantt_count = len(jobs)
        # Heatmap YAMLs aren't registered in ArtifactLayout yet; iterate the
        # progress zone per (scenario, instance) and route the HTML output
        # into the same instance's report zone.
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins = ir.instance_name
                progress_dir = self.layout.zone_dir(
                    "progress", scenario_name=sc.name, instance_name=ins
                )
                report_dir = self.layout.zone_dir(
                    "report", scenario_name=sc.name, instance_name=ins
                )
                for hm_yaml in sorted(progress_dir.glob("*_C_heatmap.yaml")):
                    html_path = report_dir / hm_yaml.with_suffix(".html").name
                    jobs.append((_render_heatmap_from_yaml, hm_yaml, html_path))
        heatmap_count = len(jobs) - gantt_count

        if not jobs:
            return

        worker_cnt = max(1, min(self.painter_thread_cnt, len(jobs)))
        logger.info(
            "Rendering %d artifacts (%d Gantt, %d heatmap) with %d worker(s)",
            len(jobs),
            gantt_count,
            heatmap_count,
            worker_cnt,
        )
        if worker_cnt == 1:
            for render_fn, src, dst in jobs:
                render_fn(src, dst)
            return

        pool_kwargs: dict[str, Any] = {"max_workers": worker_cnt}
        if self._setup_logging_args is not None:
            pool_kwargs["initializer"] = setup_logging
            pool_kwargs["initargs"] = self._setup_logging_args
        with ProcessPoolExecutor(**pool_kwargs) as executor:
            futures = [executor.submit(fn, src, dst) for fn, src, dst in jobs]
            for future in futures:
                try:
                    future.result()
                except Exception:
                    logger.exception("Gantt worker failed")

    def _write_excel_report(self) -> None:
        """Write Excel report with dashboard, statistics, and analysis sheets."""
        try:
            import xlsxwriter
        except ImportError:
            logger.warning("xlsxwriter not available, skipping Excel report")
            return

        path = self.layout.artifact_path("report_xlsx")
        workbook = xlsxwriter.Workbook(str(path))
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#4472C4",
                "font_color": "white",
                "border": 1,
            }
        )
        cell_fmt = workbook.add_format({"border": 1})
        rpdf_fmt = workbook.add_format({"border": 1, "num_format": "0.0000"})

        self._write_dashboard_sheet(workbook, header_fmt, cell_fmt)
        self._write_statistics_sheet(workbook, header_fmt, cell_fmt)
        self._write_analysis_sheets(workbook, header_fmt, cell_fmt, rpdf_fmt)

        workbook.close()
        logger.info("Excel report written to %s", path)

    def _write_dashboard_sheet(self, workbook, header_fmt, cell_fmt) -> None:
        dashboard = workbook.add_worksheet("Dashboard")
        dashboard.write_row(
            "A1",
            [
                "Scenario",
                "insIndex",
                "insFileName",
                "workStatus",
                "reports",
                "elapsed_sec",
                "objValue",
            ],
            header_fmt,
        )

        row = 2
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins_index = self._resolve_ins_index(ir.instance_name)
                dashboard.write_row(
                    f"A{row}",
                    [
                        sc.name,
                        ins_index if ins_index is not None else "",
                        ir.instance_name,
                        ir.work_status,
                        ir.report_count,
                        round(ir.elapsed_time, 3),
                        ir.obj_value,
                    ],
                    cell_fmt,
                )
                row += 1

    def _write_statistics_sheet(self, workbook, header_fmt, cell_fmt) -> None:
        stats_sheet = workbook.add_worksheet("Statistics")
        has_meta = bool(self._index_to_meta)
        meta_cols = ["n", "c", "totalMcCount", "T", "R", "W", "BKS"] if has_meta else []
        header = (
            ["Scenario", "insIndex"]
            + meta_cols
            + [
                "Best Obj",
                "First Obj",
                "Best Bound",
                "First Bound",
                "Improvement %",
                "Total Elapsed",
                "makespan",
                "Report Count",
            ]
        )
        stats_sheet.write_row("A1", header, header_fmt)

        row = 2
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                if ir.obj_value is None:
                    continue
                first_obj = ir.first_obj_value
                improvement = None
                if (
                    first_obj is not None
                    and first_obj != 0
                    and ir.obj_value != first_obj
                ):
                    improvement = round((first_obj - ir.obj_value) / first_obj * 100, 2)
                ins_index = self._resolve_ins_index(ir.instance_name)
                meta = (
                    self._index_to_meta.get(ins_index, {})
                    if ins_index is not None
                    else {}
                )
                values: list[Any] = [
                    sc.name,
                    ins_index if ins_index is not None else "",
                ]
                if has_meta:
                    for key in meta_cols:
                        val = meta.get(key)
                        values.append(val if val is not None else "")
                values.extend(
                    [
                        ir.obj_value,
                        first_obj,
                        ir.obj_bound,
                        ir.first_obj_bound,
                        improvement,
                        round(ir.elapsed_time, 3),
                        ir.makespan,
                        ir.report_count,
                    ]
                )
                stats_sheet.write_row(f"A{row}", values, cell_fmt)
                row += 1

    def _write_analysis_sheets(self, workbook, header_fmt, cell_fmt, rpdf_fmt) -> None:
        """Write analysis_long and analysis_wide sheets with RPDf vs. BKS."""
        if not self._index_to_meta:
            return

        long_sheet = workbook.add_worksheet("analysis_long")
        meta_keys = ["n", "c", "totalMcCount", "T", "R", "W"]
        long_sheet.write_row(
            "A1",
            ["Scenario", "insIndex"]
            + meta_keys
            + ["TL", "elapsedSec", "time%", "ObjValue", "BKS", "RPDf", "Win", "Tie"],
            header_fmt,
        )

        entries = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins_index = self._resolve_ins_index(ir.instance_name)
                meta = (
                    self._index_to_meta.get(ins_index, {})
                    if ins_index is not None
                    else {}
                )
                bks = meta.get("BKS")
                tl = None
                if ir.job_count is not None and ir.stage_count is not None:
                    tl = TIMELIMIT_NC_MULTIPLIER * ir.job_count * ir.stage_count
                entries.append(
                    {
                        "ins_index": ins_index,
                        "sc_name": sc.name,
                        "meta": meta,
                        "tl": tl,
                        "elapsed": ir.elapsed_time,
                        "obj": ir.obj_value,
                        "bks": bks,
                    }
                )

        entries.sort(
            key=lambda e: (
                e["ins_index"] if e["ins_index"] is not None else 10**9,
                e["sc_name"],
            )
        )

        row = 1
        for entry in entries:
            col = 0
            long_sheet.write(row, col, entry["sc_name"], cell_fmt)
            col += 1
            long_sheet.write(
                row,
                col,
                entry["ins_index"] if entry["ins_index"] is not None else "",
                cell_fmt,
            )
            col += 1
            for key in meta_keys:
                val = entry["meta"].get(key)
                if val is not None:
                    long_sheet.write(row, col, val, cell_fmt)
                else:
                    long_sheet.write_blank(row, col, None, cell_fmt)
                col += 1
            if entry["tl"] is not None:
                long_sheet.write_number(row, col, entry["tl"], cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            elapsed_rounded = round(entry["elapsed"], 3)
            long_sheet.write_number(row, col, elapsed_rounded, cell_fmt)
            col += 1
            if entry["tl"] is not None and entry["tl"] != 0:
                time_pct = (elapsed_rounded / entry["tl"]) * 100
                long_sheet.write_number(row, col, time_pct, cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            if entry["obj"] is not None:
                long_sheet.write_number(row, col, float(entry["obj"]), cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            if entry["bks"] is not None:
                long_sheet.write_number(row, col, float(entry["bks"]), cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            rpdf = _compute_rpdf(entry["obj"], entry["bks"])
            if rpdf is not None:
                long_sheet.write_number(row, col, rpdf, rpdf_fmt)
            else:
                long_sheet.write_blank(row, col, None, rpdf_fmt)
            col += 1
            # Win: 1 if BKS > ObjValue, else blank
            if entry["obj"] is not None and entry["bks"] is not None:
                if entry["bks"] > entry["obj"]:
                    long_sheet.write_number(row, col, 1, cell_fmt)
                else:
                    long_sheet.write_blank(row, col, None, cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            # Tie: 1 if BKS == ObjValue, else blank
            if entry["obj"] is not None and entry["bks"] is not None:
                if entry["bks"] == entry["obj"]:
                    long_sheet.write_number(row, col, 1, cell_fmt)
                else:
                    long_sheet.write_blank(row, col, None, cell_fmt)
            else:
                long_sheet.write_blank(row, col, None, cell_fmt)
            row += 1

        wide_sheet = workbook.add_worksheet("analysis_wide")
        scenario_names = [sc.name for sc in self.scenario_results]
        wide_meta_keys = ["n", "c", "totalMcCount", "T", "R", "W"]
        wide_header: list[str] = ["insIndex"] + wide_meta_keys + ["BKS"]
        for sc_name in scenario_names:
            wide_header.append(f"obj_{sc_name}")
            wide_header.append(f"RPDf_{sc_name}")
        wide_sheet.write_row("A1", wide_header, header_fmt)

        per_instance: dict[int | None, dict[str, Any]] = {}
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins_index = self._resolve_ins_index(ir.instance_name)
                row_data = per_instance.setdefault(
                    ins_index,
                    {
                        "meta": {},
                        "BKS": None,
                        "by_sc": {},
                    },
                )
                if not row_data["meta"] and ins_index is not None:
                    row_data["meta"] = self._index_to_meta.get(ins_index, {})
                    row_data["BKS"] = row_data["meta"].get("BKS")
                row_data["by_sc"][sc.name] = ir.obj_value

        ordered_indices = sorted(
            per_instance.keys(),
            key=lambda k: k if k is not None else 10**9,
        )
        row = 1
        for ins_index in ordered_indices:
            data = per_instance[ins_index]
            col = 0
            wide_sheet.write(
                row, col, ins_index if ins_index is not None else "", cell_fmt
            )
            col += 1
            for key in wide_meta_keys:
                val = data["meta"].get(key)
                if val is not None:
                    wide_sheet.write(row, col, val, cell_fmt)
                else:
                    wide_sheet.write_blank(row, col, None, cell_fmt)
                col += 1
            bks = data["BKS"]
            if bks is not None:
                wide_sheet.write_number(row, col, float(bks), cell_fmt)
            else:
                wide_sheet.write_blank(row, col, None, cell_fmt)
            col += 1
            for sc_name in scenario_names:
                obj = data["by_sc"].get(sc_name)
                if obj is not None:
                    wide_sheet.write_number(row, col, float(obj), cell_fmt)
                else:
                    wide_sheet.write_blank(row, col, None, cell_fmt)
                col += 1
                rpdf = _compute_rpdf(obj, bks)
                if rpdf is not None:
                    wide_sheet.write_number(row, col, rpdf, rpdf_fmt)
                else:
                    wide_sheet.write_blank(row, col, None, rpdf_fmt)
                col += 1
            row += 1
