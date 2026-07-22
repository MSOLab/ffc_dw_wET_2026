"""Tests for the RESUME config dry-run validator.

This script's exit code gates whether a multi-hour experiment resumes from the
intended base incumbent, so the two failure modes it exists to catch are pinned
here: a scenario flow fully covered by the base flow (which would run *no*
steps), and a base run missing per-instance incumbent artifacts.

Loading the module by path also executes its ``import main as entrypoint``, so
this file transitively pins the four ``main`` helpers the script depends on --
renaming any of them turns a silent runtime break into a red test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not an importable package; load the module by path.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_resume_config.py"
_spec = importlib.util.spec_from_file_location("validate_resume_config", _SCRIPT)
assert _spec and _spec.loader
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


# The first two steps of a real base flow, verbatim -- the validator fills
# controller defaults from these, so synthetic method names would not survive.
_BASE_FLOW_YAML = """\
- method: calc_mcf_lb_and_derive_full_sch
  draw_pmtn_sch_heatmap: false
  job_placement_priority: end_time
  last_stage_only_placement_criteria: dist
  makespan_delta_ref: lastStageOnlyMakespan
  adjust_p: true
  adjust_r: true
  r_adjust_coeff: 0.5
  proceed_r2_when_nonpositive_cmax: true
- method: run_flip_makespan_cp_from_incumbent
  cp_tl: 0.009nc
  solver_thread_cnt: 8
  log_search_progress: false
  emit_phase_schedules: false
"""

_STEP_0 = {
    "method": "calc_mcf_lb_and_derive_full_sch",
    "draw_pmtn_sch_heatmap": False,
    "job_placement_priority": "end_time",
    "last_stage_only_placement_criteria": "dist",
    "makespan_delta_ref": "lastStageOnlyMakespan",
    "adjust_p": True,
    "adjust_r": True,
    "r_adjust_coeff": 0.5,
    "proceed_r2_when_nonpositive_cmax": True,
}
_STEP_1 = {
    "method": "run_flip_makespan_cp_from_incumbent",
    "cp_tl": "0.009nc",
    "solver_thread_cnt": 8,
    "log_search_progress": False,
    "emit_phase_schedules": False,
}
_STEP_2 = {"method": "solve_base_model_cpsat", "solver_thread_cnt": 8}


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def _make_base_run(tmp_path: Path) -> Path:
    """A base scenario dir holding the 2-step flow cache."""
    resume_dir = tmp_path / "run" / "base_scenario"
    resume_dir.mkdir(parents=True)
    (resume_dir / V.SUBROUTINE_FLOW_CACHE_FN).write_text(_BASE_FLOW_YAML)
    return resume_dir


def _run_main(monkeypatch: pytest.MonkeyPatch, config: Path, *flags: str) -> int:
    monkeypatch.setattr(sys, "argv", ["validate_resume_config", str(config), *flags])
    return V.main()


def _yaml_flow(steps: list[dict]) -> str:
    """Render steps as a nested YAML block under `subroutine_flow:`."""
    lines = []
    for step in steps:
        first = True
        for key, val in step.items():
            bullet = "      - " if first else "        "
            lines.append(f"{bullet}{key}: {val}")
            first = False
    return "\n".join(lines)


def _resume_config(resume_dir: Path, steps: list[dict]) -> str:
    return (
        "run_mode: RESUME\n"
        "benchmark_dir: benchmarks/PRA2017/large\n"
        f"resume_dir: {resume_dir}\n"
        "scenarios:\n"
        "  - name: case_a\n"
        "    output_subdir: case_a\n"
        "    subroutine_flow:\n" + _yaml_flow(steps) + "\n"
    )


def test_non_resume_config_returns_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Given a FULL_RUN config, when validated, then it exits 0 without resuming."""
    config = _write_config(tmp_path / "c.yaml", "run_mode: FULL_RUN\nscenarios: []\n")

    assert _run_main(monkeypatch, config) == 0

    out = capsys.readouterr().out
    assert "nothing to validate" in out
    assert "resume_dir" not in out


def test_no_steps_guard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Given a scenario flow fully covered by the base flow, then it fails loudly.

    This is the `resume_dir pointed at a case run` mistake: every step would be
    skipped and the run would do nothing.
    """
    resume_dir = _make_base_run(tmp_path)
    config = _write_config(
        tmp_path / "c.yaml", _resume_config(resume_dir, [_STEP_0, _STEP_1])
    )

    assert _run_main(monkeypatch, config) == 1

    out = capsys.readouterr().out
    assert "would run no steps" in out
    assert "resume_dir" in out


def test_partial_prefix_resumes_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Given a scenario flow extending the base flow, then only the tail re-runs."""
    resume_dir = _make_base_run(tmp_path)
    config = _write_config(
        tmp_path / "c.yaml", _resume_config(resume_dir, [_STEP_0, _STEP_1, _STEP_2])
    )

    assert _run_main(monkeypatch, config) == 0

    out = capsys.readouterr().out
    assert "flow_resume_idx=2 of 3 steps" in out
    assert "RERUN  solve_base_model_cpsat" in out
    assert "resume cleanly" in out


def test_prefix_mismatch_prints_only_differing_keys(
    capsys: pytest.CaptureFixture,
) -> None:
    """Given a wide mismatch payload, then only the keys that differ are printed."""
    exc = ValueError(
        {
            "index": 1,
            "resume_element": {"method": "neh_cp", "added_batch_size": 15, "pf": "PF1"},
            "current_element": {
                "method": "neh_cp",
                "added_batch_size": 20,
                "pf": "PF1",
            },
        }
    )

    V._print_prefix_mismatch(exc)

    out = capsys.readouterr().out
    assert "added_batch_size: base=15 scenario=20" in out
    assert "pf:" not in out  # matching key stays unprinted
    assert "method:" not in out  # ditto, even though it is in both payloads
    assert "steps [0..0] match" in out


def test_prefix_mismatch_falls_back_on_unstructured_payload(
    capsys: pytest.CaptureFixture,
) -> None:
    """Given a plain-string ValueError, then it is printed rather than crashing."""
    V._print_prefix_mismatch(ValueError("flow lengths differ"))

    assert "flow lengths differ" in capsys.readouterr().out


def test_check_artifacts_reports_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an instance lacking a base incumbent, then its name is returned."""

    class _FakeInstance:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeLoader:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load_all(self, ins_index=None):
            return [_FakeInstance("ins_ok"), _FakeInstance("ins_missing")]

    monkeypatch.setattr(V, "BenchmarkLoader", _FakeLoader)

    resume_dir = tmp_path / "base"
    ok_dir = resume_dir / "ins_ok"
    ok_dir.mkdir(parents=True)
    (ok_dir / "ins_ok_solution.json").write_text("{}")
    (ok_dir / "ins_ok_instance_result.yaml").write_text("obj_value: 1\n")
    # ins_missing has no directory at all.

    missing = V._check_artifacts({"benchmark_dir": "unused"}, resume_dir)

    assert missing == ["ins_missing"]


def test_check_artifacts_reports_partial_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a solution.json with no instance_result.yaml, then it counts as missing."""

    class _FakeInstance:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeLoader:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load_all(self, ins_index=None):
            return [_FakeInstance("ins_half")]

    monkeypatch.setattr(V, "BenchmarkLoader", _FakeLoader)

    half_dir = tmp_path / "base" / "ins_half"
    half_dir.mkdir(parents=True)
    (half_dir / "ins_half_solution.json").write_text("{}")

    assert V._check_artifacts({"benchmark_dir": "unused"}, tmp_path / "base") == [
        "ins_half"
    ]
