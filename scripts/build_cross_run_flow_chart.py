"""Build one cross-run multi-scenario subroutine-flow comparison HTML.

Like ``scripts/build_subroutine_flow_charts.py``, but instead of operating
on a single run directory's scenarios, this driver accepts an arbitrary
list of scenario directories — typically drawn from different runs — and
renders one combined flow comparison chart.

Each positional argument is a scenario directory shaped like
``<run_dir>/<scenario_name>/``, containing instance subdirs that hold
``<instance>_obj_log.json`` and ``<instance>_instance_result.yaml``
(the standard layout written by the runner).

Usage::

    uv run python scripts/build_cross_run_flow_chart.py \\
        output/<dateA>/<runA>/<scenarioA> \\
        output/<dateB>/<runB>/<scenarioB>

By default the output HTML is written to
``analysis/<YYYYMMDDTHHMMSS_uuuuuu>/cross_run_flow.html`` (timestamp
generated at run time). Override with ``--output``.

Trace labels default to ``<run_id>/<scenario_name>`` (parent dir + dir
name) so scenarios with the same name in different runs stay
distinguishable. Override with ``--labels`` (must match positional count).

Per-scenario scatter HTMLs are intentionally *not* written; this script
only emits the combined flow chart and never touches the source run dirs.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ffc_ddw_sum_et.report import (
    InstanceProgression,
    build_endpoint_df,
    build_raw_progression_df,
    export_multi_scenario_method_rpdf_comparison_html,
    load_baseline_df,
    load_instance_progression,
)
from ffc_ddw_sum_et.report.post_run_chart_writer import attach_rpdf_columns

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_BENCHMARKS_DIR = _REPO_ROOT / "benchmarks" / "PRA2017"
_DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "analysis"

logger = logging.getLogger(__name__)


def _default_output_path() -> Path:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    return _DEFAULT_OUTPUT_ROOT / ts / "cross_run_flow.html"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scenario_dirs",
        type=Path,
        nargs="+",
        help="Scenario directories (each shaped like <run_dir>/<scenario_name>/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output HTML path. Default: "
            "analysis/<YYYYMMDDTHHMMSS_uuuuuu>/cross_run_flow.html "
            "(timestamp generated at run time)."
        ),
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        default=None,
        help=(
            "Optional custom trace labels (one per scenario dir). "
            "Default: <run_id>/<scenario_name>."
        ),
    )
    parser.add_argument(
        "--hybrid-match-csv",
        type=Path,
        default=_DEFAULT_BENCHMARKS_DIR / "pra2017_hybrid_match.csv",
        help="CSV mapping ffc_ddw_sum_et_filename -> insIndex (default: PRA2017)",
    )
    parser.add_argument(
        "--bks-csv",
        type=Path,
        default=_DEFAULT_BENCHMARKS_DIR / "pra2017_bks_table.csv",
        help="CSV with BKS_data per insIndex (default: PRA2017)",
    )
    parser.add_argument(
        "--instance-table-csv",
        type=Path,
        default=_DEFAULT_BENCHMARKS_DIR / "pra2017_instance_table.csv",
        help="CSV with PRA2017 generator factors T,R per insIndex",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging",
    )
    return parser.parse_args(argv)


def _default_label(scenario_dir: Path) -> str:
    return f"{scenario_dir.parent.name}/{scenario_dir.name}"


def _load_scenario_progressions(scenario_dir: Path) -> list[InstanceProgression]:
    """Scan ``scenario_dir`` for instance subdirs and decode each progression.

    Mirrors :func:`iter_scenario_instance_progressions` policy without
    requiring an ``ArtifactLayout``: silently skip instance subdirs that
    have no ``<instance>_obj_log.json``; raise if the obj_log exists but
    the sibling manifest is missing.
    """
    progressions: list[InstanceProgression] = []
    for subdir in sorted(scenario_dir.iterdir()):
        if not subdir.is_dir():
            continue
        instance_name = subdir.name
        obj_log_path = subdir / f"{instance_name}_obj_log.json"
        if not obj_log_path.exists():
            logger.debug("Skipping %s: no obj_log_json", instance_name)
            continue
        manifest_path = subdir / f"{instance_name}_instance_result.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"obj_log_json present but manifest missing for "
                f"{instance_name}: {manifest_path}"
            )
        progressions.append(load_instance_progression(obj_log_path, manifest_path))
    return progressions


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    output_path: Path = (
        args.output if args.output is not None else _default_output_path()
    )

    scenario_dirs: list[Path] = [p.resolve() for p in args.scenario_dirs]
    for sd in scenario_dirs:
        if not sd.is_dir():
            print(f"scenario_dir does not exist: {sd}", file=sys.stderr)
            return 2

    if args.labels is not None:
        if len(args.labels) != len(scenario_dirs):
            print(
                f"--labels count ({len(args.labels)}) must match scenario_dirs "
                f"count ({len(scenario_dirs)})",
                file=sys.stderr,
            )
            return 2
        labels = list(args.labels)
    else:
        labels = [_default_label(sd) for sd in scenario_dirs]

    for csv_path, flag in (
        (args.hybrid_match_csv, "--hybrid-match-csv"),
        (args.bks_csv, "--bks-csv"),
        (args.instance_table_csv, "--instance-table-csv"),
    ):
        if not csv_path.exists():
            print(f"{flag} not found: {csv_path}", file=sys.stderr)
            return 2

    baseline_df = load_baseline_df(
        args.hybrid_match_csv, args.bks_csv, args.instance_table_csv
    )

    scenario_metrics: list[dict[str, Any]] = []
    for scenario_dir, label in zip(scenario_dirs, labels, strict=True):
        progressions = _load_scenario_progressions(scenario_dir)
        if not progressions:
            logger.warning(
                "Scenario %s has no instances with obj_log_json; skipping",
                scenario_dir,
            )
            continue
        endpoint_df = attach_rpdf_columns(build_endpoint_df(progressions), baseline_df)
        raw_progression_df = attach_rpdf_columns(
            build_raw_progression_df(progressions), baseline_df
        )
        scenario_metrics.append(
            {
                "label": label,
                "endpoint_df": endpoint_df,
                "raw_progression_df": raw_progression_df,
            }
        )

    if not scenario_metrics:
        print(
            "No scenario yielded usable chart data; nothing to write.", file=sys.stderr
        )
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok = export_multi_scenario_method_rpdf_comparison_html(
        scenario_metrics=scenario_metrics,
        output_path=output_path,
    )
    if not ok:
        print(
            "Multi-scenario flow comparison HTML produced no traces.", file=sys.stderr
        )
        return 1

    print(f"Wrote {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
