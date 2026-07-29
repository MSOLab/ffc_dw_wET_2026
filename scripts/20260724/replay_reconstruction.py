"""Offline CSR coarse-candidate reconstruction replay.

Re-runs every reconstruction mode against the same set of coarse candidate
schedules (dumped on disk via ``dump_csr_coarse: true``) to measure pure
reconstruction quality decoupled from solver trajectory divergence.

Usage::

    uv run python scripts/20260724/replay_reconstruction.py \\
        --run-dir output/20260724_active_but_last_semi_csr_ab/csr_k1_tl05_semi \\
        --modes semi_active active active_but_last_semi \\
        --out analysis/20260724_recon_replay/replay_et.csv

Output columns: insIndex, mode, winner_source, E, T, obj
"""

from __future__ import annotations

import argparse
import csv
import glob
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("replay_reconstruction")

BENCHMARK_DIR = Path("benchmarks/PRA2017/large")
HYBRID_MATCH_CSV = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")


def _load_instance_by_ins_index(ins_index: str) -> tuple[str]:
    import pandas as pd

    df = pd.read_csv(HYBRID_MATCH_CSV)
    row = df[df["insIndex"] == int(ins_index)]
    if row.empty:
        raise KeyError(f"insIndex {ins_index} not found in hybrid match CSV")
    filename = row.iloc[0]["ffc_ddw_sum_et_filename"]
    filepath = BENCHMARK_DIR / filename
    return filepath


def _load_ffcddw_instance(filepath: Path):
    from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

    instance_name = filepath.stem
    with open(filepath) as f:
        return FFcDDWParameters.from_pra_2017_data(instance_name, f)


def _find_coarse_candidate_jsons(run_dir: Path, ins_index: str) -> list[Path]:
    progress_dir = run_dir / "progress"
    if not progress_dir.is_dir():
        return []
    instance_subdirs = [
        d for d in progress_dir.iterdir() if d.is_dir() and d.name.startswith(ins_index)
    ]
    candidates: list[Path] = []
    for subdir in instance_subdirs:
        for cand_file in sorted(subdir.glob("*_csr_coarse_cand_*.json")):
            candidates.append(cand_file)
    return candidates


def _reconstruct_candidate(
    coarse_schedule,
    instance,
    mode: str,
):
    """Reconstruct a coarse candidate under the given mode and return (schedule, E, T, obj)."""
    from ffc_ddw_sum_et.solution.schedule_build import (
        reconstruct_active_coarse_schedule,
        reconstruct_active_except_last_coarse_schedule,
        reconstruct_coarse_schedule,
    )

    if mode == "active":
        final = reconstruct_active_coarse_schedule(coarse_schedule, instance)
    elif mode == "active_but_last_semi":
        final = reconstruct_active_except_last_coarse_schedule(
            coarse_schedule, instance
        )
    elif mode == "semi_active":
        factor = 1
        final = reconstruct_coarse_schedule(coarse_schedule, instance, factor)
    else:
        raise ValueError(f"Unknown reconstruct_mode: {mode!r}")

    from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

    sum_e, sum_t = compute_weighted_earliness_tardiness(final, instance)
    obj = float(sum_e + sum_t)
    return final, sum_e, sum_t, obj


def main():
    parser = argparse.ArgumentParser(
        description="Offline CSR coarse-candidate reconst replay"
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        action="append",
        help="Scenario run directories (repeatable)",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        default=["semi_active", "active", "active_but_last_semi"],
        help="Reconstruct modes to compare",
    )
    parser.add_argument(
        "--out",
        default="analysis/20260724_recon_replay/replay_et.csv",
        help="Output CSV path",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ins_indices: set[str] = set()
    run_ins_files: dict[str, dict[str, list[Path]]] = {}
    for run_dir_s in args.run_dir:
        run_dir = Path(run_dir_s)
        for instance_dir in glob.glob(str(run_dir / "progress" / "*")):
            d = Path(instance_dir)
            if not d.is_dir():
                continue
            ins_name = d.name
            ins_index = ins_name.split("_")[0]
            ins_indices.add(ins_index)
        run_ins_files[run_dir_s] = {}

    run_dir_label_map: dict[str, str] = {d: Path(d).name for d in args.run_dir}

    rows: list[dict] = []
    for ins_index in sorted(ins_indices):
        filepath = _load_instance_by_ins_index(ins_index)
        instance = _load_ffcddw_instance(filepath)
        logger.info("Processing insIndex=%s (%s)", ins_index, filepath.name)

        for run_dir_s in args.run_dir:
            run_dir = Path(run_dir_s)
            candidates = _find_coarse_candidate_jsons(run_dir, ins_index)
            if not candidates:
                logger.warning(
                    "  No coarse candidates in %s for insIndex=%s", run_dir_s, ins_index
                )
                continue
            for mode in args.modes:
                best_obj: float | None = None
                best_source: str | None = None
                best_e: float | None = None
                best_t: float | None = None
                for cand_path in candidates:
                    from ffc_ddw_sum_et.io.schedule_json import load_schedule_json

                    coarse_sch, _obj_val, _obj_bound = load_schedule_json(cand_path)
                    final, sum_e, sum_t, obj = _reconstruct_candidate(
                        coarse_sch, instance, mode
                    )
                    from ffc_ddw_sum_et.solution.schedule_build import (
                        validate_reconstructed_schedule,
                    )

                    validate_reconstructed_schedule(final, instance)
                    if best_obj is None or obj < best_obj:
                        best_obj = obj
                        source_part = (
                            cand_path.stem.split("_csr_coarse_cand_")[1]
                            if "_csr_coarse_cand_" in cand_path.stem
                            else cand_path.stem
                        )
                        best_source = source_part
                        best_e = float(sum_e)
                        best_t = float(sum_t)
                if best_obj is not None:
                    rows.append(
                        {
                            "insIndex": ins_index,
                            "run_dir": run_dir_label_map.get(run_dir_s, run_dir_s),
                            "mode": mode,
                            "winner_source": best_source,
                            "E": best_e,
                            "T": best_t,
                            "obj": best_obj,
                        }
                    )
                else:
                    rows.append(
                        {
                            "insIndex": ins_index,
                            "run_dir": run_dir_label_map.get(run_dir_s, run_dir_s),
                            "mode": mode,
                            "winner_source": None,
                            "E": None,
                            "T": None,
                            "obj": None,
                        }
                    )

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "insIndex",
                "run_dir",
                "mode",
                "winner_source",
                "E",
                "T",
                "obj",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    logger.info("Wrote %d rows to %s", len(rows), out_path)
    print(f"Done — {len(rows)} rows written to {out_path}")


if __name__ == "__main__":
    main()
