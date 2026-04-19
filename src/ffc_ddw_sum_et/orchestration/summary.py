"""Per-instance CSV summary, shaped after hybridflowshop/hfs_summary.py.

An ``FFcDDWSummary`` packs one instance's inputs + outputs into a single CSV
row. ``save()`` writes the header on first call and appends a row on each
subsequent call, matching the hybridflowshop pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


INPUT_HEADER = "instanceName,jobCount,stageCount,machinesPerStage,timelimit"


@dataclass
class FFcDDWInputSummary:
    name: str
    job_count: int
    stage_count: int
    machines_per_stage: int
    timelimit: float

    def comma_separated_values(self) -> str:
        return (
            f"{self.name},{self.job_count},{self.stage_count},"
            f"{self.machines_per_stage},{self.timelimit}"
        )

    @staticmethod
    def header() -> str:
        return INPUT_HEADER


@dataclass
class FFcDDWOutputSummary:
    """Flat output metrics for one instance."""

    scenario_name: str
    work_status: str | None
    init_obj: float | None
    init_bound: float | None
    best_obj: float | None
    best_bound: float | None
    elapsed_time: float
    improvement_ratio: float | None
    has_incumbent: bool
    report_count: int
    method_call_counts: str

    def to_string_dict(self) -> dict[str, str]:
        def _fmt(value: Any) -> str:
            return "" if value is None else str(value)

        return {
            "scenarioName": _fmt(self.scenario_name),
            "workStatus": _fmt(self.work_status),
            "initObj": _fmt(self.init_obj),
            "initBound": _fmt(self.init_bound),
            "bestObj": _fmt(self.best_obj),
            "bestBound": _fmt(self.best_bound),
            "elapsedTime": f"{self.elapsed_time:.3f}",
            "improvementRatio": _fmt(self.improvement_ratio),
            "hasIncumbent": _fmt(self.has_incumbent),
            "reportCount": _fmt(self.report_count),
            "methodCallCounts": _fmt(self.method_call_counts),
        }


@dataclass
class FFcDDWSummary:
    inputs: FFcDDWInputSummary
    outputs: FFcDDWOutputSummary
    extra_outputs: dict[str, Any] = field(default_factory=dict)

    def _stringified_outputs(self) -> dict[str, str]:
        out = dict(self.outputs.to_string_dict())
        out.update(
            {k: ("" if v is None else str(v)) for k, v in self.extra_outputs.items()}
        )
        return out

    def header_row(self) -> str:
        input_header = self.inputs.header()
        output_headers = ",".join(self._stringified_outputs().keys())
        return f"{input_header},{output_headers}"

    def value_row(self) -> str:
        input_values = self.inputs.comma_separated_values()
        output_values = ",".join(self._stringified_outputs().values())
        return f"{input_values},{output_values}"

    def save(self, output_path: Path, encoding: str = "utf-8") -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            with open(output_path, "a", encoding=encoding) as f:
                f.write("\n" + self.value_row())
        else:
            with open(output_path, "w", encoding=encoding) as f:
                f.write(self.header_row())
                f.write("\n" + self.value_row())
