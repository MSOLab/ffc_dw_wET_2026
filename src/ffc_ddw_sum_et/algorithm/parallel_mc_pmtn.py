from __future__ import annotations

import math

from ortools.graph.python.min_cost_flow import SimpleMinCostFlow

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


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

    Nodes:
        source -> job(j) -> time(t) -> sink

    Capacities:
        source -> job : P_cj (processing time of job j in the last stage)
        job -> time   : 1 if t > r_j, 0 otherwise
        time -> sink  : m_c (number of machines in the last stage)

    Costs:
        C_{jt} =
            w^-_j * ceil((d^-_j - p_j - t + 1) / p_j)  if t <= d^-_j - p_j
            0                                          if d^-_j - p_j < t <= d^+_j
            w^+_j * ceil((t - d^+_j) / p_j)            if t > d^+_j
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
    def from_instance(cls, instance: FFcDDWParameters) -> ParallelMachinePreemptionMcf:
        obj = cls()
        obj.name = f"{cls.__name__}_{instance.name}"
        obj._define_parameters(instance)
        obj._build_mcf()
        return obj

    # Model construction

    def _define_parameters(self, instance: FFcDDWParameters) -> None:
        self.calJ = instance.job_id_list
        self.p = instance.get_job_2_p_map_for_stage(instance.stage_id_list[-1])
        self.r = instance.get_job_2_p_sum_except_last_stage()
        ddw = instance.job_2_due_window_map
        w_minus = _resolve_weight_map(instance.job_2_ewt_map, self.calJ, "ewt")
        w_plus = _resolve_weight_map(instance.job_2_twt_map, self.calJ, "twt")
        self.mc_count = instance.machine_count_per_stage[-1]

        # T = max r_j + sum p_j
        t_max = max(self.r.values()) + sum(self.p.values())
        self.calT = list(range(1, t_max + 1))
        if not self.calT:
            raise ValueError("calT cannot be empty; check instance parameters")

        self.C = {}
        for j in self.calJ:
            d_minus, d_plus = ddw[j]
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

    def get_job_2_average_time_map(self) -> dict[str, float | None]:
        x_val = self.get_variable_value_dict()
        job_2_average_time: dict[str, float | None] = {}
        for j in self.calJ:
            times = list(x_val[j].keys())
            job_2_average_time[j] = sum(times) / len(times) if times else None
        return job_2_average_time

    def get_job_2_avg_time_minus_half_processing_time_sum_map(
        self,
    ) -> dict[str, float | None]:
        x_val = self.get_variable_value_dict()
        job_2_avg_minus_p_sum: dict[str, float | None] = {}
        for j in self.calJ:
            times = list(x_val[j].keys())
            avg_time = sum(times) / len(times) if times else None
            if avg_time is not None:
                job_2_avg_minus_p_sum[j] = avg_time - (self.r[j] + self.p[j]) / 2
            else:
                job_2_avg_minus_p_sum[j] = None
        return job_2_avg_minus_p_sum
