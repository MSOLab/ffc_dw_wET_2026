from __future__ import annotations

import pandas as pd
import pytest
from ortools.sat.python import cp_model

from ffc_ddw_sum_et.algorithm.cumulative import BaseModelBuilder
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="makespan_obj_instance",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="p", df=pd.DataFrame([[2, 3], [1, 2]])
        ),
        job_2_due_window_map={"j0": (0, 100), "j1": (0, 100)},
    )


def test_makespan_objective_returns_no_et_vars_and_minimises_makespan() -> None:
    instance = _make_instance()
    horizon = sum(BaseModelBuilder.make_params(instance).p.values())

    builder = BaseModelBuilder()
    mdl, params, op_vars, et_vars = builder.build(
        instance, horizon=horizon, objective="makespan"
    )
    assert et_vars is None

    solver = cp_model.CpSolver()
    status = solver.solve(mdl)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    # Optimal makespan for two-job two-stage instance with p=[[2,3],[1,2]] on
    # one machine per stage: schedule j1 then j0 (or vice versa). The minimum
    # makespan is 7 (longer job j0 needs 2+3, plus j1's stage 2 starts after).
    last_i = params.i_list[-1]
    end_times = [solver.value(op_vars.op_end[j, last_i]) for j in params.j_list]
    assert max(end_times) == int(solver.objective_value)


def test_makespan_objective_rejects_obj_lb_or_lex_flags() -> None:
    instance = _make_instance()
    builder = BaseModelBuilder()
    horizon = sum(BaseModelBuilder.make_params(instance).p.values())

    with pytest.raises(ValueError):
        builder.build(instance, horizon=horizon, objective="makespan", obj_lb=1.0)

    with pytest.raises(ValueError):
        builder.build(
            instance,
            horizon=horizon,
            objective="makespan",
            minimize_makespan_lex=True,
        )

    with pytest.raises(ValueError):
        builder.build(instance, horizon=horizon, objective="makespan", et_ub=1.0)
