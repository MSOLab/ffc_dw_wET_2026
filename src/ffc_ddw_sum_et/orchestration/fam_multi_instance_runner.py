"""Multi-instance concurrent runner for FAM experiment orchestration."""

from __future__ import annotations

from typing import Any

from routix.runner.multi_instance_concurrent_runner import (
    MultiInstanceConcurrentRunner,
)

from ..parameters.ffc_ddw_params import FFcDueDateWindowParameters
from .fam_single_instance_runner import FAMSingleInstanceRunner


class FAMMultiInstanceRunner(
    MultiInstanceConcurrentRunner[FFcDueDateWindowParameters, FAMSingleInstanceRunner]
):
    """Runs instances concurrently for one scenario."""

    def __init__(self, instance_worker_cnt: int = 2, **kwargs: Any):
        super().__init__(
            instance_worker_cnt=instance_worker_cnt,
            **kwargs,
        )

    def post_run_process(self) -> list[Any]:
        """Aggregates per-instance results."""
        return list(self.results)
