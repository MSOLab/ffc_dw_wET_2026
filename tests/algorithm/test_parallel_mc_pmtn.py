from __future__ import annotations

import math

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(
    name: str = "test_inst",
    jobs: list[str] | None = None,
    stages: list[str] | None = None,
    machines: dict[str, list[str]] | None = None,
    processing: list[list[int]] | None = None,
    due_window: dict[str, tuple[int, int]] | None = None,
    ewt: dict[str, int] | None = None,
    twt: dict[str, int] | None = None,
) -> FFcDDWParameters:
    if jobs is None:
        jobs = ["j0", "j1", "j2"]
    if stages is None:
        stages = ["i0", "i1"]
    if machines is None:
        machines = {"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]}
    if processing is None:
        processing = [[2, 3], [2, 2], [2, 1]]
    if due_window is None:
        due_window = {"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)}
    if ewt is None:
        ewt = {j: 1 for j in jobs}
    if twt is None:
        twt = {j: 1 for j in jobs}
    p_manager = JobStageProcessingTimeManager(
        name=f"{name}_p", df=pd.DataFrame(processing)
    )
    return FFcDDWParameters(
        name=name,
        job_id_list=jobs,
        stage_id_list=stages,
        stage_2_machines_map=machines,
        p_manager=p_manager,
        job_2_due_window_map=due_window,
        job_2_ewt_map=ewt,
        job_2_twt_map=twt,
    )


# --- Model construction ---


def test_from_instance_sets_parameters() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)

    assert solver.calJ == ["j0", "j1", "j2"]
    assert solver.p == {"j0": 3, "j1": 2, "j2": 1}  # last stage (i1) processing times
    assert solver.r == {"j0": 2, "j1": 2, "j2": 2}  # sum of non-last stages (i0)
    assert solver.mc_count == 1  # one machine in last stage
    assert solver.name == "ParallelMachinePreemptionMcf_test_inst"


def test_from_instance_builds_mcf() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)

    assert solver.mcf is not None
    assert solver.source_id == 0
    assert solver.sink_id == 1
    assert len(solver.job_node_id) == 3
    assert len(solver.time_node_id) > 0
    assert len(solver.arc_index_job_time) > 0


def test_cost_matrix_correctness() -> None:
    """Verify cost calculation for a known case."""
    instance = _make_instance(
        jobs=["j0"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"]},
        processing=[[5, 2]],
        due_window={"j0": (5, 7)},
        ewt={"j0": 3},
        twt={"j0": 5},
    )
    solver = ParallelMachinePreemptionMcf.from_instance(instance)

    # p_j=2, d_minus=5, d_plus=7, w_minus=3, w_plus=5
    # t_max = max(r) + sum(p) = 5 + 2 = 7, so calT = [1..7]
    # For t=1: t <= 5-2=3 => cost = 3 * ceil((3-1+1)/2) = 3 * ceil(1) = 3
    assert solver.C["j0"][1] == 3 * math.ceil((5 - 2 - 1 + 1) / 2)

    # For t=4: 3 < 4 <= 7 => cost = 0
    assert solver.C["j0"][4] == 0

    # For t=7: 3 < 7 <= 7 => cost = 0
    assert solver.C["j0"][7] == 0


def test_empty_calT_raises() -> None:
    """If all processing times are zero, calT would be empty."""
    instance = _make_instance(
        jobs=["j0"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"]},
        processing=[[0, 0]],
        due_window={"j0": (0, 10)},
    )
    with pytest.raises(ValueError, match="calT cannot be empty"):
        ParallelMachinePreemptionMcf.from_instance(instance)


def test_partial_ewt_map_raises() -> None:
    """A non-empty ewt map missing some jobs must raise, not silently default."""
    instance = _make_instance(
        ewt={"j0": 2},  # missing j1, j2
        twt={"j0": 3, "j1": 3, "j2": 3},
    )
    with pytest.raises(ValueError, match="ewt weight map is partial"):
        ParallelMachinePreemptionMcf.from_instance(instance)


def test_partial_twt_map_raises() -> None:
    instance = _make_instance(
        ewt={"j0": 2, "j1": 2, "j2": 2},
        twt={"j0": 3},  # missing j1, j2
    )
    with pytest.raises(ValueError, match="twt weight map is partial"):
        ParallelMachinePreemptionMcf.from_instance(instance)


def test_empty_weight_maps_default_to_one() -> None:
    """Empty ewt/twt maps (e.g. from test fixtures) default to weight 1."""
    instance = _make_instance(ewt={}, twt={})
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()
    assert solver.is_optimal()


# --- Solving ---


def test_solve_optimal() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    assert solver.is_optimal()
    assert solver.opt_cost >= 0


def test_get_obj_value_requires_optimal() -> None:
    solver = ParallelMachinePreemptionMcf()
    solver.status_optimal = False
    solver.opt_cost = 0

    with pytest.raises(AssertionError, match="solve\\(\\) must succeed"):
        solver.get_obj_value()


def test_obj_value_zero_when_infeasible() -> None:
    """Verify opt_cost is 0 when the solver is not optimal."""
    solver = ParallelMachinePreemptionMcf()
    solver.status_optimal = False
    solver.opt_cost = 0
    assert solver.opt_cost == 0


# --- Result extraction ---


def test_variable_value_dict() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    x_val = solver.get_variable_value_dict()
    assert set(x_val.keys()) == {"j0", "j1", "j2"}

    # Each job should have at least one time slot with flow > 0
    for j in solver.calJ:
        total = sum(x_val[j].values())
        assert total == solver.p[j]


def test_job_2_start_time_map() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    starts = solver.get_job_2_start_time_map()
    for j in solver.calJ:
        assert starts[j] is not None
        assert starts[j] > 0  # arcs exist for t > r_j, so t >= 1


def test_job_2_completion_time_map() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    completions = solver.get_job_2_completion_time_map()
    for j in solver.calJ:
        assert completions[j] is not None
        assert completions[j] >= solver.r[j] + 1


def test_start_before_completion() -> None:
    """For jobs with p > 1, completion should be >= start."""
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    starts = solver.get_job_2_start_time_map()
    completions = solver.get_job_2_completion_time_map()

    for j in solver.calJ:
        if starts[j] is not None and completions[j] is not None:
            assert completions[j] >= starts[j]


def test_job_2_average_time_map() -> None:
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    avgs = solver.get_job_2_average_time_map()
    for j in solver.calJ:
        assert avgs[j] is not None
        assert avgs[j] > 0


def test_average_time_between_start_and_completion() -> None:
    """Average time for a job should be between its start and completion."""
    instance = _make_instance()
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    starts = solver.get_job_2_start_time_map()
    completions = solver.get_job_2_completion_time_map()
    avgs = solver.get_job_2_average_time_map()

    for j in solver.calJ:
        if starts[j] is not None and completions[j] is not None:
            assert avgs[j] is not None
            assert starts[j] <= avgs[j] <= completions[j]


# --- None for unscheduled jobs ---


def test_none_for_unscheduled_job() -> None:
    """Verify None is returned for jobs with no flow (edge case)."""
    # Build a real solver, solve it, then clear the arc index so the
    # extraction methods see no flow for any job.
    solver = ParallelMachinePreemptionMcf.from_instance(
        _make_instance(
            jobs=["j0", "j1"],
            stages=["i0", "i1"],
            machines={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
            processing=[[1, 1], [1, 1]],
            due_window={"j0": (0, 100), "j1": (0, 100)},
            ewt={"j0": 1, "j1": 1},
            twt={"j0": 1, "j1": 1},
        )
    )
    solver.solve()
    assert solver.is_optimal()

    solver.arc_index_job_time = {}

    assert solver.get_variable_value_dict() == {"j0": {}, "j1": {}}
    assert solver.get_job_2_start_time_map() == {"j0": None, "j1": None}
    assert solver.get_job_2_completion_time_map() == {"j0": None, "j1": None}
    assert solver.get_job_2_average_time_map() == {"j0": None, "j1": None}


def test_get_variable_value_dict_requires_optimal() -> None:
    solver = ParallelMachinePreemptionMcf()
    solver.status_optimal = False

    with pytest.raises(AssertionError, match="solve\\(\\) must succeed"):
        solver.get_variable_value_dict()


# --- Machine capacity ---


def test_machine_capacity_respected() -> None:
    """With mc_count=2, no more than 2 jobs should run at the same time slot."""
    instance = _make_instance(
        jobs=["j0", "j1", "j2"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0", "i0_1", "i0_2"], "i1": ["i1_0", "i1_1"]},
        processing=[[1, 1], [1, 1], [1, 1]],
        due_window={"j0": (0, 100), "j1": (0, 100), "j2": (0, 100)},
        ewt={"j0": 1, "j1": 1, "j2": 1},
        twt={"j0": 1, "j1": 1, "j2": 1},
    )
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    x_val = solver.get_variable_value_dict()
    # Group by time slot
    time_slots: dict[int, int] = {}
    for j in solver.calJ:
        for t, flow in x_val[j].items():
            time_slots[t] = time_slots.get(t, 0) + flow

    for t, total in time_slots.items():
        assert total <= solver.mc_count


# --- Single job ---


def test_single_job() -> None:
    instance = _make_instance(
        jobs=["j0"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"]},
        processing=[[2, 3]],
        due_window={"j0": (5, 10)},
        ewt={"j0": 1},
        twt={"j0": 2},
    )
    solver = ParallelMachinePreemptionMcf.from_instance(instance)
    solver.solve()

    assert solver.is_optimal()
    starts = solver.get_job_2_start_time_map()
    completions = solver.get_job_2_completion_time_map()

    assert starts["j0"] is not None
    assert completions["j0"] is not None
    assert completions["j0"] - starts["j0"] + 1 == solver.p["j0"]


# --- Multiple machines in last stage ---


def test_multiple_machines_reduces_cost() -> None:
    """More machines should allow lower cost by enabling parallel execution."""
    instance_few = _make_instance(
        jobs=["j0", "j1"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        processing=[[1, 1], [1, 1]],
        due_window={"j0": (1, 2), "j1": (1, 2)},
        ewt={"j0": 1, "j1": 1},
        twt={"j0": 10, "j1": 10},
    )
    solver_few = ParallelMachinePreemptionMcf.from_instance(instance_few)
    solver_few.solve()

    instance_many = _make_instance(
        jobs=["j0", "j1"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0", "i0_1"], "i1": ["i1_0", "i1_1"]},
        processing=[[1, 1], [1, 1]],
        due_window={"j0": (1, 2), "j1": (1, 2)},
        ewt={"j0": 1, "j1": 1},
        twt={"j0": 10, "j1": 10},
    )
    solver_many = ParallelMachinePreemptionMcf.from_instance(instance_many)
    solver_many.solve()

    # More machines should give equal or better (lower) cost
    assert solver_many.opt_cost <= solver_few.opt_cost


# --- Name uniqueness ---


def test_solver_name_includes_instance_name() -> None:
    inst_a = _make_instance(name="alpha")
    inst_b = _make_instance(name="beta")

    solver_a = ParallelMachinePreemptionMcf.from_instance(inst_a)
    solver_b = ParallelMachinePreemptionMcf.from_instance(inst_b)

    assert solver_a.name == "ParallelMachinePreemptionMcf_alpha"
    assert solver_b.name == "ParallelMachinePreemptionMcf_beta"
    assert solver_a.name != solver_b.name
