# Replace the initializer with CSR κ=1, then widen the ISW-CP batch

- **Date:** 2026-07-21
- **Status:** code change and config done (§9); the 1440-grid run has not been
  launched
- **Predecessor:** [`../20260720/csr_coarsening_rounding_modes.md`](../20260720/csr_coarsening_rounding_modes.md)
  — closed the coarsening direction (κ>1 loses under every rounding rule)
- **Baseline plan:** [`../20260517/ablation_ladder_plan.md`](../20260517/ablation_ladder_plan.md)
  (C5 = the current proposed algorithm)

---

## 1. Plan in one line

Add `extra_batch_size_expr` to `incremental_sw_cp` so `m+2` is expressible (§4),
then run one experiment (§6) that replaces the C5 initializer with
`coarsen_solve_reconstruct(factor=1)` at **f=20 %** and **f=30 %**, with the
widened ISW-CP batch, on the full 1440-instance grid.

## 2. Why this is not the coarsening idea that just died

`coarsen_processing_times(instance, 1, mode)` is the identity for all three modes
(`ceil(p/1) = round(p/1) = p//1 = p`). At κ=1 the CSR step reduces to:

1. run its five-step `solve_flow` under a **sub-budget** `f × 0.09nc`;
2. **harvest every intermediate candidate** the inner flow produces;
3. re-score all candidates at original scale and **register the argmin**.

No coarsening is involved. The value is the harvest-and-argmin wrapper around a
compressed ladder, which is why the 2026-07-20 negative result does not touch
this. Worth stating explicitly, because the step is named
`coarsen_solve_reconstruct` and reads like it should have died with κ>1.

## 3. The premise, measured

Mean RPDf vs `BKS_data`, lower is better. `output/20260624/20260624T100235_282833`
and `output/20260709T231643_016242` share **byte-identical** step parameters for
`calc_mcf_lb_and_derive_full_sch`, `run_flip_makespan_cp_from_incumbent`, and
`neh_cp` (verified by config diff), so their rungs compose into one ladder:

| rung | budget | all 1440 | (T,R)=(0.6,0.2) |
| --- | --- | --- | --- |
| `mcf_lb` | ~0 | 53.92 | 46.09 |
| `mcf_lb + flip` | 0.009nc | 46.41 | 41.49 |
| `neh_cp` alone (unseeded) | 0.027nc | 29.30 | 38.56 |
| `mcf_lb + flip + neh_cp` (C5 init) | 0.036nc | **10.56** | **32.28** |
| **CSR κ=1, f=20 %** | **0.018nc** | **−2.59** | **24.12** |
| **CSR κ=1, f=30 %** | **0.027nc** | **−5.60** | **22.83** |

CSR κ=1 dominates every rung at or below that rung's cost: f=20 % beats the
0.036nc C5 initializer by **13.15 %p while costing half**, and beats unseeded
`neh_cp` (same 0.027nc as f=30 %) by 31.89 %p. This is not a narrow win over one
baseline — it is a win over the whole existing initializer family.

**Why both f=20 % and f=30 %.** κ=1 quality improves monotonically with f, with
no knee:

| f | 5 % | 10 % | 15 % | **20 %** | 25 % | **30 %** |
| --- | --- | --- | --- | --- | --- | --- |
| all 1440 | 26.51 | 6.10 | 0.55 | **−2.59** | −4.44 | **−5.60** |
| (0.6,0.2) | 35.62 | 28.55 | 25.87 | **24.12** | 23.40 | **22.83** |

So f is a pure budget-split choice, not a quality optimum: f=30 % buys 3.01 %p
more initializer quality than f=20 % but hands 0.009nc less to the CP-SAT tail.
The two arms measure whether that trade pays after the full pipeline. f=30 % is
also the point where CSR κ=1 costs exactly what unseeded `neh_cp` costs, which
makes the 31.89 %p gap a clean like-for-like statement.

### Why these numbers are comparable

Three threats were checked, not assumed:

1. **The CSR figures predate the reconstruction fix `a6c4150`.** Measured impact
   at κ=1 is **+0.01 %p**: the same 160-instance slice at f=25 % scores 23.40
   pre-fix and 23.41 post-fix (`20260720_csr_coarsen_mode/…/csr_k1`). The
   predecessor plan's +0.10 %p estimate was conservative.
2. **The initializer figures predate the fix by 10+ days.** `a6c4150` touches
   only `reconstruct_coarse_schedule` / `build_schedule_from_op_starts` and
   `coarsen_solve_reconstruct.py`; the prefix flow never calls CSR.
3. **Different instance sets.** Every row is computed from per-instance CSVs on
   the same two slices, never quoted across sets.

**Caveat.** In `20260709T231643_016242`, `run_flip_makespan_cp_from_incumbent`
fired on 1426/1440 and `neh_cp` on 1423/1440 — a few instances ran out of wall
clock first. Scenario A re-measures it rather than quoting it.

### ⚠️ The hard slice is structurally initializer-insensitive

The ladder spans **43.36 %p** on the full grid (53.92 → 10.56) but only
**13.81 %p** on (T,R)=(0.6,0.2) (46.09 → 32.28) — a 3.1× compression.
Initialization quality simply matters less there, for reasons unrelated to which
initializer is used.

This is why §6 runs the **full 1440 grid** rather than the 160-instance subset
used for the coarsening work: a subset null would be a false negative, not a
strong negative, and a subset win would understate the effect. It is also why
results must be **stratified by T**.

## 4. Code change — `batch_size: 'm+2'` does not parse

`resolve_value_expr` (`orchestration/value_resolver.py:21`) accepts only
`<number>nc`, `<number>n`, `<number>c`, `<number>m`, or a bare float. `"m+2"`
matches no suffix branch and `float("m+2")` raises, so it exits via the
`ValueError` at `:44`. **`incremental_sw_cp(batch_size="m+2")` crashes at step
entry** (`controller.py:2533`).

### Mirror `neh_cp`'s existing `extra_batch_size_expr`

`neh_cp` already solves this: it takes `extra_batch_size_expr`, resolves it, and
adds it to the batch size (`controller.py:2144`, `:2189-2193`, `:2216`). Add the
same parameter to `incremental_sw_cp` (`controller.py:2459`):

```python
batch_size_resolved = max(1, ceil(resolve_value_expr(batch_size, n, c, m)))
if extra_batch_size_expr is not None:
    extra = resolve_value_expr(extra_batch_size_expr, n, c, m)
    if extra is not None:
        batch_size_resolved += int(extra)
```

Then `batch_size: 'm'` + `extra_batch_size_expr: 2` expresses `m+2`.

**Rejected: generalising `resolve_value_expr` to parse `m+2`.** It is shared by
every timing parameter in the controller (`cp_tl`, `total_timelimit`,
`timelimit`, …), so an arithmetic grammar there has a blast radius far beyond
this experiment, for one call site a two-line addition already covers. Record it
in `TODO.md` if a second call site ever wants it.

### Tests (TDD, red first)

1. `resolve_value_expr("m+2", …)` raises — pins the reason this parameter exists.
2. `incremental_sw_cp(batch_size="m", extra_batch_size_expr=2)` resolves to `m+2`.
3. `extra_batch_size_expr=None` leaves today's behaviour byte-identical.
4. The resolved value is still floored at 1.
5. `factor=1` coarsening is the identity for all three modes on a real instance —
   pins §2, so the dead coarsening result cannot contaminate this.

Also assert the log line at `controller.py:2546` reports the **final** batch
size, not the pre-offset one — otherwise every run log misattributes its own
configuration.

Then `uv run ruff check` / `uv run ruff format`; full suite green. No new
algorithm code; everything else in this plan is configuration.

### What `m+2` is hypothesised to do

`batch_size` sets the sliding window's width in jobs, and
`batch_count = ceil(job_count / batch_size)` (`controller.py:2577`). At
`batch_size = m` each batch is exactly one machine-width of jobs, so a job can
only be reordered against the m−1 jobs sharing its slot. Widening to `m+2` gives
the CP two jobs of overlap across the machine boundary, at the cost of a larger
sub-model per step and fewer steps within the same `total_timelimit`. A genuine
trade, not a free improvement — §6 keeps it separable so a loss is attributable.

## 5. Budget arithmetic

Outer budget 0.09nc throughout. `solve_base_model_cpsat` is not separately
configured — it runs only if anything is left when ISW-CP returns, which is
intended and is usually nothing (see below).

| step | C5 today | proposed, f=20 % | proposed, f=30 % |
| --- | --- | --- | --- |
| `calc_mcf_lb_and_derive_full_sch` | untimed | — | — |
| `run_flip_makespan_cp_from_incumbent` | 0.009nc | — | — |
| `neh_cp` | 0.027nc | — | — |
| `coarsen_solve_reconstruct(factor=1)` | — | **0.018nc** | **0.027nc** |
| `incremental_sw_cp` (per inner `sw_cp` call) | 0.018nc | 0.018nc | 0.018nc |
| `solve_base_model_cpsat` | opportunistic | opportunistic | opportunistic |

ISW-CP's `total_timelimit` is held at 0.018nc in every arm so the batch-width
change is not confounded with a budget change.

### `incremental_sw_cp` is not capped at 0.018nc — the tail is opportunistic

`total_timelimit` is **per inner `sw_cp` call**, not a cap on the composite:
`incremental_sw_cp` passes it down through `base_kwargs` and each
`unfixed_batch_count` re-resolves it. With counts 2..6 that is 5 × 0.018nc =
**0.09nc**, the entire outer budget. ISW-CP therefore runs until the global wall
clock unless it converges early, and `solve_base_model_cpsat` gets whatever is
left — often zero.

Measured on the 2026-07-21 smoke (`Instance_50_5_3_0,2_0,2_10_Rep0`, 0.09nc =
22.5 s), step end times in seconds:

| arm | steps | `solve_base_model_cpsat` |
| --- | --- | --- |
| A | mcf 0.20 → flip 2.47 → neh 9.40 → ISW-CP 22.40 | never ran |
| C | same, ISW-CP 22.40 | never ran |
| B20 | CSR 4.52 → ISW-CP 22.52 | entered with ~0 s |
| B30 | CSR 6.78 → ISW-CP 22.51 | entered with ~0 s |

Two consequences for how this experiment reads:

1. **A is still a faithful C5 baseline.** This is C5's long-standing behaviour —
   `metadata/20260517/ablation_ladder_config.yaml` configures ISW-CP exactly the
   same way, so historical C5 runs did this too. Nothing here is new to the
   `extra_batch_size_expr` change.
2. **Freed initializer budget goes to ISW-CP, not to the base CP-SAT tail.**
   B20 does not hand 0.054nc to `solve_base_model_cpsat`; it hands it to ISW-CP,
   which spends it on more `unfixed_batch_count` iterations. Every "tail"
   statement below is about *post-initializer optimization* in that sense.

Not a small-instance artifact: all budgets scale with nc, so 5 × 0.018nc =
0.09nc holds at every size.

## 6. Experiment

Four scenarios, full 1440 grid, outer budget 0.09nc, `metadata/20260721/csr_init_isw_batch.yaml`.
Step parameters are copied, not transcribed:

- A / C's four steps, and B20 / B30's **outer** ISW-CP + base CP-SAT, come from
  `metadata/20260517/ablation_ladder_config.yaml` : `c5_isw_cp`. A is
  byte-identical to it; C differs only by `extra_batch_size_expr: 2`.
- B20 / B30's `coarsen_solve_reconstruct` step, inner `solve_flow` included,
  comes from `metadata/20260714/csr_tl_scaling_sweep.yaml` :
  `csr_full_d2wp_k1_tl20` / `_tl30` — the blocks that produced §3's f=20 % /
  f=30 % figures. (Not `metadata/20260720/csr_coarsen_mode_T06.yaml`, whose
  `csr_k1` is f=25 %; its four inner TL knobs are scaled by `s = f/0.25`, so
  taking the tl20/tl30 scenarios keeps that scaling exact.)

Equivalence against both sources was checked by parsing the YAMLs and comparing
the step objects, not by reading.

| id | initializer | ISW-CP batch | post-init budget | purpose |
| --- | --- | --- | --- | --- |
| **A** | C5 prefix, 0.036nc | `m` | 0.054nc | baseline — C5 as it stands today |
| **C** | C5 prefix, 0.036nc | `m+2` | 0.054nc | batch effect, isolated |
| **B20** | CSR κ=1 f=20 %, 0.018nc | `m+2` | 0.072nc | the proposal, cheap init |
| **B30** | CSR κ=1 f=30 %, 0.027nc | `m+2` | 0.063nc | the proposal, rich init |

"Post-init budget" is what ISW-CP and then, if anything survives, the base
CP-SAT tail share — see §5; it is not a base-CP-SAT allocation.

Reading it: `C−A` is the batch widening alone. `B20−C` and `B30−C` are the
initializer swap at fixed batch width. `B30−B20` is the init-vs-post-init budget
split. `B20−A` and `B30−A` are the headline end-to-end numbers.

**Cost:** 0.09nc × 1 350 000 nc ÷ 12 workers ≈ **2.81 h per scenario**; four
scenarios ≈ **11.3 h**. (Model validated against `20260709T231643_016242`:
predicted 48 600 s, actual 46 082 s — 95 %.)

**Report stratified by T**, always. The ladder span differs 3.1× across slices,
so a pooled mean hides the mechanism. `T`, `R`, `n`, `c` are already columns in
`<run>_rpdf_comparison.csv`.

### Optional 5th arm, if the result is a null

If `B20 ≈ B30 ≈ A`, add **E**: `mcf_lb + flip` only (0.009nc) + ISW-CP `m` +
a 0.072nc tail. It is the cheapest rung on §3's ladder and the largest possible
tail. `E ≈ A` would establish that initializer quality does not bind at 0.09nc at
all, which closes the whole direction; `E` clearly worse would mean it does bind
and the search should continue toward initializers better than CSR κ=1. Costs
2.81 h and is only worth running once a null has actually appeared — the
`B20`/`B30` contrast already provides a weaker version of the same signal.

## 7. Gate, pre-committed

| outcome | reading | action |
| --- | --- | --- |
| `B20` or `B30` beats `A` by ≥ 2 %p | the swap works end-to-end | adopt the winner as the new proposed algorithm |
| `B30 < B20` materially | initializer quality still pays at 0.027nc; post-initializer optimization is not the bottleneck | sweep f upward (35 %, 40 %) |
| `B20 < B30` materially | ISW-CP iterations are worth more than initializer quality | sweep f downward (10 %, 15 %) |
| `B20 ≈ B30` | initializer budget does not bind in [0.018, 0.027]nc | take f=20 %, the cheaper one; run arm E |
| `B20 ≈ B30 ≈ A` | the head start is erased downstream | run arm E to decide whether to close the direction |
| `C < A` materially | batch widening helps independently of the initializer | sweep `m+1` / `m+4` to locate the optimum |
| `C > A` | widening hurts | rerun the winning initializer at `m` before adopting |

**The `B20 ≈ B30 ≈ A` outcome is the one to take seriously.** §3's margin is
measured on the *initializer output*, but 0.054–0.072nc of further optimization
follows — mostly extra ISW-CP iterations rather than base CP-SAT time (§5) —
whose job is precisely to erase differences in starting point. A 13 %p head start
that survives to the final objective is a strong result; one that vanishes is the
expected outcome and must not be re-litigated as a measurement failure.

## 8. What this does not do

- It does not revisit coarsening. κ is pinned at 1 and no rounding mode is in
  play (§2).
- It does not tune the ISW-CP budget. `total_timelimit` is held at 0.018nc in all
  four arms so the batch-width change stays separable. It does **not** cap ISW-CP
  as a whole (§5).
- It does not test `solve_base_model_cpsat` directly, and mostly will not reach
  it: ISW-CP absorbs the freed budget, and the base CP-SAT tail runs only if
  ISW-CP converges early. That is intended, not a misconfiguration. If a gain
  turns out to come from extra post-initializer optimization rather than from a
  better incumbent, only a run holding that budget fixed can show it — which
  needs a real composite cap on ISW-CP. Out of scope; first follow-up.
- It does not produce the C1–C5 ablation table that
  `../20260517/ablation_ladder_plan.md` still owes. §3's ladder covers the C1–C3
  rungs on the full grid with identical step parameters, so that plan is closer
  to satisfiable than it looks — but its rungs span two runs on two dates, and it
  wants one.

## 9. Progress log

**2026-07-21 — §4 code change done.** `incremental_sw_cp` takes
`extra_batch_size_expr`; the offset is added to the resolved `batch_size` and the
sum is floored at 1 (floor *after* the addition, so a negative offset cannot
produce a zero-width batch). `resolve_value_expr` untouched. Five new tests in
`tests/orchestration/test_incremental_sw_cp_batch_size.py`, plus the κ=1
identity test (§2) parametrized over all three rounding modes in
`tests/parameters/test_ffc_ddw_params.py`.

**2026-07-21 — config written.** `metadata/20260721/csr_init_isw_batch.yaml`,
provenance and equivalence check per §6. `main.py:CONFIG_PATH` points at it.

**2026-07-21 — 2-instance smoke, no full run yet.**
`output/20260721_csr_init_isw_batch_smoke/20260721T014401_917439` (config
`metadata/20260721/csr_init_isw_batch_smoke.yaml`, `ins_index: [0, 1]`, 90 s,
0 errors). Confirms the parameter reaches the solver: A logs
`batch_size='m' (+None) -> 3 (m=3)`, C/B20/B30 log `(+2) -> 5`, and the inner
`sw_cp` calls follow with `batch_size=5`. B20/B30's CSR-internal ISW-CP is a
separate sub-controller and correctly stays at `m`. CSR budgets check out
(4.50 s = 0.018nc, 6.78 s ≈ 0.027nc).

This smoke is also where the §5 budget correction came from — ISW-CP runs to the
wall clock in every arm and `solve_base_model_cpsat` is opportunistic. Pre-existing
C5 behaviour, not caused by the change; the plan's wording was corrected rather
than the code.
