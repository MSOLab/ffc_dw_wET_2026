"""Select the representative instance grid for the SW-CP TL profiling sweep.

Selection rule (decided 2026-07-05, see
`plans/20260705/sw_cp_tl_policy_investigation.md` section 6):

- one instance per ``(n, c, T, R, m, W)`` cell, ``rep == 0`` only
  (rep0 uniquely identifies one instance per cell, so no runtime tiebreak);
- drop cells whose rep0 is *optimal* (``obj_value == obj_bound``): the LB is
  the loose MCF global bound, so this is only a binary "trivially solved"
  exclude, NOT a gap-based ranking.

Optimality is read from an existing full-grid run's per-instance
``<instance>_instance_result.yaml`` (fields ``obj_value`` = UB,
``obj_bound`` = LB); see AGENTS.md "Optimality-judgment field".

Outputs a per-cell CSV and prints a YAML-ready ``ins_index`` block (plus a
per-``n`` staging breakdown) to paste into
``metadata/20260705/sw_cp_tl_profile.yaml``. It does NOT edit the config or
run git; that is left to the user's manual review.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import yaml

# Instance_{n}_{c}_{m}_{T}_{R}_{W}_Rep{rep}  (T, R use a comma decimal separator)
INSTANCE_RE = re.compile(
    r"^Instance_(?P<n>\d+)_(?P<c>\d+)_(?P<m>\d+)_"
    r"(?P<T>[\d,]+)_(?P<R>[\d,]+)_(?P<W>\d+)_Rep(?P<rep>\d+)$"
)

DEFAULT_RESULT_RUN = Path("output/20260704/20260704T164349_114896/s0_c5_base")
DEFAULT_MATCH_CSV = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")
DEFAULT_OUT_CSV = Path(
    "analysis/20260705_sw_cp_tl_profile/representative_instances.csv"
)

OPT_TOL = 1e-6


def parse_instance_name(name: str) -> dict | None:
    """Return the cell fields for an instance stem, or None if it does not match."""
    match = INSTANCE_RE.match(name)
    if match is None:
        return None
    g = match.groupdict()
    return {
        "n": int(g["n"]),
        "c": int(g["c"]),
        "m": int(g["m"]),
        "T": float(g["T"].replace(",", ".")),
        "R": float(g["R"].replace(",", ".")),
        "W": int(g["W"]),
        "rep": int(g["rep"]),
    }


def load_index_map(match_csv: Path) -> dict[str, int]:
    """Build {instance_stem -> insIndex(int)} from pra2017_hybrid_match.csv."""
    index_of: dict[str, int] = {}
    with match_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            stem = row["ffc_ddw_sum_et_filename"].removesuffix(".txt")
            index_of[stem] = int(row["insIndex"])
    return index_of


def iter_rep0_results(run_dir: Path, rep: int):
    """Yield (instance_name, obj_value, obj_bound) for every rep==`rep` result."""
    seen: set[str] = set()
    for path in sorted(run_dir.glob("**/*_instance_result.yaml")):
        data = yaml.safe_load(path.read_text())
        name = data.get("instance_name")
        if name is None:
            continue
        cell = parse_instance_name(name)
        if cell is None or cell["rep"] != rep or name in seen:
            continue
        seen.add(name)
        yield name, cell, data.get("obj_value"), data.get("obj_bound")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-run-dir", type=Path, default=DEFAULT_RESULT_RUN)
    parser.add_argument("--match-csv", type=Path, default=DEFAULT_MATCH_CSV)
    parser.add_argument("--rep", type=int, default=0)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_OUT_CSV)
    args = parser.parse_args(argv)

    if not args.result_run_dir.exists():
        parser.error(f"result run dir not found: {args.result_run_dir}")
    if not args.match_csv.exists():
        parser.error(f"match csv not found: {args.match_csv}")

    index_of = load_index_map(args.match_csv)

    rows: list[dict] = []
    n_missing_obj = 0
    n_unmapped = 0
    for name, cell, ub, lb in iter_rep0_results(args.result_run_dir, args.rep):
        if ub is None or lb is None:
            n_missing_obj += 1
            continue
        ins_index = index_of.get(name)
        if ins_index is None:
            n_unmapped += 1
            continue
        optimal = abs(ub - lb) <= OPT_TOL
        gap_pct = (ub - lb) / lb * 100.0 if lb else float("inf")
        rows.append(
            {
                "insIndex": ins_index,
                "instanceName": name,
                "n": cell["n"],
                "c": cell["c"],
                "m": cell["m"],
                "T": cell["T"],
                "R": cell["R"],
                "W": cell["W"],
                "rep": cell["rep"],
                "obj_value": ub,
                "obj_bound": lb,
                "gap_pct": round(gap_pct, 2),
                "optimal": optimal,
                "selected": not optimal,
            }
        )

    rows.sort(key=lambda r: r["insIndex"])
    selected = [r for r in rows if r["selected"]]

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    # ---- summary to stdout -------------------------------------------------
    print(f"source run : {args.result_run_dir}")
    print(f"rep filter : {args.rep}")
    print(f"rep{args.rep} instances with obj/bound : {len(rows)}")
    print(f"  optimal (excluded)               : {sum(r['optimal'] for r in rows)}")
    print(f"  selected (non-optimal)           : {len(selected)}")
    if n_missing_obj:
        print(f"  skipped (missing obj/bound)      : {n_missing_obj}")
    if n_unmapped:
        print(f"  skipped (no insIndex in match)   : {n_unmapped}")
    print(f"per-cell table -> {args.out_csv}")

    print("\nselected count by n:")
    for n in sorted({r["n"] for r in selected}):
        print(f"  n={n:<4} : {sum(r['n'] == n for r in selected)}")

    idx = [r["insIndex"] for r in selected]
    print("\n# paste into metadata/20260705/sw_cp_tl_profile.yaml")
    print(f"ins_index: {idx}")

    print("\n# staging by n (run in batches to bound sweep cost):")
    for n in sorted({r["n"] for r in selected}):
        sub = [r["insIndex"] for r in selected if r["n"] == n]
        print(f"# n={n}: {sub}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
