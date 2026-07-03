"""Structural tests for SwCpModelBuilder.

Verifies that op vars are created only for non-time-fixed ops and that
the dummy bars cap the available capacity at the rj_schedule's
boundaries.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cumulative import BaseModelBuilder
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.algorithm.sw_cp import (
    SwCpModelBuilder,
    build_operation_partition,
    build_stage_2_batch_list,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="sw_cp_cp_model_test",
        job_id_list=["j0", "j1", "j2", "j3"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="sw_cp_cp_model_test_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2]]),
        ),
        job_2_due_window_map={
            "j0": (4, 5),
            "j1": (3, 4),
            "j2": (5, 8),
            "j3": (5, 6),
        },
        job_2_ewt_map={j: 1 for j in ["j0", "j1", "j2", "j3"]},
        job_2_twt_map={j: 2 for j in ["j0", "j1", "j2", "j3"]},
    )


def test_op_vars_only_for_non_time_fixed() -> None:
    instance = _make_instance()
    seed_rec = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=0.5))
    )
    incumbent = seed_rec.result.schedule.deepcopy()
    incumbent.make_semi_active(instance.stage_2_job_2_p_map)
    incumbent.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )

    rj_schedule = incumbent.deepcopy()
    rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    stage_2_batch = build_stage_2_batch_list(rj_schedule, batch_size=1)
    stage_2_partition = {
        i: build_operation_partition(
            stage_2_batch[i],
            unfixed_batch_start_idx=1,
            unfixed_batch_count=2,
            left_profile_fixed_batch_count=0,
            right_profile_fixed_batch_count=0,
        )
        for i in instance.stage_id_list
    }
    sub_jobs = {j for p in stage_2_partition.values() for j, _ in p.non_time_fixed}
    sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance, sub_jobs)

    horizon = sum(BaseModelBuilder.make_params(instance).p.values())
    build = SwCpModelBuilder().build(
        sub_instance,
        rj_schedule,
        stage_2_partition,
        horizon=horizon,
        pf_method="PF1",
    )

    expected_keys = {
        (j, i)
        for i, partition in stage_2_partition.items()
        for j, _ in partition.non_time_fixed
    }
    assert set(build.op_vars.op_start.keys()) == expected_keys
    assert set(build.op_vars.op_end.keys()) == expected_keys
    assert set(build.op_vars.op_intvl.keys()) == expected_keys


def test_objective_jobs_are_last_stage_non_time_fixed() -> None:
    instance = _make_instance()
    seed_rec = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=0.5))
    )
    incumbent = seed_rec.result.schedule.deepcopy()
    incumbent.make_semi_active(instance.stage_2_job_2_p_map)
    incumbent.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    rj_schedule = incumbent.deepcopy()
    rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

    stage_2_batch = build_stage_2_batch_list(rj_schedule, batch_size=1)
    stage_2_partition = {
        i: build_operation_partition(
            stage_2_batch[i],
            unfixed_batch_start_idx=1,
            unfixed_batch_count=2,
            left_profile_fixed_batch_count=0,
            right_profile_fixed_batch_count=0,
        )
        for i in instance.stage_id_list
    }
    sub_jobs = {j for p in stage_2_partition.values() for j, _ in p.non_time_fixed}
    sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance, sub_jobs)

    horizon = sum(BaseModelBuilder.make_params(instance).p.values())
    build = SwCpModelBuilder().build(
        sub_instance,
        rj_schedule,
        stage_2_partition,
        horizon=horizon,
        pf_method="PF1",
    )

    last_i = instance.stage_id_list[-1]
    expected_obj_jobs = {j for j, _ in stage_2_partition[last_i].non_time_fixed}
    assert set(build.objective_jobs) == expected_obj_jobs
