from io import StringIO
from pathlib import Path

import pytest

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.ffc_params import FFcParameters

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_pra_instance(relative_path: str) -> FFcDDWParameters:
    instance_path = REPO_ROOT / relative_path
    with instance_path.open() as stream:
        return FFcDDWParameters.from_pra_2017_data(instance_path.name, stream)


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


# -------------------------------------------------------------------
# create_instance_of_stage_subset
# -------------------------------------------------------------------


def test_create_instance_of_stage_subset_preserves_forward_order() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    # Intentionally pass the stage IDs in a scrambled set to prove the final
    # order comes from ``original.stage_id_list``, not from the caller.
    subset = FFcDDWParameters.create_instance_of_stage_subset(
        original, {"i3", "i0", "i2"}
    )

    assert subset.stage_id_list == ["i0", "i2", "i3"]
    assert subset.stage_count == 3
    assert subset.job_id_list == original.job_id_list
    assert subset.job_count == original.job_count

    for stage_id in subset.stage_id_list:
        assert (
            subset.stage_2_machines_map[stage_id]
            == original.stage_2_machines_map[stage_id]
        )
        for job_id in subset.job_id_list:
            assert (
                subset.stage_2_job_2_p_map[stage_id][job_id]
                == original.stage_2_job_2_p_map[stage_id][job_id]
            )


def test_create_instance_of_stage_subset_reverses_stage_seq_when_requested() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    subset = FFcDDWParameters.create_instance_of_stage_subset(
        original, {"i0", "i1", "i2", "i3"}, reverse_stage_seq=True
    )

    assert subset.stage_id_list == ["i3", "i2", "i1", "i0"]
    # Processing times must follow the reversed stage order per job.
    for job_id in subset.job_id_list:
        original_durations = [
            original.job_2_stage_2_p_map[job_id][s] for s in ["i0", "i1", "i2", "i3"]
        ]
        subset_durations = [
            subset.job_2_stage_2_p_map[job_id][s] for s in subset.stage_id_list
        ]
        assert subset_durations == list(reversed(original_durations))


def test_create_instance_of_stage_subset_propagates_ddw_fields() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    subset = FFcDDWParameters.create_instance_of_stage_subset(original, {"i1", "i2"})

    assert subset.job_2_due_window_map == original.job_2_due_window_map
    assert subset.job_2_ewt_map == original.job_2_ewt_map
    assert subset.job_2_twt_map == original.job_2_twt_map
    assert subset.generation_params == original.generation_params
    assert subset.name == original.name


def test_create_instance_of_stage_subset_full_subset_matches_original_shape() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    subset = FFcDDWParameters.create_instance_of_stage_subset(
        original, set(original.stage_id_list)
    )

    assert subset.stage_id_list == original.stage_id_list
    assert subset.stage_2_job_2_p_map == original.stage_2_job_2_p_map


def test_create_instance_of_stage_subset_rejects_invalid_stage_id() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    with pytest.raises(ValueError, match="Stage subset contains invalid stage IDs"):
        FFcDDWParameters.create_instance_of_stage_subset(
            original, {"i0", "not_a_stage"}
        )


def test_create_instance_of_stage_subset_rejects_empty_subset() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )

    with pytest.raises(ValueError, match="Stage subset must be non-empty"):
        FFcDDWParameters.create_instance_of_stage_subset(original, set())


def test_create_instance_of_stage_subset_rejects_non_ddw_instance() -> None:
    # A bare FFcParameters instance must be rejected — only FFcDDWParameters
    # carry the due-window / weight fields required by the DDW overload.
    minimal = FFcParameters.__new__(FFcParameters)
    with pytest.raises(TypeError, match="requires FFcDDWParameters"):
        FFcDDWParameters.create_instance_of_stage_subset(minimal, {"i0"})


# -----------------------------------------------------------------------------
# with_stage_processing_time_increment
# -----------------------------------------------------------------------------


def test_with_stage_processing_time_increment_inflates_only_target_stage() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )
    target_stage = original.stage_id_list[-1]

    augmented = FFcDDWParameters.with_stage_processing_time_increment(
        original, target_stage, 5
    )

    for stage_id in original.stage_id_list:
        for job_id in original.job_id_list:
            expected = original.stage_2_job_2_p_map[stage_id][job_id]
            if stage_id == target_stage:
                expected = expected + 5
            assert augmented.stage_2_job_2_p_map[stage_id][job_id] == expected
    # Non-processing fields are preserved.
    assert augmented.job_2_due_window_map == original.job_2_due_window_map
    assert augmented.job_2_ewt_map == original.job_2_ewt_map
    assert augmented.job_2_twt_map == original.job_2_twt_map
    assert augmented.stage_id_list == original.stage_id_list
    # Original instance is untouched.
    for job_id in original.job_id_list:
        assert (
            original.stage_2_job_2_p_map[target_stage][job_id]
            == augmented.stage_2_job_2_p_map[target_stage][job_id] - 5
        )


def test_with_stage_processing_time_increment_zero_clones_unchanged() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )
    target_stage = original.stage_id_list[-1]

    clone = FFcDDWParameters.with_stage_processing_time_increment(
        original, target_stage, 0
    )

    assert clone.stage_2_job_2_p_map == original.stage_2_job_2_p_map


def test_with_stage_processing_time_increment_rejects_negative() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )
    with pytest.raises(ValueError, match="non-negative integer"):
        FFcDDWParameters.with_stage_processing_time_increment(
            original, original.stage_id_list[-1], -1
        )


def test_with_stage_processing_time_increment_rejects_invalid_stage() -> None:
    original = _load_pra_instance(
        "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
    )
    with pytest.raises(ValueError, match="not in instance.stage_id_list"):
        FFcDDWParameters.with_stage_processing_time_increment(
            original, "not_a_stage", 1
        )


def test_with_stage_processing_time_increment_rejects_non_ddw_instance() -> None:
    minimal = FFcParameters.__new__(FFcParameters)
    with pytest.raises(TypeError, match="requires FFcDDWParameters"):
        FFcDDWParameters.with_stage_processing_time_increment(minimal, "i0", 1)


# -----------------------------------------------------------------------------
# coarsen_time_resolution
# -----------------------------------------------------------------------------


def _make_small_instance() -> FFcDDWParameters:
    """Construct a minimal 2-job × 2-stage instance for coarsen tests."""
    import pandas as pd

    from ffc_ddw_sum_et.parameters.base.job_stage_p import (
        JobStageProcessingTimeManager,
    )

    # p values: job0=[10, 20], job1=[30, 40]
    df = pd.DataFrame([[10, 20], [30, 40]])
    p_manager = JobStageProcessingTimeManager("test_p", df)
    return FFcDDWParameters(
        name="test_instance",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=p_manager,
        job_2_due_window_map={"j0": (96, 749), "j1": (100, 200)},
        job_2_ewt_map={"j0": 2, "j1": 3},
        job_2_twt_map={"j0": 4, "j1": 5},
        generation_params=None,
    )


def test_coarsen_time_resolution_applies_ceil_to_processing_times() -> None:
    instance = _make_small_instance()
    factor = 7

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, factor)

    import math

    for job_id in instance.job_id_list:
        for stage_id in instance.stage_id_list:
            original_p = instance.job_2_stage_2_p_map[job_id][stage_id]
            expected = math.ceil(original_p / factor)
            assert coarsened.job_2_stage_2_p_map[job_id][stage_id] == expected


def test_coarsen_time_resolution_applies_ceil_to_due_windows() -> None:
    """96..749 with factor=50 must become 2..15."""
    instance = _make_small_instance()
    factor = 50

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, factor)

    assert coarsened.job_2_due_window_map["j0"] == (2, 15)


def test_coarsen_time_resolution_all_p_ge_1() -> None:
    """All coarsened processing times must be >= 1 when originals are > 0."""
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, 50)

    for job_id in coarsened.job_id_list:
        for stage_id in coarsened.stage_id_list:
            assert coarsened.job_2_stage_2_p_map[job_id][stage_id] >= 1


def test_coarsen_time_resolution_lower_le_upper_preserved() -> None:
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, 50)

    for job_id in coarsened.job_id_list:
        lower, upper = coarsened.job_2_due_window_map[job_id]
        assert lower <= upper


def test_coarsen_time_resolution_preserves_weights_and_layout() -> None:
    instance = _make_small_instance()
    factor = 50

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, factor)

    assert coarsened.job_2_ewt_map == instance.job_2_ewt_map
    assert coarsened.job_2_twt_map == instance.job_2_twt_map
    assert coarsened.job_id_list == instance.job_id_list
    assert coarsened.stage_id_list == instance.stage_id_list
    assert coarsened.stage_2_machines_map == instance.stage_2_machines_map
    assert coarsened.generation_params == instance.generation_params


def test_coarsen_time_resolution_name_has_coarsen_suffix() -> None:
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_time_resolution(instance, 50)

    assert coarsened.name == "test_instance_coarsen50"


def test_coarsen_time_resolution_raises_value_error_for_zero_factor() -> None:
    instance = _make_small_instance()

    with pytest.raises(ValueError):
        FFcDDWParameters.coarsen_time_resolution(instance, 0)


def test_coarsen_time_resolution_raises_value_error_for_negative_factor() -> None:
    instance = _make_small_instance()

    with pytest.raises(ValueError):
        FFcDDWParameters.coarsen_time_resolution(instance, -1)


def test_coarsen_time_resolution_raises_type_error_for_non_ddw_instance() -> None:
    minimal = FFcParameters.__new__(FFcParameters)

    with pytest.raises(TypeError, match="requires FFcDDWParameters"):
        FFcDDWParameters.coarsen_time_resolution(minimal, 50)


def test_coarsen_time_resolution_does_not_mutate_original() -> None:
    instance = _make_small_instance()
    original_p = {
        j: {s: instance.job_2_stage_2_p_map[j][s] for s in instance.stage_id_list}
        for j in instance.job_id_list
    }
    original_dw = instance.job_2_due_window_map.copy()

    FFcDDWParameters.coarsen_time_resolution(instance, 50)

    for j in instance.job_id_list:
        for s in instance.stage_id_list:
            assert instance.job_2_stage_2_p_map[j][s] == original_p[j][s]
    assert instance.job_2_due_window_map == original_dw
