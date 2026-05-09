"""Phase-name parsing regexes and recording-label orders for the
``calc_mcf_lb_and_derive_full_sch`` composite step.

The constants here describe orchestration-side naming conventions, not
algorithm internals:

- ``MCF_LB_LOCAL_NAME_RE`` extracts the ``<local_name>`` tail from full
  phase names recorded under ``calc_mcf_lb_and_derive_full_sch``. The
  recorded full name has shape ``…<inner_step>_<digit>+_<local_name>``
  (see ``FFcDDWSubroutineControllerCore._mcf_lb_phase_name``).
- ``MCF_LB_ROUND_RE`` matches the round marker (``r1`` / ``r2``) injected
  by ``temporarily_extended_context("r1" | "r2")`` and rendered as
  ``<count>-r1`` / ``<count>-r2`` inside the dotted context string.
- ``MCF_LB_R1_LABEL_ORDER`` / ``MCF_LB_R2_LABEL_ORDER`` define the
  per-round snapshot label order used by both per-instance CSV emission
  (controller) and per-scenario summary aggregation (reporting).
"""

import re

MCF_LB_LOCAL_NAME_RE = re.compile(r"_(\d+)_([A-Za-z][A-Za-z_]+)$")
MCF_LB_ROUND_RE = re.compile(r"(?:^|\.)\d+-r([12])(?:[._]|$)")

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
