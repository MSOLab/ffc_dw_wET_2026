"""Compute BKS_T, BKS_F, BKS_calc for all 1440 PRA2017 instances."""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "src"))

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters  # noqa: E402
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule  # noqa: E402
from ffc_ddw_sum_et.solution.objectives import (  # noqa: E402
    compute_weighted_earliness_tardiness,
)

LARGE_DIR = ROOT / "large"
BEST_SEQ_DIR = ROOT / "best_seq_large"
MATCH_CSV = ROOT / "pra2017_hybrid_match.csv"
INSTANCE_TABLE = ROOT / "pra2017_instance_table.csv"
OUTPUT = ROOT / "pra2017_bks_table.csv"

INSTANCE_COLS = ["n", "c", "totalMcCount", "T", "R", "W", "BKS_data"]


def parse_best_seq(path: Path, n_jobs: int) -> list[list[str]]:
    """Return per-stage job sequences with correct digit padding."""
    digit = max(2, len(str(n_jobs - 1)))
    lines = path.read_text().splitlines()
    sequences: list[list[str]] = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        sequences.append([f"j{int(x):0{digit}d}" for x in line.split(",") if x.strip()])
    return sequences


def decode_instance(
    instance: FFcDDWParameters,
    sequences: list[list[str]],
    force: bool,
) -> int:
    """Decode best_seq into a schedule and return the objective value."""
    stage_ids = list(instance.stage_id_list)
    stage_2_p = instance.stage_2_job_2_p_map

    schedule = FFcSchedule(
        jobs=list(instance.job_id_list),
        stages=list(instance.stage_id_list),
        machines_per_stage={
            sid: list(mids) for sid, mids in instance.stage_2_machines_map.items()
        },
    )

    for stage_id in stage_ids:
        schedule.dispatch_stage_by_jobs(
            stage_id,
            sequences[stage_ids.index(stage_id)],
            stage_2_p[stage_id],
            force_job_id_seq_as_priority=force,
        )

    schedule.insert_idle_time(
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
    )

    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    return sum_e + sum_t


def main() -> None:
    # Load instance table: insIndex -> {n, c, totalMcCount, T, R, W, BKS}
    ins_table: dict[str, dict] = {}
    with INSTANCE_TABLE.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = {c: row[c].strip() for c in INSTANCE_COLS if c != "BKS_data"}
            d["BKS_data"] = row["BKS"].strip()
            ins_table[row["insIndex"].strip()] = d

    # Build insIndex -> filename map from the CSV
    ins_index_map: dict[str, str] = {}
    with MATCH_CSV.open(newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            ins_idx = row[0].strip()
            filename = row[1].strip().strip('"')
            # CSV has .txt, stem doesn't — strip it for matching
            ins_index_map[filename.removesuffix(".txt")] = ins_idx

    filenames = sorted(LARGE_DIR.iterdir(), key=lambda p: p.name)
    print(f"Processing {len(filenames)} instances ...")

    results: list[dict] = []
    start = time.time()
    skips: list[str] = []

    for i, fname in enumerate(filenames):
        stem = fname.stem
        best_seq_path = BEST_SEQ_DIR / fname.name
        ins_index = ins_index_map.get(stem, "")

        # Load instance first (need n_jobs for best_seq parsing)
        try:
            with fname.open() as f:
                instance = FFcDDWParameters.from_pra_2017_data(stem, f)
        except Exception as exc:
            msg = f"{stem}: instance load failed: {exc}"
            skips.append(msg)
            print(f"  SKIP {stem} (load)", file=sys.stderr)
            continue

        n_jobs = len(instance.job_id_list)

        # Parse best_seq with correct digit padding
        try:
            sequences = parse_best_seq(best_seq_path, n_jobs)
        except Exception as exc:
            msg = f"{stem}: best_seq parse failed: {exc}"
            skips.append(msg)
            print(f"  SKIP {stem}", file=sys.stderr)
            continue

        # Validate: best_seq job count must match instance job count
        seq_jobs = len(sequences[0]) if sequences else 0
        if seq_jobs != n_jobs:
            msg = f"{stem}: job count mismatch instance={n_jobs} seq={seq_jobs}"
            skips.append(msg)
            print(
                f"  SKIP {stem} (job mismatch {n_jobs} vs {seq_jobs})", file=sys.stderr
            )
            continue

        try:
            obj_t = decode_instance(instance, sequences, force=True)
            obj_f = decode_instance(instance, sequences, force=False)
            obj_calc = min(obj_t, obj_f)
        except Exception as exc:
            msg = f"{stem}: decode failed: {exc}"
            skips.append(msg)
            print(f"  SKIP {stem} (decode): {exc}", file=sys.stderr)
            continue

        row_data = {
            "insIndex": ins_index,
            "BKS_T": obj_t,
            "BKS_F": obj_f,
            "BKS_calc": obj_calc,
        }
        # Merge instance table columns
        inst = ins_table.get(ins_index, {})
        for c in INSTANCE_COLS:
            row_data[c] = inst.get(c, "")
        results.append(row_data)

        if (i + 1) % 100 == 0:
            elapsed = time.time() - start
            print(f"  [{i + 1}/{len(filenames)}] done in {elapsed:.1f}s")

    # Sort by insIndex ascending
    results.sort(key=lambda r: int(r["insIndex"]) if r["insIndex"].isdigit() else 0)

    # Column order: insIndex, n, c, totalMcCount, T, R, W, BKS_data, BKS_calc, BKS_T, BKS_F
    fieldnames = [
        "insIndex",
        "n",
        "c",
        "totalMcCount",
        "T",
        "R",
        "W",
        "BKS_data",
        "BKS_calc",
        "BKS_T",
        "BKS_F",
    ]

    # Write output CSV
    with OUTPUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. {len(results)}/{len(filenames)} instances processed.")
    print(f"  Skipped: {len(skips)}")
    if skips:
        print("\nSkipped files:")
        for s in skips:
            print(f"  {s}")
    print(f"\nOutput: {OUTPUT}")


if __name__ == "__main__":
    main()
