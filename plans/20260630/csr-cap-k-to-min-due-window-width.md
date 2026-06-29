# CSR: cap coarsening factor K at the minimum due-window width

Status: **SUPERSEDED (rejected)** by `csr-floor-shift-overshoot-safe.md` (2026-06-30).
The user chose **not to cap K** (the cap collapsed K to single digits / 2 on
narrow-window instances — see "Magnitude" below). Kept as a recorded
rejected-alternative; the magnitude data here is still the evidence for *why*
the global cap was abandoned in favor of the floor-based shift.
Date: 2026-06-30
Branch base: `20260624_more_init_dispatch`
Builds on: the staged `csr-time-factor-ssot` work (`insert_idle_time(time_factor=…)`,
`coarsen_processing_times`, `factor * C` objective) — see
`plans/20260629/csr-time-factor-ssot.md`.

## Motivation

`insert_idle_time` on the coarse grid is **provably optimal only when every job
has a non-empty in-due (on-time) coarse cell** — i.e. early → in-due → tardy are
all reachable. When a due window is narrower than the coarsening factor `K` and
falls strictly between two coarse grid points, the job's in-due set is empty; the
right-only NBM greedy then **overshoots** (shifts an early job directly into
tardy, skipping the absent in-due region, with no ability to backtrack).

This was confirmed on real PRA2017 instances (analysis scripts in scratchpad):

- `Instance_50_5_3_0,2_1_10_Rep0` (narrow windows, 5 stages): at K=64, **5 of 50
  jobs** are genuinely mis-placed by the greedy; across K∈[2,64], **18/50 (36%)**
  jobs overshoot at some K. Per-job loss up to 89.
- `Instance_200_10_5_0,6_1_20_Rep4` (wide windows, 10 stages): ~0 genuine
  overshoots — the long critical path makes narrow-window jobs always-tardy, so
  the bug hides.

The final reconstruct (`reconstruct_coarse_schedule`, `K=1`) re-times at full
resolution, so the **final timing is correct regardless**. But the overshoot
degrades the **warm-start seed** and can **bias the coarse CP's sequence choice**.
We do not want an algorithm that is "possibly wrong but the benchmark didn't show
it." We want a **structural correctness guarantee**.

## Decision (confirmed by user)

Guarantee correctness by **capping K so every job's due window is at least `K`
wide**. Then `d⁺_j − d⁻_j ≥ K` for all `j` ⟹ every job has a non-empty in-due
coarse cell ⟹ no overshoot ⟹ `insert_idle_time` is optimal for the coarse
problem (reduces structurally to the proven fine-grid case).

    effective_K = min(K_requested, W_min),   W_min = min_j (d⁺_j − d⁻_j)

`effective_K` replaces `K_requested` everywhere in that CSR run (coarsen, solve,
reconstruct, metrics).

## Correctness argument

The in-due coarse set of job `j` is
`{ c ∈ ℤ : d⁻_j ≤ K·c < d⁺_j } = { c : ⌈d⁻_j/K⌉ ≤ c < ⌈d⁺_j/K⌉ }`.

- `d⁺_j − d⁻_j ≥ K ⟹ d⁺_j/K − d⁻_j/K ≥ 1 ⟹ ⌈d⁺_j/K⌉ ≥ ⌈d⁻_j/K⌉ + 1`
  ⟹ the set is **non-empty** (contains `c = ⌈d⁻_j/K⌉`).
- With a non-empty in-due set, an early job's first right-shift breakpoint
  `Δ₁ = ⌈d⁻_j/K⌉ − c` lands it **on** an in-due cell (not past `d⁺`). Every unit
  of the greedy's block shift then crosses exactly **one** slope change of the
  per-job convex cost `g_j(c) = h_j(K·c)`, and NBM re-evaluates set membership at
  each breakpoint — structurally identical to the fine-grid (`K=1`) case where
  the procedure is proven optimal (Pan et al. / NBM). ∎
- `effective_K = min(K_requested, W_min)` ensures `effective_K ≤ d⁺_j − d⁻_j` for
  all positive-width `j` (since `effective_K ≤ W_min ≤ width_j`). □

## ⚠️ Magnitude / tradeoff (DECISION POINT — confirm before execution)

The cap is **global**: a single narrow window collapses K for the whole instance.
Measured on the two benchmark instances (requesting K up to 64):

| instance | W_min (positive) | effective K |
|---|---|---|
| `Instance_50_5_3_0,2_1_10_Rep0` | 6 | **6** (from 64; 10.7× less coarsening) |
| `Instance_200_10_5_0,6_1_20_Rep4` | 2 | **2** (from 64; coarsening ~disabled) |

So on these instances the cap **substantially weakens coarsening** (K=2 means the
coarse instance is essentially the original). This is the deliberate price of the
guarantee. Two things to confirm at execution time:

1. **Accept the magnitude?** If "K capped to single digits / 2" is unacceptable
   for the experiment, the alternative that **preserves the requested K while
   staying provably correct** is the per-job **cost-guard** (for window-skip jobs
   `⌈d⁻/K⌉ == ⌈d⁺/K⌉`, replace the NBM proxy with an exact best-early-vs-first-
   tardy comparison). The user chose the cap for its simplicity / clean
   provability; this note records the alternative in case the magnitude changes
   that.
2. **Even-K snapping?** Experiments use even K. `effective_K = W_min` may be odd
   (correctness holds for any `K ≥ 1`). Options: use `W_min` exactly
   (recommended — simplest, correct) or snap to the largest even `≤ W_min`
   (experimental consistency, slightly more conservative). Low-stakes.

## Edge case: zero-width / degenerate windows

`W_min` over **all** jobs can be 0 (a job with `d⁻ = d⁺`), which would make
`effective_K = 0` (invalid). Handling:

- A `(0, 0)` window (`d⁻ = d⁺ = 0`) is **provably harmless**: the job is always
  tardy (`K·c ≥ 0 = d⁻`), never early, so it never overshoots and `insert_idle_time`
  handles it optimally for any `K`. Both benchmark instances' zero-width windows
  are all `(0,0)` (#problematic-zero = 0).
- A `(d, d)` window with `d > 0` is genuinely problematic and the cap **cannot**
  protect it (its width is 0). It must be rejected.

**Rule:** `W_min = min over positive-width windows`. Validate that every
zero-width window has `d⁻ = 0`; otherwise raise a clear error
(`coarsen-solve-reconstruct requires every due window to have width ≥ 1 or
d⁻ = d⁺ = 0`). If there are **no** positive-width windows at all, raise
(fully degenerate). Both benchmarks pass this validation.

## Design / file-by-file changes

### 1. `parameters/ffc_ddw_params.py` — instance property
- Add `min_positive_due_window_width(self) -> int`:
  `min(hi - lo for lo, hi in self._job_2_due_window_map.values() if hi > lo)`.
  Raise `ValueError` if no positive-width window exists. Neutral instance
  property (no coarsening semantics baked in), SSOT, unit-testable.
- (Optional) `has_only_harmless_zero_windows(self) -> bool` or fold the
  zero-width validation into the CSR helper below — keep params neutral; prefer
  the validation living in CSR.

### 2. `algorithm/coarsen_solve_reconstruct.py` — apply the cap in the pure pipeline
This is the SSOT for the pipeline factor and has both `option.factor` and
`instance`, so the cap belongs here (every caller — adapter and direct — gets it;
do **not** put it only in the adapter's `_resolve_option`).

At the top of `run_coarsen_solve_reconstruct` (before L330 `coarsen_processing_times`):
- Validate zero-width harmlessness (every `d⁻ == d⁺` window has `d⁻ == 0`); raise
  otherwise.
- `w_min = instance.min_positive_due_window_width()`
- `factor = min(option.factor, w_min)`  ← the effective factor
- `if factor < option.factor:` `logger.info("CSR: K capped %d -> %d (min due-window
  width) for %s", option.factor, factor, instance.name)`
- Replace **every** `option.factor` in this function with `factor`:
  - L330 `coarsen_processing_times(instance, factor)`
  - L333-337 log line (log both requested and effective)
  - L353 `_solve_coarsened_model(coarsened, factor, …)`
  - L417-421 reconstruct already uses local `factor` — feed it the capped value
    (delete the `factor = option.factor` reassignment at L417; the top-level
    `factor` is now the single source).
  - metrics (both branches, L378-389 and L428-439): record **both**
    `"requested_factor": option.factor` and `"factor": factor` (effective), so the
    override is visible and runs are reproducible.

No other call site changes: `coarsen_processing_times`, `_solve_coarsened_model`,
the seed builders, and the reconstruct functions already take `factor` as a
parameter and use it consistently (post `csr-time-factor-ssot`).

### 3. Tests (TDD)

- `tests/parameters/test_ffc_ddw_params.py`:
  - `min_positive_due_window_width` returns the min positive width; ignores
    zero-width windows; raises when all windows are zero-width.
- `tests/algorithm/test_coarsen_solve_reconstruct.py`:
  - **Cap applied:** an instance with a narrow window → `effective_K = W_min`;
    assert `metrics["factor"] == W_min`, `metrics["requested_factor"] ==
    option.factor`, and that the log/override fired.
  - **No cap (no behavior change):** an instance with all windows ≥ requested K
    → `metrics["factor"] == option.factor`; reconstructed objective byte-identical
    to pre-change (regression-lock a small instance).
  - **Correctness property (the point of this change):** on the narrow-window
    stress instance, after capping, assert **every job has a non-empty in-due
    coarse cell** (`⌈d⁻/K⌉ < ⌈d⁺/K⌉` for all `j`) — i.e. the structural
    guarantee holds, independent of any overshoot search.
  - **Zero-width validation:** instance with a `(d,d)`, `d>0` window → raises;
    instance with `(0,0)` windows → proceeds (cap from positive widths).
- `tests/solution/test_ffc_schedule.py` (optional regression):
  - Lock the overshoot-free behaviour: on a single-job schedule with
    `K = d⁺ − d⁻` (the cap boundary), `insert_idle_time` lands the job on its
    in-due cell, **not** overshot into tardy. (Mirror of the scratchpad
    `narrow_window_demo` but at the capped K.)

### 4. Validation / docs
- `docs/algorithm-principles.md` or a CSR docstring: state the invariant —
  "CSR caps the coarsening factor at the minimum positive due-window width so
  that every job retains a non-empty in-due coarse cell, making `insert_idle_time`
  provably optimal on the coarse instance." Reference the correctness argument
  above.

## Equivalence / scope

- Wide-window instances (every width ≥ requested K): `effective_K = K_requested`,
  **no behavior change** — same coarsening, same objective, same output.
- Narrow-window instances: coarsening is weaker (smaller K) but **correct**; the
  reconstructed objective changes only because the coarse grid is finer (strictly
  a more faithful surrogate).
- Final reconstruct path, `compute_weighted_earliness_tardiness`, the CP model:
  unchanged — they already consume `factor` consistently.

## Out of scope
- The per-job cost-guard alternative (kept as a documented fallback under
  "Magnitude / tradeoff" if the cap magnitude proves unacceptable).
- Any change to the final (`K=1`) reconstruct — already exact.
- Non-CSR `insert_idle_time` callers — unaffected (`time_factor` defaults to 1).

## Execution order (TDD)
1. `min_positive_due_window_width` + tests (red→green) — pure, isolated.
2. Cap + validation + logging in `run_coarsen_solve_reconstruct`; thread the
   effective `factor`; metrics record requested + effective.
3. Correctness-property test on the narrow-window stress instance.
4. Regression-lock a wide-window instance (no behavior change) and a narrow-window
   instance (objective at capped K).
5. `uv run ruff check` / `uv run ruff format`; full `uv run pytest`.

## Open decisions (confirm at execution)
1. **Accept cap magnitude** (K→6 / K→2 on the sampled instances) vs switch to the
   per-job cost-guard to preserve requested K. (User chose the cap.)
2. **`effective_K`**: exact `W_min` (recommended) vs snapped to largest even
   `≤ W_min`.
3. **Zero-width policy**: positive-width min + validate `(0,0)`-only (recommended)
   vs strict raise on any zero-width vs clamp-to-1 fallback.
