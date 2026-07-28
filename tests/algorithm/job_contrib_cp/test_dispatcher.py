"""Tests for ``JobContribCpDispatcher``."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cumulative import BaseModelBuilder
from ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher import JobContribCpDispatcher
from ffc_ddw_sum_et.algorithm.job_contrib_cp.option import JobContribCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import (
    compute_job_2_obj_contrib_map,
)


def _make_small_instance() -> FFcDDWParameters:
    jobs = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name="jc_test",
        job_id_list=jobs,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0", "i1_1"]},
        p_manager=JobStageProcessingTimeManager(
            name="jc_test_p",
            df=pd.DataFrame([[2, 3], [3, 2], [1, 1], [2, 2]]),
        ),
        job_2_due_window_map={
            "j0": (4, 6),
            "j1": (5, 8),
            "j2": (3, 5),
            "j3": (6, 10),
        },
        job_2_ewt_map={"j0": 1, "j1": 2, "j2": 1, "j3": 1},
        job_2_twt_map={"j0": 2, "j1": 1, "j2": 2, "j3": 1},
    )


def _build_schedule(
    instance: FFcDDWParameters,
    s1_ops: list[tuple[str, int, int]],
) -> FFcSchedule:
    """Build a 2-stage schedule from stage-1 (s, e) ops.

    Stage-0 ops are left-shifted in sequence.
    """
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    p = instance.p_manager.stage_job_2_value_map(
        instance.stage_id_list, instance.job_id_list
    )
    s0, s1 = instance.stage_id_list[0], instance.stage_id_list[1]
    mc0 = instance.stage_2_machines_map[s0][0]

    s0_t = 0
    for j, s1_start, s1_end in s1_ops:
        p0 = int(p[s0, j])
        schedule.add_ops_times_2_mc(s0, mc0, j, s0_t, s0_t + p0)
        schedule.add_ops_times_2_mc(s1, "i1_0", j, s1_start, s1_end)
        s0_t += p0
    return schedule


def _make_zero_contrib_instance() -> FFcDDWParameters:
    """Same shape as ``_make_small_instance`` but with due windows so wide that
    *every* job is on time no matter how the seed is laid out.

    ``d^- = 0`` makes earliness impossible and ``d^+ = 100`` is far beyond any
    reachable completion time, so ``f_j(C_j) == 0`` for all j by construction.
    This is what exercises the ``jd_count_eff == 0`` early-exit path; a fixture
    that merely *happens* to be all-zero would silently stop covering it.
    """
    jobs = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name="jc_test_zero_contrib",
        job_id_list=jobs,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0", "i1_1"]},
        p_manager=JobStageProcessingTimeManager(
            name="jc_test_zero_contrib_p",
            df=pd.DataFrame([[2, 3], [3, 2], [1, 1], [2, 2]]),
        ),
        job_2_due_window_map={j: (0, 100) for j in jobs},
        job_2_ewt_map={j: 1 for j in jobs},
        job_2_twt_map={j: 2 for j in jobs},
    )


def _apply_postprocess(
    schedule: FFcSchedule, instance: FFcDDWParameters
) -> FFcSchedule:
    sched = schedule.deepcopy()
    sched.make_semi_active(instance.stage_2_job_2_p_map)
    sched.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )
    return sched


class TestEarlyExit:
    """P2b: jd_count_eff == 0 early exit."""

    def test_all_jobs_zero_contrib_returns_early(self) -> None:
        instance = _make_zero_contrib_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 2, 5),
                ("j1", 5, 7),
                ("j2", 7, 8),
                ("j3", 8, 10),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        contrib = compute_job_2_obj_contrib_map(seed, instance)
        assert all(v == 0 for v in contrib.values()), (
            f"fixture must have zero contribution everywhere, got {contrib}"
        )

        with patch.object(
            BaseModelBuilder,
            "build",
            side_effect=RuntimeError("should not be called"),
        ):
            record = JobContribCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobContribCpOption(jd_count_target=2),
                    ref_solution=seed,
                )
            )

        assert record.work_status == WorkStatus.OPTIMAL
        assert record.result is not None
        assert record.result.obj_value == 0.0
        assert record.result.obj_bound == 0.0
        assert record.result.metrics is not None
        assert record.result.metrics["jd_count_eff"] == 0

    def test_early_exit_does_not_require_solver(self) -> None:
        instance = _make_zero_contrib_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 2, 5),
                ("j1", 5, 7),
                ("j2", 7, 8),
                ("j3", 8, 10),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        with patch.object(BaseModelBuilder, "build") as mock_build:
            record = JobContribCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobContribCpOption(jd_count_target=2),
                    ref_solution=seed,
                )
            )
        mock_build.assert_not_called()
        assert record.result is not None
        assert record.result.schedule is seed


class TestDispatcherBasics:
    """P5: dispatcher end-to-end basics."""

    def test_no_ref_solution_raises(self) -> None:
        instance = _make_small_instance()
        d = JobContribCpDispatcher()
        with pytest.raises(RuntimeError, match="ref_solution"):
            d.run(
                AlgSpec(
                    instance=instance,
                    option=JobContribCpOption(jd_count_target=1),
                )
            )

    def test_wrong_instance_type_raises(self) -> None:
        with pytest.raises(TypeError, match="FFcDDWParameters"):
            JobContribCpDispatcher().run(
                AlgSpec(
                    instance=object(),
                    option=JobContribCpOption(jd_count_target=1),
                )
            )

    def test_returns_alg_record_with_metrics(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.work_status in (WorkStatus.FEASIBLE, WorkStatus.OPTIMAL)
        assert record.result is not None
        assert record.result.schedule is not None
        assert record.result.obj_value is not None
        assert record.result.metrics is not None
        assert "jd_count_target" in record.result.metrics
        assert "jd_count_eff" in record.result.metrics
        assert "positive_contrib_job_count" in record.result.metrics
        assert "incumbent_obj" in record.result.metrics

    def test_jd_count_eff_le_jd_count_target(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=10,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.result is not None
        assert record.result.metrics is not None
        assert record.result.metrics["jd_count_eff"] <= 10
        assert record.result.metrics["jd_count_eff"] <= len(instance.job_id_list)

    def test_horizon_covers_max_due_upper(self) -> None:
        """P5: horizon이 max d⁺를 충분히 덮는지."""
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)
        max_du = max(instance.job_2_dw_ub_map.values())

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                    horizon_multiplier=1.25,
                ),
                ref_solution=seed,
            )
        )

        assert record.result is not None
        assert record.result.metrics is not None
        horizon = record.result.metrics["horizon"]
        assert horizon >= max_du * 1.25

    def test_incumbent_fallback_on_budget_exhausted(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=0.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.work_status == WorkStatus.FEASIBLE
        assert record.result is not None
        assert record.result.metrics is not None
        assert record.result.metrics.get("fallback") == "incumbent"
        assert (
            record.result.metrics.get("cpsat_status")
            == "budget_exhausted_before_solve:cp_tl"
        )

    def test_wall_clock_deadline_bounds_cp_tl(self) -> None:
        """A generous ``cp_tl`` must not outlive the controller's remaining
        wall-clock budget: the tighter of the two bounds wins."""
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=3600.0,  # far more than the run has left
                    wall_clock_deadline_sec=time.monotonic() - 1.0,  # already past
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.result is not None
        assert record.result.metrics is not None
        assert (
            record.result.metrics.get("cpsat_status")
            == "budget_exhausted_before_solve:wall_clock_deadline"
        )

    def test_wall_clock_deadline_alone_bounds_solve(self) -> None:
        """``wall_clock_deadline_sec`` is honoured even when ``cp_tl`` is unset."""
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=None,
                    wall_clock_deadline_sec=time.monotonic() - 1.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.result is not None
        assert record.result.metrics is not None
        assert (
            record.result.metrics.get("cpsat_status")
            == "budget_exhausted_before_solve:wall_clock_deadline"
        )

    def test_progress_log_obj_bound_none(self) -> None:
        """B-1: 모든 progress_log 엔트리의 obj_bound가 None."""
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.progress_log is not None
        for entry in record.progress_log:
            assert entry.obj_bound is None, (
                f"progress_log entry obj_bound must be None, got {entry.obj_bound}"
            )

    def test_metrics_contains_cp_progress(self) -> None:
        """B-2: metrics[cp_progress]에 t/obj_value/obj_bound 형식의 궤적이 존재."""
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                ),
                ref_solution=seed,
            )
        )

        assert record.result is not None
        assert record.result.metrics is not None
        cp_progress = record.result.metrics.get("cp_progress")
        assert cp_progress is not None, "metrics must contain 'cp_progress'"
        assert isinstance(cp_progress, list), "cp_progress must be a list"
        for entry in cp_progress:
            assert "t" in entry
            assert "obj_value" in entry
            assert "obj_bound" in entry
            assert isinstance(entry["t"], (int, float))
        if len(cp_progress) >= 2:
            ts = [e["t"] for e in cp_progress]
            assert ts == sorted(ts), "cp_progress t must be monotonic non-decreasing"


class TestProfileFixBridging:
    """P3: profile fix bridging — A->X->B with X removed produces A->B arc."""

    def test_bridging_after_remove_jobs(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 2, 5),
                ("j1", 5, 7),
                ("j2", 7, 8),
                ("j3", 8, 10),
            ],
        )

        s0 = instance.stage_id_list[0]
        mc0 = instance.stage_2_machines_map[s0][0]

        seq_before = [t[0] for t in seed.get_job_sequence(s0, mc0)]
        assert len(seq_before) == 4

        pf = seed.deepcopy()
        pf.remove_jobs({"j1"})

        seq_after = [t[0] for t in pf.get_job_sequence(s0, mc0)]
        assert "j1" not in seq_after
        assert len(seq_after) == 3


class TestCompleteHint:
    """P4: complete hint validation."""

    def test_hint_is_complete(self) -> None:
        """P4: CP-SAT 로그에 'incomplete'가 없어야 한다."""
        import math

        from ortools.sat.python import cp_model as cp_module

        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        job_2_contrib = compute_job_2_obj_contrib_map(seed, instance)
        positive_jobs = [j for j, v in job_2_contrib.items() if v > 0]
        if not positive_jobs:
            pytest.skip("No contrib jobs to destroy")

        selected = sorted(positive_jobs, key=lambda j: (-job_2_contrib[j], j))[:1]

        pf = seed.deepcopy()
        pf.remove_jobs(set(selected))
        max_du = max(instance.job_2_dw_ub_map.values())
        horizon = math.ceil(max(seed.makespan, max_du) * 1.25)

        builder = BaseModelBuilder()
        mdl, params, op_vars, et_vars = builder.build(
            instance, horizon=horizon, time_factor=1
        )

        from ffc_ddw_sum_et.algorithm.cumulative import decode_pf_method

        by_machine, stride_set = decode_pf_method("PF1")
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            pf,
            profile_fix_by_machine=by_machine,
            machine_precedence_stride_set=stride_set,
        )

        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, seed.get_jik_2_start_time_map()
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, seed.get_jik_2_end_time_map()
        )
        if et_vars is not None:
            BaseModelBuilder.apply_et_hints_from_ref_schedule(
                mdl, params, et_vars, seed
            )

        log_lines: list[str] = []
        solver = cp_module.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.max_time_in_seconds = 10.0
        # log_search_progress is what makes CP-SAT emit anything at all; without
        # it log_callback never fires and the assertion below is vacuous.
        solver.parameters.log_search_progress = True
        solver.parameters.log_to_stdout = False
        solver.log_callback = log_lines.append

        solver.solve(mdl)

        log_text = "\n".join(log_lines)
        assert log_lines, "no search log captured — the assertion below is vacuous"
        assert "solution hint is complete" in log_text.lower(), (
            f"CP-SAT never reported a complete hint:\n{log_text[:2000]}"
        )
        assert "solution hint is incomplete" not in log_text.lower(), (
            f"Found an incomplete hint in the search log:\n{log_text[:2000]}"
        )


class TestCpsatStatusBranching:
    """Tests for CP-SAT status branching (UNKNOWN / INFEASIBLE / MODEL_INVALID)."""

    def _make_solver_mock(self, status: int, status_name: str) -> object:
        solver = MagicMock()
        solver.solve.return_value = status
        solver.status_name = MagicMock(return_value=status_name)
        solver.objective_value = 0.0
        solver.value = MagicMock(return_value=0)
        solver.response_proto = MagicMock()
        solver.response_proto.solve_log = ""
        return solver

    def test_unknown_returns_fallback_no_exception(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        mock_solver = self._make_solver_mock(0, "UNKNOWN")
        with patch(
            "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
            return_value=mock_solver,
        ):
            record = JobContribCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobContribCpOption(
                        jd_count_target=1,
                        cp_tl_seconds=5.0,
                        solver_thread_cnt=1,
                        error_if_infeasible=True,
                    ),
                    ref_solution=seed,
                )
            )

        assert record.work_status == WorkStatus.FEASIBLE
        assert record.result is not None
        assert record.result.schedule is seed
        assert record.result.metrics is not None
        assert record.result.metrics.get("fallback") == "incumbent"
        assert record.result.metrics.get("cpsat_status") == "UNKNOWN"

    def test_infeasible_with_flag_raises(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        mock_solver = self._make_solver_mock(3, "INFEASIBLE")
        with patch(
            "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
            return_value=mock_solver,
        ):
            with pytest.raises(RuntimeError, match="INFEASIBLE"):
                JobContribCpDispatcher().run(
                    AlgSpec(
                        instance=instance,
                        option=JobContribCpOption(
                            jd_count_target=1,
                            cp_tl_seconds=5.0,
                            solver_thread_cnt=1,
                            error_if_infeasible=True,
                        ),
                        ref_solution=seed,
                    )
                )

    def test_infeasible_without_flag_falls_back(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        mock_solver = self._make_solver_mock(3, "INFEASIBLE")
        with patch(
            "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
            return_value=mock_solver,
        ):
            record = JobContribCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobContribCpOption(
                        jd_count_target=1,
                        cp_tl_seconds=5.0,
                        solver_thread_cnt=1,
                        error_if_infeasible=False,
                    ),
                    ref_solution=seed,
                )
            )

        assert record.work_status == WorkStatus.FEASIBLE
        assert record.result is not None
        assert record.result.metrics is not None
        assert record.result.metrics.get("fallback") == "incumbent"
        assert record.result.metrics.get("cpsat_status") == "INFEASIBLE"

    def test_model_invalid_always_raises(self) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [("j0", 5, 8), ("j1", 8, 10), ("j2", 10, 11), ("j3", 11, 13)],
        )
        seed = _apply_postprocess(seed, instance)

        mock_solver = self._make_solver_mock(1, "MODEL_INVALID")
        with patch(
            "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
            return_value=mock_solver,
        ):
            with pytest.raises(RuntimeError, match="MODEL_INVALID"):
                JobContribCpDispatcher().run(
                    AlgSpec(
                        instance=instance,
                        option=JobContribCpOption(
                            jd_count_target=1,
                            cp_tl_seconds=5.0,
                            solver_thread_cnt=1,
                            error_if_infeasible=False,
                        ),
                        ref_solution=seed,
                    )
                )


class TestSearchLogOutput:
    def test_search_log_written_when_enabled(self, tmp_path: Path) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        record = JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                    log_search_progress=True,
                    solver_log_path_getter=(lambda suffix: str(tmp_path / suffix)),
                ),
                ref_solution=seed,
            )
        )

        assert record.work_status in (WorkStatus.FEASIBLE, WorkStatus.OPTIMAL)
        log_path = tmp_path / "_job_contrib_cp_search.log"
        assert log_path.is_file(), (
            f"Expected search log at {log_path}, but file not found"
        )
        content = log_path.read_text(encoding="utf-8")
        assert "Starting CP-SAT solver" in content, (
            f"search log should carry CP-SAT's own output, got: {content[:500]}"
        )
        assert content.endswith("\n"), "search log should end with a newline"

    def test_search_log_not_written_when_disabled(self, tmp_path: Path) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                    log_search_progress=False,
                    solver_log_path_getter=(lambda suffix: str(tmp_path / suffix)),
                ),
                ref_solution=seed,
            )
        )

        log_path = tmp_path / "_job_contrib_cp_search.log"
        assert not log_path.is_file(), (
            "search log should NOT be written when log_search_progress is False"
        )

    def test_search_log_not_written_when_no_getter(self, tmp_path: Path) -> None:
        instance = _make_small_instance()
        seed = _build_schedule(
            instance,
            [
                ("j0", 5, 8),
                ("j1", 8, 10),
                ("j2", 10, 11),
                ("j3", 11, 13),
            ],
        )
        seed = _apply_postprocess(seed, instance)

        JobContribCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobContribCpOption(
                    jd_count_target=1,
                    cp_tl_seconds=5.0,
                    solver_thread_cnt=1,
                    log_search_progress=True,
                ),
                ref_solution=seed,
            )
        )

        log_path = tmp_path / "_job_contrib_cp_search.log"
        assert not log_path.is_file(), (
            "search log should NOT be written when solver_log_path_getter is None"
        )
