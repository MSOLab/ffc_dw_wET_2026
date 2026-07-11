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
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_tl_policy  # noqa: E402
from analyze_tl_policy import captured_at, collect_rows  # noqa: E402

DEFAULT_P_LEVELS = (50, 80, 90, 95, 99)


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
    parser.add_argument(
        "--scenario",
        default=None,
        help=(
            "If set, pool only windows from this scenario (e.g. 'u2_pf2'). "
            "A run dir may physically contain several scenarios "
            "(u2_pf2/u4_pf2, incl. stale/partial ones); without this filter "
            "collect_rows pools ALL of them, which silently contaminates a "
            "scenario-specific table. Default: pool every scenario found."
        ),
    )
    parser.add_argument(
        "--p-levels",
        default=",".join(str(p) for p in DEFAULT_P_LEVELS),
        help=(
            "Comma-separated capture-target percentiles (each 0-100). "
            f"Default: {','.join(str(p) for p in DEFAULT_P_LEVELS)}. "
            "The B1 basis needs a per-window time-to-p%% curve point, so this "
            "list also drives analyze_tl_policy.P_LEVELS during collection."
        ),
    )
    parser.add_argument(
        "--csv",
        default=None,
        help=(
            "If set, also write the pooled table to this CSV path "
            "(same columns as k_for_capture_270_u2_pooled.csv)."
        ),
    )
    args = parser.parse_args(argv)

    p_levels = tuple(int(x) for x in args.p_levels.split(",") if x.strip())
    # The B1 basis reads a precomputed t_{p}_abs per window; compute_metrics
    # iterates the module-global P_LEVELS at call time, so set it before
    # collect_rows so the requested percentiles are actually materialized.
    analyze_tl_policy.P_LEVELS = p_levels

    all_rows = collect_rows(args.run_dirs)
    found_scenarios = sorted({r["scenario"] for r in all_rows})
    if args.scenario is not None:
        all_rows = [r for r in all_rows if r["scenario"] == args.scenario]
    rows = [r for r in all_rows if r["I"] and r["I"] > 0]
    if not rows:
        print(
            f"no I>0 windows found (scenario filter={args.scenario!r}; "
            f"scenarios present in run dirs: {found_scenarios})"
        )
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
    n_inst = len({(r["scenario"], r["instance"]) for r in rows})
    print(
        f"scenario filter: {args.scenario!r}   "
        f"scenarios present: {found_scenarios}   pooled instances: {n_inst}"
    )
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
    csv_rows: list[dict[str, float | int]] = []
    for p in p_levels:
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
        csv_rows.append(
            {
                "p_pct": p,
                "A_k": k_a,
                "A_TL_at_medNtf": k_a * ntf_med,
                "B2_k": k_b2,
                "B2_TL_at_medNtf": k_b2 * ntf_med,
                "B1_med_k": med_k,
                "B1_P75_k": p75_k,
                "B1_P90_k": p90_k,
                "B1_TL_at_medNtf_P90": p90_k * ntf_med,
                "median_ntf": ntf_med,
                "n_windows": len(rows),
                "n_instances": n_inst,
            }
        )

    if args.csv:
        out_path = Path(args.csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\nwrote pooled table: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
