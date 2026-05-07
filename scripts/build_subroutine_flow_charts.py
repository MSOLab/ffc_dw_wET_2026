"""Rebuild the two subroutine-flow HTML artifacts for an existing run dir.

Reads each scenario's ``<instance>_obj_log.json`` + manifest off disk and
emits:

* ``<run_dir>/<scenario>/summary_method_rpdf_and_norm_time_scatter.html``
* ``<run_dir>/<run_id>_multi_scenario_subroutine_flow_comparison.html``

Usage::

    uv run python scripts/build_subroutine_flow_charts.py <run_dir>

By default the BKS / hybrid-match CSVs come from
``benchmarks/PRA2017/``. Override with ``--bks-csv`` / ``--hybrid-match-csv``
when running on a different benchmark family. Calls into the same writer
that the live reporting pipeline uses, so output is identical.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from routix.io import RunRoot

from ffc_ddw_sum_et.orchestration.artifact_layout import init_ffc_artifact_layout
from ffc_ddw_sum_et.report import write_post_run_subroutine_chart_artifacts

_DEFAULT_BENCHMARKS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "PRA2017"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Run output directory (e.g. output/20260507_debug/20260507T165341_445350)",
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
        help="Enable INFO-level logging from the writer",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    run_dir: Path = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"run_dir does not exist: {run_dir}", file=sys.stderr)
        return 2

    rr = RunRoot(path=run_dir, run_id=run_dir.name)
    layout = init_ffc_artifact_layout(rr)

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=args.hybrid_match_csv,
        bks_table_csv=args.bks_csv,
        instance_table_csv=args.instance_table_csv,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
