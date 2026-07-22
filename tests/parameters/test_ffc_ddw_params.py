from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
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
# coarsen_processing_times
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


def test_coarsen_processing_times_applies_ceil_to_processing_times() -> None:
    instance = _make_small_instance()
    factor = 7

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, factor)

    import math

    for job_id in instance.job_id_list:
        for stage_id in instance.stage_id_list:
            original_p = instance.job_2_stage_2_p_map[job_id][stage_id]
            expected = math.ceil(original_p / factor)
            assert coarsened.job_2_stage_2_p_map[job_id][stage_id] == expected


def test_coarsen_processing_times_preserves_due_windows() -> None:
    """Due windows must be preserved at the original scale."""
    instance = _make_small_instance()
    factor = 50

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, factor)

    assert coarsened.job_2_due_window_map["j0"] == (96, 749)
    assert coarsened.job_2_due_window_map["j1"] == (100, 200)


def test_coarsen_processing_times_all_p_ge_1() -> None:
    """All coarsened processing times must be >= 1 when originals are > 0."""
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 50)

    for job_id in coarsened.job_id_list:
        for stage_id in coarsened.stage_id_list:
            assert coarsened.job_2_stage_2_p_map[job_id][stage_id] >= 1


def test_coarsen_processing_times_lower_le_upper_preserved() -> None:
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 50)

    for job_id in coarsened.job_id_list:
        lower, upper = coarsened.job_2_due_window_map[job_id]
        assert lower <= upper


def test_coarsen_processing_times_preserves_weights_and_layout() -> None:
    instance = _make_small_instance()
    factor = 50

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, factor)

    assert coarsened.job_2_ewt_map == instance.job_2_ewt_map
    assert coarsened.job_2_twt_map == instance.job_2_twt_map
    assert coarsened.job_id_list == instance.job_id_list
    assert coarsened.stage_id_list == instance.stage_id_list
    assert coarsened.stage_2_machines_map == instance.stage_2_machines_map
    assert coarsened.generation_params == instance.generation_params


def test_coarsen_processing_times_name_has_coarsen_k_suffix() -> None:
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 50)

    assert coarsened.name == "test_instance_coarsen_k50"


def test_coarsen_processing_times_mode_round() -> None:
    """``mode="round"`` → ``max(round(p/factor), 1)``."""
    instance = _make_small_instance()
    factor = 7

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, factor, mode="round"
    )

    # ceil: ceil(10/7)=2, ceil(20/7)=3, ceil(30/7)=5, ceil(40/7)=6
    # round: round(10/7)=1, round(20/7)=3, round(30/7)=4, round(40/7)=6
    # floor:  10//7=1,     20//7=2,      30//7=4,      40//7=5
    expected = {
        ("j0", "i0"): 1,
        ("j0", "i1"): 3,
        ("j1", "i0"): 4,
        ("j1", "i1"): 6,
    }
    for (j, i), e in expected.items():
        assert coarsened.job_2_stage_2_p_map[j][i] == e


def test_coarsen_processing_times_mode_floor() -> None:
    """``mode="floor"`` → ``max(p // factor, 1)``."""
    instance = _make_small_instance()
    factor = 7

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, factor, mode="floor"
    )

    expected = {
        ("j0", "i0"): 1,
        ("j0", "i1"): 2,
        ("j1", "i0"): 4,
        ("j1", "i1"): 5,
    }
    for (j, i), e in expected.items():
        assert coarsened.job_2_stage_2_p_map[j][i] == e


def test_coarsen_processing_times_mode_round_all_ge_1() -> None:
    """mode="round": p' >= 1 for every operation, including p=1 at large factor."""
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 50, mode="round")

    for j in coarsened.job_id_list:
        for i in coarsened.stage_id_list:
            assert coarsened.job_2_stage_2_p_map[j][i] >= 1


def test_coarsen_processing_times_mode_floor_all_ge_1() -> None:
    """mode="floor": p' >= 1 even when p < factor (would floor to 0)."""
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 50, mode="floor")

    for j in coarsened.job_id_list:
        for i in coarsened.stage_id_list:
            assert coarsened.job_2_stage_2_p_map[j][i] >= 1


def test_coarsen_processing_times_mode_cumulative() -> None:
    """``mode="cumulative"`` → round cumulative sum, derive per-stage by subtraction."""
    instance = _make_small_instance()
    factor = 7

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, factor, mode="cumulative"
    )

    expected = {
        ("j0", "i0"): 1,
        ("j0", "i1"): 3,
        ("j1", "i0"): 4,
        ("j1", "i1"): 6,
    }
    for (j, i), e in expected.items():
        assert coarsened.job_2_stage_2_p_map[j][i] == e


def test_coarsen_processing_times_mode_cumulative_lower_bound_recursion() -> None:
    """Floor-triggering instance: running-sum recursion diverges from C_i − C_{i-1}.

    With p=[1,1,1,1,1] (5 stages, one job) and K=3:
    - C_i  = round(cumsum/3): [round(1/3)=0, round(2/3)=1, round(3/3)=1,
              round(4/3)=1, round(5/3)=2]
    - C_i − C_{i-1} difference: [0, 1, 0, 0, 1] (starts with 0!)
    - Running-sum recursion with floor: [max(0,1)=1, max(1−1,1)=1, max(1−2,1)=1,
              max(1−3,1)=1, max(2−4,1)=1] → all 1s.
    """
    import pandas as pd

    from ffc_ddw_sum_et.parameters.base.job_stage_p import (
        JobStageProcessingTimeManager,
    )

    df = pd.DataFrame([[1, 1, 1, 1, 1]])
    p_manager = JobStageProcessingTimeManager("test_p", df)
    instance = FFcDDWParameters(
        name="test_instance_lb",
        job_id_list=["j0"],
        stage_id_list=["s0", "s1", "s2", "s3", "s4"],
        stage_2_machines_map={f"s{i}": [f"s{i}_0"] for i in range(5)},
        p_manager=p_manager,
        job_2_due_window_map={"j0": (100, 200)},
        job_2_ewt_map={"j0": 1},
        job_2_twt_map={"j0": 1},
        generation_params=None,
    )

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, 3, mode="cumulative"
    )

    for i in range(5):
        assert coarsened.job_2_stage_2_p_map["j0"][f"s{i}"] == 1


def test_coarsen_processing_times_mode_cumulative_all_ge_1() -> None:
    """mode="cumulative": p' >= 1 for every operation."""
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(
        instance, 50, mode="cumulative"
    )

    for j in coarsened.job_id_list:
        for i in coarsened.stage_id_list:
            assert coarsened.job_2_stage_2_p_map[j][i] >= 1


@pytest.mark.parametrize("mode", ["ceil", "round", "floor", "cumulative"])
def test_coarsen_processing_times_factor_1_is_identity(mode: str) -> None:
    """κ=1 is the identity for every rounding mode.

    ``ceil(p/1) == round(p/1) == p//1 == p``, so a CSR step configured with
    ``factor=1`` does no coarsening at all — it is only the harvest-and-argmin
    wrapper around a sub-budgeted solve flow.  Pinning this keeps the negative
    κ>1 coarsening result from being read as applying to κ=1.
    """
    instance = _make_small_instance()

    coarsened = FFcDDWParameters.coarsen_processing_times(instance, 1, mode=mode)

    assert coarsened.job_2_stage_2_p_map == instance.job_2_stage_2_p_map


def test_coarsen_processing_times_invalid_mode() -> None:
    instance = _make_small_instance()

    with pytest.raises(ValueError, match="mode must be one of"):
        FFcDDWParameters.coarsen_processing_times(instance, 7, mode="bogus")


def test_coarsen_processing_times_mode_name_suffix() -> None:
    instance = _make_small_instance()

    assert (
        FFcDDWParameters.coarsen_processing_times(instance, 8).name
        == "test_instance_coarsen_k8"
    )
    assert (
        FFcDDWParameters.coarsen_processing_times(instance, 8, mode="ceil").name
        == "test_instance_coarsen_k8"
    )
    assert (
        FFcDDWParameters.coarsen_processing_times(instance, 8, mode="round").name
        == "test_instance_coarsen_k8_round"
    )
    assert (
        FFcDDWParameters.coarsen_processing_times(instance, 8, mode="floor").name
        == "test_instance_coarsen_k8_floor"
    )
    assert (
        FFcDDWParameters.coarsen_processing_times(instance, 8, mode="cumulative").name
        == "test_instance_coarsen_k8_cumulative"
    )


def test_coarsen_processing_times_raises_value_error_for_zero_factor() -> None:
    instance = _make_small_instance()

    with pytest.raises(ValueError):
        FFcDDWParameters.coarsen_processing_times(instance, 0)


def test_coarsen_processing_times_raises_value_error_for_negative_factor() -> None:
    instance = _make_small_instance()

    with pytest.raises(ValueError):
        FFcDDWParameters.coarsen_processing_times(instance, -1)


def test_coarsen_processing_times_raises_type_error_for_non_ddw_instance() -> None:
    minimal = FFcParameters.__new__(FFcParameters)

    with pytest.raises(TypeError, match="requires FFcDDWParameters"):
        FFcDDWParameters.coarsen_processing_times(minimal, 50)


def test_coarsen_processing_times_does_not_mutate_original() -> None:
    instance = _make_small_instance()
    original_p = {
        j: {s: instance.job_2_stage_2_p_map[j][s] for s in instance.stage_id_list}
        for j in instance.job_id_list
    }
    original_dw = instance.job_2_due_window_map.copy()

    FFcDDWParameters.coarsen_processing_times(instance, 50)

    for j in instance.job_id_list:
        for s in instance.stage_id_list:
            assert instance.job_2_stage_2_p_map[j][s] == original_p[j][s]
    assert instance.job_2_due_window_map == original_dw


# -----------------------------------------------------------------------------
# get_eddub_twt_job_sequence
# -----------------------------------------------------------------------------


def _make_eddub_twt_instance() -> FFcDDWParameters:
    """3-job × 1-stage instance with controlled d⁺/w⁺ values.

    job0: d⁺=100, w⁺=5, pos=0
    job1: d⁺=100, w⁺=3,  pos=1
    job2: d⁺=200, w⁺=8,  pos=2
    Expected order: (d⁺ asc, w⁺ desc, pos asc) → [job0, job1, job2]
    """
    return FFcDDWParameters(
        name="eddub_twt_test",
        job_id_list=["job0", "job1", "job2"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="eddub_twt_p", df=pd.DataFrame([[10], [20], [30]])
        ),
        job_2_due_window_map={"job0": (50, 100), "job1": (80, 100), "job2": (150, 200)},
        job_2_ewt_map={"job0": 1, "job1": 1, "job2": 1},
        job_2_twt_map={"job0": 5, "job1": 3, "job2": 8},
    )


def test_get_eddub_twt_job_sequence_d_plus_asc() -> None:
    instance = _make_eddub_twt_instance()
    seq = instance.get_eddub_twt_job_sequence()
    assert seq == ["job0", "job1", "job2"]


def test_get_eddub_twt_job_sequence_w_plus_desc_tiebreak() -> None:
    """Same d⁺: higher w⁺ must come first."""
    instance = FFcDDWParameters(
        name="eddub_twt_tie",
        job_id_list=["a", "b", "c"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="tie_p", df=pd.DataFrame([[1], [1], [1]])
        ),
        job_2_due_window_map={"a": (0, 10), "b": (0, 10), "c": (0, 10)},
        job_2_ewt_map={"a": 1, "b": 1, "c": 1},
        job_2_twt_map={"a": 2, "b": 5, "c": 3},
    )
    seq = instance.get_eddub_twt_job_sequence()
    assert seq == ["b", "c", "a"]  # w⁺ desc: 5, 3, 2


def test_get_eddub_twt_job_sequence_position_tiebreak() -> None:
    """Same d⁺ and w⁺: preserve given (native) order."""
    instance = FFcDDWParameters(
        name="eddub_twt_pos",
        job_id_list=["x", "y", "z"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="pos_p", df=pd.DataFrame([[1], [1], [1]])
        ),
        job_2_due_window_map={"x": (0, 10), "y": (0, 10), "z": (0, 10)},
        job_2_ewt_map={"x": 1, "y": 1, "z": 1},
        job_2_twt_map={"x": 5, "y": 5, "z": 5},
    )
    seq = instance.get_eddub_twt_job_sequence()
    assert seq == ["x", "y", "z"]  # native position order


def test_get_eddub_twt_job_sequence_all_jobs_included() -> None:
    instance = _make_eddub_twt_instance()
    seq = instance.get_eddub_twt_job_sequence()
    assert set(seq) == set(instance.job_id_list)
    assert len(seq) == len(instance.job_id_list)


# -------------------------------------------------------------------
# get_lsl_job_sequence
# -------------------------------------------------------------------


def _make_lsl_instance() -> FFcDDWParameters:
    """3-job × 2-stage instance with controlled d⁺ and p_last values.

    job0: d⁺=100, p_last=10 → slack=90, pos=0
    job1: d⁺=200, p_last=30 → slack=170, pos=1
    job2: d⁺=100, p_last=20 → slack=80,  pos=2
    Expected LSL order (slack asc, pos tie-break): [job2, job0, job1]
    """
    return FFcDDWParameters(
        name="lsl_test",
        job_id_list=["job0", "job1", "job2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="lsl_p", df=pd.DataFrame([[10, 10], [20, 30], [30, 20]])
        ),
        job_2_due_window_map={"job0": (50, 100), "job1": (150, 200), "job2": (50, 100)},
        job_2_ewt_map={"job0": 1, "job1": 1, "job2": 1},
        job_2_twt_map={"job0": 1, "job1": 1, "job2": 1},
    )


def test_get_lsl_job_sequence_slack_asc() -> None:
    instance = _make_lsl_instance()
    seq = instance.get_lsl_job_sequence()
    assert seq == ["job2", "job0", "job1"]  # slack: 80, 90, 170


def test_get_lsl_job_sequence_position_tiebreak() -> None:
    """Same slack: preserve native position order."""
    instance = FFcDDWParameters(
        name="lsl_tie",
        job_id_list=["a", "b", "c"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="lsl_tie_p", df=pd.DataFrame([[5, 10], [10, 10], [15, 10]])
        ),
        job_2_due_window_map={"a": (50, 100), "b": (50, 100), "c": (50, 100)},
        job_2_ewt_map={"a": 1, "b": 1, "c": 1},
        job_2_twt_map={"a": 1, "b": 1, "c": 1},
    )
    # All three have p_last=10, d⁺=100 → slack=90 for all
    # Tie-break by position → [a, b, c]
    seq = instance.get_lsl_job_sequence()
    assert seq == ["a", "b", "c"]


def test_get_lsl_job_sequence_all_jobs_included() -> None:
    instance = _make_lsl_instance()
    seq = instance.get_lsl_job_sequence()
    assert set(seq) == set(instance.job_id_list)
    assert len(seq) == len(instance.job_id_list)


def test_get_lsl_no_clamp_vs_due_weight_pos() -> None:
    """LSL must NOT apply max(0, ...) clamp — negative slack jobs should
    sort before non-negative ones, unlike get_due_weight_pos_job_sequence."""
    instance = FFcDDWParameters(
        name="lsl_neg",
        job_id_list=["a", "b"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="lsl_neg_p", df=pd.DataFrame([[5, 100], [5, 10]])
        ),
        job_2_due_window_map={"a": (50, 50), "b": (50, 100)},
        job_2_ewt_map={"a": 1, "b": 1},
        job_2_twt_map={"a": 1, "b": 1},
    )
    # a: slack = 50-100 = -50, b: slack = 100-10 = 90
    lsl_seq = instance.get_lsl_job_sequence()
    assert lsl_seq == ["a", "b"]  # -50 < 90, no clamp


# -------------------------------------------------------------------
# get_osl_job_sequence
# -------------------------------------------------------------------


def _make_osl_instance() -> FFcDDWParameters:
    """3-job × 2-stage instance with controlled d⁺ and total p values.

    job0: d⁺=100, total_p=10+10=20 → OSL=80,  pos=0
    job1: d⁺=200, total_p=20+30=50 → OSL=150, pos=1
    job2: d⁺=100, total_p=30+20=50 → OSL=50,  pos=2
    Expected OSL order (slack asc, pos tie-break): [job2, job0, job1]
    """
    return FFcDDWParameters(
        name="osl_test",
        job_id_list=["job0", "job1", "job2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="osl_p", df=pd.DataFrame([[10, 10], [20, 30], [30, 20]])
        ),
        job_2_due_window_map={"job0": (50, 100), "job1": (150, 200), "job2": (50, 100)},
        job_2_ewt_map={"job0": 1, "job1": 1, "job2": 1},
        job_2_twt_map={"job0": 1, "job1": 1, "job2": 1},
    )


def test_get_osl_job_sequence_osl_asc() -> None:
    instance = _make_osl_instance()
    seq = instance.get_osl_job_sequence()
    assert seq == ["job2", "job0", "job1"]  # OSL: 50, 80, 150


def test_get_osl_job_sequence_position_tiebreak() -> None:
    """Same OSL: preserve native position order."""
    instance = FFcDDWParameters(
        name="osl_tie",
        job_id_list=["a", "b", "c"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="osl_tie_p", df=pd.DataFrame([[5, 5], [10, 0], [15, 0]])
        ),
        job_2_due_window_map={"a": (50, 100), "b": (50, 100), "c": (50, 100)},
        job_2_ewt_map={"a": 1, "b": 1, "c": 1},
        job_2_twt_map={"a": 1, "b": 1, "c": 1},
    )
    # OSL: a=90, b=90, c=85 → c first, then a, b by position
    seq = instance.get_osl_job_sequence()
    assert seq == ["c", "a", "b"]


def test_get_osl_job_sequence_all_jobs_included() -> None:
    instance = _make_osl_instance()
    seq = instance.get_osl_job_sequence()
    assert set(seq) == set(instance.job_id_list)
    assert len(seq) == len(instance.job_id_list)


def test_get_osl_differs_from_lsl() -> None:
    """OSL uses total p across all stages; LSL uses only last stage.
    When per-job stage distributions differ, the orders must differ."""
    # job0: d⁺=100, total_p=10+10=20, p_last=10 → OSL=80, LSL=90
    # job1: d⁺=100, total_p=5+15=20, p_last=15 → OSL=80, LSL=85
    # OSL: both 80 → tie by position → [job0, job1]
    # LSL: job1=85 < job0=90 → [job1, job0]
    instance = FFcDDWParameters(
        name="osl_vs_lsl",
        job_id_list=["job0", "job1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="osl_vs_lsl_p", df=pd.DataFrame([[10, 10], [5, 15]])
        ),
        job_2_due_window_map={"job0": (50, 100), "job1": (50, 100)},
        job_2_ewt_map={"job0": 1, "job1": 1},
        job_2_twt_map={"job0": 1, "job1": 1},
    )
    osl_seq = instance.get_osl_job_sequence()
    lsl_seq = instance.get_lsl_job_sequence()
    assert osl_seq != lsl_seq
    assert osl_seq == ["job0", "job1"]  # OSL tie → position
    assert lsl_seq == ["job1", "job0"]  # LSL: 85 < 90


def test_get_osl_uses_existing_map() -> None:
    """get_osl_job_sequence must reuse get_job_2_due_date_ub_minus_p_map."""
    instance = _make_osl_instance()
    osl_map = instance.get_job_2_due_date_ub_minus_p_map()
    seq = instance.get_osl_job_sequence()
    # The sequence must be sorted by osl_map values
    positions = [osl_map[j] for j in seq]
    assert positions == sorted(positions)
