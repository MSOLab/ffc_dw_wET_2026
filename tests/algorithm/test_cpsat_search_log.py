"""Shared CP-SAT search-log writer used by every solver-backed algorithm."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ffc_ddw_sum_et.algorithm.cpsat_search_log import write_cpsat_search_log


class TestWriteCpsatSearchLog:
    def test_writes_log_to_getter_path(self, tmp_path: Path) -> None:
        write_cpsat_search_log(
            "line one\nline two\n",
            lambda suffix: tmp_path / suffix,
            "_probe.log",
            logger=logging.getLogger(__name__),
        )

        assert (tmp_path / "_probe.log").read_text(encoding="utf-8") == (
            "line one\nline two\n"
        )

    def test_appends_missing_trailing_newline(self, tmp_path: Path) -> None:
        write_cpsat_search_log(
            "no trailing newline",
            lambda suffix: tmp_path / suffix,
            "_probe.log",
            logger=logging.getLogger(__name__),
        )

        assert (tmp_path / "_probe.log").read_text(encoding="utf-8") == (
            "no trailing newline\n"
        )

    def test_accepts_str_path_getter(self, tmp_path: Path) -> None:
        write_cpsat_search_log(
            "text\n",
            lambda suffix: str(tmp_path / suffix),
            "_probe.log",
            logger=logging.getLogger(__name__),
        )

        assert (tmp_path / "_probe.log").is_file()

    def test_no_op_without_path_getter(self, tmp_path: Path) -> None:
        write_cpsat_search_log(
            "text\n", None, "_probe.log", logger=logging.getLogger(__name__)
        )

        assert list(tmp_path.iterdir()) == []

    def test_no_op_on_empty_log(self, tmp_path: Path) -> None:
        write_cpsat_search_log(
            "",
            lambda suffix: tmp_path / suffix,
            "_probe.log",
            logger=logging.getLogger(__name__),
        )

        assert list(tmp_path.iterdir()) == []

    def test_swallows_write_failure_and_logs_it(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        def exploding_getter(suffix: str) -> Path:
            raise OSError("no such directory")

        caplog.set_level(logging.ERROR)
        write_cpsat_search_log(
            "text\n",
            exploding_getter,
            "_probe.log",
            logger=logging.getLogger(__name__),
        )

        assert "Failed to write CP-SAT search log" in "\n".join(caplog.messages)
