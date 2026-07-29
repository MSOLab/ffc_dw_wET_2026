"""Tests for CSR analysis CSV reporter and read_csr_winner helper."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import InstanceResult
from ffc_ddw_sum_et.orchestration.reporting import (
    FFcDDWReporter,
    ScenarioResult,
)
from ffc_ddw_sum_et.report.csr_candidate_analysis import read_csr_winner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path, run_id: str = "run_42") -> FFcArtifactLayout:
    return FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)


def _make_ir(
    instance_name: str = "ins",
    *,
    obj_value: float | None = 10.0,
    elapsed_time: float = 1.0,
    job_count: int | None = 10,
    stage_count: int | None = 5,
) -> InstanceResult:
    return InstanceResult(
        instance_name=instance_name,
        elapsed_time=elapsed_time,
        obj_value=obj_value,
        obj_bound=None,
        work_status="FEASIBLE" if obj_value is not None else None,
        has_incumbent=obj_value is not None,
        method_call_counts={},
        report_count=1 if obj_value is not None else 0,
        first_obj_value=obj_value,
        first_obj_bound=None,
        error=None,
        job_count=job_count,
        stage_count=stage_count,
    )


def _write_csr_candidates_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "valid",
        "coarse_obj",
        "restored_obj",
        "phase",
        "snapshot",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# read_csr_winner (pure)
# ---------------------------------------------------------------------------


class TestReadCsrWinner:
    def test_valid_with_min_restored_obj(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "cands.csv"
        _write_csr_candidates_csv(
            csv_path,
            [
                {
                    "valid": "True",
                    "coarse_obj": "100",
                    "restored_obj": "110",
                    "phase": "p1",
                    "snapshot": "s1",
                },
                {
                    "valid": "True",
                    "coarse_obj": "90",
                    "restored_obj": "95",
                    "phase": "p2",
                    "snapshot": "s2",
                },
            ],
        )
        result = read_csr_winner(csv_path)
        assert result == (90.0, 95.0)

    def test_invalid_rows_skipped(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "cands.csv"
        _write_csr_candidates_csv(
            csv_path,
            [
                {
                    "valid": "False",
                    "coarse_obj": "50",
                    "restored_obj": "60",
                    "phase": "p1",
                    "snapshot": "s1",
                },
                {
                    "valid": "True",
                    "coarse_obj": "70",
                    "restored_obj": "80",
                    "phase": "p2",
                    "snapshot": "s2",
                },
            ],
        )
        result = read_csr_winner(csv_path)
        assert result == (70.0, 80.0)

    def test_valid_with_blank_restored_obj_skipped(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "cands.csv"
        _write_csr_candidates_csv(
            csv_path,
            [
                {
                    "valid": "True",
                    "coarse_obj": "50",
                    "restored_obj": "",
                    "phase": "p1",
                    "snapshot": "s1",
                },
                {
                    "valid": "True",
                    "coarse_obj": "70",
                    "restored_obj": "80",
                    "phase": "p2",
                    "snapshot": "s2",
                },
            ],
        )
        result = read_csr_winner(csv_path)
        assert result == (70.0, 80.0)

    def test_all_invalid_returns_none(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "cands.csv"
        _write_csr_candidates_csv(
            csv_path,
            [
                {
                    "valid": "False",
                    "coarse_obj": "50",
                    "restored_obj": "60",
                    "phase": "p1",
                    "snapshot": "s1",
                },
            ],
        )
        assert read_csr_winner(csv_path) is None

    def test_blank_coarse_obj_gives_nan(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "cands.csv"
        _write_csr_candidates_csv(
            csv_path,
            [
                {
                    "valid": "True",
                    "coarse_obj": "",
                    "restored_obj": "80",
                    "phase": "p1",
                    "snapshot": "s1",
                },
            ],
        )
        result = read_csr_winner(csv_path)
        assert result is not None
        coarse, restored = result
        assert math.isnan(coarse)
        assert restored == 80.0


# ---------------------------------------------------------------------------
# _write_csr_analysis_csv (integration)
# ---------------------------------------------------------------------------


class TestWriteCsrAnalysisCsv:
    def test_writes_rows_for_instances_with_candidates(self, tmp_path: Path) -> None:
        layout = _layout(tmp_path)
        ir1 = _make_ir(
            "Instance_50_5_3_0,2_0,2_10_Rep0",
            elapsed_time=4.5,
            job_count=50,
            stage_count=5,
        )
        ir2 = _make_ir(
            "Instance_50_5_3_0,2_0,2_10_Rep1",
            elapsed_time=3.2,
            job_count=50,
            stage_count=5,
        )
        sc = ScenarioResult(name="csr_k1_tl05_semi", instance_results=[ir1, ir2])
        reporter = FFcDDWReporter(tmp_path, [sc], layout=layout)

        # Write a candidates CSV only for ir1
        cand_path = layout.artifact_path(
            "csr_candidates_csv",
            scenario_name="csr_k1_tl05_semi",
            instance_name="Instance_50_5_3_0,2_0,2_10_Rep0",
        )
        _write_csr_candidates_csv(
            cand_path,
            [
                {
                    "valid": "True",
                    "coarse_obj": "1000",
                    "restored_obj": "1100",
                    "phase": "p1",
                    "snapshot": "s1",
                },
                {
                    "valid": "True",
                    "coarse_obj": "900",
                    "restored_obj": "950",
                    "phase": "p2",
                    "snapshot": "s2",
                },
            ],
        )

        reporter._write_csr_analysis_csv()

        out_path = layout.artifact_path(
            "csr_analysis", scenario_name="csr_k1_tl05_semi"
        )
        assert out_path.exists()
        with open(out_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        assert header == list(reporter._CSR_ANALYSIS_COLUMNS)
        assert len(rows) == 1  # only ir1 had a candidates CSV

        row = rows[0]
        # TL = 0.09 * 50 * 5 = 22.5
        # elapsedSec = 4.5, time% = 4.5/22.5*100 = 20.0
        assert row[8] == "4.5"  # elapsedSec
        assert row[9] == "20.0"  # time%
        assert row[10] == "900.0"  # objValueC (winner coarse)
        assert row[11] == "950.0"  # objValueR (winner restored)

    def test_no_candidates_csv_no_output(self, tmp_path: Path) -> None:
        layout = _layout(tmp_path)
        ir = _make_ir("Instance_50_5_3_0,2_0,2_10_Rep0")
        sc = ScenarioResult(name="sc_no_csr", instance_results=[ir])
        reporter = FFcDDWReporter(tmp_path, [sc], layout=layout)

        reporter._write_csr_analysis_csv()

        out_path = layout.artifact_path("csr_analysis", scenario_name="sc_no_csr")
        assert not out_path.exists()
