"""Assemble a synthetic run directory that merges scenarios from several runs.

`RunMode.POST_PROCESS_ONLY` regenerates every run-level comparison report
(`*_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`,
`*_multi_scenario_subroutine_flow_comparison.html`, `*_win_tie_dashboard.html`,
`*_rpdf_dashboard.html`, `*_rpdf_comparison.csv`, `*_report.xlsx`, …) from one
run directory. It cannot span runs.

This script builds the missing piece: a run directory whose scenario subdirs
hold **symlinks** to instance dirs owned by other runs. Point a
POST_PROCESS_ONLY config's `analysis_dir_path` at the result and the reporter
treats the borrowed scenarios as if they had been run together.

Symlinks (not copies) keep the merged dir at a few MB instead of a few GB, and
because the reporter only reads instance dirs — scenario-level and run-level
artifacts land in the merged dir — the source runs are never written to. Run
with `draw_gantt: false` and `draw_progress_plot: false`; those painters *do*
write inside instance dirs, which would reach through the symlinks.

Only `<instance>_instance_result.yaml` and `<instance>_solution.json` are
required by the reporter; `<instance>_obj_log.json` is additionally needed for
the subroutine-flow comparison chart.

Scenario labels must be unique across the merged set — they become the scenario
subdir names, and `main._validate_scenario_uniqueness` rejects duplicates. Pass
`<scenario_dir>=<label>` to rename a scenario on the way in.

Usage::

    uv run python scripts/build_merged_run_dir.py \\
        --dest output/20260711_merge_base_p25_p50 \\
        output/20260704/20260704T164349_114896/s0_c5_base \\
        output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386/s0_c5_p25

Then write a POST_PROCESS_ONLY config whose `analysis_dir_path` is the printed
run directory and whose `scenarios` list carries one entry per label, each with
the `subroutine_flow` copied from the source run's config, and run::

    uv run python main.py --config <that config>

The instance sets of the merged scenarios must match; a mismatch aborts, because
the reporter would otherwise emit placeholder rows for the instances a scenario
lacks. Two flags override that:

- `--intersect-instances` symlinks only the instances common to every scenario,
  so the merged dir *is* one grid. Prefer this when merging a full-grid run with
  a subset run.
- `--allow-instance-mismatch` merges the sets as they are. Note that a
  POST_PROCESS_ONLY config's `ins_index` does **not** rescue this: it filters
  `summary_csv` and everything derived from it, but the run-level chart writers
  take no instance list and average over whatever is symlinked here. A superset
  left on disk therefore reports that scenario's charts on a different instance
  grid than its CSV rows, with nothing in the output saying so.
"""

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LAYOUT_SUFFIX = "_artifact_layout.yaml"
INSTANCE_GLOB = "Instance_*"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scenario_dirs",
        nargs="+",
        metavar="SCENARIO_DIR[=LABEL]",
        help="scenario directories of the form <run_dir>/<scenario_name>, "
        "optionally suffixed with =<label> to rename the scenario",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="parent directory for the synthetic run dir (e.g. output/20260711_merge)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run id (= run dir name). Defaults to a fresh UTC-naive timestamp.",
    )
    parser.add_argument(
        "--allow-instance-mismatch",
        action="store_true",
        help="permit scenarios whose instance sets differ",
    )
    parser.add_argument(
        "--intersect-instances",
        action="store_true",
        help="symlink only the instances present in EVERY scenario, so the "
        "merged dir holds one common grid (implies --allow-instance-mismatch)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def _split_label(spec: str) -> tuple[Path, str]:
    """Split ``<path>`` or ``<path>=<label>`` into a directory and its label."""
    path_str, sep, label = spec.partition("=")
    path = Path(path_str)
    return path, (label if sep else path.name)


def _instance_names(scenario_dir: Path) -> set[str]:
    return {p.name for p in scenario_dir.glob(INSTANCE_GLOB) if p.is_dir()}


def _find_layout_stamp(run_dir: Path) -> Path:
    """Return the `<run_id>_artifact_layout.yaml` stamped in `run_dir`."""
    stamps = list(run_dir.glob(f"*{LAYOUT_SUFFIX}"))
    if not stamps:
        raise FileNotFoundError(
            f"no *{LAYOUT_SUFFIX} in {run_dir} — the source run predates the "
            "artifact-layout stamp, so POST_PROCESS_ONLY cannot restore it"
        )
    return stamps[0]


def build_merged_run_dir(
    specs: list[str],
    dest_parent: Path,
    run_id: str | None = None,
    *,
    allow_instance_mismatch: bool = False,
    intersect_instances: bool = False,
) -> Path:
    """Create the synthetic run dir and return its path."""
    pairs = [_split_label(spec) for spec in specs]

    labels = [label for _, label in pairs]
    if len(set(labels)) != len(labels):
        dupes = sorted({lab for lab in labels if labels.count(lab) > 1})
        raise ValueError(
            f"duplicate scenario labels {dupes}: they would collide as subdir "
            "names. Disambiguate with <scenario_dir>=<label>."
        )

    for scenario_dir, _ in pairs:
        if not scenario_dir.is_dir():
            raise FileNotFoundError(f"scenario dir does not exist: {scenario_dir}")

    instance_sets = {label: _instance_names(d) for d, label in pairs}
    for label, names in instance_sets.items():
        if not names:
            raise ValueError(f"scenario {label!r} holds no {INSTANCE_GLOB} subdirs")

    # The run-level chart writers (`write_post_run_subroutine_chart_artifacts`)
    # take no instance list — they discover instances by walking the run dir.
    # A POST_PROCESS_ONLY config's `ins_index` therefore filters `summary_csv`
    # and everything derived from it, but NOT the scatter / flow-comparison
    # HTMLs, which average over whatever is symlinked here. Keeping a superset
    # on disk silently reports that scenario on a different instance grid than
    # the CSVs, so `--intersect-instances` makes the directory itself the one
    # common grid rather than relying on a downstream filter.
    keep: set[str] | None = None
    if intersect_instances:
        keep = set.intersection(*instance_sets.values())
        if not keep:
            raise ValueError(
                "--intersect-instances: the scenarios share no common instance"
            )
        for label, names in sorted(instance_sets.items()):
            dropped = len(names) - len(keep)
            if dropped:
                logger.info(
                    "%s: keeping %d of %d instances (%d outside the common grid)",
                    label,
                    len(keep),
                    len(names),
                    dropped,
                )
    else:
        reference_label, reference = next(iter(instance_sets.items()))
        for label, names in instance_sets.items():
            if names == reference:
                continue
            missing = sorted(reference - names)[:3]
            extra = sorted(names - reference)[:3]
            message = (
                f"instance set of {label!r} differs from {reference_label!r} "
                f"(missing e.g. {missing}, extra e.g. {extra})"
            )
            if not allow_instance_mismatch:
                raise ValueError(
                    message + "; pass --allow-instance-mismatch or "
                    "--intersect-instances to proceed"
                )
            logger.warning("%s", message)

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    run_dir = dest_parent / run_id
    if run_dir.exists():
        raise FileExistsError(f"run dir already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    # `restore_layout_from_run_dir` looks for `<run_id>_artifact_layout.yaml`.
    # The stamp's templates are keyed on `{run_id}`, so any source run's stamp
    # transplants cleanly under the new id.
    source_stamp = _find_layout_stamp(pairs[0][0].parent)
    shutil.copy2(source_stamp, run_dir / f"{run_id}{LAYOUT_SUFFIX}")

    for scenario_dir, label in pairs:
        target = run_dir / label
        target.mkdir()
        resolved = scenario_dir.resolve()
        instance_dirs = sorted(
            p
            for p in resolved.glob(INSTANCE_GLOB)
            if p.is_dir() and (keep is None or p.name in keep)
        )
        for instance_dir in instance_dirs:
            (target / instance_dir.name).symlink_to(instance_dir)
        logger.info("%s -> %s (%d instances)", label, scenario_dir, len(instance_dirs))

    return run_dir


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    try:
        run_dir = build_merged_run_dir(
            args.scenario_dirs,
            args.dest,
            args.run_id,
            allow_instance_mismatch=args.allow_instance_mismatch,
            intersect_instances=args.intersect_instances,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    logger.info("")
    logger.info("merged run dir: %s", run_dir)
    logger.info("Set this in a POST_PROCESS_ONLY config:")
    logger.info("    analysis_dir_path: %s", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
