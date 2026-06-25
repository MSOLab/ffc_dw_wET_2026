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
    "DispatchSeqKey",
    "dispatch_seq_job_sequence",
    "V3_PRIORITY_SET",
    "V4_PRIORITY_SET",
]

# v3 dispatch-initialization priority set: {edd, wspt_twt, wxd2} × {sd, rd} = 6 candidates.
V3_PRIORITY_SET: tuple[DispatchSeqKey, ...] = ("edd", "wspt_twt", "wxd2")

# v4 dispatch-initialization priority set: {wxd2, wspt_twt, wxd7} × {sd, rd} = 6 candidates.
# Rule set per analysis/20260625/dispatch_init_justification_3.md §5.
V4_PRIORITY_SET: tuple[DispatchSeqKey, ...] = ("wxd2", "wspt_twt", "wxd7")

ParamSortKey = Literal[
    "weight-due-pos",
    "due-weight-pos",
    "due*-weight-pos",
    "due2-weight-pos",
    "wxd1",
    "wxd2",
]

DispatchSeqKey = Literal[
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
    "wxd3",
    "wxd4",
    "wxd5",
    "wxd6",
    "wxd7",
    "cpd_mean",
    "cpd_wmean",
    "cpd_median",
    "wspt_twt",
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


def dispatch_seq_job_sequence(
    instance: FFcDDWParameters, key: DispatchSeqKey
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
        "wxd3": instance.get_wxd3_job_sequence,
        "wxd4": instance.get_wxd4_job_sequence,
        "wxd5": instance.get_wxd5_job_sequence,
        "wxd6": instance.get_wxd6_job_sequence,
        "wxd7": instance.get_wxd7_job_sequence,
        "cpd_mean": instance.get_cpd_mean_job_sequence,
        "cpd_wmean": instance.get_cpd_wmean_job_sequence,
        "cpd_median": instance.get_cpd_median_job_sequence,
        "wspt_twt": instance.get_wspt_twt_job_sequence,
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
    raise ValueError(f"Unknown DispatchSeqKey: {key!r}")
