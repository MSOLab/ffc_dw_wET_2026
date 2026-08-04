"""Pre-flight: does a ``(last-1)``-stage-end sort key give a *new* job order?

Doc: ``plans/experiment/20260804/neh_cp_last1_stage_seq.md`` §2.

Two of the three variants requested in the 2026-08-03 tie-break round turned
out to be algebraic aliases of existing modes, and ``bottleneck`` turned out
to be an alias of ``first_stage`` only after a pilot run was burned on it
(``plans/analysis/20260731/neh_cp_seq_source_pilot.md`` result 2). This script
runs the same check *before* the run for the two ``last-1`` variants:

- ``midpoint3``   -- primary ``(fs + ls') / 2``, secondary ``ls'``
- ``completion3`` -- primary ``ls'``, secondary ``fs``

where ``fs`` is the first stage's start, ``ls`` the last stage's end and
``ls'`` the **second-to-last** stage's end. It reports, per key pair, the mean
``normalized_mean_rank_distance`` between the induced orders, how often the two
orders are byte-identical, and the mean Spearman rho of the raw keys. A pair
that lands near ``d = 0`` / ``rho = 1`` is an alias and must not become an arm.

Reads ``<instance>_solution.json`` (the flow's final schedule), not the
schedule NEH-CP actually reads (the FMM output, which is not persisted). The
question here is structural -- how far apart the *keys* are on a schedule this
pipeline produces -- so the proxy is adequate; it is not a result about arms.

Usage:
    uv run python scripts/20260804/preflight_last1_seq_keys.py <scenario_dir> \
        [--sample 60] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from ffc_ddw_sum_et.solution.schedule_sequence import normalized_mean_rank_distance

KEYS = ("first_stage", "midpoint", "completion", "midpoint3", "completion3")

# (a, b): a's order is scored against b's order as the reference.
PAIRS = (
    ("completion3", "completion"),
    ("completion3", "first_stage"),
    ("completion3", "midpoint"),
    ("midpoint3", "midpoint"),
    ("midpoint3", "first_stage"),
    ("midpoint3", "completion"),
    ("midpoint3", "completion3"),
    ("midpoint", "first_stage"),
    ("midpoint", "completion"),
)


def _spearman(a: list[float], b: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else float("nan")


def _tie_fraction(key: dict[str, float]) -> float:
    counts: dict[float, int] = {}
    for v in key.values():
        counts[v] = counts.get(v, 0) + 1
    return sum(c for c in counts.values() if c > 1) / len(key)


def _keys_of(solution: dict) -> tuple[dict[str, dict[str, float]], int] | None:
    stages = solution["stages"]
    if len(stages) < 2:
        return None
    first, last, last_1 = stages[0], stages[-1], stages[-2]
    fs: dict[str, float] = {}
    ls: dict[str, float] = {}
    ls1: dict[str, float] = {}
    for op in solution["operations"]:
        if op["stage"] == first:
            fs[op["job"]] = float(op["start"])
        if op["stage"] == last:
            ls[op["job"]] = float(op["end"])
        if op["stage"] == last_1:
            ls1[op["job"]] = float(op["end"])
    jobs = [j for j in solution["jobs"] if j in fs and j in ls and j in ls1]
    if len(jobs) < 2:
        return None
    return {
        "first_stage": {j: fs[j] for j in jobs},
        "midpoint": {j: (fs[j] + ls[j]) / 2.0 for j in jobs},
        "completion": {j: ls[j] for j in jobs},
        "midpoint3": {j: (fs[j] + ls1[j]) / 2.0 for j in jobs},
        "completion3": {j: ls1[j] for j in jobs},
    }, len(stages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_dir", type=Path)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    dirs = sorted(p for p in args.scenario_dir.iterdir() if p.is_dir())
    random.seed(args.seed)
    sample = random.sample(dirs, min(args.sample, len(dirs)))

    dist: dict[tuple[str, str], list[float]] = {p: [] for p in PAIRS}
    rho: dict[tuple[str, str], list[float]] = {p: [] for p in PAIRS}
    identical = {p: 0 for p in PAIRS}
    ties: dict[str, list[float]] = {k: [] for k in KEYS}
    by_c: dict[int, dict[tuple[str, str], list[float]]] = {}
    c_count: dict[int, int] = {}
    used = 0

    for d in sample:
        path = d / f"{d.name}_solution.json"
        if not path.exists():
            continue
        parsed = _keys_of(json.loads(path.read_text()))
        if parsed is None:
            continue
        key, stage_count = parsed
        used += 1
        c_count[stage_count] = c_count.get(stage_count, 0) + 1

        # Secondary keys mirror ``solution/schedule_sequence.py``'s defaults,
        # plus the requested ``ls'`` tie-break for ``midpoint3``.
        second = {
            "first_stage": key["completion"],
            "midpoint": key["first_stage"],
            "completion": key["first_stage"],
            "midpoint3": key["completion3"],
            "completion3": key["first_stage"],
        }
        order = {
            k: sorted(key[k], key=lambda j, k=k: (key[k][j], second[k][j], j))
            for k in KEYS
        }
        for k in KEYS:
            ties[k].append(_tie_fraction(key[k]))
        jobs = list(key["first_stage"])
        for a, b in PAIRS:
            d_ab = normalized_mean_rank_distance(order[b], order[a])
            dist[(a, b)].append(d_ab)
            by_c.setdefault(stage_count, {}).setdefault((a, b), []).append(d_ab)
            if order[a] == order[b]:
                identical[(a, b)] += 1
            rho[(a, b)].append(
                _spearman([key[a][j] for j in jobs], [key[b][j] for j in jobs])
            )

    if used == 0:
        raise SystemExit(f"no usable solution.json under {args.scenario_dir}")

    def mean(v: list[float]) -> float:
        return sum(v) / len(v)

    print(f"instances used: {used}")
    print("\ntied-job fraction per key (mean over instances)")
    for k in KEYS:
        print(f"  {k:12s} {mean(ties[k]):.4f}")
    print("\npair (a vs b): mean rank distance | identical orders | mean rho of keys")
    for a, b in PAIRS:
        print(
            f"  {a:12s} vs {b:12s}  d={mean(dist[(a, b)]):.4f}  "
            f"identical={identical[(a, b)]:2d}/{used}  rho={mean(rho[(a, b)]):.4f}"
        )
    print("\nmean rank distance by stage count c")
    for c in sorted(by_c):
        print(f"  c={c} (instances={c_count[c]})")
        for a, b in PAIRS:
            print(f"    {a:12s} vs {b:12s} d={mean(by_c[c][(a, b)]):.4f}")


if __name__ == "__main__":
    main()
