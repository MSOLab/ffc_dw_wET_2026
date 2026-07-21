"""Tests for post_run_pivot builder functions.

Covers compute_cp_gaps, _merge_instance_meta, and build_rpdf_comparison_df.
"""

from pathlib import Path

import pandas as pd
import pytest

from ffc_ddw_sum_et.orchestration.post_run_pivot import (
    _merge_instance_meta,
    build_rpdf_comparison_df,
    compute_cp_gaps,
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
