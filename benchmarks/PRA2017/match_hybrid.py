"""Match PRA2017 large instances to hybridflowshop/resources/pra file numbers."""

from __future__ import annotations

import csv
from pathlib import Path

try:
    from .metadata import COLHEAD_INS_INDEX
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from metadata import COLHEAD_INS_INDEX


def parse_pra2017_times(path: Path) -> tuple[tuple[int, ...], ...]:
    lines = path.read_text().splitlines()
    parts = lines[1].split()
    n, s = int(parts[0]), int(parts[2])
    times = []
    for i in range(2, 2 + n):
        row = list(map(int, lines[i].split()))
        times.append(tuple(row[j * 2 + 1] for j in range(s)))
    return tuple(times)


def parse_hybrid_times(path: Path) -> tuple[tuple[int, ...], ...]:
    lines = path.read_text().splitlines()
    n = int(lines[0])
    return tuple(tuple(map(int, lines[3 + i].split())) for i in range(n))


def build_hybrid_index(hybrid_dir: Path) -> dict[tuple, int]:
    """Return {processing_times_matrix: file_number} for files 1–1440."""
    index: dict[tuple, int] = {}
    for i in range(1, 1441):
        key = parse_hybrid_times(hybrid_dir / f"{i}.txt")
        index[key] = i
    return index


def match_pra2017_to_hybrid(
    pra_path: Path, hybrid_index: dict[tuple, int]
) -> int | None:
    return hybrid_index.get(parse_pra2017_times(pra_path))


if __name__ == "__main__":
    here = Path(__file__).resolve().parent

    project_root = here.parent.parent
    hybrid_dir = Path.home() / "code/hybridflowshop/resources/pra"
    pra_dir = project_root / "benchmarks/PRA2017/large"
    out_path = here / "pra2017_hybrid_match.csv"

    print("Building hybridflowshop index…")
    index = build_hybrid_index(hybrid_dir)

    rows: list[tuple[str, str, str]] = []
    unmatched: list[str] = []
    for pra_file in sorted(pra_dir.iterdir()):
        if pra_file.suffix != ".txt":
            continue
        num = match_pra2017_to_hybrid(pra_file, index)
        if num is None:
            unmatched.append(pra_file.name)
        else:
            rows.append((f"{num - 1:04d}", pra_file.name, f"{num}.txt"))

    ins_indices = [r[0] for r in rows]
    if len(ins_indices) != len(set(ins_indices)):
        import warnings

        warnings.warn(f"ins_index values are NOT unique among {len(rows)} matched rows")
    else:
        rows.sort(key=lambda r: r[0])

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [COLHEAD_INS_INDEX, "ffc_ddw_sum_et_filename", "hybridflowshop_filename"]
        )
        writer.writerows(rows)

    print(f"Written {len(rows)} matches → {out_path}")
    if unmatched:
        print(f"UNMATCHED ({len(unmatched)}): {unmatched[:5]}")
