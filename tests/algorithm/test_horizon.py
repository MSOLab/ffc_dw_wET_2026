from __future__ import annotations

import math

import pytest

from ffc_ddw_sum_et.algorithm.horizon import compute_parallel_mc_horizon


def test_compute_parallel_mc_horizon_with_d_lower() -> None:
    p = {"j0": 4, "j1": 6, "j2": 5}
    r = {"j0": 1, "j1": 2, "j2": 0}
    d_lower = {"j0": 12, "j1": 8, "j2": 7}
    mc_count = 2

    expected = max(
        max(r["j0"], d_lower["j0"] - p["j0"]),
        max(r["j1"], d_lower["j1"] - p["j1"]),
        max(r["j2"], d_lower["j2"] - p["j2"]),
    ) + math.ceil((p["j0"] + p["j1"] + p["j2"]) / mc_count)

    assert compute_parallel_mc_horizon(p, r, mc_count, d_lower=d_lower) == expected


def test_compute_parallel_mc_horizon_without_d_lower() -> None:
    p = {"j0": 4, "j1": 6, "j2": 5}
    r = {"j0": 1, "j1": 2, "j2": 0}
    mc_count = 3

    expected = max(r.values()) + math.ceil(sum(p.values()) / mc_count)
    assert compute_parallel_mc_horizon(p, r, mc_count) == expected


def test_compute_parallel_mc_horizon_matches_parallel_mc_pmtn_semantics() -> None:
    """Parity check: replicating the inline t_max in the legacy MCF setup."""
    p = {"j0": 3, "j1": 2}
    r = {"j0": 0, "j1": 1}
    d_lower = {"j0": 5, "j1": 4}
    mc_count = 1

    legacy = max(
        max(r["j0"], d_lower["j0"] - p["j0"]),
        max(r["j1"], d_lower["j1"] - p["j1"]),
    ) + math.ceil((p["j0"] + p["j1"]) / mc_count)

    assert compute_parallel_mc_horizon(p, r, mc_count, d_lower=d_lower) == legacy


def test_compute_parallel_mc_horizon_rejects_empty_p() -> None:
    with pytest.raises(ValueError):
        compute_parallel_mc_horizon({}, {}, mc_count=1)


def test_compute_parallel_mc_horizon_rejects_non_positive_mc_count() -> None:
    with pytest.raises(ValueError):
        compute_parallel_mc_horizon({"j0": 1}, {"j0": 0}, mc_count=0)
