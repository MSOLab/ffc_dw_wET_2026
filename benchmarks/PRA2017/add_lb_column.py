"""Compute MCF-based lower bounds for PRA2017 instances and append an LB column.

Reads every PRA2017 instance under ``large/``, runs
``FFcDDWSubroutineController.run_mcf_lb`` on each, and rewrites
``pra2017_instance_table.csv`` with a new ``LB`` column next to ``BKS``.

Idempotent: overwrites any pre-existing ``LB`` column.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

HERE = Path(__file__).parent
TABLE = HERE / "pra2017_instance_table.csv"
MATCH = HERE / "pra2017_hybrid_match.csv"
INSTANCE_DIR = HERE / "large"


def compute_lb(instance: FFcDDWParameters) -> int:
    # timelimit is unused here: run_mcf_lb_4() is called directly, not via
    # ctrlr.run()'s subroutine loop. The value is only a formal argument
    # required by the controller constructor.
    ctrlr = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[],
        stopping_criteria={"timelimit": 1.0},
    )
    report = ctrlr.run_mcf_lb_4()
    assert report.obj_bound is not None
    return int(report.obj_bound)


def _load_stem_to_index(match_csv: Path) -> dict[str, int]:
    with match_csv.open(newline="") as f:
        return {
            Path(row["ffc_ddw_sum_et_filename"]).stem: int(row["insIndex"])
            for row in csv.DictReader(f)
        }


def _rewrite_table(
    table_csv: Path, lb_by_index: dict[int, int], log: logging.Logger
) -> list[dict[str, str]]:
    with table_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        original_fields = list(reader.fieldnames or [])

    fieldnames = [c for c in original_fields if c != "LB"] + ["LB"]

    for row in rows:
        idx = int(row["insIndex"])
        if idx in lb_by_index:
            row["LB"] = str(lb_by_index[idx])
        else:
            log.warning("no LB for insIndex=%04d, writing empty", idx)
            row["LB"] = ""

    with table_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    return rows


def _check_lb_le_bks(rows: list[dict[str, str]], log: logging.Logger) -> None:
    violations = [
        (row["insIndex"], row["BKS"], row["LB"])
        for row in rows
        if row["LB"] and int(row["LB"]) > int(row["BKS"])
    ]
    if violations:
        log.error("LB > BKS for %d rows: first 5 = %s", len(violations), violations[:5])
        raise RuntimeError("LB exceeds BKS — review MCF formulation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ins-index",
        type=int,
        nargs="*",
        default=None,
        help="Restrict to these insIndex values (for dry-runs).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    log = logging.getLogger(__name__)

    stem_to_index = _load_stem_to_index(MATCH)

    loader = BenchmarkLoader(directory=INSTANCE_DIR, ins_index_source=MATCH)
    instances = loader.load_all(ins_index=args.ins_index)
    log.info("loaded %d instances", len(instances))

    lb_by_index: dict[int, int] = {}
    for i, inst in enumerate(instances, 1):
        idx = stem_to_index.get(inst.name)
        if idx is None:
            log.warning("no insIndex for %s, skipping", inst.name)
            continue
        lb = compute_lb(inst)
        lb_by_index[idx] = lb
        if i % 50 == 0 or i == len(instances):
            log.info(
                "%d / %d done (latest: insIndex=%04d LB=%d)",
                i,
                len(instances),
                idx,
                lb,
            )

    rows = _rewrite_table(TABLE, lb_by_index, log)
    _check_lb_le_bks(rows, log)
    log.info("done: %d rows updated, all LB ≤ BKS", len(lb_by_index))


if __name__ == "__main__":
    main()
