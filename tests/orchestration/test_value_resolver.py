from __future__ import annotations

import pytest

from ffc_ddw_sum_et.orchestration.value_resolver import resolve_value_expr

N, C, M = 5, 3, 2


def test_none_returns_none() -> None:
    assert resolve_value_expr(None, N, C, M) is None


def test_bare_float() -> None:
    assert resolve_value_expr(30.0, N, C, M) == 30.0


def test_bare_int() -> None:
    assert resolve_value_expr(10, N, C, M) == 10.0


def test_nc_suffix() -> None:
    assert resolve_value_expr("2nc", N, C, M) == 2 * N * C


def test_n_suffix() -> None:
    assert resolve_value_expr("3n", N, C, M) == 3 * N


def test_c_suffix() -> None:
    assert resolve_value_expr("4c", N, C, M) == 4 * C


def test_m_suffix() -> None:
    assert resolve_value_expr("2m", N, C, M) == 2 * M


def test_float_string() -> None:
    assert resolve_value_expr("1.5nc", N, C, M) == 1.5 * N * C


def test_invalid_string_raises() -> None:
    with pytest.raises(ValueError):
        resolve_value_expr("abc", N, C, M)


def test_invalid_nc_prefix_raises() -> None:
    with pytest.raises(ValueError):
        resolve_value_expr("xnc", N, C, M)
