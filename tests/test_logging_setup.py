"""Unit tests for scoped ``setup_logging`` handler management."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ffc_ddw_sum_et.logging_setup import _MANAGED_TAG, get_logging_args, setup_logging


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """Snapshot/restore root logger handlers around each test."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in saved_handlers:
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
    root.setLevel(saved_level)


def _managed_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, _MANAGED_TAG, False)]


def _managed_file_handlers() -> list[logging.FileHandler]:
    return [h for h in _managed_handlers() if isinstance(h, logging.FileHandler)]


def _managed_stream_handlers() -> list[logging.Handler]:
    return [
        h
        for h in _managed_handlers()
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]


def test_terminal_only_creates_no_log_files(tmp_path: Path) -> None:
    setup_logging(None)

    assert len(_managed_file_handlers()) == 0
    assert len(_managed_stream_handlers()) == 1
    assert list(tmp_path.iterdir()) == []


def test_writes_to_single_scoped_log_file(tmp_path: Path) -> None:
    log_path = tmp_path / "smoke_io" / "smoke_io_benchmark.log"
    setup_logging(log_path)

    logging.getLogger("ffc_ddw_sum_et.main").info("benchmark msg")
    for h in _managed_file_handlers():
        h.flush()

    assert log_path.exists()
    assert "benchmark msg" in log_path.read_text(encoding="utf-8")
    assert len(list(tmp_path.rglob("*.log"))) == 1


def test_reconfigure_moves_file_target(tmp_path: Path) -> None:
    first = tmp_path / "smoke_io" / "smoke_io_benchmark.log"
    second = tmp_path / "smoke_io" / "InstanceA" / "InstanceA_solve.log"

    setup_logging(first)
    logging.getLogger("ffc_ddw_sum_et.main").info("benchmark msg")
    setup_logging(second)
    logging.getLogger("ffc_ddw_sum_et.algorithm.dispatcher").info("solve msg")
    for h in _managed_file_handlers():
        h.flush()

    assert "benchmark msg" in first.read_text(encoding="utf-8")
    assert "solve msg" not in first.read_text(encoding="utf-8")
    assert "solve msg" in second.read_text(encoding="utf-8")


def test_idempotent_does_not_duplicate_handlers(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    setup_logging(log_path)
    first_count = len(_managed_handlers())
    setup_logging(log_path)
    second_count = len(_managed_handlers())

    assert first_count == second_count == 2


def test_get_logging_args_reflects_current_scope(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    setup_logging(log_path, quiet=True, verbose=2)

    assert get_logging_args() == (log_path, True, 2)


def test_quiet_terminal_level_is_error(tmp_path: Path) -> None:
    setup_logging(tmp_path / "ts3.log", quiet=True)
    streams = _managed_stream_handlers()

    assert len(streams) == 1
    assert streams[0].level == logging.ERROR


def test_verbose_levels(tmp_path: Path) -> None:
    setup_logging(tmp_path / "ts4.log", verbose=2)
    streams = _managed_stream_handlers()

    assert streams[0].level == logging.DEBUG


def test_default_terminal_level_is_warning(tmp_path: Path) -> None:
    setup_logging(tmp_path / "ts5.log")
    streams = _managed_stream_handlers()

    assert streams[0].level == logging.WARNING


def test_main_log_default_terminal_level_is_info(tmp_path: Path) -> None:
    setup_logging(tmp_path / "main.log", is_main=True)
    streams = _managed_stream_handlers()

    assert streams[0].level == logging.INFO


def test_main_log_verbose_terminal_level_is_debug(tmp_path: Path) -> None:
    setup_logging(tmp_path / "main.log", verbose=2, is_main=True)
    streams = _managed_stream_handlers()

    assert streams[0].level == logging.DEBUG


def test_main_log_quiet_terminal_level_is_warning(tmp_path: Path) -> None:
    setup_logging(tmp_path / "main.log", quiet=True, is_main=True)
    streams = _managed_stream_handlers()

    assert streams[0].level == logging.WARNING
