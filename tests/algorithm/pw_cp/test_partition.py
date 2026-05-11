"""Tests for OperationPartition / batch helpers."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.pw_cp import (
    OperationPartition,
    build_operation_partition,
    validate_and_get_batch_count,
)


def _batches(*sizes: int) -> list[tuple[tuple[str, str], ...]]:
    """Generate a fake stage batch list with ``sizes[i]`` ops in batch i."""
    out: list[tuple[tuple[str, str], ...]] = []
    j_idx = 0
    for s in sizes:
        out.append(tuple((f"j{j_idx + k}", "m0") for k in range(s)))
        j_idx += s
    return out


def test_5_region_split_middle() -> None:
    batches = _batches(1, 1, 1, 1, 1, 1, 1)  # 7 single-op batches
    p = build_operation_partition(
        batches,
        unfixed_batch_start_idx=3,
        unfixed_batch_count=1,
        left_profile_fixed_batch_count=1,
        right_profile_fixed_batch_count=1,
    )
    assert len(p.left_time_fixed) == 2
    assert len(p.left_profile_fixed) == 1
    assert len(p.unfixed) == 1
    assert len(p.right_profile_fixed) == 1
    assert len(p.right_time_fixed) == 2


def test_split_at_start_no_left_regions() -> None:
    batches = _batches(1, 1, 1, 1, 1)
    p = build_operation_partition(
        batches,
        unfixed_batch_start_idx=0,
        unfixed_batch_count=2,
        left_profile_fixed_batch_count=1,
        right_profile_fixed_batch_count=1,
    )
    # left_pf_start = -1, so no LTF, no LPF (clipped at 0)
    assert p.left_time_fixed == ()
    assert p.left_profile_fixed == ()
    assert len(p.unfixed) == 2
    assert len(p.right_profile_fixed) == 1
    assert len(p.right_time_fixed) == 2


def test_split_at_end_no_right_regions() -> None:
    batches = _batches(1, 1, 1, 1, 1)
    p = build_operation_partition(
        batches,
        unfixed_batch_start_idx=3,
        unfixed_batch_count=2,
        left_profile_fixed_batch_count=1,
        right_profile_fixed_batch_count=2,
    )
    assert len(p.left_time_fixed) == 2
    assert len(p.left_profile_fixed) == 1
    assert len(p.unfixed) == 2
    assert p.right_profile_fixed == ()
    assert p.right_time_fixed == ()


def test_promote_job_contained_ops() -> None:
    p = OperationPartition(
        left_time_fixed=(),
        left_profile_fixed=(("j0", "m0"), ("j1", "m0")),
        unfixed=(("j2", "m0"),),
        right_profile_fixed=(("j2", "m1"),),  # j2 also has an RPF op
        right_time_fixed=(),
    )
    promoted = p.promote_job_contained_ops({"j2"})
    # j2's ops in LPF/RPF should be promoted to unfixed
    assert ("j2", "m1") in promoted.unfixed
    # j0, j1 stay in LPF
    assert promoted.left_profile_fixed == (("j0", "m0"), ("j1", "m0"))
    assert promoted.right_profile_fixed == ()


def test_validate_and_get_batch_count_uniform() -> None:
    s2b = {
        "i0": [(("j0", "m0"),), (("j1", "m0"),)],
        "i1": [(("j0", "m0"),), (("j1", "m0"),)],
    }
    assert validate_and_get_batch_count(s2b) == 2


def test_validate_and_get_batch_count_mismatched_raises() -> None:
    s2b = {"i0": [(("j0", "m0"),), (("j1", "m0"),)], "i1": [(("j0", "m0"),)]}
    with pytest.raises(ValueError, match="identical batch counts"):
        validate_and_get_batch_count(s2b)
