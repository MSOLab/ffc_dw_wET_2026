"""Validation of the intermediate-stage MCF (tardiness-only projection).

These tests pin the math of the weighted-tardiness-only stage projection
(``vault/bounds_wT_P3.tex``) on tiny, hand-checkable instances:

  - projected release ``r_j^(i) = sum_{h<i} p_{hj}`` matches
    ``get_job_2_p_sum_before_stage``;
  - projected upper-due ``dbar_j^(i) = d^+_j - tau_j^(i)`` with
    ``tau = get_job_2_p_sum_after_stage``;
  - the tardiness-only slot cost shape (``0`` until ``dbar_j`` then a
    weighted ceil ramp);
  - ``LB_T^(i) <= OPT`` for *every* stage ``i`` (brute-forced OPT on a
    2-job instance);
  - ``LB_T^(c) <= LB^ET_c`` (tardiness-only at the last stage, where
    ``tau = 0`` so ``dbar = d^+``, is dominated by the full-ET bound);
  - one upstream-bottleneck instance where ``max_{i<c} LB_T^(i) > LB^ET_c``
    — an intermediate stage gives a strictly stronger bound than the
    last-stage full-ET MCF.
"""

from __future__ import annotations

import itertools
import math

import pandas as pd

from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import apply_lb_by_mcf
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(
    name: str,
    jobs: list[str],
    stages: list[str],
    machines: dict[str, list[str]],
    processing: list[list[int]],
    due_window: dict[str, tuple[int, int]],
    ewt: dict[str, int],
    twt: dict[str, int],
) -> FFcDDWParameters:
    """Build an ``FFcDDWParameters`` directly (mirrors the convention in
    ``tests/algorithm/test_parallel_mc_pmtn.py``).
    """
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


def _brute_force_opt(instance: FFcDDWParameters, horizon: int) -> int:
    """True optimal weighted E+T over all feasible non-preemptive flowshop
    schedules on the integer grid ``[0, horizon]``.

    This is an independent ground truth (built straight from the instance
    parameters, not from ``FFcSchedule``). It enumerates an integer start
    time for every ``(job, stage)`` operation and keeps the minimum
    objective among schedules that satisfy flowshop precedence and the
    per-stage machine capacity. Keep instances tiny (2 jobs) so the
    Cartesian product is tractable.
    """
    jobs = instance.job_id_list
    stages = instance.stage_id_list
    p = {(j, s): int(instance.stage_2_job_2_p_map[s][j]) for j in jobs for s in stages}
    mc = {s: len(instance.stage_2_machines_map[s]) for s in stages}
    due_window = instance.job_2_due_window_map
    ewt = instance.job_2_ewt_map
    twt = instance.job_2_twt_map

    ops = [(j, s) for j in jobs for s in stages]
    start_choices = {op: list(range(0, horizon - p[op] + 1)) for op in ops}

    def is_feasible(starts: dict[tuple[str, str], int]) -> bool:
        # Flowshop precedence: a job cannot start a stage before finishing
        # its previous stage.
        for j in jobs:
            for k in range(len(stages) - 1):
                prev_end = starts[(j, stages[k])] + p[(j, stages[k])]
                if starts[(j, stages[k + 1])] < prev_end:
                    return False
        # Per-stage machine capacity at every unit of time.
        for s in stages:
            for t in range(horizon):
                running = sum(
                    1 for j in jobs if starts[(j, s)] <= t < starts[(j, s)] + p[(j, s)]
                )
                if running > mc[s]:
                    return False
        return True

    def objective(starts: dict[tuple[str, str], int]) -> int:
        last = stages[-1]
        total = 0
        for j in jobs:
            completion = starts[(j, last)] + p[(j, last)]
            d_lower, d_upper = due_window[j]
            total += ewt[j] * max(d_lower - completion, 0)
            total += twt[j] * max(completion - d_upper, 0)
        return total

    best: int | None = None
    for combo in itertools.product(*[start_choices[op] for op in ops]):
        starts = dict(zip(ops, combo))
        if is_feasible(starts):
            value = objective(starts)
            if best is None or value < best:
                best = value
    assert best is not None, "brute force found no feasible schedule"
    return best


# --- Projection of release / upper-due / slot cost ---


def test_projected_release_matches_p_sum_before_stage() -> None:
    """``r_j^(i) = sum_{h<i} p_{hj}`` for every intermediate stage."""
    instance = _make_instance(
        name="proj_r",
        jobs=["j0", "j1"],
        stages=["i0", "i1", "i2"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"], "i2": ["i2_0"]},
        processing=[[1, 2, 2], [2, 1, 1]],
        due_window={"j0": (4, 6), "j1": (3, 5)},
        ewt={"j0": 2, "j1": 1},
        twt={"j0": 3, "j1": 4},
    )

    # First stage: all releases zero (no upstream stages).
    mcf_i0 = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id="i0", tardiness_only=True
    )
    assert mcf_i0.r == {"j0": 0, "j1": 0}
    assert mcf_i0.r == instance.get_job_2_p_sum_before_stage("i0")

    # Middle stage: release = sum of p over strictly-earlier stages.
    mcf_i1 = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id="i1", tardiness_only=True
    )
    expected_r = instance.get_job_2_p_sum_before_stage("i1")
    assert mcf_i1.r == expected_r
    # Hand check: p_{i0,j0}=1, p_{i0,j1}=2.
    assert expected_r == {"j0": 1, "j1": 2}


def test_projected_upper_due_matches_p_sum_after_stage() -> None:
    """``dbar_j^(i) = d^+_j - tau_j^(i)`` with ``tau = p_sum_after_stage``.

    ``dbar`` is implicit in the cost matrix: ``C[j][t] = 0`` exactly for
    ``t <= dbar_j``. We recover it as the largest ``t`` with zero cost and
    compare to the hand-derived ``d^+_j - tau_j``.
    """
    instance = _make_instance(
        name="proj_d",
        jobs=["j0", "j1"],
        stages=["i0", "i1", "i2"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"], "i2": ["i2_0"]},
        processing=[[1, 2, 2], [2, 1, 1]],
        due_window={"j0": (4, 6), "j1": (3, 5)},
        ewt={"j0": 2, "j1": 1},
        twt={"j0": 3, "j1": 4},
    )

    for stage_id in ["i0", "i1"]:
        mcf = ParallelMachinePreemptionMcf.from_instance(
            instance, stage_id=stage_id, tardiness_only=True
        )
        tau = instance.get_job_2_p_sum_after_stage(stage_id)
        d_upper = instance.job_2_dw_ub_map
        for j in instance.job_id_list:
            expected_dbar = d_upper[j] - tau[j]
            # The cost is exactly the set {t : C[j][t] == 0} = {t <= dbar_j}
            # intersected with the horizon. dbar is the largest zero-cost t.
            zero_cost_t = [t for t in mcf.calT if mcf.C[j][t] == 0]
            # Within the horizon, the zero-cost prefix ends at dbar_j (or the
            # horizon if dbar_j exceeds it; here dbar_j stays inside the grid).
            assert max(zero_cost_t) == expected_dbar
            assert min(zero_cost_t) == 1  # zero cost from the first slot

    # Hand check at stage i0: tau_j = p_{i1,j} + p_{i2,j}.
    # j0: 2+2=4 -> dbar = 6-4 = 2 ; j1: 1+1=2 -> dbar = 5-2 = 3.
    mcf_i0 = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id="i0", tardiness_only=True
    )
    assert max(t for t in mcf_i0.calT if mcf_i0.C["j0"][t] == 0) == 2
    assert max(t for t in mcf_i0.calT if mcf_i0.C["j1"][t] == 0) == 3


def test_tardiness_only_slot_cost_shape() -> None:
    """``C[j][t] = 0`` for ``t <= dbar_j`` and
    ``twt_j * ceil((t - dbar_j) / p_{ij})`` for ``t > dbar_j``.
    """
    instance = _make_instance(
        name="slot_cost",
        jobs=["j0", "j1", "j2", "j3"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0", "i1_1", "i1_2", "i1_3"]},
        processing=[[4, 1], [4, 1], [4, 1], [4, 1]],
        due_window={j: (0, 5) for j in ["j0", "j1", "j2", "j3"]},
        ewt={j: 1 for j in ["j0", "j1", "j2", "j3"]},
        twt={j: 2 for j in ["j0", "j1", "j2", "j3"]},
    )

    mcf = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id="i0", tardiness_only=True
    )
    tau = instance.get_job_2_p_sum_after_stage("i0")
    d_upper = instance.job_2_dw_ub_map
    twt = instance.job_2_twt_map

    for j in instance.job_id_list:
        p_ij = int(mcf.p[j])
        dbar = d_upper[j] - tau[j]
        for t in mcf.calT:
            if t <= dbar:
                expected = 0
            else:
                expected = twt[j] * math.ceil((t - dbar) / p_ij)
            assert mcf.C[j][t] == expected, (
                f"job {j} slot {t}: got {mcf.C[j][t]}, want {expected}"
            )

    # Hand check for j0: dbar = 5 - 1 = 4, p = 4, twt = 2.
    # t=4 -> 0; t=5 -> 2*ceil(1/4)=2; t=8 -> 2*ceil(4/4)=2; t=9 -> 2*ceil(5/4)=4.
    assert mcf.C["j0"][4] == 0
    assert mcf.C["j0"][5] == 2
    assert mcf.C["j0"][8] == 2
    assert mcf.C["j0"][9] == 4


# --- Lower-bound validity against brute-force OPT ---


def test_lb_t_at_every_stage_is_below_brute_force_opt() -> None:
    """``LB_T^(i) <= OPT`` for *all* stages ``i`` (intermediate tardiness-only
    and the last-stage full-ET MCF), with OPT computed by brute force.
    """
    instance = _make_instance(
        name="all_stage_lb",
        jobs=["j0", "j1"],
        stages=["i0", "i1", "i2"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"], "i2": ["i2_0"]},
        processing=[[1, 2, 2], [2, 1, 1]],
        due_window={"j0": (4, 6), "j1": (3, 5)},
        ewt={"j0": 2, "j1": 1},
        twt={"j0": 3, "j1": 4},
    )

    opt = _brute_force_opt(instance, horizon=12)

    # Last stage: full earliness+tardiness MCF (LB^ET_c).
    last_mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    last_mcf.solve()
    assert last_mcf.opt_cost <= opt

    # Every intermediate stage: tardiness-only projection.
    for stage_id in instance.stage_id_list[:-1]:
        mcf = ParallelMachinePreemptionMcf.from_instance(
            instance, stage_id=stage_id, tardiness_only=True
        )
        mcf.solve()
        assert mcf.opt_cost <= opt, (
            f"LB_T^({stage_id})={mcf.opt_cost} exceeded OPT={opt}"
        )


def test_lb_t_below_opt_on_single_machine_instance() -> None:
    """A second, denser 2-job/2-stage instance: every stage LB <= OPT."""
    instance = _make_instance(
        name="dense2",
        jobs=["j0", "j1"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0"]},
        processing=[[2, 3], [3, 1]],
        due_window={"j0": (3, 5), "j1": (2, 4)},
        ewt={"j0": 2, "j1": 1},
        twt={"j0": 3, "j1": 2},
    )

    opt = _brute_force_opt(instance, horizon=14)

    last_mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    last_mcf.solve()
    assert last_mcf.opt_cost <= opt

    mcf_i0 = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id="i0", tardiness_only=True
    )
    mcf_i0.solve()
    assert mcf_i0.opt_cost <= opt


# --- Last-stage tardiness-only is dominated by full-ET ---


def test_last_stage_tardiness_only_dominated_by_full_et() -> None:
    """``LB_T^(c) <= LB^ET_c``.

    At the last stage ``tau = 0`` so ``dbar = d^+`` (the tardiness arm is
    identical to the full-ET cost) but the full-ET MCF additionally charges
    earliness, so its bound is at least as large.
    """
    instance = _make_instance(
        name="domination",
        jobs=["j0", "j1", "j2"],
        stages=["i0", "i1"],
        machines={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        processing=[[2, 3], [2, 2], [2, 1]],
        due_window={"j0": (4, 5), "j1": (3, 4), "j2": (0, 10)},
        ewt={"j0": 1, "j1": 1, "j2": 1},
        twt={"j0": 1, "j1": 1, "j2": 1},
    )
    last_stage_id = instance.stage_id_list[-1]

    # At the last stage tau == 0, so dbar == d^+.
    tau = instance.get_job_2_p_sum_after_stage(last_stage_id)
    assert all(v == 0 for v in tau.values())

    full_et = ParallelMachinePreemptionMcf.from_instance(instance)
    full_et.solve()

    tardiness_only = ParallelMachinePreemptionMcf.from_instance(
        instance, stage_id=last_stage_id, tardiness_only=True
    )
    tardiness_only.solve()

    assert tardiness_only.opt_cost <= full_et.opt_cost


# --- Upstream bottleneck: an intermediate stage is strictly stronger ---


def test_upstream_bottleneck_intermediate_lb_beats_last_stage_et() -> None:
    """The whole point: build an upstream-bottleneck instance where
    ``max_{i<c} LB_T^(i) > LB^ET_c``.

    Stage ``i0`` is the bottleneck (one machine, large processing times),
    while the last stage ``i1`` is fast (unit processing) and has enough
    machines to clear every job in a single slot. The last-stage full-ET
    MCF therefore sees no tardiness, while the intermediate tardiness-only
    MCF picks up the genuine congestion.

    Hand-checked numbers (4 jobs, p_{i0}=4, p_{i1}=1, d^+=5, twt=1):
      * Last stage i1 (full-ET): r_j = 4, p_j = 1, |M_i1| = 4, so all four
        jobs complete at slot 5 = d^+; earliness/tardiness = 0 => LB^ET_c = 0.
      * Intermediate i0 (tardiness-only): r_j = 0, p_j = 4, |M_i0| = 1, so the
        16 units pile up on one machine over slots 1..16. dbar_j = d^+ - tau_j
        = 5 - 1 = 4, and a min-cost flow packs the four jobs into blocks of 4
        consecutive slots, giving LB_T^(i0) = 0 + 4 + 8 + 12 = 24.
    So max_{i<c} LB_T^(i) = 24 > 0 = LB^ET_c.
    """
    jobs = ["j0", "j1", "j2", "j3"]
    instance = _make_instance(
        name="upstream_bn",
        jobs=jobs,
        stages=["i0", "i1"],
        machines={"i0": ["i0_0"], "i1": ["i1_0", "i1_1", "i1_2", "i1_3"]},
        processing=[[4, 1], [4, 1], [4, 1], [4, 1]],
        due_window={j: (0, 5) for j in jobs},
        ewt={j: 1 for j in jobs},
        twt={j: 1 for j in jobs},
    )

    # Last stage full-ET MCF.
    last_mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    last_mcf.solve()
    lb_et_c = last_mcf.opt_cost
    assert lb_et_c == 0  # hand check

    # Intermediate stage tardiness-only MCF (via the LB layer, exercising the
    # validity flag: tardiness_only must NOT invalidate the global bound).
    apply_result = apply_lb_by_mcf(instance, stage_id="i0", tardiness_only=True)
    assert apply_result.obj_bound_is_valid is True
    lb_t_i0 = apply_result.mcf_lb
    assert lb_t_i0 == 24  # hand check

    # The crux: an intermediate stage gives a strictly stronger bound.
    intermediate_lbs: list[int] = []
    for stage_id in instance.stage_id_list[:-1]:
        mcf = ParallelMachinePreemptionMcf.from_instance(
            instance, stage_id=stage_id, tardiness_only=True
        )
        mcf.solve()
        intermediate_lbs.append(mcf.opt_cost)
    max_intermediate_lb = max(intermediate_lbs)
    assert max_intermediate_lb > lb_et_c

    # Both bounds remain valid lower bounds on a feasible upper bound on OPT.
    # A concrete feasible schedule (process the jobs back-to-back on the
    # bottleneck, then each on its own last-stage machine) has all four jobs
    # complete at i0-times 4, 8, 12, 16 and i1-times 5, 9, 13, 17; tardiness
    # = (0 + 4 + 8 + 12) = 24, earliness 0 => objective 24, an upper bound on
    # OPT. Both LB_T^(i0)=24 and LB^ET_c=0 are <= 24.
    feasible_ub = 24
    assert lb_t_i0 <= feasible_ub
    assert lb_et_c <= feasible_ub
