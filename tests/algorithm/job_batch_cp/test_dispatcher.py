"""Tests for ``JobBatchCpDispatcher``.

Most cases monkeypatch ``JobContribCpDispatcher.run`` — the sweep's own logic
(batch splitting, coverage, acceptance, progress rebasing, deadline handling)
is what is under test, and running a real CP model per batch would swamp it.
``TestRealCpSweep`` keeps one end-to-end case so the wiring stays honest.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.job_batch_cp.dispatcher import JobBatchCpDispatcher
from ffc_ddw_sum_et.algorithm.job_batch_cp.option import JobBatchCpOption
from ffc_ddw_sum_et.algorithm.job_contrib_cp import JobContribCpDispatcher
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_instance(n: int, name: str = "jb_test") -> FFcDDWParameters:
    """``n``-job, 2-stage, single-machine instance with uniform p=2."""
    jobs = [f"j{i}" for i in range(n)]
    return FFcDDWParameters(
        name=name,
        job_id_list=jobs,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p", df=pd.DataFrame([[2, 2]] * n)
        ),
        job_2_due_window_map={j: (4 + 2 * k, 6 + 2 * k) for k, j in enumerate(jobs)},
        job_2_ewt_map={j: 1 for j in jobs},
        job_2_twt_map={j: 1 for j in jobs},
    )


def _make_seed(instance: FFcDDWParameters) -> FFcSchedule:
    """Lay every job out back-to-back on the single machine of each stage."""
    sch = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for k, job_id in enumerate(instance.job_id_list):
        sch.add_ops_times_2_mc("i0", "i0_0", job_id, 2 * k, 2 * k + 2)
        sch.add_ops_times_2_mc("i1", "i1_0", job_id, 2 * k + 2, 2 * k + 4)
    return sch


def _seed_obj(schedule: FFcSchedule, instance: FFcDDWParameters) -> float:
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    return float(sum_e + sum_t)


def _fake_record(
    spec: AlgSpec,
    *,
    obj_value: float,
    schedule: FFcSchedule | None = None,
    progress: tuple[ProgressLogEntry, ...] = (),
) -> AlgRecord:
    return AlgRecord(
        work_status=WorkStatus.FEASIBLE,
        instance_id=spec.instance.name,
        algorithm_id="job_contrib_cp",
        option=spec.option,
        result=AlgResult(
            schedule=schedule if schedule is not None else spec.ref_solution,
            obj_value=obj_value,
            obj_bound=None,
            metrics={
                "cpsat_status": "OPTIMAL",
                "setup_seconds": 0.001,
                "destroy_selection": "explicit",
            },
        ),
        progress_log=progress,
        termination_reason=TerminationReason.COMPLETED,
    )


def _install_noop_sub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sleep_sec: float = 0.0,
    progress: tuple[ProgressLogEntry, ...] = (),
) -> list[tuple[str, ...]]:
    """Replace the sub-dispatcher with one that never improves anything.

    Returns the list that accumulates each call's ``destroy_job_ids``.
    """
    calls: list[tuple[str, ...]] = []

    def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
        calls.append(tuple(spec.option.destroy_job_ids))
        if sleep_sec:
            time.sleep(sleep_sec)
        obj = _seed_obj(spec.ref_solution, spec.instance)
        return _fake_record(spec, obj_value=obj, progress=progress)

    monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)
    return calls


def _run(
    instance: FFcDDWParameters,
    seed: FFcSchedule,
    **option_kwargs,
) -> AlgRecord:
    option = JobBatchCpOption(job_sequence=tuple(instance.job_id_list), **option_kwargs)
    return JobBatchCpDispatcher().run(
        AlgSpec(instance=instance, option=option, ref_solution=seed)
    )


class TestPreconditions:
    def test_no_ref_solution_raises(self) -> None:
        instance = _make_instance(4)
        with pytest.raises(RuntimeError, match="requires an incumbent schedule"):
            JobBatchCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobBatchCpOption(job_sequence=tuple(instance.job_id_list)),
                )
            )

    def test_wrong_instance_type_raises(self) -> None:
        with pytest.raises(TypeError, match="FFcDDWParameters"):
            JobBatchCpDispatcher().run(
                AlgSpec(
                    instance=object(),
                    option=JobBatchCpOption(job_sequence=("j0",)),
                )
            )

    def test_wrong_option_type_raises(self) -> None:
        instance = _make_instance(4)
        with pytest.raises(TypeError, match="JobBatchCpOption"):
            JobBatchCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=object(),
                    ref_solution=_make_seed(instance),
                )
            )

    @pytest.mark.parametrize(
        "bad_sequence",
        [
            ("j0", "j1", "j2"),  # too short
            ("j0", "j1", "j2", "j3", "j4"),  # too long
            ("j0", "j1", "j2", "jX"),  # right length, wrong member
        ],
    )
    def test_non_permutation_raises(self, bad_sequence: tuple[str, ...]) -> None:
        instance = _make_instance(4)
        with pytest.raises(ValueError, match="must be a permutation"):
            JobBatchCpDispatcher().run(
                AlgSpec(
                    instance=instance,
                    option=JobBatchCpOption(job_sequence=bad_sequence),
                    ref_solution=_make_seed(instance),
                )
            )


class TestBatchSplitting:
    def test_batch_size_splits_with_short_tail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(10)
        calls = _install_noop_sub(monkeypatch)

        record = _run(instance, _make_seed(instance), batch_size=3)

        assert [len(c) for c in calls] == [3, 3, 3, 1]
        assert record.result is not None
        assert record.result.metrics is not None
        assert record.result.metrics["batch_count"] == 4
        assert record.result.metrics["batch_size"] == 3

    def test_num_batches_overrides_batch_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(10)
        calls = _install_noop_sub(monkeypatch)

        record = _run(instance, _make_seed(instance), batch_size=3, num_batches=2)

        assert [len(c) for c in calls] == [5, 5]
        assert record.result is not None
        assert record.result.metrics is not None
        assert record.result.metrics["batch_size"] == 5

    def test_batches_follow_the_job_sequence_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Batch membership comes from ``job_sequence``, not from job IDs."""
        instance = _make_instance(6)
        reversed_seq = tuple(reversed(instance.job_id_list))
        calls = _install_noop_sub(monkeypatch)

        JobBatchCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobBatchCpOption(job_sequence=reversed_seq, batch_size=2),
                ref_solution=_make_seed(instance),
            )
        )

        assert calls == [("j5", "j4"), ("j3", "j2"), ("j1", "j0")]

    def test_every_job_destroyed_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The coverage invariant that distinguishes this step from
        ``job_contrib_cp``: one pass touches each job once and only once."""
        instance = _make_instance(10)
        calls = _install_noop_sub(monkeypatch)

        _run(instance, _make_seed(instance), batch_size=4)

        flat = [job_id for batch in calls for job_id in batch]
        assert sorted(flat) == sorted(instance.job_id_list)
        assert len(flat) == len(set(flat))


class TestAcceptanceRule:
    def test_worse_batch_result_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(4)
        seed = _make_seed(instance)
        seed_obj = _seed_obj(seed, instance)

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            # A *distinct* schedule object, so an accidental swap is visible.
            return _fake_record(
                spec,
                obj_value=seed_obj + 100.0,
                schedule=spec.ref_solution.deepcopy(),
            )

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)
        record = _run(instance, seed, batch_size=2)

        assert record.result is not None
        assert record.result.schedule is seed, "a worse batch must not replace current"
        assert record.result.obj_value == seed_obj
        step_log = record.result.metrics["step_log"]
        assert [e.accepted for e in step_log] == [False, False]
        assert all(e.obj_before == seed_obj for e in step_log)
        assert all(e.obj_after == seed_obj + 100.0 for e in step_log)

    def test_equal_batch_result_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Strict improvement only — a tie must not shake the profile-fix
        reference plane for the next batch."""
        instance = _make_instance(4)
        seed = _make_seed(instance)
        seed_obj = _seed_obj(seed, instance)

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            return _fake_record(
                spec, obj_value=seed_obj, schedule=spec.ref_solution.deepcopy()
            )

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)
        record = _run(instance, seed, batch_size=2)

        assert record.result is not None
        assert record.result.schedule is seed
        assert all(not e.accepted for e in record.result.metrics["step_log"])

    def test_better_batch_result_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(4)
        seed = _make_seed(instance)
        seed_obj = _seed_obj(seed, instance)
        improved = seed.deepcopy()

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            return _fake_record(spec, obj_value=seed_obj - 10.0, schedule=improved)

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)
        record = _run(instance, seed, batch_size=4)

        assert record.result is not None
        assert record.result.schedule is improved
        step_log = record.result.metrics["step_log"]
        assert [e.accepted for e in step_log] == [True]

    def test_current_incumbent_is_fed_to_the_next_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Batch k+1 must start from batch k's accepted schedule."""
        instance = _make_instance(4)
        seed = _make_seed(instance)
        seed_obj = _seed_obj(seed, instance)
        improved = seed.deepcopy()
        refs: list[FFcSchedule] = []

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            refs.append(spec.ref_solution)
            return _fake_record(
                spec, obj_value=seed_obj - 10.0 * len(refs), schedule=improved
            )

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)
        _run(instance, seed, batch_size=2)

        assert refs[0] is seed
        assert refs[1] is improved


class TestProgressLog:
    def test_entries_are_monotonic_in_the_loop_frame(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sub-record timestamps are rebased onto the batch *start*.

        Rebasing onto the batch end instead pushes a batch's points past the
        run's own closing entry, which is what this asserts against.
        """
        instance = _make_instance(6)
        _install_noop_sub(
            monkeypatch,
            sleep_sec=0.02,
            progress=(
                ProgressLogEntry(elapsed_sec=0.0, obj_value=1.0),
                ProgressLogEntry(elapsed_sec=0.01, obj_value=1.0),
            ),
        )

        record = _run(instance, _make_seed(instance), batch_size=2)

        times = [e.elapsed_sec for e in record.progress_log]
        assert times == sorted(times), f"progress_log is not monotonic: {times}"
        assert times[-1] == max(times)
        assert len(times) == 3 * 2 + 1, "two points per batch plus the closing entry"

    def test_closing_entry_carries_the_final_objective(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(4)
        seed = _make_seed(instance)
        _install_noop_sub(monkeypatch)

        record = _run(instance, seed, batch_size=2)

        assert record.result is not None
        assert record.progress_log[-1].obj_value == record.result.obj_value
        assert record.progress_log[-1].obj_bound is None


class TestEarlyTermination:
    def test_expired_deadline_skips_every_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(6)
        seed = _make_seed(instance)
        calls = _install_noop_sub(monkeypatch)

        record = _run(
            instance,
            seed,
            batch_size=2,
            wall_clock_deadline_sec=time.monotonic() - 1.0,
        )

        assert calls == []
        assert record.termination_reason == TerminationReason.STOP_REQUESTED
        assert record.result is not None
        assert record.result.schedule is seed
        assert record.result.metrics["completed_batches"] == 0

    def test_deadline_reached_mid_sweep_keeps_the_work_so_far(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(10)
        seed = _make_seed(instance)
        calls = _install_noop_sub(monkeypatch, sleep_sec=0.03)

        record = _run(
            instance,
            seed,
            batch_size=1,
            wall_clock_deadline_sec=time.monotonic() + 0.05,
        )

        assert 0 < len(calls) < 10, f"expected a partial sweep, ran {len(calls)}"
        assert record.termination_reason == TerminationReason.STOP_REQUESTED
        assert record.result is not None
        assert record.result.schedule is not None
        assert record.result.metrics["stopped_early"] is True
        assert record.result.metrics["completed_batches"] == len(calls)

    def test_stop_predicate_halts_the_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(6)
        calls = _install_noop_sub(monkeypatch)

        record = JobBatchCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobBatchCpOption(
                    job_sequence=tuple(instance.job_id_list), batch_size=2
                ),
                ref_solution=_make_seed(instance),
                stop_predicate=lambda: True,
            )
        )

        assert calls == []
        assert record.termination_reason == TerminationReason.STOP_REQUESTED

    def test_stop_predicate_is_forwarded_to_the_sub_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        instance = _make_instance(4)
        seen: list[object] = []

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            seen.append(spec.stop_predicate)
            return _fake_record(
                spec, obj_value=_seed_obj(spec.ref_solution, spec.instance)
            )

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)

        def never_stop() -> bool:
            return False

        JobBatchCpDispatcher().run(
            AlgSpec(
                instance=instance,
                option=JobBatchCpOption(
                    job_sequence=tuple(instance.job_id_list), batch_size=4
                ),
                ref_solution=_make_seed(instance),
                stop_predicate=never_stop,
            )
        )

        assert seen == [never_stop]

    def test_sub_dispatcher_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MODEL_INVALID / ``error_if_infeasible`` are bug signals; swallowing
        them would hide a broken model behind an unimproved incumbent."""
        instance = _make_instance(4)

        def fake_run(self_disp, spec: AlgSpec) -> AlgRecord:
            raise RuntimeError("CP-SAT returned status=MODEL_INVALID")

        monkeypatch.setattr(JobContribCpDispatcher, "run", fake_run)

        with pytest.raises(RuntimeError, match="MODEL_INVALID"):
            _run(instance, _make_seed(instance), batch_size=2)


class TestStepLog:
    def test_entries_describe_each_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        instance = _make_instance(6)
        _install_noop_sub(monkeypatch)

        record = _run(instance, _make_seed(instance), batch_size=2, cp_tl_seconds=1.5)

        step_log = record.result.metrics["step_log"]
        assert [e.step for e in step_log] == [0, 1, 2]
        assert [e.batch_head for e in step_log] == ["j0", "j2", "j4"]
        assert all(e.batch_size == 2 for e in step_log)
        assert all(e.TL == 1.5 for e in step_log)
        assert all(e.cpsat_status == "OPTIMAL" for e in step_log)
        assert all(e.setup_seconds == 0.001 for e in step_log)
        elapsed = [e.elapsed_time for e in step_log]
        assert elapsed == sorted(elapsed)

    def test_as_dict_is_yaml_friendly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        instance = _make_instance(4)
        _install_noop_sub(monkeypatch)

        record = _run(instance, _make_seed(instance), batch_size=2)

        for entry in record.result.metrics["step_log"]:
            data = entry.as_dict()
            assert isinstance(data["makespan"], int)
            assert isinstance(data["accepted"], bool)
            assert set(data) == {
                "step",
                "batch_size",
                "batch_head",
                "elapsed_time",
                "TL",
                "elapsed_portion",
                "obj_before",
                "obj_after",
                "accepted",
                "cpsat_status",
                "setup_seconds",
                "makespan",
            }


class TestRealCpSweep:
    def test_sweep_never_worsens_the_incumbent(self) -> None:
        instance = _make_instance(3, name="jb_real")
        seed = _make_seed(instance)
        seed_obj = _seed_obj(seed, instance)

        record = _run(
            instance,
            seed,
            batch_size=1,
            cp_tl_seconds=2.0,
            solver_thread_cnt=1,
        )

        assert record.termination_reason == TerminationReason.COMPLETED
        assert record.result is not None
        assert record.result.schedule is not None
        assert record.result.obj_value <= seed_obj
        assert record.result.metrics["batch_count"] == 3
        assert record.result.metrics["completed_batches"] == 3
        assert _seed_obj(record.result.schedule, instance) == record.result.obj_value
