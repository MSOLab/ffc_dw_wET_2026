"""Multi-instance concurrent runner for FAM experiment orchestration."""

from __future__ import annotations

import logging
from typing import Any

from routix.runner.multi_instance_concurrent_runner import (
    MultiInstanceConcurrentRunner,
)

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffcddw_single_instance_runner import FFcDDWSingleInstanceRunner


class FFcDDWMultiInstanceRunner(
    MultiInstanceConcurrentRunner[FFcDDWParameters, FFcDDWSingleInstanceRunner]
):
    """Runs instances concurrently for one scenario."""

    def __init__(
        self,
        instance_worker_cnt: int = 2,
        setup_logging_args: tuple | None = None,
        **kwargs: Any,
    ):
        self._setup_logging_args = setup_logging_args
        if kwargs.get("logger") is None:
            kwargs["logger"] = logging.getLogger(
                "ffc_ddw_sum_et.orchestration.FFcDDWMultiInstanceRunner"
            )
        super().__init__(
            instance_worker_cnt=instance_worker_cnt,
            **kwargs,
        )

    def _make_runner_logger(self, instance: FFcDDWParameters) -> logging.Logger:
        return logging.getLogger(
            f"ffc_ddw_sum_et.orchestration.FFcDDWSingleInstanceRunner.{instance.name}"
        )

    def _init_single_instance_runners(self) -> None:
        """Pass process-local logging args to each single-instance runner."""
        self.runners.clear()
        self.results.clear()

        for instance in self.instances:
            runner = self.s_i_runner_class(
                instance=instance,
                shared_param_dict=self.shared_param_dict,
                subroutine_flow=self.subroutine_flow,
                stopping_criteria=self.stopping_criteria,
                output_dir=self.output_dir,
                output_metadata=self.output_metadata,
                mode=self.mode,
                logger=self._make_runner_logger(instance),
                setup_logging_args=self._setup_logging_args,
            )
            self.runners.append(runner)

    def post_run_process(self) -> list[Any]:
        """Aggregates per-instance results."""
        return list(self.results)
