# Plan F — marginal-partition floor for coarse `insert_idle_time`

Status: **PLAN — for review.** Test construction deferred to a separate
conversation (this plan only enumerates the test *scope*).
Date: 2026-06-30
Explainer / rationale: `csr-nbm-lookahead-coarse-shift.md` (§2 marginal partition,
§3 floor reading, §4 Option F). Read it first.
Sibling: `csr-coarse-shift-L-lookahead.md` (the more-optimal alternative).
Target: `FFcSchedule.insert_idle_time` (`src/ffc_ddw_sum_et/solution/ffc_schedule.py:1566`).

## Goal

Replace the current-cell partition + `Δ₁ == 0` termination guard with the
**marginal (next-cell) partition**, so the right-only floor greedy is internally
consistent: it stops cleanly at the floor cell `⌊d⁻/K⌋` with no guard hack, stays
overshoot-safe, and **removes the merged-block stall** the shipped guard suffers
(a right member sitting at its floor freezes the whole block, under-shooting left
members). $K=1$ behavior is byte-identical.

This is **not** a pure cosmetic refactor: it is $K=1$-identical but at $K>1$ it is
*equal-or-better* than today (fixes the stall). It does **not** close the
isolated-job sub-cell residual `≤ K−1` (that needs Plan L).

## Design

Let `K = time_factor`, `(lo, hi) = due_window_map[j]`, `c = ends[i]` (coarse
completion of the block member). The block shifts right as a unit while the
marginal net benefit is positive.

**Marginal partition** — classify by what the **next** coarse cell would do:

```
KC1 = K * (c + 1)            # fine completion of the next cell
KC1 <= lo  -> S_E            # next cell still ≤ lower edge: a right step still helps
KC1 >  hi  -> S_T            # next cell exceeds upper edge: a right step hurts
else       -> S_D            # next cell lands in (lo, hi]: slope-0
```

**Shift distance** (unchanged — floor):
`Δ₁ = min( ⌊lo/K⌋ − c  over S_E,   ⌊hi/K⌋ − c  over S_D )`, `Δ = min(Δ₁, Δ₂)`.

**No guard.** Under the marginal partition, whenever `W_E > W_T` every `S_E`/`S_D`
member has `Δ₁ ≥ 1` (proof below), and `Δ₂ ≥ 1` whenever finite, so `Δ ≥ 1`
always. The `Δ == 0 → j -= 1` branch becomes dead code and is deleted; the loop is:

```
if W_E > W_T:
    Δ = min(Δ₁, Δ₂)        # provably ≥ 1
    shift block [j, block_end] right by Δ      # j stays fixed
else:
    j -= 1
```

### Why `Δ₁ ≥ 1` for every marginal `S_E`/`S_D` member
- `S_E`: `K(c+1) ≤ lo ⟹ c ≤ lo/K − 1 ⟹ c ≤ ⌊lo/K⌋ − 1 ⟹ ⌊lo/K⌋ − c ≥ 1`.
- `S_D`: `K(c+1) ≤ hi ⟹ c ≤ hi/K − 1 ⟹ ⌊hi/K⌋ − c ≥ 1`.

### Why `K = 1` is byte-identical
At `K=1`: `S_E` marginal `c+1 ≤ lo ⟺ c < lo` = current-cell `K·c < lo`; `S_T`
marginal `c+1 > hi ⟺ c ≥ hi` = current `K·c ≥ hi`. Partitions coincide, `Δ₁`
formula is unchanged, and the deleted guard never fired at `K=1` anyway (floor
distances are `≥ 1`). So all `time_factor=1` callers (every non-CSR caller and the
final reconstruct, `schedule_build.py:112`) are unaffected.

### Overshoot-safety (unchanged invariant)
`Δ ≤ Δ₁ ≤ ⌊lo/K⌋ − c` for `S_E` ⟹ `K(c+Δ) ≤ K⌊lo/K⌋ ≤ lo`: an early job never
crosses into tardy. Identical argument for `S_D` vs `hi`.

## File-by-file changes

### `src/ffc_ddw_sum_et/solution/ffc_schedule.py` — `insert_idle_time` body (≈ L1598–1663)

1. **Partition loop** (≈ L1624–1633): replace
   `KC_j = K * ends[i]` / `if KC_j < d_lo … elif KC_j >= d_hi …` with the marginal
   test `KC1 = K * (ends[i] + 1)` / `if KC1 <= d_lo: s_e … elif KC1 > d_hi: s_t …
   else: s_d`.
2. **Δ₁ loop** (≈ L1638–1646): unchanged (floor `d_lo // K - ends[i]` for `s_e`,
   `d_hi // K - ends[i]` for `s_d`).
3. **Shift / guard** (≈ L1647–1657): delete the inner `if delta > 0: … else: j -= 1`
   guard; shift unconditionally when `sum_e > sum_t` (the outer `else: j -= 1`
   stays). Optionally `assert delta >= 1` behind a comment citing the proof.
4. **Docstring** (L1574–1597): replace the floor/`Δ₁==0`-guard paragraphs with the
   marginal-partition rule + the three invariants (no-stall, overshoot-safe,
   `K=1`-identical) and a one-line note that the merged-block stall is removed.
5. Leave the `ewt_map.get(..., 1)` / `twt_map.get(..., 1)` defaults as-is (out of
   scope).

### No other source changes
`coarsen_solve_reconstruct.py`, `schedule_build.py`, the seed builders, and
`compute_weighted_earliness_tardiness` are untouched; only the rounding/partition
inside `insert_idle_time` changes.

## Correctness obligations (discharge via the deferred tests)
1. **`K=1` byte-identity** against a locked pre-change fixture (all non-CSR callers
   + final reconstruct).
2. **Overshoot-safety**: no job starting early ends tardy after a shift (`K>1`).
3. **No-stall / termination**: the marginal greedy terminates and never hits
   `Δ = 0` while `W_E > W_T`.
4. **Merged-block fix (regression lock)**: the standalone-sim case (left job at
   cell 1, `wET=2510` under shipped guard) now reaches cell 6/7, `wET=150`.

## Test scope (constructed separately)
- `tests/solution/test_ffc_schedule.py`: K=1 identity; overshoot-safety
  (single-job sub-grid windows); merged-block divergence lock; termination.
- `tests/algorithm/test_coarsen_solve_reconstruct.py`: end-to-end CSR still runs;
  final (K=1) objective regression-locked (must be unaffected — reconstruct is K=1).
- v3/v4 paired seed builders (`dispatcher/test_paired.py`): coarse seed places no
  early job into tardy.

## Execution order (TDD, in the separate conversation)
1. Write K=1 identity + overshoot tests (green under shipped) — guard against
   regressions.
2. Write merged-block divergence test (red under shipped guard) → apply the
   marginal refactor → green.
3. `uv run ruff check` / `uv run ruff format`; full `uv run pytest`.

## Open items
- Confirm deleting the guard (vs keeping a defensive `if delta > 0`) — the proof
  says it is dead, but a guarded `assert` documents intent. Recommend delete +
  comment.
- Decide whether Plan F ships alone or as the simpler fallback if Plan L's
  seed-quality gain does not measure out (see explainer §8 Q3).
