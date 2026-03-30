from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ffc_ddw_sum_et.algorithm import (
    AlgOption,
    AlgRecord,
    AlgResult,
    AlgSpec,
    Algorithm,
    DispatchStagesOption,
    WorkStatus,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_params import FFcParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def make_instance() -> FFcParameters:
    return FFcParameters(
        name="toy_instance",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0", "i1_1"]},
        p_manager=JobStageProcessingTimeManager(
            name="toy_p",
            df=pd.DataFrame([[1, 2], [3, 4]]),
        ),
    )


def make_schedule() -> FFcSchedule:
    return FFcSchedule(
        jobs=["j0", "j1"],
        stages=["i0", "i1"],
        machines_per_stage={"i0": ["i0_0"], "i1": ["i1_0", "i1_1"]},
    )


def test_alg_spec_can_be_created_with_instance_only() -> None:
    spec = AlgSpec(instance=make_instance())

    assert spec.instance.name == "toy_instance"
    assert spec.option is None
    assert spec.ref_solution is None
    assert spec.alg_root is None
    assert spec.logger is None


def test_alg_spec_accepts_all_optional_fields() -> None:
    instance = make_instance()
    schedule = make_schedule()
    logger = logging.getLogger("tests.algorithm.contracts")
    option = DispatchStagesOption(
        job_sequence=("j1", "j0"),
        job_2_release_t={"j0": 5, "j1": 7},
        from_stage="i1",
        machine_then_job=True,
    )

    spec = AlgSpec(
        instance=instance,
        option=option,
        ref_solution=schedule,
        alg_root=Path("tmp/alg_run"),
        logger=logger,
    )

    assert spec.option == option
    assert spec.ref_solution is schedule
    assert spec.alg_root == Path("tmp/alg_run")
    assert spec.logger is logger


def test_alg_spec_accepts_string_alg_root() -> None:
    spec = AlgSpec(instance=make_instance(), alg_root="tmp/alg_run")

    assert spec.alg_root == "tmp/alg_run"


def test_alg_record_can_be_created_with_work_status_only() -> None:
    record = AlgRecord(work_status=WorkStatus.FEASIBLE)

    assert record.work_status is WorkStatus.FEASIBLE
    assert record.instance_id is None
    assert record.algorithm_id is None
    assert record.option is None
    assert record.result is None
    assert record.timing is None
    assert record.progress_log is None
    assert record.termination_reason is None
    assert record.error is None


def test_alg_result_keeps_objective_fields_separate_from_metrics() -> None:
    schedule = make_schedule()
    result = AlgResult(
        schedule=schedule,
        obj_value=12,
        obj_bound=10,
        metrics={"gap_percent": 20.0, "late_job_count": 1},
    )

    assert result.schedule is schedule
    assert result.obj_value == 12
    assert result.obj_bound == 10
    assert result.metrics == {"gap_percent": 20.0, "late_job_count": 1}


def test_dispatch_stages_option_resolves_default_values() -> None:
    instance = make_instance()
    option = DispatchStagesOption()

    assert option.resolve_job_sequence(instance) == tuple(instance.job_id_list)
    assert option.resolve_job_2_release_t(instance) == {"j0": 0, "j1": 0}


def test_dispatch_stages_option_preserves_explicit_values() -> None:
    instance = make_instance()
    option = DispatchStagesOption(
        job_sequence=("j1", "j0"),
        job_2_release_t={"j0": 3, "j1": 9},
        from_stage="i1",
        machine_then_job=True,
    )

    assert option.resolve_job_sequence(instance) == ("j1", "j0")
    assert option.resolve_job_2_release_t(instance) == {"j0": 3, "j1": 9}
    assert option.from_stage == "i1"
    assert option.machine_then_job is True


def test_algorithm_public_api_exports_contract_types() -> None:
    assert Algorithm is not None
    assert AlgOption is not None
    assert AlgSpec is not None
    assert AlgResult is not None
    assert AlgRecord is not None
    assert DispatchStagesOption is not None
