#!/usr/bin/env python3
"""
Setup utilities for PRA2017 Large benchmark instances.

Usage:
  python setup_large.py create   # Generate InstanceNameLarge.txt
  python setup_large.py split    # Split bestSeq_Large.txt into best_seq_large/
  python setup_large.py all      # Both steps in order
"""

import re
import sys

try:
    from .metadata import COLHEAD_INS_INDEX, HERE
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from metadata import COLHEAD_INS_INDEX, HERE

LARGE_DIR = HERE / "large"
BEST_SEQ_FILE = HERE / "bestSeq_Large.txt"
INSTANCE_NAME_FILE = HERE / "InstanceNameLarge.txt"
OUTPUT_DIR = HERE / "best_seq_large"

_HEADER_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,")
_NAME_RE = re.compile(r"^Instance_(\d+)_(\d+)_")


def _parse_header(line: str):
    m = _HEADER_RE.match(line)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def create_instance_names() -> None:
    # ASSUMPTION: InstanceNameLarge.txt was absent from the repo, so we reconstruct
    # its ordering by sorting large/*.txt lexicographically (Python's default sorted()).
    # This is consistent with bestSeq_Large.txt: index 0 has (100 jobs, 10 stages),
    # and "Instance_100_10_..." is lexicographically first; index 1439 has (50 jobs,
    # 5 stages), and "Instance_50_..." is last ("5" > "2" > "1" as characters).
    # The loop below cross-validates every (num_jobs, num_stages) pair against the
    # header in bestSeq_Large.txt — if any mismatch occurs the assumption is wrong.
    names = sorted(p.name for p in LARGE_DIR.glob("*.txt"))
    if len(names) != 1440:
        raise RuntimeError(f"Expected 1440 files in large/, found {len(names)}")

    # Build expected (num_jobs, num_stages) from filenames
    file_params = []
    for name in names:
        m = _NAME_RE.match(name)
        if not m:
            raise RuntimeError(f"Unexpected filename: {name}")
        file_params.append((int(m.group(1)), int(m.group(2))))

    # Verify against bestSeq_Large.txt
    with BEST_SEQ_FILE.open() as f:
        for expected_index in range(1440):
            header = f.readline()
            if not header:
                raise RuntimeError(
                    f"bestSeq_Large.txt ended early at index {expected_index}"
                )
            parsed = _parse_header(header)
            if parsed is None:
                raise RuntimeError(
                    f"Unrecognised header at index {expected_index}: {header!r}"
                )
            seq_index, num_jobs, num_stages = parsed
            if seq_index != expected_index:
                raise RuntimeError(
                    f"Index mismatch: expected {expected_index}, got {seq_index}"
                )
            exp_jobs, exp_stages = file_params[seq_index]
            if (num_jobs, num_stages) != (exp_jobs, exp_stages):
                raise RuntimeError(
                    f"Parameter mismatch at index {seq_index}: "
                    f"bestSeq has ({num_jobs}, {num_stages}), "
                    f"filename '{names[seq_index]}' implies ({exp_jobs}, {exp_stages})"
                )
            for _ in range(num_stages):
                f.readline()

    INSTANCE_NAME_FILE.write_text("\n".join(names) + "\n")
    print(f"Verified 1440 entries. Written {INSTANCE_NAME_FILE.name}")


def _load_ins_index_map() -> dict[str, int]:
    import csv

    csv_path = HERE / "pra2017_hybrid_match.csv"
    if not csv_path.exists():
        raise RuntimeError(
            "pra2017_hybrid_match.csv not found — run match_hybrid.py first"
        )
    with csv_path.open(newline="") as f:
        return {
            row["ffc_ddw_sum_et_filename"]: int(row[COLHEAD_INS_INDEX])
            for row in csv.DictReader(f)
        }


def split_best_seq() -> None:
    if not INSTANCE_NAME_FILE.exists():
        raise RuntimeError("InstanceNameLarge.txt not found — run 'create' first")

    names = INSTANCE_NAME_FILE.read_text().splitlines()
    if len(names) != 1440:
        raise RuntimeError(
            f"InstanceNameLarge.txt has {len(names)} lines, expected 1440"
        )

    ins_index_map = _load_ins_index_map()
    OUTPUT_DIR.mkdir(exist_ok=True)

    count = 0
    with BEST_SEQ_FILE.open() as f:
        while True:
            header = f.readline()
            if not header:
                break
            parsed = _parse_header(header)
            if parsed is None:
                raise RuntimeError(f"Unrecognised header: {header!r}")
            index, _, num_stages = parsed
            seq_lines = [f.readline() for _ in range(num_stages)]
            filename = names[index]
            ins_index = ins_index_map[filename]
            m = _HEADER_RE.match(header)
            corrected_header = (
                header[: m.start(1)] + str(ins_index) + header[m.end(1) :]
            )
            out_path = OUTPUT_DIR / filename
            out_path.write_text(corrected_header + "".join(seq_lines))
            count += 1

    print(f"Split {count} solution files to {OUTPUT_DIR.name}/")


# TODO: verify subcommand
# Purpose: confirm InstanceNameLarge.txt ordering is correct by evaluating
# stored sequences against instance data and comparing to the bestSeq objective.
#
# Algorithm:
#   1. Read best_seq_large/<name>.txt → parse header (index, obj_value) + sequences
#   2. Load large/<name>.txt via FFcDueDateWindowParameters.from_pra_2017_data()
#   3. Map integer job indices from bestSeq → string job IDs (f"j{k:0{d}d}")
#      where d = len(str(num_jobs - 1))
#   4. Run FAMDispatcher with FAMOption(job_sequence=first_stage_seq_as_job_ids)
#   5. Compare AlgRecord.result.obj_value with stored obj_value
#   6. Mismatch → ordering assumption is wrong for that instance
#
# Blocker: FAMDispatcher (ffc_ddw_sum_et.algorithm.fam) not yet implemented.
# Add `python setup_large.py verify [--sample N]` when algorithm is ready.


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "create":
        create_instance_names()
    elif cmd == "split":
        split_best_seq()
    elif cmd == "all":
        create_instance_names()
        split_best_seq()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
