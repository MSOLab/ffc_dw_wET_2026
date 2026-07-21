# CSR coarsening rule — `cumulative` (prefix-cumulative preserve rounding)

- **Date:** 2026-07-21
- **Status:** 📝 planned (pre-implementation)
- **Prerequisite (required reading):**
  [`../20260720/csr_coarsening_rounding_modes.md`](../20260720/csr_coarsening_rounding_modes.md)
  — the `ceil`/`round`/`floor` 3-mode experiment ended as a **negative result**
  (§9, Phase 2 FAIL). That document's §8 explicitly left open the "sum-preserving
  scheme (largest-remainder family)" as a separate idea — that is this `cumulative`
  mode. Hence a separate plan document.

---

## 1. What is being added

Add a fourth value **`cumulative`** to `coarsen_solve_reconstruct`'s `coarsen_mode`.
Unlike the existing three modes, which divide/round each operation **independently**,
`cumulative` **rounds the cumulative sum job-by-job, stage-by-stage** and derives
per-stage values by subtraction.

### Definition (one job, stage `i = 0 … c-1`, original processing time `p[i]`, factor `K`)

Let `C_i` be the rounded value of the original cumulative sum up to stage `i`:

```txt
C_i    = round( (p[0] + p[1] + … + p[i]) / K )         # round cumulative up to i
p'[i]  = max( C_i − (p'[0] + p'[1] + … + p'[i-1]),  1 ) # subtract prior coarse values, floor 1
```

- **First stage (`i = 0`):** prior sum is 0, so `p'[0] = max( round(p[0]/K), 1 )`.
  Matches the user spec exactly: "first operation → round original p, max(round,1)".
- **Second stage onward:** "sum processing times from first stage to current stage,
  round, then subtract all prior coarsened values, max(computed, 1)".

> **⚠️ Must implement as running-sum recursion — do NOT vectorize as `C_i − C_{i-1}`.**
> When the `max(·, 1)` floor triggers at some stage, the actual cumulative coarse sum
> `Σ p'[<i]` exceeds `C_{i-1}`. The spec says "subtract all prior coarsened values",
> so the running sum of **actually assigned coarse values** must be subtracted, not
> `C_{i-1}`. If the floor never triggers the two are identical via telescoping, but
> they diverge the moment it does.

### Rounding convention

Same as the existing `round` mode: `numpy.round` (banker's rounding, `round(0.5)=0`,
`round(2.5)=2`). No floor is applied to `C_i` itself — the floor is only on the
per-stage derived `p'[i]` (`C_i` is monotonically increasing, so the final `max`
prevents negative/zero).

### Worked example — small instance (`tests` `_make_small_instance`)

`p`: `j0 = [10, 20]`, `j1 = [30, 40]`, `K = 7`.

| job | stage | orig cumulative | `C_i = round(sum/7)` | `Σ p'[<i]` | `p'[i] = max(C_i − Σ, 1)` |
| --- | --- | --- | --- | --- | --- |
| j0 | i0 | 10 | round(1.43)=1 | 0 | **1** |
| j0 | i1 | 30 | round(4.29)=4 | 1 | **3** |
| j1 | i0 | 30 | round(4.29)=4 | 0 | **4** |
| j1 | i1 | 70 | round(10.0)=10 | 4 | **6** |

→ `cumulative` expected: `j0=[1,3]`, `j1=[4,6]`.
(For this instance `round` mode also yields `j0=[1,3], j1=[4,6]` — no floor triggered
and only 2 stages so the two modes coincidentally match. They diverge when there are
more stages or when the floor triggers. A separate test case with floor-triggering is
added to lock in the recursive behavior — see §3.)

## 2. Why worth trying (and why one should be skeptical)

### Theoretical appeal — cancel path error at job completion points

The key quantity measured in the prerequisite document §2 is **path error** (the
processing-time error accumulated along one job's c stages). Independent rounding
accumulates ±K/2 error **per stage**. `cumulative` is designed to **bound the
cumulative processing-time error up to each stage to ≤ K/2**: when the floor does
not trigger,

```txt
Σ_{s≤i} p'[s] = C_i = round( Σ_{s≤i} p[s] / K )   ⇒  |K·Σp'[≤i] − Σp[≤i]| ≤ K/2
```

That is, **the cumulative error at every prefix (every intermediate completion point
along the path) is bounded to a single operation's worth**. This attacks exactly the
point where the prerequisite document said "we are not touching variance" (§8) —
eliminating the **cumulative (bias) component** of path error.

### Honest prior — it probably won't work

Two lessons from the prerequisite experiment apply unchanged to `cumulative`:

1. **§2's κ=2 counterexample:** at κ=2 `ceil` distortion is already minor (2.5% of
   window width), yet RPDf still collapsed −4.44%→15.19% (a 19.6pp drop). The dominant
   mechanism is not rounding bias but **resolution loss** (different fine schedules
   collapsing to the same coarse schedule). `cumulative` reduces bias further but does
   not restore resolution.
2. **§9's τ-proxy failure:** `round` dramatically improved offline Kendall τ (κ=8:
   0.471→0.841) but **did not transfer at all to final quality** (`ceil` still wins at
   κ=8 by 1.70pp). Therefore **for `cumulative` too, do not decide solve/not-solve
   based on τ-like offline proxies.** The Phase 0 fidelity gate is **run but for
   reference only**; go/no-go is judged by actual solve results alone.

**Bottom-line posture:** this is not sold as a "fix". It is a cheap isolated test of
the separate hypothesis left open by prerequisite §8. If it fails (as expected),
"eliminating path-cumulative bias still cannot salvage the κ>1 penalty → resolution
loss is the true cause" — a conclusion **one step stronger** than the prerequisite,
and a publishable negative result.

## 3. Implementation (TDD)

The reconstruction side **does not need changes.** The prerequisite document §4 fix
(assignment+order-based, feasible even when `K·p' < p`) is already applied, so
`cumulative` breaking `K·p' ≥ p` like `round`/`floor` does is handled by the same
reconstruction logic. Thus this work is scoped to **coarsening rule + option wiring
+ tests**.

### 3.1 Core — `FFcDDWParameters.coarsen_processing_times`

`src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py:282`

- Extend `mode` parameter type to `Literal["ceil","round","floor","cumulative"]`.
- Add `"cumulative"` to `_valid_modes` set (`:328`).
- Add `cumulative` branch in the df dispatch (`:342-347`). The df is **row=job,
  column=stage (stage order preserved)** (see `base/job_stage_p.py:11`, `:70`'s
  `iloc[:, ::-1]` as proof), so stage accumulation is on `axis=1`.

```python
elif mode == "cumulative":
    values = df.to_numpy()                              # (n_job, n_stage), original p
    cum = np.round(np.cumsum(values, axis=1) / factor)  # C_i (no floor), float
    new = np.empty(values.shape, dtype=int)
    running = np.zeros(values.shape[0])                 # per-job Σ p'[<i]
    for col in range(values.shape[1]):
        p_col = np.maximum(cum[:, col] - running, 1)
        new[:, col] = p_col
        running = running + p_col
    new_df = pd.DataFrame(new, index=df.index, columns=df.columns)
```

- **Red tests** (`tests/parameters/test_ffc_ddw_params.py`, added next to existing
  round/floor tests near §435+):
  - `test_coarsen_processing_times_mode_cumulative` — exact expected values from
    the worked example above (`j0=[1,3], j1=[4,6]`, K=7).
  - `test_coarsen_processing_times_mode_cumulative_lower_bound_recursion` —
    **a floor-triggering instance** constructed to demonstrate running-sum recursion
    diverging from `C_i − C_{i-1}` differencing. (e.g. `p=[1,1,1,…]` type where
    early stages floor to 1, later stages catch up the cumulative.)
  - `test_coarsen_processing_times_mode_cumulative_all_ge_1` — every op ≥ 1.
  - `test_..._cumulative_preserves_due_windows` / `..._name_suffix` — due window
    preservation + name is `..._coarsen_k{K}_cumulative` (the existing generic
    branch at `:357` handles this automatically).

### 3.2 Option wiring — `CoarsenSolveReconstructOption`

`src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`

- `coarsen_mode` field type (`:168`): `Literal["ceil","round","floor","cumulative"]`.
- `__post_init__`'s `valid_modes` (`:199`): add `"cumulative"`.

### 3.3 Controller wiring (3 Literal locations)

`src/ffc_ddw_sum_et/orchestration/controller.py` — extend the `coarsen_mode` type
hint at all 3 locations identically (value passing is already string-through, no
logic change needed): `:1627`, `:2659`, `:2829`.

### 3.4 Option acceptance test update

`tests/algorithm/test_coarsen_solve_reconstruct.py:155`
`test_all_valid_coarsen_mode_values_accepted` iteration tuple: add `"cumulative"`.

### 3.5 Wrap-up

- `uv run ruff check` / `uv run ruff format`
- Verify all tests green (`uv run pytest`).

## 4. Experiment (post-implementation, optional)

Same grid/budget as the prerequisite, adding only `cumulative` on the `coarsen_mode` axis.

- **Config:** clone/extend `metadata/20260720/csr_coarsen_mode_T06.yaml`.
  To the existing 16 scenarios (k=1 ceil × 1 + k∈{2,4,8,16,32}×{ceil,round,floor}),
  add **k∈{2,4,8,16,32}×{cumulative} 5 scenarios** (`coarsen_mode: cumulative`,
  remaining flow copied verbatim from the corresponding k's ceil scenario — no
  transcription). Pilot first with the working-tree `ins_index: [60, 61]` to confirm
  wiring/no-crash, then promote to the commented-out 160-instance (T=0.6, R=0.2) list.
- **Comparison baseline:** **same-κ paired per-instance Δpp vs `ceil`**. Inherit the
  prerequisite's pre-agreed success criterion — `cumulative` must beat `ceil` at the
  same κ **by more than the κ=1↔κ=2 gap (11.20pp in that experiment)** to claim "the
  rule is the cause". Smaller than that confirms "the rule was not the cause" —
  negative result.
- **Gate:** judged by solve results only. No substituting τ-style offline proxies
  (§2, prerequisite §9).

Once results are in, document as SSOT merged analysis under `plans/analysis/<date>/`
and append a progress log below.

## 5. What this work does NOT do

- Does not pit κ>1 against κ=1 — the prerequisite §2 argued that is likely impossible
  and this document agrees.
- Does not touch due windows / objective function.
- Does not touch reconstruction logic (already rule-agnostic feasible).
- **Variance is still only partially addressed** — `cumulative` cancels path-cumulative
  **bias** but per-operation variance (±K/2) remains. A true variance reduction like
  machine-level largest-remainder is a separate idea and is not folded in here.

## 6. Progress log

_(to be filled after implementation/experiment)_
