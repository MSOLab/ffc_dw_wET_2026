# CSR: original due window as single source of truth via `time_factor`

Status: **PLAN — approved, not yet implemented**
Date: 2026-06-29

Decisions (confirmed): (1) **rename** `coarsen_time_resolution` →
`coarsen_processing_times`; (2) **net-delete** the `original*` plumbing
(fewer params than today). The former "Open decisions" section is resolved
inline below.
Branch base: `20260624_more_init_dispatch`
Follow-up to: `6a8da8e fix(csr)!: drop due-window quantization`

## Goal

Make the **original due window the single source of truth** across the entire
coarsen-solve-reconstruct (CSR) pipeline. `coarsen_time_resolution` stops
quantizing the due window (coarsens **processing times only**); `time_factor`
becomes the *one* bridge between the coarse grid and the original time scale,
symmetric with the `time_factor` that `compute_weighted_earliness_tardiness`
already takes.

This **removes** the dual-storage / dual-plumbing the previous commit
introduced (`original_due_windows` build param, `Params.original_scale_*`
fields, `original`/`original_instance` threading through the seed builders) and
ends up **simpler than the current state**, not just equivalent.

## Key insight (why this is exact, not lossy)

`insert_idle_time` never uses E/T *magnitudes*. It only:

1. **classifies** each job early/tardy/on-time by comparing completion `c`
   against the window, and
2. **shifts blocks** by integer **coarse-grid** distances (`delta1`, `delta2`).

For a coarse completion `c` and original window bound `d`, the factor-scaled
objective treats real completion as `factor*c`. Then:

- early ⇔ `factor*c < d_lo` ⇔ `c < ceil(d_lo/factor)`
- tardy ⇔ `factor*c >= d_hi` ⇔ `c >= ceil(d_hi/factor)`

(both `⇔` proven for integer `c`). So classifying against the **effective
coarse window** `ceil(d/factor)` is *identical* to classifying `factor*c`
against the original window, and the optimal coarse shift to clear earliness is
`ceil(d_lo/factor) - c`. In other words `ceil(d/factor)` is the **exact**
coarse-grid representation of the boundary — not a lossy rounding. The
quantization the previous commit removed only mattered for the CP/wET
**magnitude**, which `factor*C` against the original window already fixes.

Therefore `insert_idle_time` can derive its effective coarse window internally
from `(original_window, time_factor)` via ceil-division, with `time_factor=1`
collapsing to today's behaviour (`ceil(d/1)=d`) — a true no-op for every
non-CSR caller.

## Design

Two real changes; everything else is **deletion** of now-redundant plumbing.

1. **`coarsen_time_resolution` → renamed `coarsen_processing_times`, coarsens
   `p` only.** The coarsened instance carries the **original** due window. The
   rename names the scale-hybrid result at every call site. (Breaking contract
   change — see Risks.)

2. **`insert_idle_time` gains `time_factor: int = 1`.** It computes an
   effective coarse window `(ceil(d_lo/tf), ceil(d_hi/tf))` once at entry, then
   runs the existing algorithm verbatim against it.

Consequences that let us *delete* code:

- The coarsened instance now satisfies `compute_weighted_earliness_tardiness`
  (with `time_factor=factor`) directly — so the seed builders no longer need a
  separate `original`/`original_instance` argument.
- The CP builder reads the (now-original) window straight off the coarsened
  instance — so `build()` no longer needs `original_due_windows`, and `Params`
  no longer needs `original_scale_d_lower/upper`. The objective and hint code
  collapse to a **single branch**: `scaled_C = time_factor * C`.

## File-by-file changes

### 1. `parameters/ffc_ddw_params.py` — rename `coarsen_time_resolution` → `coarsen_processing_times`
- Coarsen `p` via `ceil(p/factor)` (unchanged). **Stop** applying
  `ceil(·/factor)` to the due window; pass `instance._job_2_due_window_map`
  through unchanged.
- **Rename** the classmethod to `coarsen_processing_times`. Update the name in
  the docstring and the new-instance name suffix (`_coarsen{factor}` →
  keep or `_coarsenp{factor}`; pick at implementation time, low-stakes).
- Update docstring: "coarsens processing times only; due windows are preserved
  at the original scale and must be interpreted with `time_factor=factor`".
- Update all 3 src call sites (`orchestration/controller.py:1642`,
  `coarsen_solve_reconstruct.py:336`, and any other) + all test call sites
  (`tests/parameters/`, `tests/solution/test_schedule_build.py`,
  `tests/algorithm/test_coarsen_solve_reconstruct.py`).

### 2. `solution/ffc_schedule.py` — `insert_idle_time`
- Add `time_factor: int = 1` parameter.
- At entry, build effective window:
  `eff = {j: (-(-lo // tf), -(-hi // tf)) for j,(lo,hi) in due_window_map.items()}`
  (ceil division; or `math.ceil`). Use `eff` everywhere the body currently
  reads `due_window_map`.
- Docstring: document `time_factor` and the scale-consistency invariant
  (coarse-grid schedule + original window + `time_factor=factor`), mirroring
  the note already in `compute_weighted_earliness_tardiness`.
- `time_factor=1` ⇒ `eff == due_window_map` ⇒ **byte-identical behaviour** for
  all existing (non-CSR) callers; none of them change.

### 3. `algorithm/cumulative.py` — `BaseModelBuilder` / `Params`
- **Remove** `Params.original_scale_d_lower`, `Params.original_scale_d_upper`.
- **Remove** the `original_due_windows` parameter from `build()` and
  `make_params()`. `d_lower`/`d_upper` come from the instance as today (which,
  for a coarsened instance, is now the original window).
- Keep `Params.time_factor` and the `time_factor` parameter on `build()`.
- Objective (`_add_et_objective` region, ~L446) and
  `apply_et_hints_from_ref_schedule` (~L726): delete the `if time_factor>1 …
  else …` split; always `scaled_C = params.time_factor * C_j`,
  `E = max(0, d_lower - scaled_C)`, `T = max(0, scaled_C - d_upper)`.
  `E_j_ub = d_lower`, `T_j_ub = time_factor * horizon` (reduces to `horizon`
  when `time_factor=1`).

### 4. `algorithm/coarsen_solve_reconstruct.py`
- `_build_dispatch_seed_schedule`: drop the `original` parameter. Keep
  `factor`. Pass `time_factor=factor` to each `insert_idle_time(dw, …)` (`dw`
  is now the original window off the coarsened instance) and evaluate
  `compute_weighted_earliness_tardiness(cand, coarsened, time_factor=factor)`.
- `_solve_coarsened_model`: drop `original_instance` parameter. Call
  `builder.build(coarsened_instance, horizon=…, time_factor=factor)` (no
  `original_due_windows`). Seed-obj metric:
  `compute_weighted_earliness_tardiness(seed, coarsened_instance,
  time_factor=factor)`.
- `run_coarsen_solve_reconstruct`: drop the extra `instance` argument now
  unnecessary at the `_solve_coarsened_model` call (the **final** post-process
  and original-scale objective evaluation on the reconstructed schedule stay
  exactly as they are — those already run at `time_factor=1` on the original
  instance).

### 5. `algorithm/dispatcher/paired.py`
- Thread `time_factor: int = 1` through the internal helpers
  (`dispatch_forward_with_iit` and the rd counterpart) so their
  `insert_idle_time` and `compute_weighted_earliness_tardiness` use it.
- `build_v3_paired_dispatch_schedule` / `build_v4_…`: drop `original_instance`;
  keep `factor`; pass `time_factor=factor` **into the helper calls**
  (`dispatch_forward_with_iit(instance, seq, log, time_factor=factor)` and the
  rd counterpart). The helpers then build *and* score each candidate under the
  CSR objective in one pass.
  - **Critical:** once `time_factor` is threaded down, the helpers already
    return the factor-scaled wET, so there must be **no** separate
    `if factor > 1: re-score with compute_weighted_earliness_tardiness(...)`
    block. Re-scoring tf=1-positioned schedules at tf=factor leaves the
    *schedule* mispositioned (idle inserted against the original window on the
    coarse grid) even though the *number* looks scaled — the seed fed to the CP
    warm-start is then wrong. Build and score in the same `time_factor`.

### 6. Tests
- `tests/parameters/test_ffc_ddw_params.py`:
  - `test_coarsen_time_resolution_applies_ceil_to_due_windows` → rewrite as
    `…_preserves_due_windows` (assert window unchanged).
  - Rename-related test updates if we rename the method.
  - Keep the `p`-ceil and `lower<=upper` tests.
- `tests/solution/` — add `insert_idle_time` `time_factor` tests: assert
  `tf=1` identical to no-arg; assert `tf=factor` against original window equals
  the *old* behaviour of `tf=1` against `ceil(window/factor)` (equivalence
  oracle).
- `tests/algorithm/test_coarsen_solve_reconstruct.py` and
  `tests/algorithm/dispatcher/test_mixed.py`,
  `tests/algorithm/cumulative` tests: drop references to `original_due_windows`
  / `original_scale_*`; the **observable** CSR objective values must be
  unchanged (regression-lock a couple of small instances before/after).
- `tests/algorithm/dispatcher/test_paired.py` — add a build_v3/v4
  candidate-construction regression: on a coarsened instance with **finite**
  windows (jobs must be early on the coarse grid so `insert_idle_time` actually
  shifts), `build_v*(coarsened, factor=f).best_obj` must equal the oracle that
  enumerates candidates with `time_factor=f` threaded into the helpers. This is
  the test that fails if the helpers are called without `time_factor` (the
  §5 "critical" trap). Note: wide windows like `(0, 9999)` do **not** exercise
  it — every job is on-time, no shift, tf=1 and tf=f coincide.
- `scripts/validate_csr_dw_twt_ewt.py`: update to the new API; it should still
  confirm seed-wET == CP-reported obj.

## Equivalence / correctness argument

End-to-end, the **observable** outputs (seed schedules, CP objective values,
final reconstructed objective) must be **identical** to `6a8da8e`, because:

- CP objective: `factor*C` vs original window — unchanged formula, just sourced
  from one window instead of two.
- `insert_idle_time`: effective window `ceil(d/factor)` == the window the
  previous code passed explicitly (the coarsened instance's then-quantized
  window). Same classification, same deltas, same result.
- Seed wET ranking: same window, same weights, same `factor`.

So this is a **refactor with an equivalence guarantee**, not a behaviour
change. Regression-lock objective values on 2–3 small instances to prove it.

## Risks / tradeoffs

- **Scale-hybrid coarsened instance (main risk).** After this change the
  coarsened `FFcDDWParameters` is no longer a self-consistent problem: coarse
  `p`, original window. Evaluating it *without* `time_factor` (e.g. a naive
  `compute_weighted_earliness_tardiness(coarsened)`) silently mixes scales.
  Mitigation (confirmed): **rename** `coarsen_time_resolution` →
  `coarsen_processing_times` so the asymmetry is named at every call site; plus
  lean on the existing scale-consistency invariant doc and the equivalence
  tests above.
- **Breaking change** to a public classmethod contract → commit as `!`
  (`refactor(csr)!:` or `fix(csr)!:`).
- `insert_idle_time` is hot and widely called; the `tf=1` fast path must avoid
  per-call rework — guard: when `time_factor == 1`, skip building `eff` and use
  `due_window_map` directly.

## Resolved decisions

1. **Rename** `coarsen_time_resolution` → `coarsen_processing_times`. ✅
2. **Net-delete** the `original*` plumbing (fewer params than today). ✅

## Out of scope
- Reconstruct / final post-process path (already original-scale, untouched).
- Any non-CSR `insert_idle_time` caller (all keep `time_factor=1`).
- The CP trajectory "coarsened scale" docstrings (already stale post-`6a8da8e`;
  optional doc-only cleanup, not required here).

## Execution order (TDD)
1. `insert_idle_time` + equivalence test (red→green) — pure, isolated.
2. `coarsen_*` window-preserve + test.
3. CP builder simplification + tests.
4. CSR + paired plumbing deletion + regression-lock objective values.
5. `uv run ruff check` / `uv run ruff format`; full `uv run pytest`.

## Implementation notes (post-review)

- **Deviation found in review (now fixed):** the first implementation of §5
  did **not** thread `time_factor` into the `build_v3/v4` helper calls; it kept
  a separate `if factor > 1:` re-score block instead. That left v3/v4 seed
  schedules positioned at `time_factor=1` against the original window on the
  coarse grid (mispositioned by ~`factor×`), even though the reported obj was
  factor-scaled. Default seed is `mixed` (unaffected), but
  `coarsen_solve_reconstruct_v4_seed_config.yaml` uses v4, so experiments were
  hit. Fix: thread `time_factor=factor` into the helpers and delete the
  redundant re-score block (§5 "critical"). Added the regression test in §6.
  Full suite green (485 passed).
