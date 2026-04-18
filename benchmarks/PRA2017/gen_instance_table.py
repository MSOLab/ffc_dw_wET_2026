"""Generate pra2017_instance_table.csv from pra2017_hybrid_match.csv and best_seq_large/."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import NamedTuple

try:
    from .metadata import BEST_SEQ_DIR, COLHEAD_INS_INDEX, COLHEAD_TOTAL_MC_COUNT, MATCH_CSV, OUT_CSV
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from metadata import (
        BEST_SEQ_DIR,
        COLHEAD_INS_INDEX,
        COLHEAD_TOTAL_MC_COUNT,
        MATCH_CSV,
        OUT_CSV,
    )

_NAME_RE = re.compile(r"Instance_(\d+)_(\d+)_(\d+)_([\d,]+)_([\d,]+)_(\d+)_Rep\d+\.txt")


class InstanceTableRow(NamedTuple):
    ins_index: str
    n: int
    c: int
    total_mc_count: int
    T: float
    R: float
    W: int
    BKS: int

    @staticmethod
    def column_headers() -> list[str]:
        col_headers = list(InstanceTableRow._fields)
        # replace ins_index with insIndex
        col_headers[0] = COLHEAD_INS_INDEX
        # replace total_mc_count with totalMcCount
        col_headers[3] = COLHEAD_TOTAL_MC_COUNT
        return col_headers


def _parse_filename(name: str) -> tuple[int, int, int, float, float, int]:
    """Return (n, c, total_mc_count, T, R, W)."""
    m = _NAME_RE.match(name)
    if not m:
        raise ValueError(f"Unexpected filename: {name!r}")
    n = int(m.group(1))
    c = int(m.group(2))
    mc = int(m.group(3))
    T = float(m.group(4).replace(",", "."))
    R = float(m.group(5).replace(",", "."))
    W = int(m.group(6))
    return n, c, mc * c, T, R, W


def _read_bks(path: Path) -> int:
    parts = [p.strip() for p in path.read_text().splitlines()[0].split(",")]
    return int(parts[3])


def main() -> None:
    with MATCH_CSV.open(newline="") as f:
        match_rows = list(csv.DictReader(f))

    rows = []
    for row in match_rows:
        ins_index = f"{int(row[COLHEAD_INS_INDEX]):04d}"
        name = row["ffc_ddw_sum_et_filename"]
        n, c, total_mc_count, T, R, W = _parse_filename(name)
        bks = _read_bks(BEST_SEQ_DIR / name)
        rows.append(InstanceTableRow(ins_index, n, c, total_mc_count, T, R, W, bks))

    with OUT_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(InstanceTableRow.column_headers())
        writer.writerows(rows)

    print(f"Written {len(rows)} rows → {OUT_CSV}")


if __name__ == "__main__":
    main()
