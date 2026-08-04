"""Per-batch step-log entry for JobBatchCp runs."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class JobBatchCpStepEntry:
    """One batch entry in the job-batch CP step log."""

    step: int
    batch_size: int
    batch_head: str
    elapsed_time: float | None
    TL: float | None
    elapsed_portion: float | None
    obj_before: float
    obj_after: float
    accepted: bool
    cpsat_status: str | None
    setup_seconds: float | None
    makespan: int

    def as_dict(self) -> dict:
        return asdict(self)
