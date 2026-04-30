"""Central logging configuration for ffc_ddw_sum_et.

The output tree is scoped by experiment structure rather than by package
domain: scenario-level benchmark logs live under the scenario directory, and
per-instance solve logs live under each instance directory.

``setup_logging`` is idempotent and safe to call from ``ProcessPoolExecutor``
workers. Each call replaces previously managed handlers in the current process.
"""

from __future__ import annotations

import logging
from pathlib import Path

_MANAGED_TAG = "_ffcddw_managed"
_MANAGED_LOG_PATH = "_ffcddw_log_path"

_TERMINAL_FMT = "[%(levelname).1s %(asctime)s] %(message)s"
_FILE_FMT = (
    "%(asctime)s [%(levelname)s] %(name)s "
    "(%(filename)s:%(lineno)d) %(processName)s/%(threadName)s: %(message)s"
)
_current_log_path: Path | None = None
_current_quiet = False
_current_verbose = 0


def _terminal_level(quiet: bool, verbose: int) -> int:
    if quiet:
        return logging.ERROR
    if verbose >= 2:
        return logging.DEBUG
    if verbose == 1:
        return logging.INFO
    return logging.WARNING


def _main_log_terminal_level(quiet: bool, verbose: int) -> int:
    if quiet:
        return logging.WARNING
    if verbose >= 2:
        return logging.DEBUG
    return logging.INFO


def setup_logging(
    log_path: Path | str | None = None,
    quiet: bool = False,
    verbose: int = 0,
    *,
    is_main: bool = False,
) -> None:
    """Attach one optional file handler + one terminal handler to root.

    Idempotent: removes previously-managed handlers before re-attaching, so
    re-invocation in the same process (e.g. tests) or in a fresh
    ``ProcessPoolExecutor`` worker is safe.
    """
    global _current_log_path, _current_quiet, _current_verbose

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MANAGED_TAG, False):
            try:
                h.close()
            finally:
                root.removeHandler(h)

    root.setLevel(logging.DEBUG)

    file_fmt = logging.Formatter(_FILE_FMT)

    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, mode="a", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(file_fmt)
        setattr(fh, _MANAGED_TAG, True)
        setattr(fh, _MANAGED_LOG_PATH, str(path))
        root.addHandler(fh)

    sh = logging.StreamHandler()
    level_fn = _main_log_terminal_level if is_main else _terminal_level
    sh.setLevel(level_fn(quiet, verbose))
    sh.setFormatter(logging.Formatter(_TERMINAL_FMT, datefmt="%H:%M:%S"))
    setattr(sh, _MANAGED_TAG, True)
    root.addHandler(sh)

    _current_log_path = Path(log_path) if log_path is not None else None
    _current_quiet = quiet
    _current_verbose = verbose


def get_logging_args() -> tuple[Path | None, bool, int]:
    """Return the current process-local logging configuration."""
    return _current_log_path, _current_quiet, _current_verbose
