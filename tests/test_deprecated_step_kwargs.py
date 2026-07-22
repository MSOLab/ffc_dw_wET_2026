"""Tests for the launch-time deprecated-step-kwarg preflight.

`idle_mode` was removed from `CoarsenSolveReconstructOption` / `SwCpOption` on
2026-07-22 (see `plans/experiment/20260722/csr_idle_mode_lookahead_only.md`).
Without a preflight, a config still carrying the key would die inside a worker
with a bare `TypeError: … unexpected keyword argument`, after the run has
already started. `main._reject_deprecated_step_kwargs` turns that into a
fail-fast `ValueError` naming the deprecation, before any worker is spawned.
"""

from __future__ import annotations

import pytest

from main import _reject_deprecated_step_kwargs


def test_clean_flow_passes() -> None:
    _reject_deprecated_step_kwargs(
        [
            {
                "name": "alpha",
                "subroutine_flow": [
                    {"method": "coarsen_solve_reconstruct", "factor": 1},
                    {"method": "incremental_sw_cp", "batch_size": "m"},
                ],
            }
        ]
    )


def test_deprecated_kwarg_rejected() -> None:
    with pytest.raises(ValueError, match="idle_mode"):
        _reject_deprecated_step_kwargs(
            [
                {
                    "name": "alpha",
                    "subroutine_flow": [
                        {"method": "coarsen_solve_reconstruct", "idle_mode": "flooring"}
                    ],
                }
            ]
        )


def test_rejected_even_when_value_is_lookahead() -> None:
    """The key itself is gone — accepting the surviving value would keep
    implying that the mode is still configurable."""
    with pytest.raises(ValueError, match="removed 2026-07-22"):
        _reject_deprecated_step_kwargs(
            [
                {
                    "name": "alpha",
                    "subroutine_flow": [
                        {"method": "incremental_sw_cp", "idle_mode": "lookahead"}
                    ],
                }
            ]
        )


def test_nested_solve_flow_is_scanned() -> None:
    """`coarsen_solve_reconstruct` carries a nested `solve_flow`; a deprecated
    key one level down must be caught too."""
    with pytest.raises(ValueError, match="idle_mode"):
        _reject_deprecated_step_kwargs(
            [
                {
                    "name": "alpha",
                    "subroutine_flow": [
                        {
                            "method": "coarsen_solve_reconstruct",
                            "factor": 1,
                            "solve_flow": [
                                {
                                    "method": "incremental_sw_cp",
                                    "idle_mode": "lookahead",
                                }
                            ],
                        }
                    ],
                }
            ]
        )


def test_error_names_scenario_and_method() -> None:
    """The message must be actionable: which scenario, which step."""
    with pytest.raises(ValueError, match="b30_csr") as excinfo:
        _reject_deprecated_step_kwargs(
            [
                {
                    "name": "b30_csr",
                    "subroutine_flow": [
                        {"method": "incremental_sw_cp", "idle_mode": "flooring"}
                    ],
                }
            ]
        )
    assert "incremental_sw_cp" in str(excinfo.value)
