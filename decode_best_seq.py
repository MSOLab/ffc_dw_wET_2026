"""Decode a best_seq file and print the schedule table with TWET-DDW objective."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule, validate_schedule
from ffc_ddw_sum_et.solution.objectives import compute_window_et


INSTANCE_FILE = (
    ROOT
    / "benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
)
BEST_SEQ_FILE = (
    ROOT
    / "benchmarks/PRA2017/best_seq_large/Instance_50_5_3_0,2_0,2_10_Rep0.txt"
)


def parse_best_seq(path: Path) -> list[list[str]]:
    """Return per-stage job-id sequences, converting 0-based int to 'jNN' ids."""
    lines = path.read_text().splitlines()
    sequences: list[list[str]] = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        sequences.append([f"j{int(x):02d}" for x in line.split(",") if x.strip()])
    return sequences


def main() -> None:
    with INSTANCE_FILE.open() as f:
        instance = FFcDDWParameters.from_pra_2017_data(INSTANCE_FILE.stem, f)

    sequences = parse_best_seq(BEST_SEQ_FILE)
    assert len(sequences) == len(instance.stage_id_list), (
        f"Expected {len(instance.stage_id_list)} stage sequences, "
        f"got {len(sequences)}"
    )

    schedule = FFcSchedule(
        jobs=list(instance.job_id_list),
        stages=list(instance.stage_id_list),
        machines_per_stage={
            sid: list(mids)
            for sid, mids in instance.stage_2_machines_map.items()
        },
    )

    stage_ids = list(instance.stage_id_list)
    stage_2_p = instance.stage_2_job_2_p_map

    for stage_idx, stage_id in enumerate(stage_ids):
        schedule.dispatch_stage_by_jobs(
            stage_id,
            sequences[stage_idx],
            stage_2_p[stage_id],
            force_job_id_seq_as_priority=True,
        )

    # TEMP: disable idle time insertion to check base objective
    # schedule.insert_idle_time(
    #     instance.job_2_due_window_map,
    #     instance.job_2_ewt_map,
    #     instance.job_2_twt_map,
    # )

    validate_schedule(schedule, stage_2_p)

    sum_e, sum_t = compute_window_et(schedule, instance)
    obj = sum_e + sum_t

    last_stage = stage_ids[-1]
    header = (
        f"{'Job':>4} | "
        + " | ".join(f"C{i}" for i in range(len(stage_ids)))
        + f" |  d-  |  d+  |  E_j |  T_j | w- | w+ | Penalty"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    total_penalty = 0
    for job_id in sorted(instance.job_id_list, key=lambda j: int(j[1:])):
        completions = [
            schedule.get_job_end_time(sid, job_id) for sid in stage_ids
        ]
        c_last = completions[-1]
        d_lo, d_hi = instance.job_2_due_window_map[job_id]
        ewt = instance.job_2_ewt_map.get(job_id, 1)
        twt = instance.job_2_twt_map.get(job_id, 1)
        e_j = max(d_lo - c_last, 0)
        t_j = max(c_last - d_hi, 0)
        penalty = ewt * e_j + twt * t_j
        total_penalty += penalty

        c_str = " | ".join(f"{c:4d}" for c in completions)
        print(
            f"{job_id:>4} | {c_str} | {d_lo:5d} | {d_hi:5d} | "
            f"{e_j:4d} | {t_j:4d} | {ewt:2d} | {twt:2d} | {penalty:7d}"
        )

    print(sep)
    print(f"{'Total':>4}   {'':>{4*len(stage_ids) + 3*(len(stage_ids)-1)}}   "
          f"                        OBJ = {obj}  (E={sum_e}, T={sum_t})")
    known_best = 8952
    print(f"Known best from file header: {known_best}")


if __name__ == "__main__":
    main()
