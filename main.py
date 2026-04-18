"""Experiment orchestration entry point for FAM scheduling."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from routix.type_defs import RunMode

from ffc_ddw_sum_et.orchestration import (
    BenchmarkLoader,
    FAMMultiInstanceRunner,
    FAMMultiScenarioRunner,
    FAMSingleInstanceRunner,
)

CONFIG_PATH = Path("metadata/fam_config.yaml")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = _load_config(CONFIG_PATH)
    mode = _parse_run_mode(config.get("run_mode", "FULL_RUN"))
    instance_worker_cnt = config.get("instance_worker_cnt", 1)

    benchmark_dir = Path(config["benchmark_dir"])
    ins_index_source = config.get("ins_index_source")
    if ins_index_source:
        ins_index_source = Path(ins_index_source)
    logger = logging.getLogger("ffc_ddw_sum_et.main")
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

    output_dir = Path(config.get("output_dir", "output"))
    output_metadata = {"start_dt": datetime.now()}

    runner = FAMMultiScenarioRunner(
        m_i_runner_class=FAMMultiInstanceRunner,
        s_i_runner_class=FAMSingleInstanceRunner,
        instances=instances,
        shared_param_dict={},
        scenario_configs=scenario_configs,
        output_dir=output_dir,
        base_output_metadata=output_metadata,
        mode=mode,
        scenario_names=scenario_names,
        instance_worker_cnt=instance_worker_cnt,
    )

    logger.info(
        "Starting experiment run (mode=%s, instances=%d, scenarios=%d)",
        mode.name,
        len(instances),
        len(scenario_configs),
    )
    runner.run()
    logger.info("Experiment run complete")


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


if __name__ == "__main__":
    main()
