"""Build CSR CP gap comparison report (CSV + PivotTable.js dashboard).

Usage::

    uv run python scripts/build_cp_gap_report.py <run_dir> [options]

Reads all ``*_csr_cp_trajectory.json`` files under the run directory,
computes final CP gap (lb_gap, solver_gap) from coarsened UB/LB endpoints,
joins with instance metadata, and writes:

  * ``<run_id>_cp_gap_comparison.csv``
  * ``<run_id>_cp_gap_dashboard.html``

By default only ``_v3`` scenarios are included. Use ``--init all`` to
include ``_mixed`` scenarios as well.
"""

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_BENCHMARKS_DIR = _REPO_ROOT / "benchmarks" / "PRA2017"

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Run directory (e.g. output/20260625/20260625T032109_514704)",
    )
    parser.add_argument(
        "--hybrid-match",
        type=Path,
        default=_DEFAULT_BENCHMARKS_DIR / "pra2017_hybrid_match.csv",
        help="CSV mapping filename -> insIndex (default: PRA2017)",
    )
    parser.add_argument(
        "--bks-table",
        type=Path,
        default=_DEFAULT_BENCHMARKS_DIR / "pra2017_bks_table.csv",
        help="CSV with BKS_data per insIndex (default: PRA2017)",
    )
    parser.add_argument(
        "--init",
        type=str,
        default="v3",
        choices=["v3", "all"],
        help='Filter scenarios by init: "v3" (default) or "all"',
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    run_root = args.run_dir.resolve()
    if not run_root.is_dir():
        print(f"run_dir does not exist: {run_root}", file=sys.stderr)
        return 2

    init_filter = None if args.init == "all" else "v3"

    # Extract run_id from the directory name (e.g. "20260625T032109_514704")
    run_id = run_root.name

    from routix.io import RunRoot

    from ffc_ddw_sum_et.orchestration.artifact_layout import init_ffc_artifact_layout
    from ffc_ddw_sum_et.orchestration.post_run_pivot import write_cp_gap_artifacts

    layout = init_ffc_artifact_layout(RunRoot(run_root, run_id))

    write_cp_gap_artifacts(
        run_root,
        layout,
        args.hybrid_match,
        args.bks_table,
        init_filter=init_filter,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
