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
        --out analysis/csr_idle_modes_v4_20260702.csv \
        --workers 96          # parallelise across instances (full 1440 run)

``--workers`` only changes wall-clock, not the output: rows are re-sorted to a
deterministic (instanceName, mode, factor) order before writing, so a parallel
dump is byte-identical to a sequential one.

Columns: instanceName, jobCount, factor, mode,
         coarse_obj, coarse_wE, coarse_wT, recon_obj, recon_wE, recon_wT

``coarse_obj == coarse_wE + coarse_wT`` and ``recon_obj == recon_wE + recon_wT``
exactly (both are weighted earliness + weighted tardiness sums); the ``w``
prefix marks them as *weighted* sums to avoid confusion with raw E/T counts.
"""

from __future__ import annotations

import argparse
import csv
import logging
from multiprocessing import Pool
from pathlib import Path

import yaml

from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructOption,
    run_coarsen_solve_reconstruct,
)
from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

MODES = ("flooring", "ceiling", "lookahead")
FACTORS = (1, 2, 4, 8, 16)
_MODE_RANK = {m: i for i, m in enumerate(MODES)}

# Set once per process (main + Pool workers) so the worker fn stays picklable.
_SEED_DISPATCH = "v4"
_LOGGER = logging.getLogger("dump_csr_coarse_obj")


def _init_worker(seed_dispatch: str) -> None:
    global _SEED_DISPATCH
    _SEED_DISPATCH = seed_dispatch
    logging.basicConfig(level=logging.WARNING)


def _rows_for_instance(instance: FFcDDWParameters) -> list[dict]:
    """All (mode, factor) rows for one instance. Runs in a worker process."""
    rows: list[dict] = []
    for mode in MODES:
        for factor in FACTORS:
            option = CoarsenSolveReconstructOption(
                factor=factor,
                timelimit_sec=None,
                seed_dispatch=_SEED_DISPATCH,
                solve=False,
                idle_mode=mode,
            )
            trace = run_coarsen_solve_reconstruct(instance, option, _LOGGER)

            # Coarse (pre-uncoarsening) obj + E/T split: factor * C^c vs the
            # original due window, on the v4-selected seed schedule.
            c_e, c_t = compute_weighted_earliness_tardiness(
                trace.coarse_schedule, instance, time_factor=factor
            )
            coarse_obj = trace.metrics["dispatch_seed_coarsened_obj"]
            # Reconstructed (original-scale) obj + E/T split.
            r_e, r_t = compute_weighted_earliness_tardiness(
                trace.final_schedule, instance
            )
            rows.append(
                {
                    "instanceName": instance.name,
                    "jobCount": len(instance.job_id_list),
                    "factor": factor,
                    "mode": mode,
                    "coarse_obj": float(coarse_obj),
                    "coarse_wE": float(c_e),
                    "coarse_wT": float(c_t),
                    "recon_obj": float(trace.obj_value),
                    "recon_wE": float(r_e),
                    "recon_wT": float(r_t),
                }
            )
    return rows


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
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel worker processes across instances (default 1 = sequential).",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.WARNING)

    cfg = yaml.safe_load(args.config.read_text())
    loader = BenchmarkLoader(
        directory=Path(cfg["benchmark_dir"]),
        ins_index_source=Path(cfg["ins_index_source"]),
    )
    instances = loader.load_all(ins_index=cfg.get("ins_index"))
    instances.sort(key=lambda ins: ins.name)
    print(
        f"Loaded {len(instances)} instances from {cfg['benchmark_dir']} "
        f"(workers={args.workers})"
    )

    rows: list[dict] = []
    if args.workers <= 1:
        _init_worker(args.seed_dispatch)
        for k, ins in enumerate(instances, 1):
            rows.extend(_rows_for_instance(ins))
            if k % 50 == 0 or k == len(instances):
                print(f"  {k}/{len(instances)} instances done")
    else:
        with Pool(
            processes=args.workers,
            initializer=_init_worker,
            initargs=(args.seed_dispatch,),
        ) as pool:
            done = 0
            for inst_rows in pool.imap_unordered(_rows_for_instance, instances):
                rows.extend(inst_rows)
                done += 1
                if done % 50 == 0 or done == len(instances):
                    print(f"  {done}/{len(instances)} instances done")

    # Deterministic order regardless of worker scheduling.
    rows.sort(key=lambda r: (r["instanceName"], _MODE_RANK[r["mode"]], r["factor"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
