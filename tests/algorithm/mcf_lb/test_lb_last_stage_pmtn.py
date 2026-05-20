"""Direct tests for the pure ``apply_lb_by_mcf`` algorithm function.

These bypass the controller wrapper to verify the algorithm-level
contract: validity of the returned bound (``obj_bound_is_valid``) under
the original / augmented instance and per-job release adjustments.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import (
    ApplyLbByMcfResult,
    apply_lb_by_mcf,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "apply_test") -> FFcDDWParameters:
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def test_apply_lb_by_mcf_default_yields_valid_global_bound() -> None:
    """With ``p_increment=0``, ``r_multiplier=1``, ``r_increment=0``, the
    returned bound is a valid global LB on the original instance.
    """
    instance = _make_instance()

    result = apply_lb_by_mcf(instance)

    assert isinstance(result, ApplyLbByMcfResult)
    assert result.obj_bound_is_valid is True
    assert result.mcf_lb >= 0
    assert result.mcf_solve_sec >= 0
    assert result.p_increment_used == 0
    assert result.r_multiplier_used == 1.0
    assert result.r_increment_used == 0


@pytest.mark.parametrize(
    "p_inc,r_mul,r_inc",
    [
        (1, 1.0, 0),  # augmented instance
        (0, 1.5, 0),  # release multiplier > 1
        (0, 1.0, 1),  # release increment > 0
    ],
)
def test_apply_lb_by_mcf_invalidates_global_bound(
    p_inc: int, r_mul: float, r_inc: int
) -> None:
    instance = _make_instance()

    result = apply_lb_by_mcf(
        instance, p_increment=p_inc, r_multiplier=r_mul, r_increment=r_inc
    )

    assert result.obj_bound_is_valid is False
    assert result.p_increment_used == p_inc
    assert result.r_multiplier_used == r_mul
    assert result.r_increment_used == r_inc


@pytest.mark.parametrize(
    "kwargs",
    [
        {"p_increment": -1},
        {"r_multiplier": -0.5},
        {"r_increment": -1},
    ],
)
def test_apply_lb_by_mcf_negative_inputs_raise(kwargs: dict) -> None:
    instance = _make_instance()

    with pytest.raises(ValueError):
        apply_lb_by_mcf(instance, **kwargs)
