from __future__ import annotations

import math

from ortools.graph.python.min_cost_flow import SimpleMinCostFlow

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

from .horizon import compute_parallel_mc_horizon

__all__ = ["ParallelMachinePreemptionMcf"]


def _resolve_weight_map(
    raw: dict[str, int], jobs: list[str], kind: str
) -> dict[str, int]:
    """Return a weight map with an entry for every job.

    An empty input map defaults every weight to 1 (matches FAM behavior).
    A partial map is rejected — silently defaulting missing jobs would
    produce an incorrect (still valid but loose) LB without the caller noticing.
    """
    if not raw:
        return dict.fromkeys(jobs, 1)
    missing = [j for j in jobs if j not in raw]
    if missing:
        raise ValueError(
            f"{kind} weight map is partial; missing jobs: {missing[:5]}"
            + ("..." if len(missing) > 5 else "")
        )
    return raw


class ParallelMachinePreemptionMcf:
    """
    Pm | r_j, pmtn | sum{C_jt x_jt} as a min cost flow problem.

    The MCF is built for a target stage ``q`` (``stage_id``; the last
    stage when ``stage_id is None``).

    Nodes:
        source -> job(j) -> time(t) -> sink

    Capacities:
        source -> job : p_{qj} (processing time of job j at stage q)
        job -> time   : 1 if t > r_j, 0 otherwise
        time -> sink  : m_q (number of machines at stage q)

    where ``r_j = sum_{h<q} p_{hj}`` (release of job j at stage q),
    optionally scaled/shifted by ``r_multiplier`` / ``r_increment``.

    Costs (``tardiness_only=False``, full earliness+tardiness):
        The due window is projected by the downstream tail
        ``tau_j = sum_{h>q} p_{hj}`` to ``dbar^-_j = d^-_j - tau_j`` and
        ``dbar^+_j = d^+_j - tau_j`` (vault/bounds_A_C_P3.tex):
        C_{jt} =
            w^-_j * ceil((dbar^-_j - p_j - t + 1) / p_j)  if t <= dbar^-_j - p_j
            0                                             if dbar^-_j - p_j < t <= dbar^+_j
            w^+_j * ceil((t - dbar^+_j) / p_j)            if t > dbar^+_j
        At the last stage ``tau_j == 0`` so the projected window equals the
        raw window and this cost (and the horizon) are byte-identical to the
        un-projected last-stage bound. For an intermediate stage ``q < c`` the
        earliness arm makes this an *approximate* objective and **not** a valid
        LB (earliness is over-counted by the upstream projection); callers must
        treat it accordingly.

    Costs (``tardiness_only=True``, weighted-tardiness-only projection):
        The earliness arm is dropped (earliness is non-regular and would
        be over-counted by the upstream projection) and the upper-due is
        shifted by the downstream tail ``tau_j = sum_{h>q} p_{hj}`` to
        ``dbar_j = d^+_j - tau_j``:
        C_{jt} =
            0                                if t <= dbar_j
            w^+_j * ceil((t - dbar_j) / p_j) if t > dbar_j
        The horizon then drops the ``d^-_j`` term (no lower-due pressure).
    """

    name: str
    calJ: list[str]
    p: dict[str, int]
    r: dict[str, int]
    mc_count: int
    calT: list[int]
    C: dict[str, dict[int, int]]

    mcf: SimpleMinCostFlow | None
    source_id: int
    sink_id: int
    job_node_id: dict[str, int]
    time_node_id: dict[int, int]
    arc_index_job_time: dict[tuple[str, int], int]

    # Results
    status_optimal: bool
    opt_cost: int

    def __init__(self):
        self.name = "ParallelMachinePreemptionMcf"
        self.calJ = []
        self.p = {}
        self.r = {}
        self.mc_count = 0
        self.calT = []
        self.C = {}

        self.mcf = None

        self.source_id = 0
        self.sink_id = 1
        self.job_node_id = {}
        self.time_node_id = {}
        self.arc_index_job_time = {}

        self.status_optimal = False
        self.opt_cost = 0

    @classmethod
    def from_instance(
        cls,
        instance: FFcDDWParameters,
        *,
        r_multiplier: float = 1.0,
        r_increment: int = 0,
        stage_id: str | None = None,
        tardiness_only: bool = False,
    ) -> ParallelMachinePreemptionMcf:
        if r_multiplier < 0:
            raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
        if r_increment < 0:
            raise ValueError(
                f"r_increment must be 0 or a positive integer; got {r_increment}."
            )
        obj = cls()
        obj.name = f"{cls.__name__}_{instance.name}"
        obj._define_parameters(
            instance,
            r_multiplier=r_multiplier,
            r_increment=r_increment,
            stage_id=stage_id,
            tardiness_only=tardiness_only,
        )
        obj._build_mcf()
        return obj

    # Model construction

    def _define_parameters(
        self,
        instance: FFcDDWParameters,
        *,
        r_multiplier: float = 1.0,
        r_increment: int = 0,
        stage_id: str | None = None,
        tardiness_only: bool = False,
    ) -> None:
        self.calJ = instance.job_id_list
        if stage_id is None:
            target_stage = instance.stage_id_list[-1]
            self.p = instance.get_job_2_p_map_for_stage(target_stage)
            self.r = instance.get_job_2_p_sum_except_last_stage()
            self.mc_count = instance.machine_count_per_stage[-1]
        else:
            target_stage = stage_id
            self.p = instance.get_job_2_p_map_for_stage(target_stage)
            self.r = instance.get_job_2_p_sum_before_stage(target_stage)
            self.mc_count = len(instance.stage_2_machines_map[target_stage])
        if r_multiplier != 1.0:
            self.r = {j: math.ceil(v * r_multiplier) for j, v in self.r.items()}
        if r_increment != 0:
            self.r = {j: v + r_increment for j, v in self.r.items()}
        ddw = instance.job_2_due_window_map
        w_plus = _resolve_weight_map(instance.job_2_twt_map, self.calJ, "twt")

        if tardiness_only:
            # Weighted-tardiness-only projection (vault/bounds_wT_P3.tex):
            # earliness arm dropped, upper-due shifted by downstream tail.
            tau = instance.get_job_2_p_sum_after_stage(target_stage)
            d_bar = {j: ddw[j][1] - tau[j] for j in self.calJ}

            # No lower-due pressure -> drop the d^-_j term from the horizon.
            t_max = compute_parallel_mc_horizon(
                self.p, self.r, self.mc_count, d_lower=None
            )
            self.calT = list(range(1, t_max + 1))
            if not self.calT:
                raise ValueError("calT cannot be empty; check instance parameters")

            self.C = {}
            for j in self.calJ:
                self.C[j] = {}
                for t in self.calT:
                    if t <= d_bar[j]:
                        self.C[j][t] = 0
                    else:
                        self.C[j][t] = w_plus[j] * math.ceil((t - d_bar[j]) / self.p[j])
            return

        w_minus = _resolve_weight_map(instance.job_2_ewt_map, self.calJ, "ewt")

        # Full earliness+tardiness cost with the due window projected by the
        # downstream tail tau_j = sum_{h>q} p_{hj} (vault/bounds_A_C_P3.tex):
        # dbar^-_j = d^-_j - tau_j, dbar^+_j = d^+_j - tau_j. At the last stage
        # tau_j == 0, so the projected window equals the raw window and the cost
        # (and horizon) are byte-identical to the un-projected last-stage bound.
        # For an intermediate stage q < c the earliness arm makes this an
        # *approximate* objective, NOT a valid LB (earliness is over-counted by
        # the upstream projection).
        tau = instance.get_job_2_p_sum_after_stage(target_stage)
        d_minus_bar = {j: ddw[j][0] - tau[j] for j in self.calJ}
        d_plus_bar = {j: ddw[j][1] - tau[j] for j in self.calJ}

        # T = max_j(max(r_j, dbar^-_j - p_j)) + ceil(sum(p_j) / mc_count)
        t_max = compute_parallel_mc_horizon(
            self.p, self.r, self.mc_count, d_lower=d_minus_bar
        )
        self.calT = list(range(1, t_max + 1))
        if not self.calT:
            raise ValueError("calT cannot be empty; check instance parameters")

        self.C = {}
        for j in self.calJ:
            d_minus = d_minus_bar[j]
            d_plus = d_plus_bar[j]
            self.C[j] = {}
            for t in self.calT:
                if t <= d_minus - self.p[j]:
                    self.C[j][t] = w_minus[j] * math.ceil(
                        (d_minus - self.p[j] - t + 1) / self.p[j]
                    )
                elif t <= d_plus:
                    self.C[j][t] = 0
                else:
                    self.C[j][t] = w_plus[j] * math.ceil((t - d_plus) / self.p[j])

    def _build_mcf(self) -> None:
        mcf = SimpleMinCostFlow()

        # Job nodes
        self.job_node_id = {j: 2 + i for i, j in enumerate(self.calJ)}
        # Time nodes
        self.time_node_id = {t: 2 + len(self.calJ) + i for i, t in enumerate(self.calT)}

        total_supply: int = int(sum(self.p.values()))

        # Source -> job arcs
        for j in self.calJ:
            mcf.add_arc_with_capacity_and_unit_cost(
                self.source_id, self.job_node_id[j], self.p[j], 0
            )

        # Job -> time arcs
        # Arcs with capacity 1 if t > r_j, and no arc otherwise
        for j in self.calJ:
            for t in self.calT:
                if t > self.r[j]:
                    arc_idx = mcf.add_arc_with_capacity_and_unit_cost(
                        self.job_node_id[j], self.time_node_id[t], 1, self.C[j][t]
                    )
                    self.arc_index_job_time[(j, t)] = arc_idx

        # Time -> sink arcs
        for t in self.calT:
            mcf.add_arc_with_capacity_and_unit_cost(
                self.time_node_id[t], self.sink_id, self.mc_count, 0
            )

        # Set supplies
        mcf.set_node_supply(self.source_id, total_supply)
        mcf.set_node_supply(self.sink_id, -total_supply)

        self.mcf = mcf

    # Solve & extract

    def solve(self) -> None:
        assert self.mcf is not None
        status = self.mcf.solve()
        self.status_optimal = status == SimpleMinCostFlow.Status.OPTIMAL
        if self.status_optimal:
            self.opt_cost = self.mcf.optimal_cost()
        else:
            self.opt_cost = 0

    def is_optimal(self) -> bool:
        return self.status_optimal

    def get_obj_value(self) -> int:
        assert self.status_optimal, "solve() must succeed before get_obj_value()"
        return self.opt_cost

    def get_variable_value_dict(self) -> dict[str, dict[int, int]]:
        """Get a dict of variable values: x[j][t] = flow on arc (j,t)."""
        assert self.status_optimal, (
            "solve() must succeed before get_variable_value_dict()"
        )
        assert self.mcf is not None
        x_val: dict[str, dict[int, int]] = {j: {} for j in self.calJ}
        for (j, t), arc_idx in self.arc_index_job_time.items():
            flow = self.mcf.flow(arc_idx)
            if flow > 0:
                x_val[j][t] = flow
        return x_val

    def get_job_2_start_time_map(self) -> dict[str, int | None]:
        """Get a mapping from job IDs to start times."""
        x_val = self.get_variable_value_dict()
        job_2_start_time: dict[str, int | None] = {}
        for j in self.calJ:
            start_time = min(x_val[j].keys()) if x_val[j] else None
            job_2_start_time[j] = start_time
        return job_2_start_time

    def get_job_2_completion_time_map(self) -> dict[str, int | None]:
        """Get a mapping from job IDs to completion times."""
        x_val = self.get_variable_value_dict()
        job_2_completion_time: dict[str, int | None] = {}
        for j in self.calJ:
            completion_time = max(x_val[j].keys()) if x_val[j] else None
            job_2_completion_time[j] = completion_time
        return job_2_completion_time

    def get_job_2_time_window_map(self) -> dict[str, tuple[int, int] | None]:
        """For each job, return ``(min_t, max_t)`` over arcs ``(j, t)`` with
        ``x_jt > 0`` in the optimal MCF flow.

        ``None`` for jobs with no flow (cannot occur once ``solve()`` is
        optimal because every job carries supply ``p_j > 0``, but the
        signature mirrors the start/completion accessors for consistency).
        """
        x_val = self.get_variable_value_dict()
        job_2_window: dict[str, tuple[int, int] | None] = {}
        for j in self.calJ:
            times = x_val[j].keys()
            job_2_window[j] = (min(times), max(times)) if times else None
        return job_2_window

    def get_job_priority_by_avg_time(self) -> dict[str, float | None]:
        x_val = self.get_variable_value_dict()
        job_2_avg: dict[str, float | None] = {}
        for j in self.calJ:
            times = list(x_val[j].keys())
            if not times:
                raise ValueError(f"Job {j} has no start times; cannot compute avg_time")
            job_2_avg[j] = sum(times) / len(times)
        return job_2_avg

    def get_job_priority_by_avg_time_minus_half_p(self) -> dict[str, float | None]:
        x_val = self.get_variable_value_dict()
        job_2_avg_minus_p_sum: dict[str, float | None] = {}
        for j in self.calJ:
            times = list(x_val[j].keys())
            if not times:
                raise ValueError(
                    f"Job {j} has no start times; cannot compute avg_time_minus_half_p"
                )
            job_2_avg_minus_p_sum[j] = sum(times) / len(times) - (self.p[j] / 2)
        return job_2_avg_minus_p_sum

    def get_job_2_completion_time_minus_p_map(self) -> dict[str, int | None]:
        x_val = self.get_variable_value_dict()
        job_2_completion_minus_p: dict[str, int | None] = {}
        for j in self.calJ:
            times = list(x_val[j].keys())
            if not times:
                raise ValueError(
                    f"Job {j} has no start times; cannot compute completion_time_minus_p"
                )
            completion_time = max(times)
            job_2_completion_minus_p[j] = completion_time - self.p[j]
        return job_2_completion_minus_p

    def get_job_priority_by_half_time(self) -> dict[str, float | None]:
        """Get job priority by (start_time + completion_time) / 2."""
        start_time_map = self.get_job_2_start_time_map()
        completion_time_map = self.get_job_2_completion_time_map()
        job_2_half_time: dict[str, float | None] = {}
        for j in self.calJ:
            start_time = start_time_map[j]
            completion_time = completion_time_map[j]
            if start_time is None or completion_time is None:
                job_2_half_time[j] = None
            else:
                job_2_half_time[j] = (start_time + completion_time) / 2
        return job_2_half_time
