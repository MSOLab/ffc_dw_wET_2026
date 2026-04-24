"""Experiment orchestration entry point for FAM scheduling."""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from routix.io.path import init_timestamped_working_dir
from routix.type_defs import RunMode

from ffc_ddw_sum_et.orchestration import (
    BenchmarkLoader,
    FFcDDWMultiInstanceRunner,
    FFcDDWMultiScenarioRunner,
    FFcDDWSingleInstanceRunner,
)

CONFIG_PATH = Path("metadata/20260424/neh_cp_config_6.yaml")


def _setup_main_logger(output_dir: Path) -> logging.Logger:
    timestamp = output_dir.name
    log_path = output_dir / f"{timestamp}_main.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger = logging.getLogger("ffc_ddw_sum_et.main")
    logger.setLevel(logging.INFO)
    # 재실행/재호출 시 핸들러 중복 부착 방지
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


def main() -> None:
    main_start_dt = datetime.now()
    time_main_start = time.monotonic()
    # Load config and set up output directory based on run mode
    config = _load_config(CONFIG_PATH)
    mode = _parse_run_mode(config.get("run_mode", "FULL_RUN"))
    base_output_dir = Path(config.get("output_dir", "output"))
    if mode == RunMode.POST_PROCESS_ONLY:
        output_dir = _resolve_post_process_dir(config, base_output_dir)
    else:
        output_dir = init_timestamped_working_dir(base_output_dir=base_output_dir)
        shutil.copy2(CONFIG_PATH, output_dir / CONFIG_PATH.name)

    # Set up logger after determining output directory
    logger = _setup_main_logger(output_dir)
    logger.info("Starting main() at %s with run mode: %s", main_start_dt, mode.name)
    if mode == RunMode.POST_PROCESS_ONLY:
        logger.info("Post-processing existing output directory: %s", output_dir)
    else:
        logger.info("Run output directory: %s", output_dir)

    # Load instances and prepare scenario configs
    instance_worker_cnt = config.get("instance_worker_cnt", 1)
    draw_gantt = bool(config.get("draw_gantt", True))
    painter_thread_cnt = int(config.get("painter_thread_cnt", 1))

    benchmark_dir = Path(config["benchmark_dir"])
    ins_index_source = config.get("ins_index_source")
    if ins_index_source:
        ins_index_source = Path(ins_index_source)
    logger.info("Loading instances from %s", benchmark_dir)
    loader = BenchmarkLoader(benchmark_dir, ins_index_source=ins_index_source)
    ins_index_filter = config.get("ins_index")
    instances = loader.load_all(ins_index=ins_index_filter)
    logger.info("Loaded %d instances", len(instances))

    scenario_configs = []
    scenario_names = []
    for sc in config.get("scenarios", []):
        scenario_configs.append(
            {
                "subroutine_flow": sc["subroutine_flow"],
                "stopping_criteria": {"timelimit": sc["timelimit"]},
                "output_subdir": sc.get("output_subdir"),
            }
        )
        scenario_names.append(sc.get("name", f"scenario_{len(scenario_configs)}"))

    output_metadata = {"start_dt": main_start_dt}

    runner = FFcDDWMultiScenarioRunner(
        m_i_runner_class=FFcDDWMultiInstanceRunner,
        s_i_runner_class=FFcDDWSingleInstanceRunner,
        instances=instances,
        shared_param_dict={},
        scenario_configs=scenario_configs,
        output_dir=output_dir,
        base_output_metadata=output_metadata,
        mode=mode,
        scenario_names=scenario_names,
        instance_worker_cnt=instance_worker_cnt,
        draw_gantt=draw_gantt,
        painter_thread_cnt=painter_thread_cnt,
        ins_index_source=ins_index_source,
    )

    logger.info(
        "Starting experiment run (mode=%s, instances=%d, scenarios=%d)",
        mode.name,
        len(instances),
        len(scenario_configs),
    )
    try:
        runner.run()
        logger.info("Experiment run completed successfully.")
    finally:
        time_main_end = time.monotonic()
        logger.info(
            "Finished main() at %s. Total elapsed time: %f seconds",
            datetime.now(),
            time_main_end - time_main_start,
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


if __name__ == "__main__":
    main()
