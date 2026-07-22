"""Build a single-instance trace run directory from an existing multi-instance run.

Creates a symlink-based merged run dir with only one instance from specified
scenarios, plus a POST_PROCESS_ONLY config. Then run the printed command
to generate all report artifacts (including _csr_inner_flow_comparison.html).

Usage::

    uv run python scripts/build_single_instance_trace.py \\
        --src output/20260721_csr_coarsen_mode/20260721T194407_731892 \\
        --instance Instance_200_10_5_0,6_0,2_10_Rep0 \\
        --scenarios csr_k1 csr_k2_ceil csr_k4_ceil csr_k8_ceil csr_k16_ceil \\
        --dest output/20260721_trace_single_n200

If ``--scenarios`` is omitted, all scenario subdirs found in the source run dir
are included. Scenarios lacking the requested instance are skipped silently. The
generated config is written alongside the merged run dir
(``<run_dir>/_post_process_config.yaml``).
"""

import argparse
import csv
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

LAYOUT_SUFFIX = "_artifact_layout.yaml"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="source run directory (contains scenario subdirs + "
        "*_artifact_layout.yaml)",
    )
    parser.add_argument(
        "--instance",
        required=True,
        help="instance directory name (e.g. Instance_200_10_5_0,6_0,2_10_Rep0)",
    )
    parser.add_argument(
        "--scenarios",
        nargs="*",
        default=None,
        help="scenario subdir names to include (default: all found in --src)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="parent directory for the synthetic run dir "
        "(e.g. output/20260721_trace_single_n200)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run id (= run dir name). Defaults to a timestamp.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def _find_layout_stamp(run_dir: Path) -> Path:
    stamps = list(run_dir.glob(f"*{LAYOUT_SUFFIX}"))
    if not stamps:
        raise FileNotFoundError(
            f"no *{LAYOUT_SUFFIX} in {run_dir} — the source run predates the "
            "artifact-layout stamp"
        )
    return stamps[0]


def _find_source_config(run_dir: Path) -> Path | None:
    yamls = sorted(run_dir.glob("*.yaml"))
    for y in yamls:
        if y.name.endswith(LAYOUT_SUFFIX):
            continue
        return y
    return None


def _resolve_ins_index(instance_name: str) -> int:
    hybrid_csv = _repo_root() / "benchmarks/PRA2017/pra2017_hybrid_match.csv"
    filename_key = instance_name + ".txt"
    with open(hybrid_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("ffc_ddw_sum_et_filename") == filename_key:
                return int(row["insIndex"])
    raise ValueError(f"instance {instance_name!r} not found in {hybrid_csv}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _discover_scenario_dirs(src: Path) -> list[str]:
    """Auto-discover scenario subdirs in a source run directory.

    A subdir is treated as a scenario if it contains at least one ``Instance_*``
    subdir.  This filters out non-scenario artifacts (layout yaml, config yamls,
    logs, etc.).
    """
    return sorted(
        p.name
        for p in src.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and any(sub.name.startswith("Instance_") for sub in p.iterdir())
    )


def build_single_instance_trace(
    src: Path,
    instance: str,
    dest_parent: Path,
    scenario_names: list[str] | None = None,
    run_id: str | None = None,
) -> tuple[Path, list[str]]:
    """Create symlink-based trace run dir; return ``(run_dir, included_scenarios)``.

    ``included_scenarios`` is the subset of ``scenario_names`` (or auto-discovered
    names when ``None``) whose instance dir actually exists — others are skipped
    with a warning.
    """
    src = src.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"source run dir does not exist: {src}")

    available = _discover_scenario_dirs(src)

    if scenario_names is None:
        scenario_names = available
    else:
        missing = set(scenario_names) - set(available)
        if missing:
            raise ValueError(f"scenarios not found in {src}: {sorted(missing)}")

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    run_dir = dest_parent / run_id
    if run_dir.exists():
        raise FileExistsError(f"run dir already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    source_stamp = _find_layout_stamp(src)
    shutil.copy2(source_stamp, run_dir / f"{run_id}{LAYOUT_SUFFIX}")

    included: list[str] = []
    for name in scenario_names:
        scenario_dir = src / name
        instance_dir = scenario_dir / instance
        if not instance_dir.is_dir():
            logger.warning("skip %s: instance %s not found", name, instance)
            continue
        target = run_dir / name
        target.mkdir()
        (target / instance).symlink_to(instance_dir.resolve(), target_is_directory=True)
        logger.info("%s -> %s", name, instance_dir)
        included.append(name)

    if not included:
        raise ValueError(
            f"instance {instance!r} not found in any of {len(scenario_names)} scenarios"
        )
    return run_dir, included


def generate_post_process_config(
    src: Path,
    run_dir: Path,
    scenario_names: list[str],
    instance: str,
) -> Path:
    source_config = _find_source_config(src)
    if source_config is None:
        raise FileNotFoundError(f"no config YAML found in {src}")

    with open(source_config, encoding="utf-8") as f:
        src_cfg = yaml.safe_load(f)

    src_scenarios = src_cfg.get("scenarios", [])
    name_to_block = {}
    for s in src_scenarios:
        name_to_block[s["name"]] = s

    filtered = []
    for name in scenario_names:
        if name not in name_to_block:
            raise ValueError(
                f"scenario {name!r} not found in source config {source_config}"
            )
        filtered.append(name_to_block[name])

    ins_index = _resolve_ins_index(instance)

    out_cfg: dict = {
        "run_mode": "POST_PROCESS_ONLY",
        "analysis_dir_path": str(run_dir),
    }
    for key in (
        "benchmark_dir",
        "ins_index_source",
        "bks_table_csv_path",
        "output_dir",
        "instance_worker_cnt",
    ):
        if key in src_cfg:
            out_cfg[key] = src_cfg[key]

    out_cfg["ins_index"] = [ins_index]
    out_cfg["draw_gantt"] = False
    out_cfg["draw_progress_plot"] = False
    out_cfg["scenarios"] = filtered

    config_path = run_dir / "_post_process_config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            out_cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
    return config_path


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    try:
        run_dir, included = build_single_instance_trace(
            args.src,
            args.instance,
            args.dest,
            args.scenarios,
            args.run_id,
        )
        config_path = generate_post_process_config(
            args.src,
            run_dir,
            included,
            args.instance,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    logger.info("")
    logger.info("trace run dir: %s", run_dir)
    logger.info("config:        %s", config_path)
    logger.info("")
    logger.info("Run:")
    logger.info("  uv run python main.py --config %s", config_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
