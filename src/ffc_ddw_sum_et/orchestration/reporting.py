"""Scenario runner and reporting for FAM experiment orchestration."""

from __future__ import annotations

import csv
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

logger = logging.getLogger(__name__)


def _first_line(text: str | None) -> str | None:
    """Return the trailing non-empty line, or None when text is empty."""
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
            logging.info(
                f"--- Starting Scenario {i + 1}/{runner_cnt}: {scenario_name} ---"
            )
            try:
                result = multi_instance_runner.run()
                self.results.append(result)
            except Exception:
                logging.error(
                    f"Error in scenario {i + 1}: {scenario_name}", exc_info=True
                )
                self.results.append(None)
            logging.info(
                f"--- Finished Scenario {i + 1}/{runner_cnt}: {scenario_name} ---"
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
        """Write master summary CSV with all instance results."""
        path = self.output_dir / self.generate_summary_filename("csv")
        fieldnames = [
            "scenario_name",
            "instance_name",
            "first_obj_value",
            "obj_value",
            "obj_bound",
            "elapsed_time",
            "work_status",
            "has_incumbent",
            "report_count",
            "method_call_counts",
            "error",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for sc in self.scenario_results:
                for ir in sc.instance_results:
                    writer.writerow(
                        {
                            "scenario_name": sc.name,
                            "instance_name": ir.instance_name,
                            "first_obj_value": ir.first_obj_value,
                            "obj_value": ir.obj_value,
                            "obj_bound": ir.obj_bound,
                            "elapsed_time": round(ir.elapsed_time, 3),
                            "work_status": ir.work_status,
                            "has_incumbent": ir.has_incumbent,
                            "report_count": ir.report_count,
                            "method_call_counts": json.dumps(ir.method_call_counts),
                            "error": _first_line(ir.error),
                        }
                    )
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
        """Generate Gantt chart PNGs for best solutions."""
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.patches as patches
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not available, skipping Gantt charts")
            return

        cmap = plt.get_cmap("tab20")

        for sc in self.scenario_results:
            for ir in sc.instance_results:
                if not ir.has_incumbent or not ir.solution_path:
                    continue

                try:
                    solution_data = json.loads(Path(ir.solution_path).read_text())
                except Exception:
                    continue

                operations = solution_data.get("operations", [])
                if not operations:
                    continue

                start_map: dict[tuple[str, str, str], int] = {}
                end_map: dict[tuple[str, str, str], int] = {}
                for op in operations:
                    key = (op["job"], op["stage"], op["machine"])
                    start_map[key] = op["start"]
                    end_map[key] = op["end"]

                machines_per_stage = solution_data.get("machines_per_stage", {})
                job_list = solution_data.get("jobs", [])
                stage_list = solution_data.get("stages", [])

                fig, ax = plt.subplots(figsize=(14, 6))

                # Machine lanes
                machine_lanes: list[tuple[str, str]] = []
                machine_labels: list[str] = []
                for stage in stage_list:
                    machines = sorted(machines_per_stage.get(stage, []))
                    for mc in machines:
                        machine_lanes.append((stage, mc))
                        machine_labels.append(f"{stage}-{mc}")

                machine_to_y = {mc: 1.0 * idx for idx, mc in enumerate(machine_lanes)}

                # Color map
                n_jobs = max(len(job_list) - 1, 1)
                job_to_color = {
                    job: cmap(i / n_jobs) for i, job in enumerate(sorted(job_list))
                }

                # Draw bars
                for (job, stage, machine), s_time in sorted(start_map.items()):
                    e_time = end_map.get((job, stage, machine), s_time)
                    y = machine_to_y.get((stage, machine))
                    if y is None:
                        continue
                    duration = e_time - s_time
                    color = job_to_color.get(job, (0.5, 0.5, 0.5, 0.5))

                    ax.add_patch(
                        patches.Rectangle(
                            (s_time, y),
                            duration,
                            0.8,
                            edgecolor="black",
                            facecolor=color,
                            alpha=0.5,
                            linewidth=1.0,
                        )
                    )
                    ax.text(
                        (s_time + e_time) / 2,
                        y + 0.4,
                        job,
                        ha="center",
                        va="center",
                        color="black",
                        fontsize=7,
                    )

                ax.set_yticks([y + 0.4 for y in range(len(machine_lanes))])
                ax.set_yticklabels(machine_labels)
                ax.set_ylim(-0.5, len(machine_lanes) + 0.5)
                ax.set_xlabel("Time")
                ax.set_title(f"Gantt Chart - {ir.instance_name} ({sc.name})")
                ax.grid(True, axis="x", linestyle="--", alpha=0.3)
                ax.invert_yaxis()
                fig.subplots_adjust(left=0.1, right=0.98, top=0.92, bottom=0.1)

                gantt_path = (
                    Path(ir.solution_path).parent / f"{ir.instance_name}_gantt.png"
                )
                fig.savefig(gantt_path, dpi=120)
                plt.close(fig)

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
