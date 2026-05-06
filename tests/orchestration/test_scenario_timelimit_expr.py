"""Per-instance resolution of `stopping_criteria["timelimit"]` expression.

Verifies that `FFcDDWSingleInstanceRunner.__init__` resolves a string
timelimit expression (e.g. ``"0.5nc"``) against the instance's `n`/`c`/`m`,
and — critically — does so without mutating the upstream
`stopping_criteria` dict that is shared by reference across every SIR in
a scenario (see `ffcddw_multi_instance_runner._init_single_instance_runners`).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from routix.type_defs import RunMode

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(
    name: str,
    *,
    n: int,
    c: int,
    m: int,
) -> FFcDDWParameters:
    job_id_list = [f"j{i}" for i in range(n)]
    stage_id_list = [f"i{k}" for k in range(c)]
    stage_2_machines_map: dict[str, list[str]] = {
        stage: [f"{stage}_0"] for stage in stage_id_list[:-1]
    }
    last_stage = stage_id_list[-1]
    stage_2_machines_map[last_stage] = [f"{last_stage}_{j}" for j in range(m)]
    p_df = pd.DataFrame([[1] * c for _ in range(n)])
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map=stage_2_machines_map,
        p_manager=JobStageProcessingTimeManager(name=f"{name}_p", df=p_df),
        job_2_due_window_map={j: (0, 1) for j in job_id_list},
        job_2_ewt_map={j: 1 for j in job_id_list},
        job_2_twt_map={j: 1 for j in job_id_list},
    )


def _make_runner(
    tmp_path: Path,
    *,
    instance: FFcDDWParameters,
    stopping_criteria: dict,
) -> FFcDDWSingleInstanceRunner:
    layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
    return FFcDDWSingleInstanceRunner(
        instance=instance,
        shared_param_dict={},
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=stopping_criteria,
        output_dir=tmp_path / "run",
        output_metadata={},
        mode=RunMode.FULL_RUN,
        layout=layout,
        scenario_name="sc",
    )


def test_nc_expression_resolves_per_instance(tmp_path: Path) -> None:
    instance = _make_instance("inst_nc", n=4, c=3, m=2)
    sc_dict = {"timelimit": "0.5nc"}
    runner = _make_runner(tmp_path, instance=instance, stopping_criteria=sc_dict)

    assert runner.stopping_criteria["timelimit"] == 0.5 * 4 * 3
    assert isinstance(runner.stopping_criteria["timelimit"], float)


def test_n_c_m_expressions_each_use_correct_dimension(tmp_path: Path) -> None:
    instance = _make_instance("inst_dims", n=4, c=3, m=2)

    for expr, expected in [
        ("2n", 2 * 4),
        ("3c", 3 * 3),
        ("5m", 5 * 2),
        ("0.25nc", 0.25 * 4 * 3),
    ]:
        runner = _make_runner(
            tmp_path, instance=instance, stopping_criteria={"timelimit": expr}
        )
        assert runner.stopping_criteria["timelimit"] == expected, expr


def test_float_input_passes_through_unchanged(tmp_path: Path) -> None:
    instance = _make_instance("inst_float", n=4, c=3, m=2)
    sc_dict = {"timelimit": 7.5}
    runner = _make_runner(tmp_path, instance=instance, stopping_criteria=sc_dict)

    assert runner.stopping_criteria["timelimit"] == 7.5
    # The legacy float path must not allocate a new dict either; identity
    # check guards against accidental copies that other code may rely on.
    assert runner.stopping_criteria is sc_dict


def test_shared_dict_is_not_mutated_across_instances(tmp_path: Path) -> None:
    """Regression: the scenario `stopping_criteria` dict is shared across
    SIRs by reference. Resolving instance A's expression must not leak into
    instance B's resolution.
    """
    sc_dict = {"timelimit": "1.0nc"}
    sc_dict_snapshot = dict(sc_dict)

    inst_a = _make_instance("inst_a", n=2, c=3, m=1)
    inst_b = _make_instance("inst_b", n=5, c=4, m=1)

    runner_a = _make_runner(tmp_path, instance=inst_a, stopping_criteria=sc_dict)
    runner_b = _make_runner(tmp_path, instance=inst_b, stopping_criteria=sc_dict)

    assert sc_dict == sc_dict_snapshot, (
        "shared upstream stopping_criteria dict was mutated"
    )
    assert runner_a.stopping_criteria["timelimit"] == 1.0 * 2 * 3
    assert runner_b.stopping_criteria["timelimit"] == 1.0 * 5 * 4
    assert runner_a.stopping_criteria is not runner_b.stopping_criteria
    assert runner_a.stopping_criteria is not sc_dict
    assert runner_b.stopping_criteria is not sc_dict


def test_extra_stopping_keys_are_preserved(tmp_path: Path) -> None:
    instance = _make_instance("inst_extra", n=2, c=3, m=1)
    sc_dict = {"timelimit": "1.0nc", "future_field": "keep_me"}
    runner = _make_runner(tmp_path, instance=instance, stopping_criteria=sc_dict)

    assert runner.stopping_criteria["timelimit"] == 1.0 * 2 * 3
    assert runner.stopping_criteria["future_field"] == "keep_me"
