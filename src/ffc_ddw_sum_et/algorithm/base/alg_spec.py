"""Execution request contract for algorithms."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from os import PathLike

from ...parameters.ffc_params import FFcParameters
from ...solution.ffc_schedule import FFcSchedule
from .alg_option import AlgOption

__all__ = ["AlgSpec"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgSpec:
    """Immutable execution request for one algorithm run."""

    instance: FFcParameters
    option: AlgOption | None = None
    ref_solution: FFcSchedule | None = None
    alg_root: PathLike[str] | str | None = None
    logger: logging.Logger | None = None

