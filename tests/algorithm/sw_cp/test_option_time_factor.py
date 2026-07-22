"""Tests for SwCpOption.time_factor field and its validation (CSR W1)."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.sw_cp import SwCpOption


def test_time_factor_default_is_one() -> None:
    assert SwCpOption().time_factor == 1


def test_time_factor_accepts_values_above_one() -> None:
    assert SwCpOption(time_factor=50).time_factor == 50


def test_time_factor_below_one_rejected() -> None:
    with pytest.raises(ValueError, match="time_factor must be >= 1"):
        SwCpOption(time_factor=0)
