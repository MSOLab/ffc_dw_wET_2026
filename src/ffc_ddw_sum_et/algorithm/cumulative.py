from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from typing import Mapping

from ortools.sat.python.cp_model import CpModel, IntervalVar, IntVar

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import (
    FFcSchedule,
    JobIdType,
    McIdType,
    StageIdType,
)


@dataclass(frozen=True)
class Params:
    # Indices & Parameters

    i_list: list[str]
    """$I$: stage index (i) list"""

    M_of: dict[str, list[str]]
    """$M_i$: machine index (k) list for stage i"""

    j_list: list[str]
    """$J$: job index (j) list"""

    p: dict[tuple[str, str], int]
    """$P_{ji}$: processing time of job j at stage i"""

    d_lower: dict[str, int]
    """$d^{-}_j$: lower bound of the due date window for job j"""

    d_upper: dict[str, int]
    """$d^{+}_j$: upper bound of the due date window for job j"""

    w_e: dict[str, int]
    """$w^{-}_j$: earliness weight for job j"""

    w_t: dict[str, int]
    """$w^{+}_j$: tardiness weight for job j"""


@dataclass
class OperationVars:
    op_start: dict[tuple[str, str], IntVar]
    """
    (j,i) -> start time variables for each operation in a job.
    """

    op_end: dict[tuple[str, str], IntVar]
    """
    (j,i) -> end time variables for each operation in a job.
    """

    op_intvl: dict[tuple[str, str], IntervalVar]
    """
    (j,i) -> interval variables for each operation in a job.
    """


@dataclass
class EarlinessTardinessVars:
    E: dict[str, IntVar]
    """$E_j$: earliness variable for job j (= max(0, d^{-}_j - C_j))"""

    T: dict[str, IntVar]
    """$T_j$: tardiness variable for job j (= max(0, C_j - d^{+}_j))"""


class BaseModelBuilder:
    def build(
        self,
        instance: FFcDDWParameters,
        horizon: int,
        tighten_ranges: bool = False,
        link_job_completion: bool = False,
        use_max_equality_for_obj: bool = True,
        last_stage_only: bool = False,
        job_2_release: dict[str, int] | None = None,
        obj_lb: float | None = None,
    ) -> tuple[CpModel, Params, OperationVars, EarlinessTardinessVars]:
        mdl = CpModel()
        params: Params = self.make_params(instance, last_stage_only=last_stage_only)
        ops_vars: OperationVars = self._make_vars(
            mdl,
            params,
            horizon,
            tighten_ranges=tighten_ranges,
            job_2_release=job_2_release,
        )
        self._add_structural_constraints(mdl, params, ops_vars)
        if link_job_completion:
            self._add_job_completion_link_constraints(mdl, params, ops_vars)
        # self._add_inter_stage_structural_constraints(mdl, params, variables)
        obj_vars = self._define_objective(
            mdl,
            params,
            ops_vars,
            horizon=horizon,
            use_max_equality=use_max_equality_for_obj,
            obj_lb=obj_lb,
        )

        return mdl, params, ops_vars, obj_vars

    @staticmethod
    def make_params(
        instance: FFcDDWParameters, last_stage_only: bool = False
    ) -> Params:
        i_list = instance.stage_id_list
        M_of = instance.stage_2_machines_map
        j_list = instance.job_id_list
        _p = instance.p_manager.job_stage_2_value_map(j_list, i_list)
        p = {(j, i): int(float(_p[j, i])) for j in j_list for i in i_list}
        if last_stage_only:
            i_list = [i_list[-1]]
            M_of = {i_list[0]: M_of[i_list[-1]]}
            p = {(j, i_list[0]): p[j, i_list[-1]] for j in j_list}
        due_window = instance.job_2_due_window_map
        ewt = instance.job_2_ewt_map
        twt = instance.job_2_twt_map
        d_lower = {j: due_window[j][0] for j in j_list}
        d_upper = {j: due_window[j][1] for j in j_list}
        w_e = {j: ewt[j] for j in j_list}
        w_t = {j: twt[j] for j in j_list}
        return Params(
            i_list=i_list,
            M_of=M_of,
            j_list=j_list,
            p=p,
            d_lower=d_lower,
            d_upper=d_upper,
            w_e=w_e,
            w_t=w_t,
        )

    @staticmethod
    def _compute_head(params: Params) -> dict[tuple[str, str], int]:
        j_i_2_head: dict[tuple[str, str], int] = {}
        for j in params.j_list:
            acc = 0
            for i in params.i_list:
                j_i_2_head[j, i] = acc
                acc += params.p[j, i]

        return j_i_2_head

    @staticmethod
    def _compute_tail(params: Params) -> dict[tuple[str, str], int]:
        j_i_2_tail: dict[tuple[str, str], int] = {}
        for j in params.j_list:
            acc = 0
            for i in reversed(params.i_list):
                j_i_2_tail[j, i] = acc
                acc += params.p[j, i]

        return j_i_2_tail

    @staticmethod
    def _make_vars(
        mdl: CpModel,
        params: Params,
        horizon: int,
        tighten_ranges: bool = False,
        job_2_release: dict[str, int] | None = None,
    ) -> OperationVars:
        op_start: dict[tuple[str, str], IntVar] = {}
        op_end: dict[tuple[str, str], IntVar] = {}
        op_intvl: dict[tuple[str, str], IntervalVar] = {}

        if tighten_ranges:
            j_i_2_head = BaseModelBuilder._compute_head(params)
            j_i_2_tail = BaseModelBuilder._compute_tail(params)
        else:
            j_i_2_head = {(j, i): 0 for j in params.j_list for i in params.i_list}
            j_i_2_tail = {(j, i): 0 for j in params.j_list for i in params.i_list}

        for j in params.j_list:
            for i in params.i_list:
                p = params.p[j, i]

                assert j_i_2_head[j, i] <= horizon - j_i_2_tail[j, i] - p
                assert j_i_2_head[j, i] + p <= horizon - j_i_2_tail[j, i]

                if (
                    i == params.i_list[0]
                    and job_2_release is not None
                    and j in job_2_release
                ):
                    release_t = max(job_2_release[j], j_i_2_head[j, i])
                else:
                    release_t = j_i_2_head[j, i]

                start_var = mdl.new_int_var(
                    release_t, horizon - j_i_2_tail[j, i] - p, f"start_{j}_{i}"
                )
                end_var = mdl.new_int_var(
                    release_t + p, horizon - j_i_2_tail[j, i], f"end_{j}_{i}"
                )
                interval_var = mdl.new_interval_var(
                    start_var, p, end_var, f"interval_{j}_{i}"
                )

                op_start[(j, i)] = start_var
                op_end[(j, i)] = end_var
                op_intvl[(j, i)] = interval_var

        return OperationVars(
            op_start=op_start,
            op_end=op_end,
            op_intvl=op_intvl,
        )

    @staticmethod
    def _add_precedence_constraints(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
    ) -> None:
        """Add consecutive-stage precedence constraints for each job."""
        j_list = params.j_list
        i_list = params.i_list

        consecutive_stage_pairs = list(zip(i_list[:-1], i_list[1:]))
        for j in j_list:
            for i, next_i in consecutive_stage_pairs:
                mdl.add(variables.op_end[j, i] <= variables.op_start[j, next_i])

    @staticmethod
    def _add_capacity_constraints(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        stage_2_mc_2_horizon: Mapping[str, Mapping[str, int]] = {},
    ) -> None:
        """Add stage capacity constraints."""
        i_list = params.i_list
        last_i = i_list[-1]

        stage_2_horizon: dict[str, int] = {
            stage: max(mc_2_horizon.values())
            for stage, mc_2_horizon in stage_2_mc_2_horizon.items()
        }

        for i in i_list:
            intervals = [variables.op_intvl[j, i] for j in params.j_list]
            demands = [1] * len(params.j_list)
            # Additional dummy intervals based on stage_2_mc_2_horizon
            if i in stage_2_mc_2_horizon and i != last_i:
                stage_horizon = stage_2_horizon[i]
                dummy_idx = 0
                for mc_horizon in stage_2_mc_2_horizon[i].values():
                    if mc_horizon < stage_horizon:
                        dummy_interval = mdl.new_interval_var(
                            mc_horizon,
                            stage_horizon - mc_horizon,
                            stage_horizon,
                            f"dummy_{i}_{dummy_idx}",
                        )
                        intervals.append(dummy_interval)
                        demands.append(1)
                        dummy_idx += 1

            capacity = len(params.M_of[i])
            mdl.add_cumulative(intervals, demands, capacity)

    @staticmethod
    def _add_structural_constraints(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        stage_2_mc_2_horizon: Mapping[str, Mapping[str, int]] = {},
    ) -> None:
        """Add precedence and default stage-capacity constraints."""
        BaseModelBuilder._add_precedence_constraints(mdl, params, variables)
        BaseModelBuilder._add_capacity_constraints(
            mdl,
            params,
            variables,
            stage_2_mc_2_horizon,
        )

    @staticmethod
    def _add_job_completion_link_constraints(
        mdl: CpModel, params: Params, variables: OperationVars
    ) -> None:
        j_i_2_tail = BaseModelBuilder._compute_tail(params)
        j_list = params.j_list
        i_list = params.i_list
        last_i = i_list[-1]

        for j in j_list:
            for i in i_list:
                mdl.add(
                    variables.op_end[j, i] + j_i_2_tail[j, i]
                    <= variables.op_end[j, last_i]
                )

    @staticmethod
    def _define_objective(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        horizon: int = 0,
        use_max_equality: bool = True,
        obj_lb: float | None = None,
    ) -> EarlinessTardinessVars:
        """Define the weighted earliness/tardiness objective with due date window.

        Minimizes the weighted sum of earliness and tardiness deviations from
        each job's due date window ``[d^{-}_j, d^{+}_j]``. Completion inside
        the window incurs zero cost.

        ``minimize sum_j (w^{-}_j * max(0, d^{-}_j - C_j)
                          + w^{+}_j * max(0, C_j - d^{+}_j))``

        where ``C_j = op_end[j, last_i]``.

        Tie ``E_j`` and ``T_j`` to ``max(0, ·)`` exactly (via
        ``add_max_equality``) so every feasible solution — not just the final
        optimum — reports a matching objective value against a post-hoc E/T
        recomputation from completion times.

        Args:
            mdl (CpModel): The CP-SAT model to which the objective will be added.
            params (Params): Parameters containing job and stage index sets plus
                per-job due window and weights.
            variables (OperationVars): Operation decision variables.
            horizon (int, optional): Upper bound for ``T_j``. If 0 or negative,
                computed as the sum of all processing times. Defaults to 0.
            use_max_equality (bool, optional): If True, use add_max_equality to
                tie E_j and T_j to their respective max(0, ·) expressions exactly.
                If False, use inequality constraints which may lead to looser LP relaxations.
                Defaults to True.

        Returns:
            EarlinessTardinessVars: The per-job ``E_j`` and ``T_j`` IntVars.
        """
        j_list = params.j_list
        last_i = params.i_list[-1]

        if horizon <= 0:
            horizon = sum(params.p[j, i] for j in j_list for i in params.i_list)

        E: dict[str, IntVar] = {}
        T: dict[str, IntVar] = {}
        et_terms: list = []
        for j in j_list:
            C_j = variables.op_end[j, last_i]
            d_lower_j = params.d_lower[j]
            d_upper_j = params.d_upper[j]
            E_j = mdl.new_int_var(0, max(d_lower_j, 0), f"E_{j}")
            T_j = mdl.new_int_var(0, horizon, f"T_{j}")
            if use_max_equality:
                mdl.add_max_equality(E_j, [d_lower_j - C_j, 0])
                mdl.add_max_equality(T_j, [C_j - d_upper_j, 0])
            else:
                mdl.add(E_j >= d_lower_j - C_j)
                mdl.add(T_j >= C_j - d_upper_j)
            E[j] = E_j
            T[j] = T_j
            if params.w_e[j]:
                et_terms.append(params.w_e[j] * E_j)
            if params.w_t[j]:
                et_terms.append(params.w_t[j] * T_j)
        if obj_lb is not None:
            mdl.add(sum(et_terms) >= math.ceil(obj_lb))
        mdl.minimize(sum(et_terms))
        return EarlinessTardinessVars(E=E, T=T)

    # Additional constraints

    @staticmethod
    def add_fixed_operation_precedence_constraint(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        j1: str,
        j2: str,
        i: str,
        ignore_integrity_check: bool = True,
    ) -> None:
        """Adds a precedence constraint between two operations.
        The operation of job j1 must finish before the operation of job j2 starts.

        Args:
            j1 (str): preceding job index
            j2 (str): succeeding job index
            i (str): stage index
            ignore_integrity_check (bool, optional): Skip data integrity check. Defaults to True.
        """
        if not ignore_integrity_check:
            assert j1 in params.j_list, f"Job {j1} not in job list."
            assert j2 in params.j_list, f"Job {j2} not in job list."
            assert i in params.i_list, f"Stage {i} not in stage list."

        mdl.add(variables.op_end[j1, i] <= variables.op_start[j2, i])

    @staticmethod
    def add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        current_schedule: FFcSchedule,
        profile_fix_by_machine: bool = False,
        machine_precedence_stride: int = 1,
    ) -> None:
        """
        Add precedence constraints from a reference dispatch schedule.

        Adds constraints of the form ``op_end[j1, i] <= op_start[j2, i]`` to
        preserve ordering information observed in ``current_schedule``.

        Modes:
            - ``profile_fix_by_machine=True``: preserve machine-sequence order
            with a configurable stride per machine at each stage.
            - ``profile_fix_by_machine=False``: use stage-level start/end times
            to select successor candidates and add a bounded number of arcs.

        Args:
            mdl (CpModel): Target CP-SAT model.
            params (Params): Index sets and processing parameters.
            variables (OperationVars): Decision variables used in constraints.
            current_schedule (FFcSchedule): Reference schedule providing start/end
                times and machine-level sequences.
            profile_fix_by_machine (bool, optional): If True, fix precedence by machine
                sequence; otherwise apply stage-level time-based selection.
                Defaults to False.
            machine_precedence_stride (int, optional): Gap between predecessor and
                successor positions when ``profile_fix_by_machine=True``.
                - 1: adjacent precedence (default), e.g. 1->2->3->4->5
                - 2: every-other precedence, e.g. 1->3->5 and 2->4
                Ignored when ``profile_fix_by_machine=False``.
                Defaults to 1.
        """
        if machine_precedence_stride < 1:
            raise ValueError("machine_precedence_stride must be >= 1")

        start_time_map = current_schedule.get_jik_2_start_time_map()
        end_time_map = current_schedule.get_jik_2_end_time_map()

        for i in params.i_list:
            if profile_fix_by_machine:
                for m in params.M_of[i]:
                    job_tuple_seq = current_schedule.get_job_sequence(i, m)
                    seq_len = len(job_tuple_seq)

                    for idx in range(seq_len - machine_precedence_stride):
                        j1 = job_tuple_seq[idx][0]
                        j2 = job_tuple_seq[idx + machine_precedence_stride][0]
                        BaseModelBuilder.add_fixed_operation_precedence_constraint(
                            mdl, params, variables, j1, j2, i
                        )
            else:
                current_j_set = {j for j, ip, _ in start_time_map if ip == i}
                current_j_list = [j for j in params.j_list if j in current_j_set]
                stage_job_2_index_map = {j: idx for idx, j in enumerate(current_j_list)}
                # Extract start and end times for jobs at stage i
                # Map of job -> start time at stage i
                j_2_start_time_map = {
                    j: start_time_map[j, i, k]
                    for j in current_j_list
                    for k in params.M_of[i]
                    if (j, i, k) in start_time_map
                }
                # Map of job -> end time at stage i
                j_2_end_time_map = {
                    j: end_time_map[j, i, k]
                    for j in current_j_list
                    for k in params.M_of[i]
                    if (j, i, k) in end_time_map
                }
                # List of jobs sorted by their 1) end times 2) start times 3) job index
                sorted_by_end = sorted(
                    current_j_list,
                    key=lambda j: (
                        j_2_end_time_map.get(j, float("inf")),
                        j_2_start_time_map.get(j, float("inf")),
                        stage_job_2_index_map.get(j, float("inf")),
                    ),
                )

                # List of jobs sorted by their 1) start times 2) end times 3) job index
                sorted_by_start = sorted(
                    current_j_list,
                    key=lambda j: (
                        j_2_start_time_map.get(j, float("inf")),
                        j_2_end_time_map.get(j, float("inf")),
                        stage_job_2_index_map.get(j, float("inf")),
                    ),
                )

                # Index-based selection of precedence arcs:
                # for each job j1 in end-time order
                for idx, j1 in enumerate(sorted_by_end):
                    j1_end_time = j_2_end_time_map.get(j1, float("inf"))
                    max_candidates = min(
                        len(params.M_of[i]), len(sorted_by_end) - idx - 1
                    )

                    # Find the position in the start-time sorted list where jobs start
                    # after j1 ends; use bisect_left to find the insertion point
                    # for j1_end_time in the sorted_by_start list
                    start_idx = bisect_left(
                        sorted_by_start,
                        j1_end_time,
                        key=lambda j: j_2_start_time_map.get(j, float("inf")),
                    )

                    # Add precedence constraints from j1 to a bounded number
                    # of successor candidates in start-time order
                    j2_list: list[JobIdType] = sorted_by_start[
                        start_idx : start_idx + max_candidates
                    ]
                    for j2 in j2_list:
                        BaseModelBuilder.add_fixed_operation_precedence_constraint(
                            mdl, params, variables, j1, j2, i
                        )

    @staticmethod
    def add_start_time_freezed_operation_constraints(
        mdl: CpModel,
        variables: OperationVars,
        start_time_map: dict[tuple[JobIdType, StageIdType, McIdType], int],
    ) -> None:
        for (j, i, k), s_time in start_time_map.items():
            mdl.add(variables.op_start[j, i] == s_time)

    # Hints

    @staticmethod
    def apply_start_hints_from_start_time_map(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        start_time_map: dict[tuple[JobIdType, StageIdType, McIdType], int],
        ignore_integrity_check: bool = True,
    ) -> None:
        """Applies start time hints to the model from a given start time map.

        Args:
            start_time_map (dict[tuple[JobIdType, StageIdType, McIdType], int]): A mapping from (job_id, stage_id, machine_id) to start time.
        """
        for (j, i, _), s_time in start_time_map.items():
            if not ignore_integrity_check:
                assert j in params.j_list, f"Job {j} not in job list."
                assert i in params.i_list, f"Stage {i} not in stage list."
            mdl.add_hint(variables.op_start[j, i], s_time)

    @staticmethod
    def apply_end_hints_from_end_time_map(
        mdl: CpModel,
        params: Params,
        variables: OperationVars,
        end_time_map: dict[tuple[JobIdType, StageIdType, McIdType], int],
        ignore_integrity_check: bool = True,
    ) -> None:
        """Applies end time hints to the model from a given end time map.

        Args:
            end_time_map (dict[tuple[JobIdType, StageIdType, McIdType], int]): A mapping from (job_id, stage_id, machine_id) to end time.
        """
        for (j, i, _), e_time in end_time_map.items():
            if not ignore_integrity_check:
                assert j in params.j_list, f"Job {j} not in job list."
                assert i in params.i_list, f"Stage {i} not in stage list."
            mdl.add_hint(variables.op_end[j, i], e_time)
