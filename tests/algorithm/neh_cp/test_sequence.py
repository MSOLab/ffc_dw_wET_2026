from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.neh_cp import neh_cp_job_sequence
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def test_neh_cp_job_sequence_priority() -> None:
    # j0: max(w) = 10, sum = 11, window = 5
    # j1: max(w) = 5, sum = 10, window = 2
    # j2: max(w) = 5, sum = 10, window = 1
    # Expected order by (max desc, sum desc, window asc, position): j0, j2, j1
    instance = FFcDDWParameters(
        name="priority_instance",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="priority_instance_p",
            df=pd.DataFrame([[1], [1], [1]]),
        ),
        job_2_due_window_map={"j0": (0, 5), "j1": (3, 5), "j2": (2, 3)},
        job_2_ewt_map={"j0": 10, "j1": 5, "j2": 5},
        job_2_twt_map={"j0": 1, "j1": 5, "j2": 5},
    )

    assert neh_cp_job_sequence(instance) == ["j0", "j2", "j1"]
    assert neh_cp_job_sequence(instance, job_priority="weight-due-pos") == [
        "j0",
        "j2",
        "j1",
    ]


def test_neh_cp_job_sequence_due_weight_pos() -> None:
    # Last-stage p_j: j0=2, j1=3, j2=4, j3=4, j4=4
    # d_plus:         j0=10, j1=10, j2=10, j3=10, j4=10
    # d_minus:        j0=0,  j1=0,  j2=2,  j3=5,  j4=5
    # w_sum:          j0=3,  j1=3,  j2=3,  j3=4,  j4=2
    # Priority keys (max(0, d+ - p) asc, d+ asc, d- asc, w_sum desc, pos asc):
    #   j0: (8, 10, 0, 3, 0)
    #   j1: (7, 10, 0, 3, 1)
    #   j2: (6, 10, 2, 3, 2)
    #   j3: (6, 10, 5, 4, 3)
    #   j4: (6, 10, 5, 2, 4)
    # Sorted: j2 (6, d-=2), then j3 (6, d-=5, w_sum=4), then j4 (6, d-=5,
    # w_sum=2 — comes after j3 because w_sum sorts desc), then j1 (7), then
    # j0 (8).
    instance = FFcDDWParameters(
        name="due_weight_pos_instance",
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="due_weight_pos_instance_p",
            df=pd.DataFrame([[2], [3], [4], [4], [4]]),
        ),
        job_2_due_window_map={
            "j0": (0, 10),
            "j1": (0, 10),
            "j2": (2, 10),
            "j3": (5, 10),
            "j4": (5, 10),
        },
        job_2_ewt_map={"j0": 2, "j1": 2, "j2": 2, "j3": 2, "j4": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 2, "j4": 1},
    )

    assert neh_cp_job_sequence(instance, job_priority="due-weight-pos") == [
        "j2",
        "j3",
        "j4",
        "j1",
        "j0",
    ]


def test_neh_cp_job_sequence_due_star_weight_pos() -> None:
    # d* = (w_e * d_lower + w_t * d_upper) / (w_e + w_t)
    # j0: d=(0,10), w_e=1, w_t=3 -> d* = 30/4 = 7.5
    # j1: d=(0,8),  w_e=2, w_t=2 -> d* = 16/4 = 4.0
    # j2: d=(0,6),  w_e=1, w_t=1 -> d* =  6/2 = 3.0
    # Sorted by (d* asc, d+ asc, -(w_e+w_t) asc, pos asc): j2, j1, j0
    instance = FFcDDWParameters(
        name="due_star_weight_pos_instance",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="due_star_weight_pos_instance_p",
            df=pd.DataFrame([[1], [1], [1]]),
        ),
        job_2_due_window_map={"j0": (0, 10), "j1": (0, 8), "j2": (0, 6)},
        job_2_ewt_map={"j0": 1, "j1": 2, "j2": 1},
        job_2_twt_map={"j0": 3, "j1": 2, "j2": 1},
    )

    assert neh_cp_job_sequence(instance, job_priority="due*-weight-pos") == [
        "j2",
        "j1",
        "j0",
    ]


def test_neh_cp_job_sequence_due2_weight_pos() -> None:
    # Two stages; key = (max(r_j, d+ - p_last) asc, d+ asc, d- asc, w_sum desc, pos asc)
    # p = [[3,2],[5,2],[6,3]] (rows=jobs, cols=stages)
    # r_j: j0=3, j1=5, j2=6  |  p_last: j0=2, j1=2, j2=3
    # j0: (max(3,10-2)=8, 10, 0, -2, 0)
    # j1: (max(5,8-2)=6,   8, 0, -4, 1)
    # j2: (max(6,8-3)=6,   8, 2, -2, 2)
    # Sorted: j1 (6,8,0), j2 (6,8,2), j0 (8,...)
    instance = FFcDDWParameters(
        name="due2_weight_pos_instance",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["s0", "s1"],
        stage_2_machines_map={"s0": ["s0_0"], "s1": ["s1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="due2_weight_pos_instance_p",
            df=pd.DataFrame([[3, 2], [5, 2], [6, 3]]),
        ),
        job_2_due_window_map={"j0": (0, 10), "j1": (0, 8), "j2": (2, 8)},
        job_2_ewt_map={"j0": 1, "j1": 2, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 2, "j2": 1},
    )

    assert neh_cp_job_sequence(instance, job_priority="due2-weight-pos") == [
        "j1",
        "j2",
        "j0",
    ]
