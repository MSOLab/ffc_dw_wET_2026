"""Persist a CP-SAT search log captured via ``log_to_response``.

Every solver-backed algorithm hands the same three things to this writer: the
log text CP-SAT collected in ``solver.response_proto.solve_log``, the caller's
path getter (``None`` when the caller has no output directory), and the file
name suffix that identifies the solve. Writing the log is diagnostics, never
part of the result, so an IO failure is logged and swallowed rather than
allowed to fail the run.
"""

from __future__ import annotations

import logging
from os import PathLike
from typing import Callable

__all__ = ["write_cpsat_search_log"]


def write_cpsat_search_log(
    solve_log: str,
    path_getter: Callable[[str], PathLike[str] | str] | None,
    filename_suffix: str,
    *,
    logger: logging.Logger,
) -> None:
    """Write ``solve_log`` to ``path_getter(filename_suffix)``.

    No-op when the log is empty or no ``path_getter`` was supplied. A trailing
    newline is appended when the log lacks one.
    """
    if not solve_log or path_getter is None:
        return
    try:
        log_path = path_getter(filename_suffix)
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(solve_log)
            if not solve_log.endswith("\n"):
                fp.write("\n")
    except Exception:
        logger.exception("Failed to write CP-SAT search log")
