"""Integration tests for SC log lifecycle in `FFcDDWSingleInstanceRunner.run`.

Verifies that the SIR / SC log handler dance described in doc § 7.2 holds
end-to-end:
- SC logger has a managed file handler attached during controller.run()
- SC logger has the handler removed (and file fd closed) after run
- The detach also runs when controller raises
- A `PrefixLevelFilter` is attached to the SIR file handler so SC INFO/DEBUG
  is dropped at the SIR layer but WARNING+ propagates
- The default routix `_routix_managed` tag is what guards attach/detach
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from routix.logging import PrefixLevelFilter, _MANAGED_TAG
from routix.type_defs import RunMode

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
    _SC_LOGGER_PREFIX,
)


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """Snapshot/restore root handlers so test pollution doesn't leak across."""
    root = logging.getLogger()
    snapshot = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in snapshot:
            try:
                h.close()
            finally:
                root.removeHandler(h)
    # Drop any PrefixLevelFilter we left on the snapshot handlers
    for h in snapshot:
        for f in list(h.filters):
            if isinstance(f, PrefixLevelFilter):
                h.removeFilter(f)


def _bare_sir(
    tmp_path: Path,
    ins_name: str,
    *,
    controller_run: Any,
    raises: bool = False,
) -> tuple[FFcDDWSingleInstanceRunner, FFcArtifactLayout]:
    layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
    runner: Any = FFcDDWSingleInstanceRunner.__new__(FFcDDWSingleInstanceRunner)
    runner.layout = layout
    runner._scenario_name = "sc"
    runner.ins_name = ins_name
    runner.instance = SimpleNamespace(name=ins_name)
    runner.mode = RunMode.FULL_RUN
    runner._setup_logging_args = (None, False, 0)
    runner.subroutine_flow = {}
    runner.stopping_criteria = {"timelimit": 1.0}
    runner.shared_param_dict = {}
    runner.output_metadata = {}
    runner.logger = logging.getLogger(f"test.sir.{ins_name}")
    runner._init_working_dir()

    fake_ctrlr = SimpleNamespace(
        set_artifact_layout=lambda *a, **kw: None,
        set_working_dir=lambda *a, **kw: None,
        run=controller_run,
    )

    def _get_controller(self=runner):
        return fake_ctrlr

    runner.get_controller = _get_controller  # type: ignore[assignment]
    runner.post_run_process = lambda: None  # type: ignore[assignment]
    runner._run_error = None
    return runner, layout


def _sc_logger_managed_handlers(ins_name: str) -> list[logging.Handler]:
    log = logging.getLogger(f"{_SC_LOGGER_PREFIX}.{ins_name}")
    return [h for h in log.handlers if getattr(h, _MANAGED_TAG, False)]


def test_sc_handler_attached_then_detached_on_normal_run(tmp_path: Path) -> None:
    seen: dict[str, list[logging.Handler]] = {"during": []}

    def _ctrlr_run() -> None:
        seen["during"] = list(_sc_logger_managed_handlers("inst_ok"))

    runner, _ = _bare_sir(tmp_path, "inst_ok", controller_run=_ctrlr_run)
    runner.run()

    assert len(seen["during"]) == 1
    fh = seen["during"][0]
    assert isinstance(fh, logging.FileHandler)
    # After run: detach removed the managed handler
    assert _sc_logger_managed_handlers("inst_ok") == []
    assert fh.stream is None or fh.stream.closed


def test_sc_handler_detached_when_controller_raises(tmp_path: Path) -> None:
    def _ctrlr_run() -> None:
        raise RuntimeError("ctrlr boom")

    runner, _ = _bare_sir(tmp_path, "inst_err", controller_run=_ctrlr_run)
    runner.run()

    assert _sc_logger_managed_handlers("inst_err") == []


def test_sc_log_file_written_to_layout_path(tmp_path: Path) -> None:
    sc_log_path: list[Path] = []

    def _ctrlr_run() -> None:
        # Emit a record to confirm it lands in the SC log.
        logging.getLogger(f"{_SC_LOGGER_PREFIX}.inst_log").info("during-run")
        managed = _sc_logger_managed_handlers("inst_log")
        if managed:
            sc_log_path.append(Path(managed[0].baseFilename))

    runner, layout = _bare_sir(tmp_path, "inst_log", controller_run=_ctrlr_run)
    runner.run()

    expected = layout.log_path(
        "subroutine_controller", scenario_name="sc", instance_name="inst_log"
    )
    assert sc_log_path == [expected]
    assert expected.exists()
    assert "during-run" in expected.read_text(encoding="utf-8")


def test_prefix_level_filter_attached_to_sir_file_handler(tmp_path: Path) -> None:
    def _ctrlr_run() -> None:
        return None

    runner, _ = _bare_sir(tmp_path, "inst_filt", controller_run=_ctrlr_run)
    runner.run()

    # After run: SIR setup_logging restores prior args (which we passed as
    # (None, False, 0), so no file handler remains), but during the run a
    # file handler with our prefix filter was active. We can verify the
    # attach helper installed a filter on each managed file handler by
    # re-running and capturing during run.
    captured: list[bool] = []

    def _ctrlr_run2() -> None:
        root = logging.getLogger()
        for h in root.handlers:
            if not isinstance(h, logging.FileHandler):
                continue
            captured.append(
                any(
                    isinstance(f, PrefixLevelFilter)
                    and getattr(f, "_prefix", None) == _SC_LOGGER_PREFIX
                    for f in h.filters
                )
            )

    runner2, _ = _bare_sir(tmp_path, "inst_filt2", controller_run=_ctrlr_run2)
    runner2.run()
    assert captured and all(captured), captured
