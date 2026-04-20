"""Scenario runner and reporting for FAM experiment orchestration."""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from routix.runner.multi_instance_concurrent_runner import (
    MultiInstanceConcurrentRunner,
)
from routix.runner.multi_scenario_runner import MultiScenarioRunner

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffcddw_single_instance_runner import FFcDDWSingleInstanceRunner, InstanceResult
from .summary import FFcDDWInputSummary, FFcDDWOutputSummary, FFcDDWSummary

logger = logging.getLogger(__name__)


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


def _render_gantt_from_yaml(yaml_path: Path) -> None:
    """Render a single Gantt PNG from one schedule YAML.

    Module-level so it's picklable by ``ProcessPoolExecutor``. Imports
    matplotlib inside the worker to keep the algorithm process clean.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ..io import load_schedule_yaml
        from ..io.gantt import GanttPlotter
    except ImportError:
        logger.warning("matplotlib not available, skipping %s", yaml_path)
        return

    try:
        data = load_schedule_yaml(yaml_path)
    except Exception:
        logger.exception("Failed to load schedule yaml %s", yaml_path)
        return

    operations = data.get("operations") or []
    if not operations:
        return

    start_map: dict[tuple[str, str, str], int] = {}
    end_map: dict[tuple[str, str, str], int] = {}
    for op in operations:
        key = (op["job"], op["stage"], op["machine"])
        start_map[key] = int(op["start"])
        end_map[key] = int(op["end"])

    png_path = yaml_path.with_name(
        yaml_path.stem.replace("_schedule", "_gantt") + ".png"
    )
    try:
        GanttPlotter().export(
            png_path,
            start_map,
            end_map,
            job_list=data.get("jobs"),
            stage_list=data.get("stages"),
            machine_list_per_stage=data.get("machinesPerStage"),
            all_job_list=data.get("jobs"),
        )
    except Exception:
        logger.exception("Failed to render Gantt for %s", yaml_path)


def _render_preemptive_gantt_from_yaml(yaml_path: Path) -> None:
    """Render a preemptive Gantt PNG from one MCF-preemptive schedule YAML."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        from ..io import load_preemptive_schedule_yaml
        from ..io.gantt import PreemptiveGanttPlotter
    except ImportError:
        logger.warning("matplotlib not available, skipping %s", yaml_path)
        return

    try:
        data = load_preemptive_schedule_yaml(yaml_path)
    except Exception:
        logger.exception("Failed to load preemptive schedule yaml %s", yaml_path)
        return

    segment_records = data.get("segments") or []
    if not segment_records:
        return

    segments: list[tuple[str, str, str, int, int]] = [
        (
            seg["job"],
            seg["stage"],
            seg["machine"],
            int(seg["start"]),
            int(seg["end"]),
        )
        for seg in segment_records
    ]

    stage_id = data.get("stageId")
    machines_per_stage = data.get("machinesPerStage") or {}
    machines = machines_per_stage.get(stage_id, []) if stage_id else []
    jobs = data.get("jobs")
    all_jobs = data.get("allJobs") or jobs

    png_path = yaml_path.with_name(
        yaml_path.stem.replace("_mcf_preemptive_schedule", "_mcf_preemptive_gantt")
        + ".png"
    )
    try:
        PreemptiveGanttPlotter().export(
            png_path,
            segments,
            stage_id=stage_id,
            machines=machines,
            jobs=jobs,
            all_jobs=all_jobs,
        )
    except Exception:
        logger.exception("Failed to render preemptive Gantt for %s", yaml_path)


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
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.scenario_names = scenario_names or [
            f"scenario_{i + 1}" for i in range(len(self.scenario_configs))
        ]
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = ins_index_source

    def run(self):
        runner_cnt = len(self.runners)
        self.results.clear()
        for i, multi_instance_runner in enumerate(self.runners):
            scenario_name = (
                self.scenario_names[i]
                if i < len(self.scenario_names)
                else f"scenario_{i + 1}"
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
            logger.info(
                "--- Finished Scenario %d/%d: %s ---", i + 1, runner_cnt, scenario_name
            )
        return self.post_run_process()

    def post_run_process(self) -> FinalResult:
        scenario_results = []
        all_instance_results: list[InstanceResult] = []

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

        FFcDDWReporter(
            self.output_dir,
            scenario_results,
            draw_gantt=self.draw_gantt,
            painter_thread_cnt=self.painter_thread_cnt,
            ins_index_source=self.ins_index_source,
        ).generate()

        return FinalResult(scenario_results=scenario_results)


class FFcDDWReporter:
    """Generates summary reports: CSV, JSON, YAML, Gantt charts, Excel."""

    def __init__(
        self,
        output_dir: Path | None,
        scenario_results: list[ScenarioResult],
        *,
        draw_gantt: bool = True,
        painter_thread_cnt: int = 1,
        ins_index_source: Path | None = None,
    ):
        self.output_dir = output_dir or Path("output")
        self.scenario_results = scenario_results
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = (
            Path(ins_index_source) if ins_index_source is not None else None
        )
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
        self._write_mcf_lb_analysis_csv()
        self._write_statistics_yaml()
        self._write_excel_report()
        self._generate_gantt_charts()

    def generate_summary_filename(self, extension: str) -> str:
        return f"{self.output_dir.name}_summary.{extension}"

    def _write_summary_csv(self) -> None:
        """Write master summary CSV, one row per (scenario, instance).

        Uses the ``FFcDDWSummary`` append-per-row layout shaped after
        ``hybridflowshop/hfs_summary.py`` so downstream analysis scripts
        line up across projects.
        """
        path = self.output_dir / self.generate_summary_filename("csv")
        if path.exists():
            path.unlink()
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
                summary.save(path)
        logger.info("Summary CSV written to %s", path)

    def _build_mcf_lb_extras(self, ir: InstanceResult) -> dict[str, Any]:
        """Flatten the controller's MCF-LB diagnostic + BKS into summary columns."""
        diag = ir.mcf_lb_diagnostic or {}

        ins_index = self._resolve_ins_index(ir.instance_name)
        bks = (
            self._index_to_meta.get(ins_index, {}).get("BKS")
            if ins_index is not None
            else None
        )

        # Mirrors controller.py: obj_bound_final = max(mcf_lb, pf_bound).
        reported_obj_bound: float | None = None
        if diag.get("mcf_lb") is not None:
            reported_obj_bound = diag["mcf_lb"]
            if diag.get("profile_fix_bound") is not None:
                reported_obj_bound = max(reported_obj_bound, diag["profile_fix_bound"])

        def _gap(a_key: str, b_key: str) -> float | None:
            a, b = diag.get(a_key), diag.get(b_key)
            return a - b if a is not None and b is not None else None

        pf_vs_bks = (
            diag["profile_fix_obj"] - bks
            if diag.get("profile_fix_obj") is not None and bks is not None
            else None
        )

        return {
            "mcfLb": diag.get("mcf_lb"),
            "lastStageOnlyBound": diag.get("last_stage_only_bound"),
            "lastStageOnlyObj": diag.get("last_stage_only_obj"),
            "bks": bks,
            "dispatchedObj": diag.get("dispatched_obj"),
            "profileFixObj": diag.get("profile_fix_obj"),
            "profileFixBound": diag.get("profile_fix_bound"),
            "reportedObjBound": reported_obj_bound,
            "lastStageBoundMinusMcfGap": _gap("last_stage_only_bound", "mcf_lb"),
            "lastStagePrimalMinusBoundGap": _gap(
                "last_stage_only_obj", "last_stage_only_bound"
            ),
            "dispatchedMinusProfileFixGap": _gap("dispatched_obj", "profile_fix_obj"),
            "profileFixMinusBksGap": pf_vs_bks,
            "mcfSolveSec": diag.get("mcf_solve_sec"),
            "lastStageCpSatSec": diag.get("last_stage_cp_sat_sec"),
            "dispatchSec": diag.get("dispatch_sec"),
            "profileFixCpSatSec": diag.get("profile_fix_cp_sat_sec"),
            "mcfLbReachedPhase": diag.get("reached_phase") or "",
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
        "lastStageOnlyBound",
        "lastStageOnlyObj",
        "bks",
        "dispatchedObj",
        "profileFixObj",
        "profileFixBound",
        "mcfSolveSec",
        "lastStageCpSatSec",
        "dispatchSec",
        "profileFixCpSatSec",
    )

    def _write_mcf_lb_analysis_csv(self) -> None:
        """Per-instance lower-bound analysis table.

        One CSV per scenario that ran ``run_mcf_lb`` at least once. Rows are
        sorted by ``insIndex``; instances not in the PRA2017 instance table
        still appear with empty benchmark-meta columns.
        """
        for sc in self.scenario_results:
            rows = [
                ir for ir in sc.instance_results if ir.mcf_lb_diagnostic is not None
            ]
            if not rows:
                continue
            path = self.output_dir / f"{sc.name}_mcf_lb_analysis.csv"
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

    def _mcf_lb_analysis_row(self, ir: InstanceResult) -> list[str]:
        diag = ir.mcf_lb_diagnostic or {}
        ins_index = self._resolve_ins_index(ir.instance_name)
        meta = self._index_to_meta.get(ins_index, {}) if ins_index is not None else {}

        def _s(v: Any) -> str:
            return "" if v is None else str(v)

        values: dict[str, Any] = {
            "insIndex": ins_index,
            "error": _last_non_empty_line(ir.error) or "",
            "n": meta.get("n"),
            "c": meta.get("c"),
            "totalMcCount": meta.get("totalMcCount"),
            "T": meta.get("T"),
            "R": meta.get("R"),
            "W": meta.get("W"),
            "mcfLb": diag.get("mcf_lb"),
            "lastStageOnlyBound": diag.get("last_stage_only_bound"),
            "lastStageOnlyObj": diag.get("last_stage_only_obj"),
            "bks": meta.get("BKS"),
            "dispatchedObj": diag.get("dispatched_obj"),
            "profileFixObj": diag.get("profile_fix_obj"),
            "mcfSolveSec": diag.get("mcf_solve_sec"),
            "lastStageCpSatSec": diag.get("last_stage_cp_sat_sec"),
            "dispatchSec": diag.get("dispatch_sec"),
            "profileFixCpSatSec": diag.get("profile_fix_cp_sat_sec"),
            "profileFixBound": diag.get("profile_fix_bound"),
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
            path = self.output_dir / f"{sc.name}_statistics.yaml"
            dump_yaml(data, path)
            logger.info("Statistics YAML written to %s", path)

    def _generate_gantt_charts(self) -> None:
        """Render Gantt PNGs from every `*_schedule.yaml` under output_dir.

        Gated by ``draw_gantt``. When enabled, rendering fans out across a
        ``ProcessPoolExecutor`` sized by ``painter_thread_cnt``; matplotlib is
        imported inside the worker so the algorithm path still pays nothing.

        Preemptive schedule YAMLs (``*_mcf_preemptive_schedule.yaml``) use a
        different schema and a dedicated renderer.
        """
        if not self.draw_gantt:
            logger.info("draw_gantt=False; skipping Gantt chart rendering")
            return

        all_yaml_paths = sorted(self.output_dir.rglob("*_schedule.yaml"))
        preemptive_paths = [
            p
            for p in all_yaml_paths
            if p.name.endswith("_mcf_preemptive_schedule.yaml")
        ]
        regular_paths = [p for p in all_yaml_paths if p not in preemptive_paths]

        jobs: list[tuple[Path, Any]] = [
            (p, _render_gantt_from_yaml) for p in regular_paths
        ] + [(p, _render_preemptive_gantt_from_yaml) for p in preemptive_paths]
        if not jobs:
            return

        worker_cnt = max(1, min(self.painter_thread_cnt, len(jobs)))
        logger.info(
            "Rendering %d Gantt charts (%d regular, %d preemptive) with %d worker(s)",
            len(jobs),
            len(regular_paths),
            len(preemptive_paths),
            worker_cnt,
        )
        if worker_cnt == 1:
            for yaml_path, render_fn in jobs:
                render_fn(yaml_path)
            return

        with ProcessPoolExecutor(max_workers=worker_cnt) as executor:
            futures = [
                executor.submit(render_fn, yaml_path) for yaml_path, render_fn in jobs
            ]
            for future in futures:
                try:
                    future.result()
                except Exception:
                    logger.exception("Gantt worker failed")

    def generate_report_filename(self, extension: str) -> str:
        return f"{self.output_dir.name}_report.{extension}"

    def _write_excel_report(self) -> None:
        """Write Excel report with dashboard, statistics, and analysis sheets."""
        try:
            import xlsxwriter
        except ImportError:
            logger.warning("xlsxwriter not available, skipping Excel report")
            return

        path = self.output_dir / self.generate_report_filename("xlsx")
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
                "Obj Value",
                "Elapsed (s)",
                "Work Status",
                "Reports",
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
                        ir.obj_value,
                        round(ir.elapsed_time, 3),
                        ir.work_status,
                        ir.report_count,
                    ],
                    cell_fmt,
                )
                row += 1

    def _write_statistics_sheet(self, workbook, header_fmt, cell_fmt) -> None:
        stats_sheet = workbook.add_worksheet("Statistics")
        has_meta = bool(self._index_to_meta)
        meta_cols = ["n", "c", "totalMcCount", "T", "R", "W", "BKS"] if has_meta else []
        header = (
            ["insIndex", "insFileName"]
            + meta_cols
            + [
                "Best Obj",
                "First Obj",
                "Best Bound",
                "First Bound",
                "Improvement %",
                "Total Elapsed",
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
                    ins_index if ins_index is not None else "",
                    ir.instance_name,
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
        long_sheet.write_row(
            "A1",
            ["insIndex", "insFileName", "Scenario", "Obj Value", "BKS", "RPDf"],
            header_fmt,
        )

        entries: list[tuple[int | None, str, str, float | None, float | None]] = []
        for sc in self.scenario_results:
            for ir in sc.instance_results:
                ins_index = self._resolve_ins_index(ir.instance_name)
                bks = None
                if ins_index is not None:
                    bks = self._index_to_meta.get(ins_index, {}).get("BKS")
                entries.append(
                    (ins_index, ir.instance_name, sc.name, ir.obj_value, bks)
                )

        entries.sort(
            key=lambda e: (
                e[0] if e[0] is not None else 10**9,
                e[2],
            )
        )

        row = 1
        for ins_index, ins_name, sc_name, obj, bks in entries:
            long_sheet.write(
                row, 0, ins_index if ins_index is not None else "", cell_fmt
            )
            long_sheet.write(row, 1, ins_name, cell_fmt)
            long_sheet.write(row, 2, sc_name, cell_fmt)
            if obj is not None:
                long_sheet.write_number(row, 3, float(obj), cell_fmt)
            else:
                long_sheet.write_blank(row, 3, None, cell_fmt)
            if bks is not None:
                long_sheet.write_number(row, 4, float(bks), cell_fmt)
            else:
                long_sheet.write_blank(row, 4, None, cell_fmt)
            rpdf = _compute_rpdf(obj, bks)
            if rpdf is not None:
                long_sheet.write_number(row, 5, rpdf, rpdf_fmt)
            else:
                long_sheet.write_blank(row, 5, None, rpdf_fmt)
            row += 1

        wide_sheet = workbook.add_worksheet("analysis_wide")
        scenario_names = [sc.name for sc in self.scenario_results]
        wide_header: list[str] = ["insIndex", "insFileName", "BKS"]
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
                    {"insFileName": ir.instance_name, "BKS": None, "by_sc": {}},
                )
                if row_data["BKS"] is None and ins_index is not None:
                    row_data["BKS"] = self._index_to_meta.get(ins_index, {}).get("BKS")
                row_data["by_sc"][sc.name] = ir.obj_value

        ordered_indices = sorted(
            per_instance.keys(),
            key=lambda k: k if k is not None else 10**9,
        )
        row = 1
        for ins_index in ordered_indices:
            data = per_instance[ins_index]
            wide_sheet.write(
                row, 0, ins_index if ins_index is not None else "", cell_fmt
            )
            wide_sheet.write(row, 1, data["insFileName"], cell_fmt)
            bks = data["BKS"]
            if bks is not None:
                wide_sheet.write_number(row, 2, float(bks), cell_fmt)
            else:
                wide_sheet.write_blank(row, 2, None, cell_fmt)
            col = 3
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
