"""SwCpDispatcher requires spec.ref_solution and must reject None."""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.sw_cp import SwCpDispatcher, SwCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def test_missing_ref_solution_raises() -> None:
    instance = FFcDDWParameters(
        name="sw_cp_no_ref",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="sw_cp_no_ref_p",
            df=pd.DataFrame([[1, 2], [3, 4]]),
        ),
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
    )
    spec = AlgSpec(instance=instance, option=SwCpOption())
    with pytest.raises(ValueError, match="requires spec.ref_solution"):
        SwCpDispatcher().run(spec)
