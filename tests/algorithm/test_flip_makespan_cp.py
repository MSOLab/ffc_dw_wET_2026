from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.flip_makespan_cp import (
    FlipMakespanCpDispatcher,
    FlipMakespanCpOption,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance() -> FFcDDWParameters:
    return FFcDDWParameters(
        name="flip_makespan_cp_instance",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="p", df=pd.DataFrame([[2, 3], [2, 2], [1, 1]])
        ),
        job_2_due_window_map={"j0": (5, 8), "j1": (4, 7), "j2": (3, 6)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _seed_incumbent(instance: FFcDDWParameters):
    fam_record = FAMDispatcher().run(
        AlgSpec(instance=instance, option=FAMOption(job_sequence=("j2", "j1", "j0")))
    )
    assert fam_record.result is not None
    assert fam_record.result.schedule is not None
    return fam_record.result.schedule, float(fam_record.result.obj_value)


def test_flip_makespan_cp_returns_feasible_schedule() -> None:
    instance = _make_instance()
    incumbent_schedule, incumbent_obj = _seed_incumbent(instance)

    record = FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=instance,
            option=FlipMakespanCpOption(cp_tl_seconds=5.0, solver_thread_cnt=1),
            ref_solution=incumbent_schedule,
        )
    )

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.obj_value is not None
    assert record.result.metrics is not None
    assert record.result.metrics["delayed_makespan"] >= incumbent_schedule.makespan
    # Objective should not regress from incumbent (solution_manager would
    # otherwise discard it). Allow equality for the trivial case where the
    # warm-start matches the incumbent.
    assert record.result.obj_value <= incumbent_obj


def test_flip_makespan_cp_requires_ref_solution() -> None:
    instance = _make_instance()
    with pytest.raises(RuntimeError):
        FlipMakespanCpDispatcher().run(
            AlgSpec(instance=instance, option=FlipMakespanCpOption())
        )


def test_flip_makespan_cp_emits_phase_schedules(tmp_path: Path) -> None:
    instance = _make_instance()
    incumbent_schedule, _ = _seed_incumbent(instance)

    captured: list[str] = []

    def getter(phase_name: str) -> Path:
        captured.append(phase_name)
        return tmp_path / f"phase_{phase_name}.json"

    record = FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=instance,
            option=FlipMakespanCpOption(
                cp_tl_seconds=5.0,
                solver_thread_cnt=1,
                emit_phase_schedules=True,
                phase_schedule_path_getter=getter,
            ),
            ref_solution=incumbent_schedule,
        )
    )

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert captured == [
        "01_incumbent",
        "02_right_shifted",
        "03_flipped",
        "04_flipped_compacted",
        "05_cp_solved",
        "06_unflipped_semi_active",
        "07_unflipped_final",
    ]
    # ET is computed only for original-instance phases; reversed-instance
    # phases (03/04/05) carry obj_value=None.
    expects_obj = {
        "01_incumbent": True,
        "02_right_shifted": True,
        "03_flipped": False,
        "04_flipped_compacted": False,
        "05_cp_solved": False,
        "06_unflipped_semi_active": True,
        "07_unflipped_final": True,
    }
    for phase_name in captured:
        path = tmp_path / f"phase_{phase_name}.json"
        assert path.exists(), phase_name
        # Compact JSON: single line, no indentation whitespace after colons.
        text = path.read_text()
        assert "\n" not in text.rstrip("\n"), phase_name
        payload = json.loads(text)
        # instance_name is the bare instance (no phase suffix); the
        # phase identity is encoded in the filename.
        assert payload["instanceName"] == instance.name, phase_name
        assert payload["operations"], phase_name
        if expects_obj[phase_name]:
            assert payload["objValue"] is not None, phase_name
        else:
            assert payload["objValue"] is None, phase_name


def test_flip_makespan_cp_phase_emission_off_by_default(tmp_path: Path) -> None:
    instance = _make_instance()
    incumbent_schedule, _ = _seed_incumbent(instance)

    called = False

    def getter(_phase_name: str) -> Path:  # pragma: no cover - must not be invoked
        nonlocal called
        called = True
        return tmp_path / "should_not_be_written"

    FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=instance,
            option=FlipMakespanCpOption(
                cp_tl_seconds=5.0,
                solver_thread_cnt=1,
                phase_schedule_path_getter=getter,
            ),
            ref_solution=incumbent_schedule,
        )
    )
    assert called is False


def test_flip_makespan_cp_rejects_non_ddw_instance() -> None:
    from ffc_ddw_sum_et.parameters.ffc_params import FFcParameters

    plain = FFcParameters(
        name="plain",
        job_id_list=["j0"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(name="p", df=pd.DataFrame([[1]])),
    )
    with pytest.raises(TypeError):
        FlipMakespanCpDispatcher().run(AlgSpec(instance=plain))
