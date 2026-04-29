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


def _render_heatmap_from_yaml(yaml_path: Path) -> None:
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

    html_path = yaml_path.with_suffix(".html")
    try:
        fig = make_figure(data, title=heatmap_title(data))
        fig.write_html(str(html_path), include_plotlyjs="cdn")
    except Exception:
        logger.exception("Failed to render heatmap for %s", yaml_path)


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
        bks_table_csv_path: Path | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.scenario_names = scenario_names or [
            f"scenario_{i + 1}" for i in range(len(self.scenario_configs))
        ]
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = ins_index_source
        self.bks_table_csv_path = bks_table_csv_path

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
            bks_table_csv_path=self.bks_table_csv_path,
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
        bks_table_csv_path: Path | None = None,
    ):
        self.output_dir = output_dir or Path("output")
        self.scenario_results = scenario_results
        self.draw_gantt = draw_gantt
        self.painter_thread_cnt = painter_thread_cnt
        self.ins_index_source = (
            Path(ins_index_source) if ins_index_source is not None else None
        )
        self.bks_table_csv_path = (
            Path(bks_table_csv_path) if bks_table_csv_path is not None else None
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
        self._write_mcf_lb_pivot_artifacts()
        self._write_mcf_lb_last_stage_only_obj_bks_wintie_pivot()
        self._write_mcf_lb_last_stage_only_obj_bks_wintie_table()
        self._write_statistics_yaml()
        self._write_excel_report()
        self._write_post_run_pivot_artifacts()
        self._generate_gantt_charts()

    def _write_post_run_pivot_artifacts(self) -> None:
        """Emit long-format RPDf comparison CSV + 3 PivotTable.js HTML files."""
        if not self.ins_index_source or not self.ins_index_source.exists():
            return
        if not self.bks_table_csv_path or not self.bks_table_csv_path.exists():
            return

        from .post_run_pivot import write_post_run_pivot_artifacts

        summary_csv = self.output_dir / self.generate_summary_filename("csv")
        if not summary_csv.exists():
            return
        write_post_run_pivot_artifacts(
            summary_csv=summary_csv,
            output_dir=self.output_dir,
            run_id=self.output_dir.name,
            hybrid_match_csv=self.ins_index_source,
            bks_table_csv=self.bks_table_csv_path,
        )

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

        self._write_last_stage_only_obj_summary_csv()

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
                diag = ir.mcf_lb_diagnostic
                if diag is None:
                    continue
                val = diag.get("last_stage_only_obj")
                if val is None:
                    continue
                ins_index = self._resolve_ins_index(ir.instance_name)
                if ins_index is None:
                    continue
                per_instance.setdefault(ins_index, {})[sc.name] = float(val)

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

        path = (
            self.output_dir / f"{self.output_dir.name}_mcf_lb_last_stage_only_obj.csv"
        )
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

        count_path = (
            self.output_dir
            / f"{self.output_dir.name}_mcf_lb_last_stage_only_obj_count.csv"
        )
        with open(count_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["scenarioName", "bestCount", "ltBksCount"])
            for name in scenario_names:
                writer.writerow([name, best_counts[name], lt_bks_counts[name]])
        logger.info("Last-stage-only-obj count CSV written to %s", count_path)

    _MCF_LB_REF_OBJ_COLUMNS: tuple[str, ...] = (
        "lastStageOnlyBound",
        "lastStageOnlyObj",
        "dispatchedObj",
        "profileFixObj",
        "profileFixBound",
    )
    _MCF_LB_STEP_SEC_COLUMNS: tuple[str, ...] = (
        "mcfSolveSec",
        "lastStageCpSatSec",
        "dispatchSec",
        "profileFixCpSatSec",
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
            csv_path = self.output_dir / f"{sc.name}_mcf_lb_analysis.csv"
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

        path = self.output_dir / f"{self.output_dir.name}_mcf_lb_dashboard.html"
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
            csv_path = self.output_dir / f"{sc.name}_mcf_lb_analysis.csv"
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

        path = (
            self.output_dir
            / f"{self.output_dir.name}_mcf_lb_lastStageOnlyObj_BKS_wintie_pivot.html"
        )
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
            csv_path = self.output_dir / f"{sc.name}_mcf_lb_analysis.csv"
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
        path = (
            self.output_dir
            / f"{self.output_dir.name}_mcf_lb_lastStageOnlyObj_BKS_wintie_table.html"
        )
        path.write_text(payload, encoding="utf8")
        logger.info("MCF-LB lastStageOnlyObj vs BKS win/tie table written to %s", path)

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
        """Render Gantt PNGs from every `*_schedule.yaml` under output_dir, plus
        signed C-cost HTML heatmaps from every `*_C_heatmap.yaml`.

        Gated by ``draw_gantt``. When enabled, rendering fans out across a
        ``ProcessPoolExecutor`` sized by ``painter_thread_cnt``; matplotlib /
        plotly are imported inside the worker so the algorithm path still
        pays nothing.

        Preemptive schedule YAMLs (``*_mcf_preemptive_schedule.yaml``) use a
        different schema and a dedicated renderer. Heatmap YAMLs
        (``*_C_heatmap.yaml``) are written by ``apply_lb_by_mcf`` when its
        ``draw_heatmap`` kwarg is True.
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
        heatmap_paths = sorted(self.output_dir.rglob("*_C_heatmap.yaml"))

        jobs: list[tuple[Path, Any]] = (
            [(p, _render_gantt_from_yaml) for p in regular_paths]
            + [(p, _render_preemptive_gantt_from_yaml) for p in preemptive_paths]
            + [(p, _render_heatmap_from_yaml) for p in heatmap_paths]
        )
        if not jobs:
            return

        worker_cnt = max(1, min(self.painter_thread_cnt, len(jobs)))
        logger.info(
            "Rendering %d artifacts (%d Gantt, %d preemptive, %d heatmap) "
            "with %d worker(s)",
            len(jobs),
            len(regular_paths),
            len(preemptive_paths),
            len(heatmap_paths),
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
