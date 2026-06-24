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

__all__ = [
    "ParamSortKey",
    "param_sort_job_sequence",
    "ReverseDispatchSeqKey",
    "reverse_dispatch_seq_job_sequence",
]

ParamSortKey = Literal[
    "weight-due-pos",
    "due-weight-pos",
    "due*-weight-pos",
    "due2-weight-pos",
    "wxd1",
    "wxd2",
]

ReverseDispatchSeqKey = Literal[
    "edd",
    "eddub_twt",
    "lsl",
    "osl",
    "weight_due_pos",
    "due_weight_pos",
    "due2_weight_pos",
    "due_star_weight_pos",
    "w1",
    "wxd1",
    "wxd2",
]


def param_sort_job_sequence(instance: FFcDDWParameters, key: ParamSortKey) -> list[str]:
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


def reverse_dispatch_seq_job_sequence(
    instance: FFcDDWParameters, key: ReverseDispatchSeqKey
) -> list[str]:
    """Map a sweep key to its instance-derived forward priority sequence."""
    direct = {
        "edd": instance.get_eddub_job_sequence,
        "eddub_twt": instance.get_eddub_twt_job_sequence,
        "lsl": instance.get_lsl_job_sequence,
        "osl": instance.get_osl_job_sequence,
        "w1": instance.get_w1_job_sequence,
        "wxd1": instance.get_wxd1_job_sequence,
        "wxd2": instance.get_wxd2_job_sequence,
    }
    if key in direct:
        return direct[key]()
    shared = {  # ParamSortKey로 위임
        "weight_due_pos": "weight-due-pos",
        "due_weight_pos": "due-weight-pos",
        "due2_weight_pos": "due2-weight-pos",
        "due_star_weight_pos": "due*-weight-pos",
    }
    if key in shared:
        return param_sort_job_sequence(instance, shared[key])
    raise ValueError(f"Unknown ReverseDispatchSeqKey: {key!r}")
