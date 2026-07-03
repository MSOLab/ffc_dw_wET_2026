"""Per-step step-log entry for SW-CP runs."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class SwCpStepEntry:
    """One sliding-window step entry in the SW-CP step log.

    ``incumbent_obj_before`` / ``cp_obj`` / ``incumbent_obj_after``
    capture the weighted E+T of the surrounding incumbent state per
    step:

    - ``incumbent_obj_before``: full-instance E+T of the incumbent
      entering the step.
    - ``cp_obj``: full-instance E+T of the candidate reconstructed
      from the CP solution (``None`` if the solver returned no
      feasible solution within its budget).
    - ``incumbent_obj_after``: full-instance E+T of the incumbent
      leaving the step (= ``cp_obj`` when accepted, else
      ``incumbent_obj_before``).

    ``non_time_fixed_op_count`` is the size of the CP sub-problem this
    step solved (sum across stages of jobs with at least one
    non-time-fixed op in the partition).
    """

    step: int
    elapsed_time: float | None
    TL: float | None
    elapsed_portion: float | None
    unfixed_batch_start_idx: int
    non_time_fixed_op_count: int
    sub_job_count: int
    incumbent_obj_before: float
    cp_obj: float | None
    incumbent_obj_after: float
    accepted: bool
    status: str
    wall_seconds: float
    cp_divergence_count: int = 0
    """Number of replayed ops (LPF + unfixed + RPF + RTF) whose realised
    end-time differed from the CP-provided end-time during merge. >0
    means the cumulative model found a solution the auto-assignment
    policy couldn't realise; the schedule is still feasible, just
    potentially worse than the CP promise. RTF is placed via explicit
    source-to-target machine matching with ``add_ops_times_2_mc`` (no
    slide possible); it only contributes to this counter when the
    matched target overlaps and the merge falls back to greedy
    ``add_operation_2_stage``."""

    def as_dict(self) -> dict:
        return asdict(self)
