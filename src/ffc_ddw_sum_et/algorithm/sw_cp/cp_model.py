"""Partition-aware CP-SAT builder for SW-CP sliding-window batches.

Mirrors hybridflowshop's ``SwCpModelBuilder`` for the structural
skeleton (op vars only for non-time-fixed; left/right dummy bars per
machine; cumulative-with-dummy-bar capacity; non-fixed job precedence
constants from a right-justified incumbent), but replaces the
``common_spacing`` / makespan objective with a *partial weighted E/T*
objective that includes only jobs whose last-stage op is non-time-fixed
in the partition.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python.cp_model import CpModel, IntervalVar, IntVar

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule, StageIdType
from ..cumulative import (
    BaseModelBuilder,
    EarlinessTardinessVars,
    OperationVars,
    Params,
    PFMethod,
    decode_pf_method,
)
from .partition import OperationPartition

__all__ = ["SwCpBuildResult", "SwCpModelBuilder"]


@dataclass
class SwCpBuildResult:
    mdl: CpModel
    sub_params: Params
    op_vars: OperationVars
    et_vars: EarlinessTardinessVars
    objective_jobs: tuple[str, ...]
    """Jobs whose last-stage op is non-time-fixed (E_j, T_j contribute to the CP objective)."""
    et_offset_partial: int
    """Constant E+T contribution from jobs whose last-stage op is time-fixed
    within the sub-instance (not added to the CP objective; the dispatcher
    recomputes the full progress offset from rj_schedule instead)."""


class SwCpModelBuilder:
    """Build the partition-aware CP-SAT model for one SW-CP batch."""

    def build(
        self,
        sub_instance: FFcDDWParameters,
        rj_schedule: FFcSchedule,
        stage_2_partition: dict[StageIdType, OperationPartition],
        *,
        horizon: int,
        pf_method: PFMethod,
        time_factor: int = 1,
    ) -> SwCpBuildResult:
        """Build the partition-aware CP-SAT model for one SW-CP batch.

        When ``time_factor > 1`` (CSR coarse mode) a job's coarse last-stage
        completion ``C^c`` is interpreted as original-scale ``time_factor * C^c``
        against the sub-instance's (original-scale) due window — mirroring
        :meth:`BaseModelBuilder._define_objective`. Every downstream helper
        reads the factor from ``sub_params.time_factor``, so no other signature
        changes are needed. ``time_factor=1`` (default) is byte-identical to the
        pre-CSR behavior.
        """
        mdl = CpModel()

        # Parameters (carry time_factor so all sub_params readers pick it up)
        sub_params = BaseModelBuilder.make_params(sub_instance, time_factor=time_factor)

        # Variables
        op_vars = self._make_non_time_fixed_op_vars(
            mdl, sub_params, horizon, stage_2_partition
        )

        # Objective
        et_vars, objective_jobs, et_offset = self._define_partial_et_objective(
            mdl,
            sub_params,
            op_vars,
            stage_2_partition,
            rj_schedule,
            horizon=horizon,
        )

        # Constraints
        self._create_dummy_bar_vars_and_add_cumulative_constrs(
            mdl, sub_params, stage_2_partition, rj_schedule, op_vars, horizon=horizon
        )
        self._add_non_fixed_job_precedence_constraints(
            mdl, sub_params, stage_2_partition, rj_schedule, op_vars
        )
        self._add_profile_fix_precedence_constraints(
            mdl,
            sub_params,
            op_vars,
            rj_schedule,
            stage_2_partition,
            pf_method=pf_method,
        )

        # Hints
        self._apply_hints(mdl, sub_params, op_vars, rj_schedule, stage_2_partition)
        self._apply_et_hints(mdl, sub_params, et_vars, objective_jobs, rj_schedule)

        return SwCpBuildResult(
            mdl=mdl,
            sub_params=sub_params,
            op_vars=op_vars,
            et_vars=et_vars,
            objective_jobs=objective_jobs,
            et_offset_partial=et_offset,
        )

    # ---- variable creation ----

    @staticmethod
    def _make_non_time_fixed_op_vars(
        mdl: CpModel,
        params: Params,
        horizon: int,
        stage_2_partition: dict[StageIdType, OperationPartition],
    ) -> OperationVars:
        op_start: dict[tuple[str, str], IntVar] = {}
        op_end: dict[tuple[str, str], IntVar] = {}
        op_intvl: dict[tuple[str, str], IntervalVar] = {}

        for i in params.i_list:
            partition = stage_2_partition[i]
            for j, _ in partition.non_time_fixed:
                p = params.p[j, i]
                if p > horizon:
                    raise ValueError(
                        f"Processing time p[{j},{i}]={p} exceeds horizon={horizon}."
                    )
                start_var = mdl.new_int_var(0, horizon - p, f"start_{j}_{i}")
                end_var = mdl.new_int_var(p, horizon, f"end_{j}_{i}")
                interval_var = mdl.new_interval_var(
                    start_var, p, end_var, f"interval_{j}_{i}"
                )
                op_start[j, i] = start_var
                op_end[j, i] = end_var
                op_intvl[j, i] = interval_var

        return OperationVars(op_start=op_start, op_end=op_end, op_intvl=op_intvl)

    # ---- objective ----

    @staticmethod
    def _define_partial_et_objective(
        mdl: CpModel,
        sub_params: Params,
        op_vars: OperationVars,
        stage_2_partition: dict[StageIdType, OperationPartition],
        rj_schedule: FFcSchedule,
        *,
        horizon: int,
    ) -> tuple[EarlinessTardinessVars, tuple[str, ...], int]:
        """Add ``min sum_{j ∈ obj_jobs} (w_e[j]*E_j + w_t[j]*T_j)``.

        ``obj_jobs`` = jobs whose last-stage op is non-time-fixed (the
        only ones whose ``C_j`` is a CP variable). Time-fixed last-stage
        jobs contribute a constant ``et_offset`` that is *not* added to
        the CP objective (constants don't change argmin) but is reported
        for diagnostics.
        """
        last_i = sub_params.i_list[-1]
        last_stage_ntf_jobs = {j for j, _ in stage_2_partition[last_i].non_time_fixed}

        # CSR: a coarse completion C^c is interpreted as time_factor * C^c
        # against the (original-scale) due window. time_factor == 1 leaves every
        # expression identical to the pre-CSR path.
        k = sub_params.time_factor
        t_ub = k * horizon if k > 1 else horizon

        E: dict[str, IntVar] = {}
        T: dict[str, IntVar] = {}
        et_terms: list = []
        objective_jobs: list[str] = []
        offset = 0

        for j in sub_params.j_list:
            d_lower = sub_params.d_lower[j]
            d_upper = sub_params.d_upper[j]
            w_e = sub_params.w_e[j]
            w_t = sub_params.w_t[j]
            if j in last_stage_ntf_jobs:
                E_j = mdl.new_int_var(0, max(d_lower, 0), f"E_{j}")
                T_j = mdl.new_int_var(0, t_ub, f"T_{j}")
                scaled_C_j = k * op_vars.op_end[j, last_i]
                mdl.add_max_equality(E_j, [d_lower - scaled_C_j, 0])
                mdl.add_max_equality(T_j, [scaled_C_j - d_upper, 0])
                E[j] = E_j
                T[j] = T_j
                if w_e:
                    et_terms.append(w_e * E_j)
                if w_t:
                    et_terms.append(w_t * T_j)
                objective_jobs.append(j)
            else:
                # last-stage op time-fixed: C_j is a constant from rj_schedule
                scaled_C_const = k * rj_schedule.get_job_end_time(last_i, j)
                offset += w_e * max(0, d_lower - scaled_C_const)
                offset += w_t * max(0, scaled_C_const - d_upper)

        if et_terms:
            mdl.minimize(sum(et_terms))
        # else: no CP variables in the objective; the model is purely
        # feasibility-shaped and the dispatcher will skip-or-record-only.

        return (
            EarlinessTardinessVars(E=E, T=T),
            tuple(objective_jobs),
            int(offset),
        )

    # ---- structural constraints ----

    @staticmethod
    def _create_dummy_bar_vars_and_add_cumulative_constrs(
        mdl: CpModel,
        params: Params,
        stage_2_partition: dict[StageIdType, OperationPartition],
        rj_schedule: FFcSchedule,
        op_vars: OperationVars,
        horizon: int,
    ) -> None:
        """Stage capacity = cumulative over (non-time-fixed intervals + LTF/RTF dummy bars).

        Boundaries are read from ``rj_schedule``:
        - left_boundary[i,k]  = max end of LTF ops on (i,k), 0 if none
        - right_boundary[i,k] = min start of RTF ops on (i,k), horizon if none

        Both bars are fixed (start, length, end are all constants).
        Zero-length bars are skipped.
        """
        start_map = rj_schedule.get_jik_2_start_time_map()
        end_map = rj_schedule.get_jik_2_end_time_map()

        for i in params.i_list:
            partition = stage_2_partition[i]
            intervals: list[IntervalVar] = [
                op_vars.op_intvl[j, i] for j, _ in partition.non_time_fixed
            ]

            for mc_id in params.M_of[i]:
                ltf_ends = [
                    end_map[(j, i, k)]
                    for j, k in partition.left_time_fixed
                    if k == mc_id
                ]
                left_boundary = max(ltf_ends) if ltf_ends else 0

                rtf_starts = [
                    start_map[(j, i, k)]
                    for j, k in partition.right_time_fixed
                    if k == mc_id
                ]
                right_boundary = min(rtf_starts) if rtf_starts else horizon

                if left_boundary > 0:
                    intervals.append(
                        mdl.new_interval_var(
                            0, left_boundary, left_boundary, f"l_dummy_{i}_{mc_id}"
                        )
                    )
                if right_boundary < horizon:
                    intervals.append(
                        mdl.new_interval_var(
                            right_boundary,
                            horizon - right_boundary,
                            horizon,
                            f"r_dummy_{i}_{mc_id}",
                        )
                    )

            if not intervals:
                continue
            demands = [1] * len(intervals)
            capacity = len(params.M_of[i])
            mdl.add_cumulative(intervals, demands, capacity)

    @staticmethod
    def _add_non_fixed_job_precedence_constraints(
        mdl: CpModel,
        params: Params,
        stage_2_partition: dict[StageIdType, OperationPartition],
        rj_schedule: FFcSchedule,
        op_vars: OperationVars,
    ) -> None:
        """Job-stage precedence respecting the partition.

        For each job ``j`` and consecutive stages ``(i, next_i)``:
        - both ops non-time-fixed → ``op_end[j,i] <= op_start[j,next_i]``
        - i time-fixed, next_i non-time-fixed → constant lower bound
        - i non-time-fixed, next_i time-fixed → constant upper bound
        Constants come from ``rj_schedule`` (right-justified).
        """
        i_list = params.i_list
        if len(i_list) < 2:
            return
        for i, next_i in zip(i_list[:-1], i_list[1:]):
            i_partition = stage_2_partition[i]
            ni_partition = stage_2_partition[next_i]
            i_ntf_jobs = {j for j, _ in i_partition.non_time_fixed}
            ni_ntf_jobs = {j for j, _ in ni_partition.non_time_fixed}

            for j, _ in ni_partition.non_time_fixed:
                if j in i_ntf_jobs:
                    mdl.add(op_vars.op_end[j, i] <= op_vars.op_start[j, next_i])
                else:
                    i_end = rj_schedule.get_job_end_time(i, j)
                    mdl.add(op_vars.op_start[j, next_i] >= i_end)

            for j, _ in i_partition.non_time_fixed:
                if j not in ni_ntf_jobs:
                    ni_end = rj_schedule.get_job_end_time(next_i, j)
                    ni_start = ni_end - params.p[j, next_i]
                    mdl.add(op_vars.op_end[j, i] <= ni_start)

    @staticmethod
    def _add_profile_fix_precedence_constraints(
        mdl: CpModel,
        params: Params,
        op_vars: OperationVars,
        rj_schedule: FFcSchedule,
        stage_2_partition: dict[StageIdType, OperationPartition],
        *,
        pf_method: PFMethod,
    ) -> None:
        """Constrain order inside LPF and RPF using the existing helper.

        Builds a copy of ``rj_schedule`` with every non-profile-fixed op
        removed (LTF, unfixed, RTF), then forwards to
        :meth:`BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`.
        That helper only references jobs present in the schedule, so
        precedence is added strictly inside LPF and RPF.
        """
        non_pf_ops: set[tuple[str, str, str]] = set()
        any_profile_fixed = False
        for i, partition in stage_2_partition.items():
            if partition.profile_fixed:
                any_profile_fixed = True
            for j, k in partition.non_profile_fixed:
                non_pf_ops.add((j, i, k))
        if not any_profile_fixed:
            return

        profile_fixed_schedule = rj_schedule.deepcopy()
        profile_fixed_schedule.remove_operations(non_pf_ops)

        by_machine, stride_set = decode_pf_method(pf_method)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            profile_fixed_schedule,
            profile_fix_by_machine=by_machine,
            machine_precedence_stride_set=stride_set,
        )

    # ---- hints ----

    @staticmethod
    def _apply_hints(
        mdl: CpModel,
        params: Params,
        op_vars: OperationVars,
        rj_schedule: FFcSchedule,
        stage_2_partition: dict[StageIdType, OperationPartition],
    ) -> None:
        ntf_ops: set[tuple[str, str, str]] = {
            (j, i, k)
            for i, partition in stage_2_partition.items()
            for j, k in partition.non_time_fixed
        }
        if not ntf_ops:
            return
        all_start = rj_schedule.get_jik_2_start_time_map()
        all_end = rj_schedule.get_jik_2_end_time_map()
        ntf_start = {op: all_start[op] for op in ntf_ops}
        ntf_end = {op: all_end[op] for op in ntf_ops}
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, ntf_start
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, ntf_end
        )

    @staticmethod
    def _apply_et_hints(
        mdl: CpModel,
        sub_params: Params,
        et_vars: EarlinessTardinessVars,
        objective_jobs: tuple[str, ...],
        rj_schedule: FFcSchedule,
    ) -> None:
        """Hint E_j and T_j to their actual values from the reference schedule.

        Under ``time_factor > 1`` the completion is interpreted as
        ``time_factor * C^c`` against the original due window, matching the
        objective terms in :meth:`_define_partial_et_objective`.
        """
        last_i = sub_params.i_list[-1]
        k = sub_params.time_factor
        for j in objective_jobs:
            scaled_c_j = k * rj_schedule.get_job_end_time(last_i, j)
            e_hint = max(0, sub_params.d_lower[j] - scaled_c_j)
            t_hint = max(0, scaled_c_j - sub_params.d_upper[j])
            if j in et_vars.E:
                mdl.add_hint(et_vars.E[j], e_hint)
            if j in et_vars.T:
                mdl.add_hint(et_vars.T[j], t_hint)

    # ---- post-CP schedule reconstruction ----

    @staticmethod
    def build_full_schedule_from_cp(
        full_instance: FFcDDWParameters,
        rj_schedule: FFcSchedule,
        stage_2_partition: dict[StageIdType, OperationPartition],
        op_vars: OperationVars,
        solver,  # cp_model.CpSolver
    ) -> tuple[FFcSchedule, int]:
        """Merge LTF baseline + CP-decoded non-time-fixed + RTF (matched)
        into a full :class:`FFcSchedule`.

        The CP model only fixes the **time** of right-time-fixed ops (via
        per-machine dummy bars); their incumbent machine assignment is
        *not* part of the contract — "right time fixed" not "right freeze".
        Reconstruction runs in two phases on an LTF-only baseline:

        Phase A (:meth:`_replay_non_time_fixed`): LPF + unfixed + RPF are
        replayed in ``cp_start`` ascending order via
        :meth:`FFcSchedule.add_operation_2_stage` (release_t = CP start,
        from ``solver.Value(op_start)``).

        Phase B (:meth:`_replay_right_time_fixed`): RTF is grouped by
        source (incumbent) machine, source machines are sorted by their
        group's earliest start time, and each source group is matched 1:1
        to the un-dispatched target machine with the smallest
        latest-end-time. Ops within a group are placed on the target via
        :meth:`FFcSchedule.add_ops_times_2_mc` (explicit time + machine,
        no slide). This mirrors hybridflowshop's Phase 3 RTF matching.

        Cumulative-with-per-machine-dummy-bars proves SOME valid machine
        assignment exists, but neither phase's heuristic is exhaustive.
        Phase A's greedy may slide an op past ``cp_start``; Phase B's
        matching may pick a target whose existing occupants collide with
        the RTF interval, in which case we fall back to
        :meth:`FFcSchedule.add_operation_2_stage` for that op. Any
        replayed op whose realised end-time differs from the CP-provided
        end-time bumps the divergence counter (diagnostic only — the
        schedule is still feasible, the dispatcher recomputes E/T from
        the realised positions).
        """
        result = rj_schedule.deepcopy()

        # Strip non-time-fixed AND right-time-fixed; LTF stays as baseline.
        ops_to_remove: set[tuple[str, str, str]] = set()
        for stage_id, partition in stage_2_partition.items():
            for j, k in partition.non_time_fixed:
                ops_to_remove.add((j, stage_id, k))
            for j, k in partition.right_time_fixed:
                ops_to_remove.add((j, stage_id, k))
        if ops_to_remove:
            result.remove_operations(ops_to_remove)

        divergence = SwCpModelBuilder._replay_non_time_fixed(
            result, full_instance, stage_2_partition, op_vars, solver
        )
        divergence += SwCpModelBuilder._replay_right_time_fixed(
            result, full_instance, stage_2_partition, rj_schedule
        )
        return result, divergence

    @staticmethod
    def _replay_non_time_fixed(
        result: FFcSchedule,
        full_instance: FFcDDWParameters,
        stage_2_partition: dict[StageIdType, OperationPartition],
        op_vars: OperationVars,
        solver,  # cp_model.CpSolver
    ) -> int:
        """Phase A: replay LPF + unfixed + RPF in cp_start order via greedy
        machine selection. Returns the count of ops whose realised end-time
        differed from the CP-provided end-time."""
        divergence = 0
        for i in full_instance.stage_id_list:
            partition = stage_2_partition.get(i)
            if partition is None or not partition.non_time_fixed:
                continue
            cp_ops = []
            for j, _ in partition.non_time_fixed:
                cp_start = int(solver.Value(op_vars.op_start[j, i]))
                cp_end = int(solver.Value(op_vars.op_end[j, i]))
                cp_ops.append((cp_start, -cp_end, j, cp_end))
            cp_ops.sort()
            for cp_start, _neg_end, j, cp_end in cp_ops:
                result.add_operation_2_stage(
                    stage_id=i,
                    job_id=j,
                    duration=cp_end - cp_start,
                    release_t=cp_start,
                )
                if result.get_job_end_time(i, j) != cp_end:
                    divergence += 1
        return divergence

    @staticmethod
    def _replay_right_time_fixed(
        result: FFcSchedule,
        full_instance: FFcDDWParameters,
        stage_2_partition: dict[StageIdType, OperationPartition],
        rj_schedule: FFcSchedule,
    ) -> int:
        """Phase B: place each RTF source-machine group on an un-dispatched
        target machine via explicit ``add_ops_times_2_mc``. Source machines
        are ordered by their group's earliest start time (hybridflowshop's
        ``right_bar_init_start`` equivalent); targets are chosen by
        minimum ``get_machine_latest_end_time`` among un-dispatched
        candidates. On overlap (``ValueError``) or when no target remains,
        fall back to ``add_operation_2_stage(release_t=cp_start)`` per op
        and count any realised end-time mismatch as divergence."""
        start_map = rj_schedule.get_jik_2_start_time_map()
        end_map = rj_schedule.get_jik_2_end_time_map()
        divergence = 0
        for i in full_instance.stage_id_list:
            partition = stage_2_partition.get(i)
            if partition is None or not partition.right_time_fixed:
                continue
            src_groups: dict[str, list[tuple[int, int, str]]] = {}
            for j, k in partition.right_time_fixed:
                s = int(start_map[(j, i, k)])
                e = int(end_map[(j, i, k)])
                src_groups.setdefault(k, []).append((s, e, j))
            for k in src_groups:
                src_groups[k].sort()
            src_order = sorted(src_groups, key=lambda k: src_groups[k][0][0])
            dispatched: set[str] = set()
            for src_k in src_order:
                candidates = [
                    m for m in result.machines_per_stage[i] if m not in dispatched
                ]
                target = (
                    min(
                        candidates,
                        key=lambda m: result.get_machine_latest_end_time(i, m),
                    )
                    if candidates
                    else None
                )
                if target is None:
                    for s, e, j in src_groups[src_k]:
                        result.add_operation_2_stage(
                            stage_id=i, job_id=j, duration=e - s, release_t=s
                        )
                        if result.get_job_end_time(i, j) != e:
                            divergence += 1
                    continue
                for s, e, j in src_groups[src_k]:
                    try:
                        result.add_ops_times_2_mc(i, target, j, s, e)
                    except ValueError:
                        result.add_operation_2_stage(
                            stage_id=i, job_id=j, duration=e - s, release_t=s
                        )
                        if result.get_job_end_time(i, j) != e:
                            divergence += 1
                dispatched.add(target)
        return divergence
