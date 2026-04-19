"""Scenario runner and reporting for FAM experiment orchestration."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
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

    def __init__(self, scenario_names: list[str] | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.scenario_names = scenario_names or [
            f"scenario_{i + 1}" for i in range(len(self.scenario_configs))
        ]

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

        FFcDDWReporter(self.output_dir, scenario_results).generate()

        return FinalResult(scenario_results=scenario_results)


class FFcDDWReporter:
    """Generates summary reports: CSV, JSON, YAML, Gantt charts, Excel."""

    def __init__(self, output_dir: Path | None, scenario_results: list[ScenarioResult]):
        self.output_dir = output_dir or Path("output")
        self.scenario_results = scenario_results

    def generate(self) -> None:
        """Generate all report artifacts."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._write_summary_csv()
        self._write_statistics_json()
        self._write_statistics_yaml()
        self._generate_gantt_charts()
        self._write_excel_report()

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
                    extra_outputs={"error": _last_non_empty_line(ir.error) or ""},
                )
                summary.save(path)
        logger.info("Summary CSV written to %s", path)

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

    def _write_statistics_json(self) -> None:
        """Write per-scenario cross-instance aggregates as JSON."""
        for sc in self.scenario_results:
            if not sc.instance_results:
                continue
            data = self._aggregate_scenario(sc)
            path = self.output_dir / f"{sc.name}_statistics.json"
            path.write_text(json.dumps(data, indent=2))
            logger.info("Statistics JSON written to %s", path)

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

        matplotlib is imported lazily so the algorithm path can skip it entirely.
        """
        try:
            import matplotlib

            matplotlib.use("Agg")
            from ..io import load_schedule_yaml
            from ..io.gantt import GanttPlotter
        except ImportError:
            logger.warning("matplotlib not available, skipping Gantt charts")
            return

        for yaml_path in sorted(self.output_dir.rglob("*_schedule.yaml")):
            try:
                data = load_schedule_yaml(yaml_path)
            except Exception:
                logger.exception("Failed to load schedule yaml %s", yaml_path)
                continue

            operations = data.get("operations") or []
            if not operations:
                continue

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

    def generate_report_filename(self, extension: str) -> str:
        return f"{self.output_dir.name}_report.{extension}"

    def _write_excel_report(self) -> None:
        """Write Excel report with dashboard and raw data."""
        try:
            import xlsxwriter
        except ImportError:
            logger.warning("xlsxwriter not available, skipping Excel report")
            return

        path = self.output_dir / self.generate_report_filename("xlsx")
        workbook = xlsxwriter.Workbook(str(path))

        # Dashboard sheet
        dashboard = workbook.add_worksheet("Dashboard")
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#4472C4",
                "font_color": "white",
                "border": 1,
            }
        )
        cell_fmt = workbook.add_format({"border": 1})

        dashboard.write_row(
            "A1",
            [
                "Scenario",
                "Instance",
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
                dashboard.write_row(
                    f"A{row}",
                    [
                        sc.name,
                        ir.instance_name,
                        ir.obj_value,
                        round(ir.elapsed_time, 3),
                        ir.work_status,
                        ir.report_count,
                    ],
                    cell_fmt,
                )
                row += 1

        # Statistics sheet
        stats_sheet = workbook.add_worksheet("Statistics")
        stats_sheet.write_row(
            "A1",
            [
                "Instance",
                "Best Obj",
                "First Obj",
                "Best Bound",
                "First Bound",
                "Improvement %",
                "Total Elapsed",
                "Report Count",
            ],
            header_fmt,
        )

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
                stats_sheet.write_row(
                    f"A{row}",
                    [
                        ir.instance_name,
                        ir.obj_value,
                        first_obj,
                        ir.obj_bound,
                        ir.first_obj_bound,
                        improvement,
                        round(ir.elapsed_time, 3),
                        ir.report_count,
                    ],
                    cell_fmt,
                )
                row += 1

        workbook.close()
        logger.info("Excel report written to %s", path)
