from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.neh_cp import NehCpOption


def test_coerce_skip_pf_below_obj_makespan_string() -> None:
    assert NehCpOption.coerce_skip_pf_below_obj("makespan") == "makespan"


def test_coerce_skip_pf_below_obj_numeric_string() -> None:
    assert NehCpOption.coerce_skip_pf_below_obj("3.5") == 3.5


def test_coerce_skip_pf_below_obj_float_passthrough() -> None:
    assert NehCpOption.coerce_skip_pf_below_obj(2.0) == 2.0


def test_coerce_skip_pf_below_obj_int_to_float() -> None:
    result = NehCpOption.coerce_skip_pf_below_obj(7)
    assert result == 7.0
    assert isinstance(result, float)


def test_coerce_skip_pf_below_obj_none() -> None:
    assert NehCpOption.coerce_skip_pf_below_obj(None) is None


def test_coerce_skip_pf_below_obj_invalid_string_raises() -> None:
    with pytest.raises(ValueError, match="Invalid skip_pf_below_obj"):
        NehCpOption.coerce_skip_pf_below_obj("not-a-number")


def test_proportional_batch_tl_mode_rejected() -> None:
    with pytest.raises(ValueError, match="proportional.*not supported"):
        NehCpOption(batch_tl_mode="proportional")


def test_constant_and_linear_accepted() -> None:
    assert NehCpOption(batch_tl_mode="constant").batch_tl_mode == "constant"
    assert NehCpOption(batch_tl_mode="linear").batch_tl_mode == "linear"
