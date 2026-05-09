"""Option payload for ``FlipMakespanCpDispatcher``."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from typing import Callable

from ..base.alg_option import AlgOption

__all__ = ["FlipMakespanCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class FlipMakespanCpOption(AlgOption):
    """Inputs for one flip-makespan CP-SAT run.

    ``cp_tl_seconds`` is the single, fully pre-resolved CP-SAT cap; the
    controller is responsible for taking the strict-min of any per-call
    cap and remaining global time before constructing this option.
    ``None`` means "no time cap from the caller".

    ``log_search_progress`` mirrors the CP-SAT solver flag of the same
    name. When true the dispatcher writes the response solve_log
    (which prints hint coverage) via ``solver_log_path_getter``.

    ``emit_phase_schedules`` toggles per-phase compact-JSON dumps of
    intermediate schedules. When true the dispatcher resolves each
    phase's destination via ``phase_schedule_path_getter`` -- called
    with a phase name like ``"01_incumbent"`` (no suffix, no
    extension), expected to return the full file path. Production
    callers should resolve this through
    ``ArtifactLayout.artifact_path("flip_makespan_cp_phase_schedule",
    phase_name=..., scenario_name=..., instance_name=...)`` so the
    reporter can pick the files up via ``find_artifacts``. Tests may
    pass any callable returning a writable path.
    """

    cp_tl_seconds: float | None = None
    solver_thread_cnt: int = 1
    log_search_progress: bool = False
    solver_log_path_getter: Callable[[str], PathLike[str] | str] | None = None
    emit_phase_schedules: bool = False
    phase_schedule_path_getter: Callable[[str], PathLike[str] | str] | None = None
