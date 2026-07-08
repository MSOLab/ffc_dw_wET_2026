# Plan L — NBM look-ahead for coarse `insert_idle_time`

Status: **PLAN — for review.** Test construction deferred to a separate
conversation (this plan only enumerates the test *scope*).
Date: 2026-06-30
Explainer / rationale: `csr-nbm-lookahead-coarse-shift.md` (§4 Option L). Read it first.
Sibling: `csr-coarse-shift-F-marginal-floor.md` (the simpler alternative).
Target: `FFcSchedule.insert_idle_time` (`src/ffc_ddw_sum_et/solution/ffc_schedule.py:1566`).

## Goal

Make the coarse block shift stop on the **actual block objective $F$**, not the
marginal slope. Keep the efficient breakpoint-jumping skeleton (current-cell
partition + floor `Δ₁`); only augment the `Δ₁ == 0` dead-end with a one-step
objective look-ahead. This:

- recovers the isolated-job sub-cell residual (`F`-improving step into the window
  or a cheaper weighted early↔tardy trade — explainer §5 examples a, c), **and**
- removes the merged-block stall (the stall *is* a `Δ₁ == 0` event, so the
  look-ahead patch covers it for free).

$K=1$ behavior is byte-identical (the `Δ₁ == 0` branch is unreachable at `K=1`,
and a *strict*-improvement look-ahead never fires there).

## Design

At the `Δ₁ == 0` dead-end (greedy can no longer floor-advance but `W_E > W_T`),
evaluate the one grid step that straddles the fractional breakpoint, capped by the
block-collision bound `Δ₂`, and take it iff it strictly lowers $F$:

```
else:  # Δ == 0 (binding job sits at its floor cell)
    if 1 <= Δ₂ and block_obj(shift=1) < block_obj(shift=0):
        shift block [j, block_end] right by 1     # j stays fixed → re-evaluate
    else:
        j -= 1                                     # true integer-grid local min
```

`block_obj(shift)` is the **objective** form (current cell, `objectives.py:50`),
summed over the block:

```
Σ_{i in [j, block_end]}  ewt_i·max(0, lo_i − K·(ends[i] + shift))
                       +  twt_i·max(0, K·(ends[i] + shift) − hi_i)
```

Because $F$ is convex piecewise-linear in the integer shift, "step while it
strictly improves, else stop" converges to the integer block optimum on
`[0, Δ₂]`; the unit step straddles one breakpoint and re-evaluation handles the
next (no closed-form `⌈s*⌉` needed).

### Why `K = 1` is byte-identical
At `K=1` every `S_E`/`S_D` floor distance is `≥ 1`, so `Δ₁ == 0` is unreachable —
the new branch is never entered. (Even if entered, a strict-improvement step never
fires at a reachable integer breakpoint.) All `time_factor=1` callers (non-CSR +
final reconstruct, `schedule_build.py:112`) are unaffected.

### Overshoot / objective discipline
The look-ahead decides on `block_obj` (current-cell tardiness `max(0, K·c − hi)`,
so `K·c = hi ⟹ T = 0`), **not** the partition's `≥ hi` slope test. Keeping the two
separate is what lets the partial-cell step (which the marginal slope cannot see)
be evaluated correctly, and prevents a partition off-by-one from leaking into the
decision.

## File-by-file changes

### `src/ffc_ddw_sum_et/solution/ffc_schedule.py` — `insert_idle_time` body (≈ L1598–1663)

1. **Add a local `block_obj(shift)` helper** (closure or static): sums the wET
   objective over `range(j, block_end + 1)` using `due_window_map`, `ewt_map`,
   `twt_map`, `K`, `ends`, and the candidate `shift`. Read weights with the same
   `.get(..., 1)` defaults the partition uses (keep consistent; out of scope to
   change).
2. **Partition + Δ₁**: unchanged (keep current-cell `KC_j < d_lo` / `>= d_hi`, and
   floor `d_lo // K - ends[i]` / `d_hi // K - ends[i]`).
3. **Replace the guard branch** (≈ L1653–1655): instead of `else: j -= 1`, insert
   the look-ahead (step-1 vs Δ₂, strict-`block_obj` improvement) shown above. The
   outer `else: j -= 1` (when `sum_e <= sum_t`) stays.
4. **Docstring** (L1574–1597): document the look-ahead — stop on objective not
   slope; convexity ⟹ straddling integers suffice; `K=1` unreachable/no-op;
   marginal-vs-objective separation.

### No other source changes
Same as Plan F: only `insert_idle_time` changes.

## Correctness obligations (discharge via the deferred tests)
1. **Optimality of the integer line search**: $F$ convex PL ⟹ step-while-improving
   reaches the integer min on `[0, Δ₂]`; straddling integers suffice. State + prove.
2. **Termination**: each iteration strictly lowers $F$ (integer, bounded below by 0)
   or decrements `j`. No hang.
3. **`Δ₂` cap / merge**: `step ≤ Δ₂`; a step *to* `Δ₂` hands off to the existing
   `block_end` merge path, not double-counted.
4. **`K=1` byte-identity** against a locked pre-change fixture.
5. **Examples a/c recovered**: (a) in-window step → cost 0; (c) `ewt`-dominant
   sub-grid window → step into cheaper tardy. (b) tardiness-dominant → decline.
6. **Merged-block fix**: same regression lock as Plan F (left job cell 1 → 6/7).

## Test scope (constructed separately)
- `tests/solution/test_ffc_schedule.py`: K=1 identity; examples a/b/c (single-job);
  merged-block; a `K | hi` boundary fixture (objective vs partition off-by-one);
  termination.
- `tests/algorithm/test_coarsen_solve_reconstruct.py`: end-to-end CSR; **seed /
  coarse-incumbent** quality delta vs shipped (this is where L pays off); final
  (K=1) objective regression-locked.
- v3/v4 paired seed builders: coarse seed wET equal-or-better than shipped.

## Execution order (TDD, in the separate conversation)
1. Write example-a test (red: shipped floor leaves residual 10) → add `block_obj`
   + look-ahead → green.
2. Add examples b/c, merged-block, `K|hi` boundary.
3. K=1 identity lock; termination.
4. End-to-end CSR + seed-quality comparison.
5. `uv run ruff check` / `uv run ruff format`; full `uv run pytest`.

## Open items
- **Measurement gate** (explainer §8 Q3): since the final K=1 pass repairs the
  *final* schedule, L's win is seed/CP-incumbent quality. Recommend an A/B (L vs
  shipped floor: coarse incumbent, CP time-to-incumbent, final obj) on a
  wide-window instance set before committing.
- **Relationship to Plan F**: L strictly dominates F on coarse-objective quality
  and also fixes the merged-block stall, at the cost of one `block_obj` evaluation
  per stall (O(block size)). If A/B shows no seed benefit, fall back to Plan F.
- Keep the unit-step form (re-evaluated) over a closed-form `⌈s*⌉` unless profiling
  shows the per-stall loop is hot.
