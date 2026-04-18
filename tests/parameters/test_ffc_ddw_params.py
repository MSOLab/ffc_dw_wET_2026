from io import StringIO
from pathlib import Path

import pytest

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_from_pra_2017_data_parses_large_instance() -> None:
    instance_path = (
        REPO_ROOT
        / "benchmarks"
        / "PRA2017"
        / "large"
        / "Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    with instance_path.open() as stream:
        params = FFcDDWParameters.from_pra_2017_data(instance_path.name, stream)

    assert params.name == instance_path.name
    assert params.job_count == 50
    assert params.stage_count == 5
    assert params.job_id_list[:3] == ["j00", "j01", "j02"]
    assert params.stage_id_list == ["i0", "i1", "i2", "i3", "i4"]
    assert params.machine_count_per_stage == [3, 3, 3, 3, 3]
    assert params.stage_2_machines_map["i0"] == ["i0_0", "i0_1", "i0_2"]
    assert params.job_2_stage_2_p_map["j00"] == {
        "i0": 34,
        "i1": 5,
        "i2": 90,
        "i3": 12,
        "i4": 31,
    }
    assert params.job_2_stage_2_p_map["j49"] == {
        "i0": 12,
        "i1": 71,
        "i2": 98,
        "i3": 45,
        "i4": 40,
    }
    assert params.job_2_due_window_map["j00"] == (704, 762)
    assert params.job_2_due_window_map["j49"] == (606, 740)
    gp = params.generation_params
    assert gp is not None
    assert gp.n == 50
    assert gp.c == 5
    assert gp.m == 3
    assert gp.T_factor == 0.2
    assert gp.R_factor == 0.2
    assert gp.W_factor == 10
    assert gp.rep == 0


def test_from_pra_2017_data_parses_large_calibration_instance() -> None:
    instance_path = (
        REPO_ROOT
        / "benchmarks"
        / "PRA2017"
        / "largeCalibration"
        / "0_Cal_Instance_50_10_3_0,4_0,2_20.txt"
    )

    with instance_path.open() as stream:
        params = FFcDDWParameters.from_pra_2017_data(instance_path.name, stream)

    assert params.name == instance_path.name
    assert params.job_count == 50
    assert params.stage_count == 10
    assert params.job_id_list[:3] == ["j00", "j01", "j02"]
    assert params.stage_id_list == [
        "i0",
        "i1",
        "i2",
        "i3",
        "i4",
        "i5",
        "i6",
        "i7",
        "i8",
        "i9",
    ]
    assert params.machine_count_per_stage == [3] * 10
    assert params.stage_2_machines_map["i9"] == ["i9_0", "i9_1", "i9_2"]
    assert params.job_2_stage_2_p_map["j00"] == {
        "i0": 36,
        "i1": 50,
        "i2": 27,
        "i3": 68,
        "i4": 16,
        "i5": 88,
        "i6": 4,
        "i7": 23,
        "i8": 67,
        "i9": 12,
    }
    assert params.job_2_stage_2_p_map["j49"] == {
        "i0": 47,
        "i1": 25,
        "i2": 79,
        "i3": 99,
        "i4": 54,
        "i5": 89,
        "i6": 74,
        "i7": 49,
        "i8": 7,
        "i9": 5,
    }
    assert params.job_2_due_window_map["j00"] == (643, 771)
    assert params.job_2_due_window_map["j49"] == (835, 941)
    assert params.generation_params is None  # non-standard filename


def test_from_pra_2017_data_rejects_invalid_marker() -> None:
    with pytest.raises(ValueError, match="Expected 'HFSDDW' marker"):
        FFcDDWParameters.from_pra_2017_data("invalid.txt", StringIO("HFSPRA\n"))


def test_from_pra_2017_data_rejects_malformed_ddw_row() -> None:
    malformed_stream = StringIO(
        "\n".join(
            [
                "HFSDDW",
                "1 2 1",
                "0 7",
                "LBCmax: 7",
                "RELDUE",
                "-1 5 1 1",
                "DDW",
                "9",
            ]
        )
    )

    with pytest.raises(ValueError, match="Expected 2 integers in DDW row 0"):
        FFcDDWParameters.from_pra_2017_data("invalid_ddw.txt", malformed_stream)


def test_from_pra_data_raises_guidance_error() -> None:
    with pytest.raises(NotImplementedError, match="from_pra_2017_data"):
        FFcDDWParameters.from_pra_data("legacy.txt", StringIO(""))
