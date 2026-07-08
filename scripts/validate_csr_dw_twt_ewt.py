"""Validate the proposed CSR due-window penalty reformulation.

Context
-------
``coarsen_solve_reconstruct`` (CSR) coarsens an FFc-DDW instance by an integer
``factor`` before solving the base CP-SAT model, then reconstructs to original
scale. The *current* coarsening quantizes the due window with
``ceil(d / factor)`` and lets the CP model optimize earliness/tardiness (E/T)
in **coarsened time units** against that rounded window.

The proposed breaking change removes due-window quantization. The coarse
completion variable ``C^c_j`` (integer, coarse units) is interpreted as the
real completion ``factor * C^c_j`` (every event lands on a multiple-of-factor
grid). The penalty is evaluated against the **original** integer due window:

    E_pen_j = w^-_j * max(0, d^-_j - factor * C^c_j)
    T_pen_j = w^+_j * max(0, factor * C^c_j - d^+_j)

This script verifies two claims the reformulation rests on:

1. **Equivalence.** The integer "scaled-completion vs original window" penalty
   equals the user's "scaled-window x scaled-weight" formulation
   (window -> [d^-/factor, d^+/factor], weight -> w * factor), which in turn
   equals the original-problem penalty at the reconstructed completion.

2. **Integrality.** The weighted E/T penalty is *always* a non-negative
   integer, so CP-SAT can optimize it exactly with integer variables -- no
   fractional due window is ever needed inside the model.

It also contrasts both against the current ``ceil``-quantized coarse penalty to
show the discrepancy (in magnitude *and* in on-time/tardy classification) that
motivates the change.

Run:
    uv run python scripts/validate_csr_dw_twt_ewt.py
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    factor: int
    d_lo: int  # original due-window lower bound d^-
    d_hi: int  # original due-window upper bound d^+
    w_e: int  # earliness weight w^-
    w_t: int  # tardiness weight w^+
    c_coarse: int  # coarse completion variable C^c (integer, coarse units)


def original_penalty(case: Case) -> int:
    """Penalty against the ORIGINAL window at reconstructed completion.

    completion = factor * C^c  (the multiple-of-factor grid point).
    All terms are integers -> the result is a non-negative integer.
    """
    completion = case.factor * case.c_coarse
    return case.w_e * max(case.d_lo - completion, 0) + case.w_t * max(
        completion - case.d_hi, 0
    )


def scaled_window_penalty(case: Case) -> float:
    """User's formulation: window /factor, weight *factor, coarse completion.

    w^-*factor * max(0, d^-/factor - C^c) + w^+*factor * max(0, C^c - d^+/factor)
    Algebraically identical to ``original_penalty`` but evaluated in float to
    show the equivalence holds exactly (no rounding) despite a fractional
    window.
    """
    we = case.w_e * case.factor
    wt = case.w_t * case.factor
    lo = case.d_lo / case.factor
    hi = case.d_hi / case.factor
    return we * max(lo - case.c_coarse, 0.0) + wt * max(case.c_coarse - hi, 0.0)


def current_coarse_penalty(case: Case) -> int:
    """The CURRENT scheme: ceil-quantized window, penalty in COARSE units.

    w^- * max(0, ceil(d^-/factor) - C^c) + w^+ * max(0, C^c - ceil(d^+/factor))
    Kept here only to demonstrate the discrepancy that motivates the change.
    """
    lo = math.ceil(case.d_lo / case.factor)
    hi = math.ceil(case.d_hi / case.factor)
    return case.w_e * max(lo - case.c_coarse, 0) + case.w_t * max(case.c_coarse - hi, 0)


def check_user_example() -> None:
    """The exact example from the request: factor=50, window=(72, 115)."""
    print("=== User example: factor=50, window=(72, 115), w^-=w^+=1 ===")
    print(
        f"{'C^c':>4} {'real':>5} {'original':>9} {'scaled':>9} {'current(coarse)':>16}"
    )
    for c in (1, 2, 3):
        case = Case(factor=50, d_lo=72, d_hi=115, w_e=1, w_t=1, c_coarse=c)
        orig = original_penalty(case)
        scaled = scaled_window_penalty(case)
        cur = current_coarse_penalty(case)
        assert math.isclose(orig, scaled, rel_tol=0, abs_tol=1e-6), (
            f"equivalence broke at C^c={c}: {orig} != {scaled}"
        )
        flag = "  <- penalty differs / on-time flips" if cur != orig else ""
        print(f"{c:>4} {case.factor * c:>5} {orig:>9} {scaled:>9.2f} {cur:>16}{flag}")
    print()


def property_test(trials: int = 200_000, seed: int = 20260627) -> None:
    """Randomized property test of equivalence + integrality."""
    rng = random.Random(seed)
    max_abs_err = 0.0
    diff_from_current = 0
    for _ in range(trials):
        factor = rng.randint(2, 200)
        d_lo = rng.randint(0, 5000)
        d_hi = d_lo + rng.randint(0, 5000)  # preserve d^- <= d^+
        case = Case(
            factor=factor,
            d_lo=d_lo,
            d_hi=d_hi,
            w_e=rng.randint(1, 10),
            w_t=rng.randint(1, 10),
            c_coarse=rng.randint(0, 1 + (d_hi // factor) + 5),
        )
        orig = original_penalty(case)
        scaled = scaled_window_penalty(case)

        # Claim 1: equivalence (exact, up to float representation).
        max_abs_err = max(max_abs_err, abs(orig - scaled))
        assert math.isclose(orig, scaled, rel_tol=0, abs_tol=1e-6), (
            f"equivalence failed for {case}: original={orig} scaled={scaled}"
        )
        # Claim 2: integrality.
        assert isinstance(orig, int) and orig >= 0, f"not a non-neg int: {orig}"

        if current_coarse_penalty(case) != orig:
            diff_from_current += 1

    print(f"=== Property test: {trials} random cases (seed={seed}) ===")
    print(f"  equivalence  original == scaled : OK (max abs err {max_abs_err:.3e})")
    print("  integrality  original is non-neg int : OK")
    print(
        f"  current ceil-quantized penalty differed from the correct penalty "
        f"in {diff_from_current}/{trials} cases "
        f"({100 * diff_from_current / trials:.1f}%)"
    )
    print()


def main() -> None:
    check_user_example()
    property_test()
    print("All checks passed: the reformulation is exact and always integer.")


if __name__ == "__main__":
    main()
