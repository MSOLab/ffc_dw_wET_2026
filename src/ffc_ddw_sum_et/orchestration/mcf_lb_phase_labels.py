"""Per-round snapshot label orders for the
``calc_mcf_lb_and_derive_full_sch`` composite step.

``MCF_LB_R1_LABEL_ORDER`` / ``MCF_LB_R2_LABEL_ORDER`` define the
per-round snapshot label order used by both per-instance CSV emission
(controller) and per-scenario summary aggregation (reporting).
"""

MCF_LB_R1_LABEL_ORDER: tuple[str, ...] = (
    "mcf_preemptive",
    "lastS_only_from_mcf_lb_before_sa_iti",
    "lastS_only_from_mcf_lb_after_sa_iti",
    "lastS_only_after_rs",
    "lastS_only_flipped",
    "fullS_before_unflip",
    "fullS_after_unflip",
    "fullS_after_sa_iti",
)

MCF_LB_R2_LABEL_ORDER: tuple[str, ...] = (
    "mcf_preemptive",
    "lastS_only_from_mcf_lb_before_sa_iti",
    "lastS_only_from_mcf_lb_after_sa_iti",
    "lastS_only_before_rs",
    "lastS_only_after_rs",
    "lastS_only_flipped",
    "fullS_before_unflip",
    "fullS_after_unflip",
    "fullS_after_sa_iti",
)
