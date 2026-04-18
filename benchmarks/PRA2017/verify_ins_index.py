"""Verify that ins_index in pra2017_hybrid_match.csv matches the first value
in the corresponding best_seq_large file.
"""

from __future__ import annotations

import csv
from pathlib import Path

try:
    from .metadata import BEST_SEQ_DIR, COLHEAD_INS_INDEX, MATCH_CSV
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from metadata import BEST_SEQ_DIR, COLHEAD_INS_INDEX, MATCH_CSV


def first_value(path: Path) -> int:
    """Return the first integer on the first line of a best_seq_large file."""
    return int(path.read_text().splitlines()[0].split(",")[0])


def main() -> None:
    mismatches: list[tuple[str, int, int]] = []  # (filename, csv_ins_index, file_value)
    missing: list[str] = []
    matches = 0

    with open(MATCH_CSV, newline="") as f:
        for row in csv.DictReader(f):
            name = row["ffc_ddw_sum_et_filename"]
            csv_idx = int(row[COLHEAD_INS_INDEX])
            seq_file = BEST_SEQ_DIR / name
            if not seq_file.exists():
                missing.append(name)
                continue
            file_val = first_value(seq_file)
            if csv_idx == file_val:
                matches += 1
            else:
                mismatches.append((name, csv_idx, file_val))

    total = matches + len(mismatches) + len(missing)
    print(f"Total rows checked : {total}")
    print(f"Matches            : {matches}")
    print(f"Mismatches         : {len(mismatches)}")
    print(f"Missing seq files  : {len(missing)}")

    if mismatches:
        print("\nFirst 5 mismatches (filename, csv_ins_index, best_seq_first_value):")
        for name, csv_idx, file_val in mismatches[:5]:
            print(f"  {name!r:60s}  csv={csv_idx}  seq={file_val}")

    if missing:
        print(f"\nMissing (first 5): {missing[:5]}")


if __name__ == "__main__":
    main()
