"""Dump the CSR pre-uncoarsening (coarse) and reconstructed objectives per
(instance, idle_mode, factor), re-derived deterministically via the production
seed-only path (``run_coarsen_solve_reconstruct`` with ``solve=False``).

The per-instance run artifacts only persist the *reconstructed* ``obj_value``;
the coarse objective (``dispatch_seed_coarsened_obj`` = the v4-selected seed's
weighted-ET on the coarse grid, ``factor * C^c`` vs the original due window) is
not written to disk. Since the seed-only pipeline is fully deterministic we
simply re-run it here and record both objective layers, plus their E/T splits.

Usage::

    uv run python scripts/dump_csr_coarse_obj.py \
        --config metadata/20260702/csr_idle_modes_v4_config.yaml \
        --out analysis/csr_idle_modes_v4_20260702.csv

Columns: instanceName, jobCount, factor, mode,
         coarse_obj, coarse_E, coarse_T, recon_obj, recon_E, recon_T
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

import yaml

from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructOption,
    run_coarsen_solve_reconstruct,
)
from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

MODES = ("flooring", "ceiling", "lookahead")
FACTORS = (1, 2, 4, 8, 16)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=Path("metadata/20260702/csr_idle_modes_v4_config.yaml"),
        help="Experiment config (benchmark_dir / ins_index_source / ins_index).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("analysis/csr_idle_modes_v4_20260702.csv"),
        help="Output tidy CSV path.",
    )
    p.add_argument(
        "--seed-dispatch",
        default="v4",
        help="Seed dispatch strategy (default: v4, matching the config).",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger("dump_csr_coarse_obj")

    cfg = yaml.safe_load(args.config.read_text())
    loader = BenchmarkLoader(
        directory=Path(cfg["benchmark_dir"]),
        ins_index_source=Path(cfg["ins_index_source"]),
    )
    instances = loader.load_all(ins_index=cfg.get("ins_index"))
    instances.sort(key=lambda ins: ins.name)
    print(f"Loaded {len(instances)} instances from {cfg['benchmark_dir']}")

    rows: list[dict] = []
    for ins in instances:
        for mode in MODES:
            for factor in FACTORS:
                option = CoarsenSolveReconstructOption(
                    factor=factor,
                    timelimit_sec=None,
                    seed_dispatch=args.seed_dispatch,
                    solve=False,
                    idle_mode=mode,
                )
                trace = run_coarsen_solve_reconstruct(ins, option, logger)

                # Coarse (pre-uncoarsening) obj + E/T split: factor * C^c vs the
                # original due window, evaluated on the v4-selected seed schedule.
                c_e, c_t = compute_weighted_earliness_tardiness(
                    trace.coarse_schedule, ins, time_factor=factor
                )
                coarse_obj = trace.metrics["dispatch_seed_coarsened_obj"]
                # Reconstructed (original-scale) obj + E/T split.
                r_e, r_t = compute_weighted_earliness_tardiness(
                    trace.final_schedule, ins
                )

                rows.append(
                    {
                        "instanceName": ins.name,
                        "jobCount": len(ins.job_id_list),
                        "factor": factor,
                        "mode": mode,
                        "coarse_obj": float(coarse_obj),
                        "coarse_E": float(c_e),
                        "coarse_T": float(c_t),
                        "recon_obj": float(trace.obj_value),
                        "recon_E": float(r_e),
                        "recon_T": float(r_t),
                    }
                )
        print(f"  done: {ins.name}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
