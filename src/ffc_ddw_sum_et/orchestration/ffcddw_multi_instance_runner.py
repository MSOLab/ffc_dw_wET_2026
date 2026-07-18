"""Multi-instance concurrent runner for FFcDWwET experiment orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from routix.io import load_yaml
from routix.runner.multi_instance_concurrent_runner import (
    MultiInstanceConcurrentRunner,
)

from ..io import load_schedule_json
from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffcddw_single_instance_runner import FFcDDWSingleInstanceRunner
from .solution_manager import FFcDDWSolution


class FFcDDWMultiInstanceRunner(
    MultiInstanceConcurrentRunner[FFcDDWParameters, FFcDDWSingleInstanceRunner]
):
    """Runs instances concurrently for one scenario."""

    def __init__(
        self,
        instance_worker_cnt: int = 2,
        setup_logging_args: tuple | None = None,
        scenario_name: str | None = None,
        **kwargs: Any,
    ):
        self._setup_logging_args = setup_logging_args
        self._scenario_name = scenario_name
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
        """Pass layout + scenario_name + logging args to each single-instance runner."""
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
                layout=self.layout,
                scenario_name=self._scenario_name,
                setup_logging_args=self._setup_logging_args,
            )
            self.runners.append(runner)

    def _load_resume_data(self) -> None:
        """RunMode.RESUME: load each instance's base incumbent from
        ``resume_root`` and inject it into the matching single-instance runner.

        Overrides routix's file-existence-only check: FFcDDW stores artifacts as
        ``{resume_root}/{ins}/{ins}_solution.json`` (+ ``_instance_result.yaml``)
        — no ``results/`` segment, JSON not YAML, no per-instance summary.csv.
        The schedule comes from the solution JSON; obj_value / obj_bound (global
        MCF LB) / elapsed_time come from the manifest (the SSOT). Runners are
        submitted to a process pool by value, so this parent-side injection is
        pickled to the workers. See plans/experiment/20260709/resume_from_base.md § 4.4.
        """
        resume_root_str = self.output_metadata.get("resume_root")
        if not resume_root_str:
            raise ValueError(
                "RESUME requires 'resume_root' in output_metadata "
                f"(available keys: {list(self.output_metadata.keys())})."
            )
        resume_root = Path(resume_root_str)

        missing: list[str] = []
        loaded = 0
        for instance, runner in zip(self.instances, self.runners):
            ins_name = instance.name
            inst_dir = resume_root / ins_name
            sol_path = inst_dir / f"{ins_name}_solution.json"
            manifest_path = inst_dir / f"{ins_name}_instance_result.yaml"
            if not sol_path.is_file() or not manifest_path.is_file():
                missing.append(ins_name)
                continue

            schedule, _sol_obj_value, _sol_obj_bound = load_schedule_json(sol_path)
            manifest = load_yaml(manifest_path)
            if not isinstance(manifest, dict):
                missing.append(ins_name)
                continue
            # obj_value / obj_bound from the manifest (SSOT): the incumbent
            # solution JSON usually carries objBound=None; the global LB lives
            # in the manifest's obj_bound (max over registered reports).
            runner.resume_solution = FFcDDWSolution(
                schedule=schedule,
                obj_value=manifest.get("obj_value"),
                obj_bound=manifest.get("obj_bound"),
            )
            runner.resume_elapsed_time = manifest.get("elapsed_time")
            loaded += 1

        self.logger.info(
            "RESUME: loaded base incumbent for %d/%d instances from %s",
            loaded,
            len(self.instances),
            resume_root,
        )
        if missing:
            raise RuntimeError(
                "RESUME: missing base artifacts "
                "(_solution.json / _instance_result.yaml) under "
                f"{resume_root} for instances: {', '.join(missing)}"
            )

    def post_run_process(self) -> list[Any]:
        """Aggregates per-instance results.

        Mirror per-instance error tracebacks (already captured by SIR as
        ``traceback.format_exc()`` strings on ``InstanceResult.error``) into
        the parent process's MIR-scoped log. Without this, instance failures
        only surface in ``<scenario>/<instance>/*_SingleInstanceRunner.log``.
        """
        for ir in self.results:
            err = getattr(ir, "error", None)
            if err:
                self.logger.error(
                    "Instance %s failed:\n%s",
                    getattr(ir, "instance_name", "<unknown>"),
                    err,
                )
        return list(self.results)
