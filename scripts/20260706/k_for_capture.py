"""Find the multiplier `k` in `TL = k * non_time_fixed_op_count` (seconds/op)
that captures a target fraction p of the achievable UB improvement, under three
measurement bases. Motivation: run SW-CP with a *smaller* per-window time limit
than the current fixed 120 s — how small can `k` be and still capture p%?

Reference improvement `I_i` per window = UB improvement at the generous 120 s
cap (the run this reads). Everything is the OFFLINE replay approximation (plan
§5 caveat: ignores the sequential coupling between windows).

Bases (see plan §3.2 / the 2026-07-06 discussion):
- **A  full-sweep total objective (I-weighted):** k s.t.
  `Σ captured_i(k·ntf_i) / Σ I_i = p`. Big windows dominate.
- **B1 per-subproblem required-k distribution:** `k_i = t_p^i / ntf_i` (the
  multiplier window i needs to reach p% of its OWN improvement); report median /
  P75 / P90 of {k_i}. P90 ⇒ 90% of subproblems reach p%.
- **B2 per-subproblem unweighted-mean fraction:** k s.t.
  `mean_i[ captured_i(k·ntf_i)/I_i ] = p`. Every subproblem weighted equally.

Usage:
    uv run python scripts/20260706/k_for_capture.py <run_dir> [<run_dir> ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_tl_policy import captured_at, collect_rows  # noqa: E402

P_LEVELS = (50, 80, 90, 95, 99)


def find_k(func, target: float, hi: float = 8.0) -> float:
    """Smallest k with func(k) >= target; func monotonic non-decreasing in k."""
    lo = 0.0
    while func(hi) < target and hi < 1e6:
        hi *= 2.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if func(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="One or more run directories; windows from all are pooled.",
    )
    args = parser.parse_args(argv)

    rows = [r for r in collect_rows(args.run_dirs) if r["I"] and r["I"] > 0]
    if not rows:
        print("no I>0 windows found")
        return 1

    ntf = np.array([r["non_time_fixed_op_count"] for r in rows], dtype=float)
    sum_i = sum(r["I"] for r in rows)

    def cap_a(k: float) -> float:
        return (
            sum(captured_at(r, k * r["non_time_fixed_op_count"]) for r in rows) / sum_i
        )

    def cap_b2(k: float) -> float:
        return float(
            np.mean(
                [
                    captured_at(r, k * r["non_time_fixed_op_count"]) / r["I"]
                    for r in rows
                ]
            )
        )

    ntf_med = float(np.median(ntf))
    print(f"runs: {', '.join(args.run_dirs)}")
    print(
        f"I>0 windows: {len(rows)}   non_time_fixed: "
        f"min={ntf.min():.0f} median={ntf_med:.0f} max={ntf.max():.0f}"
    )
    print(
        "k is in seconds/op; 'TL@medNtf' = k * median_ntf (secs), vs current fixed 120 s.\n"
    )

    header = (
        f"{'p%':>4} | {'A k':>7} {'A TL@med':>9} | "
        f"{'B2 k':>7} {'B2 TL@med':>10} | "
        f"{'B1 medk':>8} {'B1 P75k':>8} {'B1 P90k':>8} {'B1 TL@med(P90)':>15}"
    )
    print(header)
    print("-" * len(header))
    for p in P_LEVELS:
        tgt = p / 100.0
        k_a = find_k(cap_a, tgt)
        k_b2 = find_k(cap_b2, tgt)
        ki = np.array(
            [
                r[f"t_{p}_abs"] / r["non_time_fixed_op_count"]
                for r in rows
                if r.get(f"t_{p}_abs") is not None
            ],
            dtype=float,
        )
        med_k = float(np.median(ki))
        p75_k = float(np.percentile(ki, 75))
        p90_k = float(np.percentile(ki, 90))
        print(
            f"{p:>4} | {k_a:>7.3f} {k_a * ntf_med:>9.1f} | "
            f"{k_b2:>7.3f} {k_b2 * ntf_med:>10.1f} | "
            f"{med_k:>8.3f} {p75_k:>8.3f} {p90_k:>8.3f} {p90_k * ntf_med:>15.1f}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
