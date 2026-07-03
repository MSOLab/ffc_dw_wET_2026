"""Tests for post_run_pivot builder functions.

Covers compute_cp_gaps, read_csr_cp_trajectory_endpoint,
collect_cp_gap_rows, _merge_instance_meta, and build_rpdf_comparison_df
regression.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from ffc_ddw_sum_et.orchestration.post_run_pivot import (
    _merge_instance_meta,
    build_rpdf_comparison_df,
    collect_cp_gap_rows,
    compute_cp_gaps,
    read_csr_cp_trajectory_endpoint,
)

# --- compute_cp_gaps ---


class TestComputeCpGaps:
    @pytest.mark.parametrize(
        "ub,lb,expected",
        [
            (None, 0, (None, None)),
            (None, 50, (None, None)),
            # lb is None (no bound logged): both gaps undefined, must not raise.
            (100, None, (None, None)),
            (0, None, (None, None)),
            (0, 0, (0.0, 0.0)),
            (100, 40, (1.5, 0.6)),
            (100, 0, (None, 1.0)),
            (50, 25, (1.0, 0.5)),
            (200, 50, (3.0, 0.75)),
        ],
    )
    def test_compute_cp_gaps(self, ub, lb, expected):
        assert compute_cp_gaps(ub, lb) == expected

    def test_compute_cp_gaps_lb_negative(self):
        # Negative LB is unusual but should still compute
        assert compute_cp_gaps(100, -10) == ((100 - (-10)) / (-10), (100 - (-10)) / 100)


# --- read_csr_cp_trajectory_endpoint ---


class TestReadCsrCpTrajectoryEndpoint:
    def test_normal_trajectory(self, tmp_path: Path):
        path = tmp_path / "test_csr_cp_trajectory.json"
        path.write_text(
            json.dumps(
                {
                    "elapsed_sec": [0.1, 0.2, 0.3],
                    "obj_value": [None, 150.0, 100.0],
                    "obj_bound": [0.0, 30.0, 40.0],
                }
            )
        )
        ub, lb, elapsed = read_csr_cp_trajectory_endpoint(path)
        assert ub == 100.0
        assert lb == 40.0
        assert elapsed == 0.3

    def test_all_null_obj_value(self, tmp_path: Path):
        path = tmp_path / "test_csr_cp_trajectory.json"
        path.write_text(
            json.dumps(
                {
                    "elapsed_sec": [0.1],
                    "obj_value": [None, None],
                    "obj_bound": [0.0],
                }
            )
        )
        ub, lb, elapsed = read_csr_cp_trajectory_endpoint(path)
        assert ub is None
        assert lb == 0.0

    def test_empty_arrays(self, tmp_path: Path):
        path = tmp_path / "test_csr_cp_trajectory.json"
        path.write_text(
            json.dumps(
                {
                    "elapsed_sec": [],
                    "obj_value": [],
                    "obj_bound": [],
                }
            )
        )
        ub, lb, elapsed = read_csr_cp_trajectory_endpoint(path)
        assert ub is None
        assert lb is None
        assert elapsed is None

    def test_missing_keys(self, tmp_path: Path):
        path = tmp_path / "test_csr_cp_trajectory.json"
        path.write_text(json.dumps({}))
        ub, lb, elapsed = read_csr_cp_trajectory_endpoint(path)
        assert ub is None
        assert lb is None
        assert elapsed is None

    def test_corrupted_json(self, tmp_path: Path):
        path = tmp_path / "test_csr_cp_trajectory.json"
        path.write_text("not json")
        ub, lb, elapsed = read_csr_cp_trajectory_endpoint(path)
        assert ub is None
        assert lb is None
        assert elapsed is None


# --- collect_cp_gap_rows ---


class TestCollectCpGapRows:
    def _make_tree(self, tmp_path: Path):
        """Create a synthetic run directory tree with trajectory files."""
        scenarios = [
            ("csr16_v3", "pra01"),
            ("csr16_v3", "pra02"),
            ("csr32_v3", "pra01"),
            ("csr16_mixed", "pra01"),
        ]
        for scenario, instance in scenarios:
            dirpath = tmp_path / scenario / instance / "progress"
            dirpath.mkdir(parents=True, exist_ok=True)
            traj = dirpath / f"{instance}_csr_cp_trajectory.json"
            traj.write_text(
                json.dumps(
                    {
                        "elapsed_sec": [1.0, 2.0],
                        "obj_value": [None, 200.0, 180.0],
                        "obj_bound": [10.0, 50.0],
                    }
                )
            )
        return tmp_path

    def test_v3_filter(self, tmp_path: Path):
        root = self._make_tree(tmp_path)
        df = collect_cp_gap_rows(root, init_filter="v3")
        assert len(df) == 3
        assert all(s.endswith("_v3") for s in df["scenarioName"])

    def test_all_filter(self, tmp_path: Path):
        root = self._make_tree(tmp_path)
        df = collect_cp_gap_rows(root, init_filter=None)
        assert len(df) == 4

    def test_factor_parsing(self, tmp_path: Path):
        root = self._make_tree(tmp_path)
        df = collect_cp_gap_rows(root, init_filter="v3")
        factors = set(df["factor"])
        assert factors == {16, 32}

    def test_init_parsing(self, tmp_path: Path):
        root = self._make_tree(tmp_path)
        df = collect_cp_gap_rows(root, init_filter="v3")
        inits = set(df["init"])
        assert inits == {"v3"}


# --- _merge_instance_meta ---


class TestMergeInstanceMeta:
    def test_left_join(self, tmp_path: Path):
        hybrid = tmp_path / "hybrid.csv"
        hybrid.write_text(
            "ffc_ddw_sum_et_filename,insIndex\npra01.txt,01\npra03.txt,03\n"
        )

        bks = tmp_path / "bks.csv"
        bks.write_text(
            "insIndex,n,c,totalMcCount,T,R,W,BKS_data\n01,10,5,20,3,2,1,100\n03,12,6,24,3,2,1,90\n"
        )

        df = pd.DataFrame({"instanceName": ["pra01", "pra02", "pra03"]})
        meta_df, bks_df = _merge_instance_meta(df, hybrid, bks)

        assert len(meta_df) == 3
        assert meta_df.loc[0, "insIndex"] == "01"
        assert pd.isna(meta_df.loc[1, "insIndex"])  # pra02 not in hybrid

    def test_inner_join(self, tmp_path: Path):
        hybrid = tmp_path / "hybrid.csv"
        hybrid.write_text("ffc_ddw_sum_et_filename,insIndex\npra01.txt,01\n")

        bks = tmp_path / "bks.csv"
        bks.write_text(
            "insIndex,n,c,totalMcCount,T,R,W,BKS_data\n01,10,5,20,3,2,1,100\n"
        )

        df = pd.DataFrame({"instanceName": ["pra01", "pra02"]})
        meta_df, bks_df = _merge_instance_meta(df, hybrid, bks, how="inner")

        assert len(meta_df) == 1
        assert meta_df.iloc[0]["instanceName"] == "pra01"


# --- build_rpdf_comparison_df regression ---


class TestBuildRpdfComparisonDf:
    def test_basic_flow(self, tmp_path: Path):
        """Smoke test: summary with valid instanceName produces expected columns."""
        summary = tmp_path / "summary.csv"
        summary.write_text(
            "instanceName,scenarioName,bestObj,elapsedTime,timelimit,error\n"
            "pra01,csr16_v3,100,10.0,10.0,\n"
            "pra02,csr16_v3,90,8.0,10.0,\n"
        )

        hybrid = tmp_path / "hybrid.csv"
        hybrid.write_text(
            "ffc_ddw_sum_et_filename,insIndex\npra01.txt,01\npra02.txt,02\n"
        )

        bks = tmp_path / "bks.csv"
        bks.write_text(
            "insIndex,n,c,totalMcCount,T,R,W,BKS_data\n"
            "01,10,5,20,3,2,1,110\n"
            "02,10,5,20,3,2,1,95\n"
        )

        df = build_rpdf_comparison_df(pd.read_csv(summary), hybrid, bks)

        assert len(df) == 2
        assert list(df.columns) == [
            "insIndex",
            "scenarioName",
            "n",
            "c",
            "totalMcCount",
            "T",
            "R",
            "W",
            "BKS_data",
            "bestObj",
            "RPDf_BKS_data",
            "elapsedTime",
            "timelimit",
            "time%",
        ]
        # RPDf = rpd_f(bestObj, BKS_data) = 2*(obj-ref)/(obj+ref)
        # rpd_f(100, 110) = 2*(100-110)/(100+110) = -20/210 = -0.095238...
        assert df.iloc[0]["insIndex"] == "01"
        assert df.iloc[0]["RPDf_BKS_data"] == pytest.approx(
            2 * (100 - 110) / (100 + 110)
        )
        assert df.iloc[0]["timelimit"] == 10 * 5 * 0.09  # n*c*factor
        assert df.iloc[0]["time%"] == 10.0 / (10 * 5 * 0.09)
