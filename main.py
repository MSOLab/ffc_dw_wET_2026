"""Experiment orchestration entry point for FFcDWwET scheduling."""

from __future__ import annotations

import argparse
import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from routix.dynamic_data_object import DynamicDataObject
from routix.io import RunRoot, init_run_root, load_yaml
from routix.subroutine_flow_validator import SubroutineFlowValidator
from routix.type_defs import RunMode

from ffc_ddw_sum_et.logging_setup import setup_logging
from ffc_ddw_sum_et.orchestration import (
    SUBROUTINE_FLOW_CACHE_FN,
    BenchmarkLoader,
    FFcDDWMultiInstanceRunner,
    FFcDDWMultiScenarioRunner,
    FFcDDWSingleInstanceRunner,
    FFcDDWSubroutineController,
    init_ffc_artifact_layout,
    restore_layout_from_run_dir,
)

CONFIG_PATH = Path("metadata/20260724/lastsemi_rounding_robust.yaml")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ffc_ddw_sum_et")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress INFO/WARNING on terminal (file logs unaffected).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase terminal verbosity. -v: INFO, -vv: DEBUG. Default: WARNING.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to experiment YAML config.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config_path = args.config

    main_start_dt = datetime.now()
    time_main_start = time.monotonic()
    config = _load_config(config_path)
    mode = _parse_run_mode(config.get("run_mode", "FULL_RUN"))
    base_output_dir = Path(config.get("output_dir", "output"))
    if mode == RunMode.POST_PROCESS_ONLY:
        existing_dir = _resolve_post_process_dir(config, base_output_dir)
        run_root = RunRoot(path=existing_dir, run_id=existing_dir.name)
    else:
        run_root = init_run_root(base_output_dir=base_output_dir)
        shutil.copy2(config_path, run_root.path / config_path.name)

    _validate_scenario_uniqueness(config.get("scenarios", []))
    _reject_deprecated_step_kwargs(config.get("scenarios", []))
    if mode == RunMode.POST_PROCESS_ONLY:
        layout = restore_layout_from_run_dir(run_root)
    else:
        layout = init_ffc_artifact_layout(run_root)
        layout.stamp()

    main_logging_args = (layout.log_path("main"), args.quiet, args.verbose)
    setup_logging(*main_logging_args, is_main=True)
    logger = logging.getLogger("ffc_ddw_sum_et.main")
    logger.info("Starting main() at %s with run mode: %s", main_start_dt, mode.name)
    if mode == RunMode.POST_PROCESS_ONLY:
        logger.info("Post-processing existing output directory: %s", run_root.path)
    else:
        logger.info("Run output directory: %s", run_root.path)

    # RESUME: resolve the base scenario dir and load its cached flow so each
    # scenario's prefix can be validated and its resume index derived.
    resume_dir: Path | None = None
    base_flow: list | None = None
    if mode == RunMode.RESUME:
        resume_dir = _resolve_resume_dir(config, base_output_dir)
        logger.info("RESUME: resolved base scenario dir: %s", resume_dir)
        base_flow = load_yaml(resume_dir / SUBROUTINE_FLOW_CACHE_FN)

    instance_worker_cnt = config.get("instance_worker_cnt", 1)
    draw_gantt = bool(config.get("draw_gantt", True))
    draw_progress_plot = bool(config.get("draw_progress_plot", False))
    painter_thread_cnt = int(config.get("painter_thread_cnt", 1))

    benchmark_dir = Path(config["benchmark_dir"])
    ins_index_source = config.get("ins_index_source")
    if ins_index_source:
        ins_index_source = Path(ins_index_source)
    bks_table_csv_path = config.get("bks_table_csv_path")
    if bks_table_csv_path:
        bks_table_csv_path = Path(bks_table_csv_path)
    logger.info("Loading instances from %s", benchmark_dir)
    loader = BenchmarkLoader(benchmark_dir, ins_index_source=ins_index_source)
    ins_index_filter = config.get("ins_index")
    instances = loader.load_all(ins_index=ins_index_filter)
    logger.info("Loaded %d instances", len(instances))

    flow_validator = (
        SubroutineFlowValidator(FFcDDWSubroutineController)
        if mode == RunMode.RESUME
        else None
    )

    scenario_configs = []
    scenario_names = []
    for sc in config.get("scenarios", []):
        scenario_name = sc.get("name", f"scenario_{len(scenario_configs) + 1}")
        scenario_config: dict[str, Any] = {
            "subroutine_flow": sc["subroutine_flow"],
            "stopping_criteria": {"timelimit": sc["timelimit"]},
            "output_subdir": sc.get("output_subdir"),
        }
        if mode == RunMode.RESUME:
            assert flow_validator is not None and base_flow is not None
            flow_resume_idx = flow_validator.validate_subroutine_flow_prefix(
                DynamicDataObject.from_obj(base_flow),
                DynamicDataObject.from_obj(sc["subroutine_flow"]),
            )
            step_cnt = len(sc["subroutine_flow"])
            if flow_resume_idx >= step_cnt:
                raise ValueError(
                    f"RESUME: scenario {scenario_name!r} would run no steps — its "
                    f"{step_cnt}-step flow is fully covered by the base flow at "
                    f"resume_dir ({resume_dir}). Point resume_dir at the base "
                    "(prefix) run, not a run of the case scenarios themselves."
                )
            scenario_config["flow_resume_idx"] = flow_resume_idx
            logger.info(
                "RESUME: scenario %s resumes at flow index %d (base prefix of %d "
                "steps validated)",
                scenario_name,
                flow_resume_idx,
                flow_resume_idx,
            )
        scenario_configs.append(scenario_config)
        scenario_names.append(scenario_name)

    output_metadata: dict[str, Any] = {"start_dt": main_start_dt}
    if mode == RunMode.RESUME and resume_dir is not None:
        output_metadata["resume_root"] = str(resume_dir)

    runner = FFcDDWMultiScenarioRunner(
        m_i_runner_class=FFcDDWMultiInstanceRunner,
        s_i_runner_class=FFcDDWSingleInstanceRunner,
        instances=instances,
        shared_param_dict={},
        scenario_configs=scenario_configs,
        output_dir=run_root.path,
        base_output_metadata=output_metadata,
        mode=mode,
        layout=layout,
        scenario_names=scenario_names,
        instance_worker_cnt=instance_worker_cnt,
        draw_gantt=draw_gantt,
        draw_progress_plot=draw_progress_plot,
        painter_thread_cnt=painter_thread_cnt,
        ins_index_source=ins_index_source,
        bks_table_csv_path=bks_table_csv_path,
        setup_logging_args=(None, args.quiet, args.verbose),
    )

    logger.info(
        "Starting experiment run (mode=%s, instances=%d, scenarios=%d)",
        mode.name,
        len(instances),
        len(scenario_configs),
    )
    try:
        final = runner.run()
        setup_logging(*main_logging_args, is_main=True)
        total_err = sum(
            1 for sr in final.scenario_results for ir in sr.instance_results if ir.error
        )
        if total_err:
            logger.error(
                "Experiment run finished with %d instance error(s); "
                "see per-scenario MultiInstanceRunner logs for tracebacks.",
                total_err,
            )
        else:
            logger.info("Experiment run completed successfully.")
    finally:
        setup_logging(*main_logging_args, is_main=True)
        time_main_end = time.monotonic()
        logger.info(
            "Finished main() at %s. Total elapsed time: %s",
            datetime.now(),
            timedelta(seconds=time_main_end - time_main_start),
        )


def _load_config(path: Path) -> dict:
    from routix.io import load_yaml

    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return load_yaml(path)


def _parse_run_mode(mode_str: str) -> RunMode:
    try:
        return RunMode[mode_str.upper()]
    except KeyError:
        raise ValueError(
            f"Invalid run_mode: {mode_str}. Must be FULL_RUN, RESUME, or POST_PROCESS_ONLY"
        )


def _resolve_post_process_dir(config: dict[str, Any], base_output_dir: Path) -> Path:
    """Resolve the prior timestamped output directory for POST_PROCESS_ONLY.

    Accepts either ``analysis_dir_path`` (full path) or ``analysis_timestamp``
    (joined under ``output_dir``). Mirrors the pattern in
    ``../hybridflowshop/main.py``.
    """
    analysis_dir_path = config.get("analysis_dir_path")
    if analysis_dir_path:
        path = Path(analysis_dir_path).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"analysis_dir_path does not exist: {path}")
        return path

    analysis_timestamp = config.get("analysis_timestamp")
    if analysis_timestamp:
        path = base_output_dir / analysis_timestamp
        if not path.is_dir():
            raise FileNotFoundError(
                f"analysis_timestamp not found under {base_output_dir}: "
                f"{analysis_timestamp}"
            )
        return path

    raise ValueError(
        "POST_PROCESS_ONLY requires 'analysis_dir_path' or 'analysis_timestamp' "
        "in the config YAML."
    )


_LATEST_PREFIX = "latest:"


def _resolve_resume_dir(config: dict[str, Any], base_output_dir: Path) -> Path:
    """Resolve the base **scenario** directory to resume from (RunMode.RESUME).

    Requires ``resume_dir`` in the config. Two accepted forms:

    1. An explicit **scenario dir** that directly holds ``subroutine_flow.yaml``
       (plus per-instance ``<ins>/<ins>_solution.json`` /
       ``<ins>_instance_result.yaml``) — used verbatim.
    2. ``latest:<scenario_name>`` — the newest run dir under ``base_output_dir``
       that contains ``<scenario_name>/subroutine_flow.yaml``. Saves re-pointing
       the config at each fresh timestamped base run.

    Form 2 **requires the scenario name**: a *case* run's scenario dir also
    carries a flow cache and resume artifacts, so an unqualified "newest flow
    cache anywhere" search can silently resolve to a case run — whose flow fully
    covers each case scenario, yielding a run that skips every step. Naming the
    base scenario (case scenarios are named differently) makes that impossible.
    """
    resume_dir_str = config.get("resume_dir")
    if not resume_dir_str:
        raise ValueError(
            "RESUME requires 'resume_dir' in the config YAML (a base scenario "
            "dir, or 'latest:<base_scenario_name>')."
        )
    if resume_dir_str.startswith(_LATEST_PREFIX):
        scenario_name = resume_dir_str[len(_LATEST_PREFIX) :].strip()
        if not scenario_name:
            raise ValueError(
                "resume_dir 'latest:' requires the base scenario name, e.g. "
                "'latest:mcf_lb_fmm_neh_cp'."
            )
        return _resolve_latest_scenario_dir(base_output_dir, scenario_name)
    if resume_dir_str == "latest":
        raise ValueError(
            "resume_dir 'latest' is ambiguous; name the base scenario instead: "
            "'latest:<base_scenario_name>' (e.g. 'latest:mcf_lb_fmm_neh_cp')."
        )
    path = Path(resume_dir_str).expanduser()
    if not path.is_dir():
        raise FileNotFoundError(f"resume_dir does not exist: {path}")
    flow_cache = path / SUBROUTINE_FLOW_CACHE_FN
    if not flow_cache.is_file():
        raise FileNotFoundError(
            f"resume_dir missing flow cache {SUBROUTINE_FLOW_CACHE_FN}: "
            f"{flow_cache}. Was the base run produced by the current code?"
        )
    return path


def _resolve_latest_scenario_dir(base_output_dir: Path, scenario_name: str) -> Path:
    """Newest run dir under ``base_output_dir`` holding ``scenario_name``'s flow cache.

    Run dir names are timestamps minted by ``init_run_root``
    (``YYYYmmddTHHMMSS_microseconds``), so lexicographic order == chronological.
    """
    if not base_output_dir.is_dir():
        raise FileNotFoundError(f"output_dir does not exist: {base_output_dir}")
    candidates = [
        run_dir / scenario_name
        for run_dir in base_output_dir.iterdir()
        if run_dir.is_dir()
        and (run_dir / scenario_name / SUBROUTINE_FLOW_CACHE_FN).is_file()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"RESUME: no run under {base_output_dir} has a scenario "
            f"{scenario_name!r} with a {SUBROUTINE_FLOW_CACHE_FN}. Run the base "
            "config first, or name the scenario that the base run emits."
        )
    return max(candidates, key=lambda scenario_dir: scenario_dir.parent.name)


DEPRECATED_STEP_KWARGS: dict[str, str] = {
    "idle_mode": (
        "removed 2026-07-22 — CSR and sw_cp always use 'lookahead'. Delete the "
        "key (see plans/experiment/20260722/csr_idle_mode_lookahead_only.md)"
    ),
}
"""Step kwargs that no longer exist, mapped to the message shown when used.

Without this preflight a stale key reaches ``_call_method(name, **kwargs)`` and
dies inside a worker with a bare ``TypeError``, mid-run. Keys are rejected
regardless of value: accepting the surviving value would keep implying the
knob is still configurable.
"""


def _reject_deprecated_step_kwargs(scenarios: list[dict[str, Any]]) -> None:
    """Fail-fast on removed step kwargs anywhere in a scenario's flow.

    Scans each step dict recursively so nested flows (``solve_flow`` under
    ``coarsen_solve_reconstruct``) are covered too.
    """
    offenders: list[str] = []

    def scan(steps: Any, scenario_name: str) -> None:
        if isinstance(steps, list):
            for step in steps:
                scan(step, scenario_name)
            return
        if not isinstance(steps, dict):
            return
        method = steps.get("method", "<unnamed step>")
        for key, reason in DEPRECATED_STEP_KWARGS.items():
            if key in steps:
                offenders.append(f"{scenario_name}/{method}: {key!r} {reason}")
        for value in steps.values():
            if isinstance(value, list):
                scan(value, scenario_name)

    for i, sc in enumerate(scenarios):
        scan(sc.get("subroutine_flow", []), sc.get("name", f"scenario_{i + 1}"))

    if offenders:
        details = "\n  ".join(offenders)
        raise ValueError(f"deprecated step kwargs in config:\n  {details}")


def _validate_scenario_uniqueness(scenarios: list[dict[str, Any]]) -> None:
    """Fail-fast on duplicated scenario `name` or `output_subdir`.

    Two scenarios sharing either coordinate would silently overwrite each
    other's output directory; doc § 7.3 captures the prior incident.
    """
    seen_names: dict[str, list[int]] = {}
    seen_subdirs: dict[str, list[int]] = {}
    for i, sc in enumerate(scenarios):
        name = sc.get("name", f"scenario_{i + 1}")
        seen_names.setdefault(name, []).append(i)
        subdir = sc.get("output_subdir") or name
        seen_subdirs.setdefault(subdir, []).append(i)
    dups: list[tuple[str, list[int]]] = []
    for k, v in seen_names.items():
        if len(v) > 1:
            dups.append((f"name={k!r}", v))
    for k, v in seen_subdirs.items():
        if len(v) > 1:
            dups.append((f"output_subdir={k!r}", v))
    if dups:
        details = "; ".join(f"{k} at indices {v}" for k, v in dups)
        raise ValueError(
            f"duplicate scenario coordinates: {details}. Each scenario must "
            "have a unique name AND unique output_subdir to prevent silent "
            "overwrite of experiment results (see docs/io/"
            "20260429_artifact_manager.md § 7.3)."
        )


if __name__ == "__main__":
    main()
