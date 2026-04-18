from __future__ import annotations

from unittest.mock import Mock

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm import (
    AlgSpec,
    FAMDispatcher,
    FAMOption,
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.ffc_params import FFcParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def make_ddw_instance(
    *,
    name: str = "fam_instance",
    stage_2_machine_count: tuple[int, ...] = (2, 1),
    processing_rows: list[list[int]] | None = None,
    job_2_due_window_map: dict[str, tuple[int, int]] | None = None,
) -> FFcDDWParameters:
    if processing_rows is None:
        processing_rows = [[2, 3], [2, 2], [2, 1]]

    job_id_list = [f"j{i}" for i in range(len(processing_rows))]
    stage_id_list = [f"i{s}" for s in range(len(stage_2_machine_count))]
    stage_2_machines_map = {
        stage_id: [f"{stage_id}_{mc_idx}" for mc_idx in range(machine_count)]
        for stage_id, machine_count in zip(stage_id_list, stage_2_machine_count)
    }
    if job_2_due_window_map is None:
        job_2_due_window_map = {job_id: (0, 10) for job_id in job_id_list}

    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map=stage_2_machines_map,
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame(processing_rows),
        ),
        job_2_due_window_map=job_2_due_window_map,
    )


def make_plain_instance() -> FFcParameters:
    return FFcParameters(
        name="plain_instance",
        job_id_list=["j0", "j1"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="plain_p",
            df=pd.DataFrame([[1, 2], [3, 4]]),
        ),
    )


def test_fam_algorithm_decodes_explicit_permutation_and_records_metadata() -> None:
    instance = make_ddw_instance(
        processing_rows=[[2, 3], [2, 2], [2, 1]],
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(
        AlgSpec(
            instance=instance,
            option=FAMOption(job_sequence=("j2", "j0", "j1")),
        )
    )

    assert record.work_status is WorkStatus.FEASIBLE
    assert record.termination_reason is TerminationReason.COMPLETED
    assert record.algorithm_id == "fam"
    assert record.instance_id == instance.name
    assert record.result is not None
    assert record.result.schedule is not None

    stage_1_jobs = [
        job_id
        for mc_id in instance.stage_2_machines_map["i0"]
        for job_id, _, _ in record.result.schedule.get_job_sequence("i0", mc_id)
    ]
    assert stage_1_jobs == ["j2", "j1", "j0"]


def test_fam_option_defaults_to_instance_job_order() -> None:
    instance = make_ddw_instance(
        processing_rows=[[2, 1], [2, 1], [2, 1]],
        job_2_due_window_map={"j0": (0, 10), "j1": (0, 10), "j2": (0, 10)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(AlgSpec(instance=instance, option=FAMOption()))

    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.schedule.get_job_sequence("i0", "i0_0") == [
        ("j0", 0, 2),
        ("j2", 2, 4),
    ]
    assert record.result.schedule.get_job_sequence("i0", "i0_1") == [("j1", 0, 2)]


def test_fam_option_defaults_release_times_to_zero() -> None:
    instance = make_ddw_instance(
        processing_rows=[[1, 1], [1, 1]],
        job_2_due_window_map={"j0": (0, 10), "j1": (0, 10)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(AlgSpec(instance=instance, option=FAMOption()))

    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.schedule.get_job_sequence("i0", "i0_0")[0][1] == 0
    assert record.result.schedule.get_job_sequence("i0", "i0_1")[0][1] == 0


def test_fam_sorts_stage_two_by_previous_completion_then_slack() -> None:
    instance = make_ddw_instance(
        processing_rows=[[2, 3], [2, 2], [2, 1]],
        job_2_due_window_map={"j0": (0, 3), "j1": (0, 4), "j2": (0, 10)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(
        AlgSpec(
            instance=instance,
            option=FAMOption(job_sequence=("j2", "j0", "j1")),
        )
    )

    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.schedule.get_job_sequence("i1", "i1_0") == [
        ("j0", 2, 5),
        ("j2", 5, 6),
        ("j1", 6, 8),
    ]


def test_fam_keeps_initial_permutation_as_last_tie_break() -> None:
    instance = make_ddw_instance(
        processing_rows=[[1, 1], [1, 1]],
        job_2_due_window_map={"j0": (0, 5), "j1": (0, 5)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(
        AlgSpec(
            instance=instance,
            option=FAMOption(job_sequence=("j1", "j0")),
        )
    )

    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.schedule.get_job_sequence("i1", "i1_0") == [
        ("j1", 1, 2),
        ("j0", 2, 3),
    ]


def test_fam_result_obj_value_matches_window_et() -> None:
    instance = make_ddw_instance(
        processing_rows=[[2, 3], [2, 2], [2, 1]],
        job_2_due_window_map={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
    )
    algorithm = FAMDispatcher()

    record = algorithm.run(
        AlgSpec(
            instance=instance,
            option=FAMOption(job_sequence=("j2", "j0", "j1")),
        )
    )

    assert record.result is not None
    assert record.result.obj_value == 4
    assert record.result.obj_bound is None
    assert record.result.metrics == {
        "sum_earliness": 0,
        "sum_tardiness": 4,
        "makespan": 8,
    }
    assert (
        record.result.metrics["sum_earliness"] + record.result.metrics["sum_tardiness"]
        == record.result.obj_value
    )


def test_fam_uses_spec_logger_when_present() -> None:
    instance = make_ddw_instance()
    logger = Mock()
    algorithm = FAMDispatcher()

    record = algorithm.run(
        AlgSpec(
            instance=instance,
            option=FAMOption(job_sequence=("j0", "j1", "j2")),
            logger=logger,
        )
    )

    assert record.result is not None
    assert logger.debug.called


def test_fam_raises_for_non_ddw_instance() -> None:
    algorithm = FAMDispatcher()

    with pytest.raises(TypeError, match="FFcDDWParameters"):
        algorithm.run(AlgSpec(instance=make_plain_instance(), option=FAMOption()))


def test_fam_raises_when_ref_solution_is_provided() -> None:
    instance = make_ddw_instance()
    algorithm = FAMDispatcher()
    ref_solution = FFcSchedule(
        jobs=list(instance.job_id_list),
        stages=list(instance.stage_id_list),
        machines_per_stage={
            stage_id: list(machine_ids)
            for stage_id, machine_ids in instance.stage_2_machines_map.items()
        },
    )

    with pytest.raises(NotImplementedError, match="ref_solution"):
        algorithm.run(
            AlgSpec(
                instance=instance,
                option=FAMOption(job_sequence=("j0", "j1", "j2")),
                ref_solution=ref_solution,
            )
        )
