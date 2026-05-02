"""Sort-key vocabulary for problem-instance-derived job sequences.

Lightweight module: only defines the :data:`ParamSortKey` literal and its
dispatcher. No runtime imports from the rest of the package, so callers
that need just the type (notably the IO heatmap module) can import it
without pulling in the full :mod:`parameters.ffc_ddw_params` chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .ffc_ddw_params import FFcDDWParameters

__all__ = ["ParamSortKey", "param_sort_job_sequence"]

ParamSortKey = Literal[
    "weight-due-pos",
    "due-weight-pos",
    "due*-weight-pos",
    "due2-weight-pos",
    "wxd1",
    "wxd2",
]


def param_sort_job_sequence(
    instance: FFcDDWParameters, key: ParamSortKey
) -> list[str]:
    """Dispatcher: map a :data:`ParamSortKey` to the corresponding job sequence."""
    if key == "weight-due-pos":
        return instance.get_weight_due_pos_job_sequence()
    if key == "due-weight-pos":
        return instance.get_due_weight_pos_job_sequence()
    if key == "due*-weight-pos":
        return instance.get_due_star_weight_pos_job_sequence()
    if key == "due2-weight-pos":
        return instance.get_due2_weight_pos_job_sequence()
    if key == "wxd1":
        return instance.get_wxd1_job_sequence()
    if key == "wxd2":
        return instance.get_wxd2_job_sequence()
    raise ValueError(f"Unknown ParamSortKey: {key!r}")
