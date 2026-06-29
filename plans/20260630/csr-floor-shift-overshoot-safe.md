# CSR: floor-based shift in `insert_idle_time` (overshoot-safe)

Status: **PLAN — for review, not yet implemented** (execution in a separate conversation)
Date: 2026-06-30
Branch base: `20260624_more_init_dispatch`
Supersedes: `csr-cap-k-to-min-due-window-width.md` (the global-K cap — rejected; it
collapsed K to single digits on narrow-window instances).
Builds on: staged `csr-time-factor-ssot` work (`insert_idle_time(time_factor=…)`).
Source of truth for the algorithm: `vault/20260629_p3_csr.pdf` slide 6
("Idle Time Insertion After Coarsening – Floor for Shifting").

## Goal

Make coarse `insert_idle_time` **structurally overshoot-safe** (not
"benchmark-didn't-show-it" safe). The right-only NBM greedy currently uses a
**ceil** shift distance, which can push an early job *past* a sub-grid due window
straight into tardy (overshoot). Switch the shift distance to **floor**, which
provably never crosses a window. Floor is *less efficient* (it can stop short of
a reachable in-due cell) but it never creates tardiness via a shift — which is
the intent of idle insertion. Decision confirmed by user; "results may get worse,
but it matches the intent."

## Design — three changes to `insert_idle_time` (`solution/ffc_schedule.py:1566`)

Per slide 6. Let `K = time_factor`, `(lo, hi) = due_window_map[j]` (original window).

1. **Partition with the Multiplication form** (exact, no rounding — the agreed
   clean form). For each op with coarse completion `c`:
   - `K*c < lo`  → `S_E` (definitely early)
   - `K*c >= hi` → `S_T` (tardy if right-shifted)
   - else        → `S_D` (in-due, right-room)

   This replaces the `eff_window = ceil(d/K)` precompute. The partition result is
   **identical** to today's ceiling form (proven earlier), so this part is a pure
   clarity refactor.

2. **Shift distance Δ₁ uses FLOOR** (the actual behavior change):
   - `S_E` term: `lo // K - c`   ( = ⌊lo/K⌋ − c )
   - `S_D` term: `hi // K - c`   ( = ⌊hi/K⌋ − c )
   - `Δ₁ = min(those)`,  `Δ = min(Δ₁, Δ₂)`.
   (Today these use `ceil`: `eff_lo - c`, `eff_hi - c`.)

3. **Termination guard: if `Δ₁ == 0`, do NOT shift — decrement `j`** (user-confirmed).
   With floor, Δ₁ can be 0 (the binding job already sits at its floor-boundary);
   shifting by 0 with `j` fixed would loop forever. So:
   ```
   if sum_e > sum_t:
       Δ₁ = min(floor-distances over S_E ∪ S_D)
       Δ  = min(Δ₁, Δ₂)
       if Δ > 0:                      # ⟺ Δ₁ > 0, since Δ₂ ≥ 1 when finite
           shift block by Δ; keep j   # (re-evaluate same leftmost)
       else:
           j -= 1                     # floor says "can't move" → make progress
   else:
       j -= 1
   ```
   (Today's ceil code never needed this: a ceil S_E/S_D distance is always ≥ 1.)

## Correctness

### Overshoot-safety (the point)
**Claim:** no shift moves a job in `S_E` (early) to positive tardiness.
**Proof:** the block shifts by `Δ ≤ Δ₁`. For `i ∈ S_E`,
`Δ ≤ ⌊lo_i/K⌋ − c_i ⟹ c_i+Δ ≤ ⌊lo_i/K⌋ ⟹ K(c_i+Δ) ≤ K⌊lo_i/K⌋ ≤ lo_i ≤ hi_i`,
so job `i` ends with `K·C ≤ lo_i` — still early or exactly at the lower window
edge, **never tardy**. Likewise `i ∈ S_D` ⟹ `K(c_i+Δ) ≤ hi_i` (stays in-due, at
worst the upper edge with `T=0`). Already-tardy `S_T` jobs may still move right
(the NBM-sanctioned tradeoff, identical to today) but no *new* early→tardy
crossing is introduced. ∎ Holds for **all** windows, wide or narrow.

### Termination
Each loop iteration either shifts by `Δ ≥ 1` (j fixed) or decrements `j`. Floor
distances are finite and shrink as the block moves right, so eventually `Δ₁ = 0`
→ `j` decrements. `j` strictly decreases when not shifting ⟹ terminates. The
`Δ₁ == 0 → j -= 1` guard is what makes this hold (without it the floor path
hangs).

### `K = 1` invariance (scope guard)
At `K=1`, `lo // 1 = lo = ⌈lo/1⌉` and the Multiplication partition is `c` vs
`(lo, hi)` — i.e. floor = ceil = exact. So the rewrite is **byte-identical to the
current algorithm at K=1**. Therefore: non-CSR callers (all `time_factor=1`) and
the **final reconstruct** (`reconstruct_coarse_schedule` runs `insert_idle_time`
at K=1) are **unaffected**. Floor changes only the coarse (K>1) seed.

### Empirical validation (standalone sim, `scratchpad/floor_sim.py`)
| case | K | floor result | overshoot | iters | floor vs coarse-opt |
|---|---|---|---|---|---|
| synthetic `(110,120)` | 50 | C=2 held (real 100) | none | 1 | gap 0 (optimal) |
| synthetic, `w⁺=100` | 50 | C=2 held | none (ceil→3000) | 1 | gap 0 |
| real j21 `(698,712)` | 29 | C=24 held | none (ceil→91) | 1 | gap 0 |
| real j037 `(670,698)` | 51 | C=13 held | none (ceil→128) | 1 | gap 0 |
| **wide `(110,220)`** | 50 | C=1→2, then stop | none | **2 (no hang)** | **gap 10 (under-shoot)** |
| `K=1` `(110,120)` | 1 | reaches window, cost 0 | none | 2 | floor = exact |

## Tradeoff — read before approving (honest reframing)

Floor's inefficiency lands on the **common** case, not the rare one:
- **Narrow / sub-grid windows (the overshoot cases): floor is OPTIMAL** (gap 0) —
  it stops at the last-early cell, which is exactly the coarse optimum there.
- **Wide windows: floor UNDER-shoots** — it stops at `⌊lo/K⌋` (last-early cell),
  leaving residual earliness `lo − K⌊lo/K⌋ ∈ [0, K−1]` per early job, even when an
  in-due cell (cost 0) is reachable. Ceil reached it; floor doesn't. With K up to
  64 this is up to `w⁻·63` of *extra seed earliness per early job*.

**Impact is seed-only.** The final reconstruct runs at K=1 (floor = exact), so
narrow-window jobs and wide-window early jobs are both re-timed to their true
optimum in the final schedule. The under-shoot degrades only the **coarse
warm-start / seed-ranking** quality (worse incumbent fed to CP). Bounded, but on
wide-window-heavy instances (e.g. the 200/10/5 family) it touches many jobs.

**Alternative kept on file (option C):** per-job — use **ceil** when an in-due
cell exists (`⌈lo/K⌉ < ⌈hi/K⌉`, reaches in-due, optimal, no overshoot possible)
and the cost-compare/floor only when it doesn't. That is overshoot-safe **and**
wide-window-optimal, at the cost of a per-job branch. If floor's seed degradation
measurably hurts CSR results, switch to option C. (Plan implements floor per the
user's choice; this records the escape hatch.)

## File-by-file changes

### `solution/ffc_schedule.py` — `insert_idle_time` body (≈ L1596–1659)
- **Delete** the `if time_factor > 1: eff_window = ceil(...) else: eff_window =
  dict(...)` block (≈ L1599–1605).
- **Partition loop** (≈ L1629–1638): read `lo, hi = due_window_map[job_ids[i]]`,
  `Kc = time_factor * ends[i]`; classify with `Kc < lo` / `Kc >= hi` / else.
- **Δ₁ loop** (≈ L1644–1646): floor distances
  `lo // time_factor - ends[i]` (S_E) and `hi // time_factor - ends[i]` (S_D).
- **Shift / guard** (≈ L1643–1653): keep `if sum_e > sum_t:`; inside, compute
  `delta = min(delta1, delta2)`; `if delta > 0: shift; (j fixed) else: j -= 1`;
  the outer `else: j -= 1` stays. (Equivalently: `Δ₁ == 0 → j -= 1`.)
- Update the docstring: replace the ceil "effective coarse window" paragraph with
  the floor rule + the overshoot-safety + K=1-invariance invariants. Note floor is
  conservative (may leave residual earliness ≤ K−1 on wide-window early jobs).
- Leave the `ewt_map.get(..., 1)` defaults as-is (out of scope).

### No other source changes
- `coarsen_processing_times`, `run_coarsen_solve_reconstruct`,
  `_solve_coarsened_model`, the seed builders, `reconstruct_*`,
  `compute_weighted_earliness_tardiness`: unchanged — `factor` flows as today; only
  the shift rounding inside `insert_idle_time` changes. No K cap, no params change.

## Tests (TDD)

- `tests/solution/test_ffc_schedule.py`:
  1. **K=1 byte-identical regression** — extend/keep `test_insert_idle_time_tf1_is_noop`:
     floor rewrite at `time_factor=1` equals the default call (and equals the
     pre-change output on a locked fixture).
  2. **Overshoot-safety property** — single-job last stage, `K=50`, window
     `(110,120)`, op at coarse C=2: assert it stays at C=2 (real 100), **not**
     shifted to C=3 (real 150). Add the real cases (j21 `(698,712)` K=29 at C=24;
     j037 `(670,698)` K=51 at C=13): no job that starts early ends tardy.
  3. **Termination on Δ₁=0** — wide window `(110,220)`, `K=50`, op at C=1: must
     terminate (no hang) and stop at C=2 (real 100). (This is the test that hangs
     if the `Δ₁==0 → j-=1` guard is missing.)
  4. **Floor vs ceil divergence (lock the inefficiency)** — same wide-window case:
     assert floor leaves residual earliness (lands at C=2, cost `w⁻·10`), i.e.
     does *not* reach the in-due cell C=3. Documents/locks the accepted tradeoff.
- `tests/algorithm/test_coarsen_solve_reconstruct.py`:
  5. **End-to-end CSR** — on a small narrow-window instance, the pipeline runs and
     the final (K=1-reconstructed) objective is computed; regression-lock the
     value. (Final timing must be unaffected by the floor change since reconstruct
     is K=1.)
- (Optional) `tests/algorithm/dispatcher/test_paired.py`: the v3/v4 seed builders
  call `insert_idle_time(time_factor=factor)`; add a coarse-seed assertion that no
  early job is positioned into tardy.

## Execution order (TDD)
1. Write test 2 + 3 (red: current ceil overshoots / would-be hang) → implement the
   floor + guard → green.
2. Test 1 (K=1 invariance) + test 4 (inefficiency lock).
3. Test 5 (end-to-end CSR regression-lock).
4. `uv run ruff check` / `uv run ruff format`; full `uv run pytest`.

## Open decisions (confirm at execution)
1. **Floor (chosen)** vs **option C** (ceil-when-in-due-exists + safe otherwise) —
   floor is simpler and uniformly safe but under-shoots wide windows; option C is
   safe and wide-window-optimal but adds a per-job branch.
2. **Keep the Multiplication-partition refactor** (recommended — matches slide 6,
   behavior-identical, deletes `eff_window`) vs keep the ceiling partition and only
   swap Δ to floor (smaller diff, same behavior). Recommend the former.
