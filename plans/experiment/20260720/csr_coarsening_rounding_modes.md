# CSR coarsening rule — `ceil` → `max(round(p/K), 1)`

- **Date:** 2026-07-20
- **Status:** Phase 2 pending (Phase 0 pass, Phase 1 done)
- **Motivating analysis:** [`../../analysis/20260720/csr_presentation_merged_reports.md`](../../analysis/20260720/csr_presentation_merged_reports.md),
[`../../analysis/20260719/csr_init_k_budget_consolidation.md`](../../analysis/20260719/csr_init_k_budget_consolidation.md)
- **Source:** deck TODO (`vault/20260719_p3_정리.pdf` p14, "Processing time coarsening 방식 다변화")

---

## 1. Question

Every CSR result to date uses `p' := ceil(p/K)`. Coarsening loses monotonically
in K at every budget. **Is the loss caused by the rounding rule, or by
coarsening itself?**

If the rule: an unbiased rule should recover part of the loss and make κ>1
competitive. If coarsening itself: no rule change helps, and the CSR direction
should be closed in favour of the deck's other TODO (ISW-CP batch-size tuning).

The deck lists four variants. **They do not reduce to four experiments** — see §3.

## 2. Why the rule is a plausible culprit — measured

`ceil` is systematically **positive**: it inflates every processing time. Over
150 000 operations (160-instance sample of the PRA2017 large grid; `p` median 50,
due-window width median 160):

| rule | metric | κ=2 | κ=4 | κ=8 | κ=16 | κ=32 |
| --- | --- | --- | --- | --- | --- | --- |
| `ceil` (current) | bias in effective `p` | +1.0 % | +3.0 % | +7.2 % | +15.4 % | +31.9 % |
| | ‖path error‖ ÷ DW width | 2.5 % | 6.9 % | **15.6 %** | 33.8 % | 70.6 % |
| `max(round, 1)` | bias | 0.0 % | +0.1 % | +0.5 % | +2.4 % | +9.9 % |
| | ‖path error‖ ÷ DW width | 0.6 % | 1.2 % | **3.1 %** | 6.9 % | 22.5 % |
| `max(floor, 1)` | bias | −1.0 % | −2.8 % | −5.8 % | −9.8 % | −10.1 % |
| | ‖path error‖ ÷ DW width | 1.9 % | 6.2 % | 12.5 % | 21.9 % | 26.2 % |

`path error` is the error accumulated along one job's c stages, in original time
units. **The ratio to due-window width is the meaningful column**: the objective
is weighted earliness/tardiness against a window, so an error comparable to the
window width is noise that swamps the signal the coarse solver optimizes.

`max(round, 1)` cuts that noise ~5× at κ=8. That is the whole case for the idea.

### Why the effect should propagate to solution quality

CSR already **re-evaluates every harvested candidate at original scale** and
registers the argmin, so the coarse distortion does not corrupt the final
*selection* — only the *search*. The inner solver spends its budget optimizing a
biased surrogate and therefore produces a biased candidate pool. Reducing the
bias should improve the pool directly, with no change to selection logic.

### The counter-evidence that must be stated up front

At **κ=2 the `ceil` distortion is already tiny** (2.5 % of window width) — yet
mean RPDf degrades from −4.44 % to 15.19 %, a **19.6 %p loss**. A rounding
artifact that small cannot plausibly explain a loss that large. This is direct
evidence that **the dominant mechanism is not rounding** but the loss of
resolution itself (distinct fine schedules collapse onto the same coarse
schedule, so the inner solver cannot see the differences it needs to optimize).

**Therefore the honest prior is that this change will not rescue κ>1.** It is
worth doing because it is cheap and it *isolates* the two mechanisms — but it
should not be sold as a fix, and §5's gate is written to kill it fast.

## 3. The four variants reduce to one

| deck variant | verdict |
| --- | --- |
| ① `round(p/K)` | **not implementable as written** — produces `p'=0` for 4 % of ops at κ=8, 16 % at κ=32. A zero-length operation lets a machine process unboundedly many jobs at once; the coarse instance stops being a scheduling problem. |
| ② `max(round(p/K), 1)` | **the one real candidate.** |
| ③ `floor(p/K)` | **strictly dominated by `ceil`** — same distortion magnitude, opposite sign (−2.8 % vs +3.0 % at κ=4), *plus* the zero problem. Nothing to learn. |
| ④ `max(floor(p/K), 1)` | dominated by ②: larger bias at every κ (−9.8 % vs +2.4 % at κ=16). Worth running only as a **sign control** — if ② helps and ④ hurts symmetrically, the bias explanation is confirmed; if both behave alike, bias was never the mechanism. |

> The deck writes `min(·, 1)`. That clamps every processing time to **at most** 1,
> collapsing the instance. Confirmed with the author: `max` is intended. Fix the
> slide.

**Plan: implement ② and ④, drop ① and ③.** ④ costs one extra enum value and buys
the control condition, which is what makes a null result interpretable.

## 4. ⚠️ Blocker — reconstruction assumes `K·p' ≥ p`

**This must be solved before any experiment.**

### What the invariant actually guarantees

`reconstruct_raw_coarse_schedule` (`solution/schedule_build.py:59`) throws away
the coarse schedule's machine assignment — it extracts only `(job, stage) →
start` (`:83` discards the `_mc` key), scales those starts, reapplies the
original processing times, and hands the result to
`build_schedule_from_op_starts`, which **re-derives** machine identity by greedy
interval coloring (`:49`).

Under `ceil`, `K·p' ≥ p` holds, so every fine operation ends no later than its
coarse end scaled up: `end = sK + p ≤ sK + K·p' = (s + p')·K`. That keeps the
reconstruction *feasible* — a machine is always free at each scaled start, so
the `RuntimeError` below is unreachable.

**It does not keep the reconstruction faithful.** Shorter fine operations free
machines *earlier* than the coarse schedule did, so the `machine_end[k] <= s`
first-fit test admits machines that were still busy in the coarse solution. The
greedy picks a different one, and because every subsequent `machine_end` is now
different, the divergence cascades in both directions.

Measured over 20 PRA2017 instances × K ∈ {1,2,4,8,16,32} (deterministic
seed-only CSR, no CP noise): **~66 % of all operations land on a machine the
coarse solver did not choose**, at every K. Reconstructing faithfully instead
moves the final objective by mean +0.25 / +1.81 / +1.37 / +1.67 / −0.63 /
−0.14 % at K = 1/2/4/8/16/32, per-instance range −32 % … +26 %.

So the current reconstruction silently re-packs the coarse solution onto
different machines and re-optimizes it — **discarding part of the very decision
CSR spent its budget making.** This is a latent correctness defect independent of
the rounding-mode question, and it is **not even specific to coarsening**: the
K=1 row above is the no-coarsening case.

**Full post-mortem, blast radius, and re-run plan:
[`../../analysis/20260720/csr_reconstruct_assignment_defect.md`](../../analysis/20260720/csr_reconstruct_assignment_defect.md).**
Read it before running anything in §7 — every `ceil` number this plan compares
against was produced by the defective reconstruction.

`round` and `floor` additionally break feasibility: an operation can end past
its scaled coarse end, and in the worst case every machine is busy and it raises

```python
raise RuntimeError(f"No free machine at stage {i} for job {j} start={s}")
```

Left unaddressed, ② and ④ would not merely perform worse — they would drop
candidates outright, producing a meaningless comparison that looks like a
quality result.

### Decision — reconstruct by assignment + order, not by time

Stop treating scaled start times as the carrier of information. The coarse
solution's content is **(a) which machine each operation runs on and (b) the job
order within each machine**; its *times* are an artifact of the coarse grid and
are discarded downstream anyway by `make_semi_active` + `insert_idle_time`.

New semantics — one forward sweep over stages, per machine in coarse-start
order:

```
start[j, i] = max( end[j, i-1],  machine_end[k] )      # k = coarse assignment of (j, i)
end[j, i]   = start[j, i] + original_p[j][i]
```

Properties:

- **Total.** `machine_end` is non-decreasing within a machine and `end[j, i-1]`
  is fixed before stage `i` is processed, so no overlap and no precedence
  violation is constructible. The `RuntimeError` path becomes unreachable —
  there is no invariant left to satisfy, for any rounding rule.
- **Faithful.** The coarse assignment is read directly instead of being guessed.
  The coarsened instance shares `stage_2_machines_map` with the original
  (`ffc_ddw_params.py:332`), so machine ids transfer verbatim.
- **Already semi-active — so `make_semi_active` is dropped.** It applies the
  identical left-shift rule (`ffc_schedule.py:1024`: "as early as possible
  without changing machine order, respecting machine availability and
  precedence"), so on the raw reconstruction it is a guaranteed no-op.
  Verified on 120 real schedules (20 PRA2017 instances × κ ∈ {1,2,4,8,16,32},
  deterministic seed-only CSR): **zero operations moved.**
  `reconstruct_coarse_schedule` is now raw + `insert_idle_time`, and
  `test_reconstruct_raw_is_semi_active` pins the property that makes the
  removal safe.

This is stronger than the right-shift repair originally sketched here, which
kept trusting scaled starts as a lower bound and only patched collisions. That
repair would avoid the crash without ever restoring the coarse solver's actual
choice — leaving the re-packing defect in place under every rule.

### ⚠️ `ceil` results DO move — the baseline must be re-run

An earlier draft of this section claimed `ceil` would be bit-identical, on the
argument that greedy re-coloring already reproduced the coarse assignment. **That
was wrong** — the measurement above refutes it. The correct statement:

- **Scored results change**, but modestly: **< 1 %p RPDf on average**
  (0.10 / 0.39 / 0.56 / 0.68 %p at κ = 1/2/4/8). `final_schedule` is what reaches
  `compute_weighted_earliness_tardiness` (`coarsen_solve_reconstruct.py:559`).
- **The fix makes CSR look slightly *worse*.** Faithful reconstruction is not
  automatically better on the objective; it is *correct*, in that it scores the
  solution the coarse solver actually chose. The old behaviour was getting real
  quality from an unintended re-packing — expect to give that back.
- **κ=1 moves too** (+0.10 %p). The defect is not coarsening-specific, so there
  is no untouched reference point anywhere in the existing CSR results.
- **Existing κ-gradient conclusions survive** (their margins are 18.5–27.5 %p).
  It is *this* plan's same-κ comparison, at sub-%p resolution, that cannot mix
  pre-fix and post-fix numbers.

Consequence for §7: **every `ceil` number this plan compares against was
produced by the defective reconstruction.** The validation gate must be restated
— see §7.

The snapshot artifact also shifts meaning. `reconstructed_raw_schedule` feeds
Gantt phase `2_reconstructed_raw` (`controller.py:2812`, `:3017`) and is never
scored; phase 2 was "before `make_semi_active` **and** `insert_idle_time`" and is
now "before `insert_idle_time`". Two tests in
`tests/solution/test_schedule_build.py` pin the old contract and **must be
rewritten, not merely kept green**:

- `test_reconstruct_raw_scales_starts_by_factor` — asserts `raw_start ==
  coarse_start * factor`;
- `test_reconstruct_raw_is_pre_postprocess` — asserts the gapped last stage
  survives un-shifted into raw.

The regression bar is therefore **not** "results unchanged" — it is
behavioural: the whole suite stays green, and the two raw-snapshot tests are
replaced by assertions on the new contract (assignment preserved, order
preserved, `make_semi_active` a no-op on raw, `K·p' < p` reconstructs feasibly).

### Fallout: `factor` becomes unused

With times derived from `original_p` and precedence alone, neither
`reconstruct_raw_coarse_schedule` nor `reconstruct_coarse_schedule` reads
`factor`. The parameter is kept in both signatures (public API in `__all__`,
three call sites, referenced across `docs/algorithms/`) and documented as
retained-for-compatibility. Removing it is a separate mechanical change —
record it in `TODO.md` rather than folding it into this experiment.

## 5. Phase 0 — offline fidelity gate (do this first, no solving)

Before touching production code, answer the question the experiment would answer,
using schedules that already exist. **Cost: no solver runs.**

**Design.** The merged sweep gives 48 schedules per instance
(`output/20260720_merge_csr_k_f_sweep/20260720T171158_514111`, 48 scenarios ×
1440). For each instance, for each rule, for each κ:

1. take each of the 48 fine-scale schedules;
2. extract its per-machine job order and dispatch that order on the
   **coarse** instance built by the candidate rule;
3. score the coarse schedule with `time_factor=κ` — this is what the inner
   solver would believe;
4. score the original fine schedule — the truth;
5. compute **Kendall τ** between the two rankings across the 48.

τ measures exactly what matters: *does the surrogate rank schedules the way the
true objective does?* Start with 20 instances × κ ∈ {2,4,8} × 3 rules to size the
effect, then extend to 160 if the signal is real.

**Gate.**

| outcome | action |
| --- | --- |
| τ rises materially `ceil` → `max(round,1)` (e.g. 0.4 → 0.6 at κ=4) | proceed to Phase 1 |
| τ is similar for all rules | **stop.** Rounding is not the mechanism; §2's κ=2 counter-evidence is confirmed. Record the null result and move to ISW-CP batch tuning. |
| τ is low (≲0.3) for every rule at κ≥4 | **stop, and this is the more valuable finding**: the coarse objective does not rank schedules usefully at all, which explains the monotone loss and closes the coarsening direction on principle rather than by exhaustion. |

This gate is the point of the plan. Two of three outcomes save the re-run
entirely, and the third is a stronger scientific result than the experiment would
have produced.

## 6. Phase 1 — implementation (only if Phase 0 passes)

**TDD, in this order.**

1. **Reconstruction first, rule second.** Red: (i) a coarse schedule with
   under-allocated operations (`K·p' < p`) currently raises `RuntimeError` —
   assert it reconstructs feasibly; (ii) assert raw preserves the coarse
   machine assignment and per-machine order; (iii) assert `make_semi_active` is
   a no-op on raw. Green: §4. **Regression bar: every existing test on
   `reconstruct_coarse_schedule` stays green untouched**; the two raw-snapshot
   tests named in §4 are rewritten to the new contract.
2. `FFcDDWParameters.coarsen_processing_times(instance, factor, mode="ceil")` —
   new keyword, default preserves today's behaviour. Red: `mode="round"` gives
   `max(round(p/K), 1)`; `mode="floor"` gives `max(p//K, 1)`; both are ≥1 for
   `p ≥ 1`; due windows still preserved; `lower ≤ upper` still holds.
3. `CoarsenSolveReconstructOption.coarsen_mode: Literal["ceil","round","floor"] =
   "ceil"`, validated in `__post_init__` exactly like the existing `idle_mode`
   (`coarsen_solve_reconstruct.py:174`). Thread it through the three call sites:
   `controller.py:1648`, `controller.py:2890`,
   `coarsen_solve_reconstruct.py:450`.
4. Red: an end-to-end CSR run at `mode="round"`, κ=4 registers a valid
   original-scale solution and **drops no candidates for reconstruction
   failure** — this is the assertion that guards §4.
5. `uv run ruff check` / `uv run ruff format`.

**Drive-by fix.** `CoarsenSolveReconstructOption`'s docstring
(`coarsen_solve_reconstruct.py:157-158`) still says the factor is applied to
"processing times **and due-window bounds**". Due windows have been preserved at
original scale since the 2026-06-30 SSOT review. Correct it while adding
`coarsen_mode`.

## 7. Phase 2 — experiment

Only the axis under test moves; everything else is held at the settings the
current results use.

- **Grid:** `coarsen_mode ∈ {ceil, round, floor}` × κ ∈ {2, 4, 8} × flow `full`.
  κ=1 is a no-op for every rule (`rule(p/1) = p`) and is included **once** as the
  reference line, not three times.
- **Budget:** f = 25 % (`0.0225nc`), unchanged — the point is to explain the
  existing numbers.
- **Data:** the 160-instance (T,R)=(0.6,0.2) subset first. Promote to the full
  1440 grid only if a mode wins there.
- **Config:** new `metadata/20260720/csr_coarsen_mode.yaml`, scenario flows
  copied from `metadata/20260713/csr_init_methods.yaml` (do not transcribe).
- **Cost:** ~21 s/instance × 160 ÷ 12 workers ≈ 5 min per scenario; 9 scenarios
  ≈ 45 min.

### Validation gate — restated after §4

The original gate ("`ceil` must reproduce 33.78 / 34.85 / 34.16 at κ=2/4/8")
**is void.** Those values were produced by the defective reconstruction, which
re-packed operations onto machines the coarse solver did not choose. The fixed
reconstruction moves the objective ~1–2 %p, so an exact reproduction would now
be evidence the fix did *not* take effect.

There is **no untouched anchor to validate against** — κ=1 moves too (+0.25 %),
because the defect was never coarsening-specific. So the gate cannot be
"reproduce a known number"; it has to be a re-baseline.

Replacement gate:

1. **Re-baseline.** Re-run `ceil` at κ ∈ {1, 2, 4, 8} on the 160-instance subset
   with the fixed reconstruction. This becomes the new reference line, and it
   replaces the 33.78 / 34.85 / 34.16 figures everywhere they are quoted.
2. **Direction preserved.** The monotone-loss-in-κ pattern that motivates this
   whole plan (§1) must survive re-baselining. It is expected to: the fix moves
   RPDf by < 1 %p while the κ=1↔κ=2 gap is 18.5–27.5 %p. Confirm rather than
   assume, but do not plan around it failing.

Only after both hold does the `round`/`floor` comparison mean anything.

**Why the re-baseline is still mandatory despite being small.** §7's success
criterion compares rules *at the same κ*, where the effect being hunted is itself
sub-%p. The reconstruction shift lives at exactly that scale (0.10–0.68 %p mean,
κ-dependent), so mixing pre-fix and post-fix numbers would inject a bias of the
same order as the signal. The κ-gradient conclusions tolerate this; a same-κ
rule comparison does not.

### Success criterion, stated in advance

`max(round,1)` must **beat `ceil` at the same κ by more than the κ=1↔κ=2 gap is
wide** to matter. Anything smaller means coarsening still loses to not
coarsening, and the finding is "the rule was not the problem" — which is a
publishable negative result, not a failure. Pre-committing this prevents reading
a 1 %p improvement as vindication.

## 8. What this does not do

- It does not make κ>1 competitive with κ=1 — §2 argues it probably cannot, and
  §7 pre-commits to saying so.
- It does not touch due windows or the objective, per the deck's constraint.
- It does not address the **variance** of the coarse objective, only its bias.
  Per-operation error stays ±K/2 under any deterministic rule. A sum-preserving
  scheme (largest-remainder rounding across a machine's operations) could attack
  the variance, but that is a different idea and should not be smuggled in here.
- It does not explain the **reconstruction gap** (`coarse_obj / restored_obj` =
  1.02 / 1.06 / 1.14 / 1.48 at κ=2/4/8/16), which is a separate measurement of
  the same surrogate's unreliability and is what Phase 0 quantifies properly.

## 9. Progress log

### 2026-07-20 — Phase 0: offline fidelity gate ✅ PASS

**Script:** `scripts/20260720/analyze_csr_surrogate_fidelity.py`
**Data:** `output/20260720_merge_csr_k_f_sweep/20260720T171158_514111` (48 schedules × 1440 instances)
**Command:** `--limit 0 --kappas 1 2 4 8 16 32 --workers 48`

| κ | ceil τ | round τ | floor τ |
| --- | --- | --- | --- |
| 1 | 1.000 | 1.000 | 1.000 |
| 2 | 0.927 | 0.971 | 0.975 |
| 4 | 0.719 | **0.947** | 0.934 |
| 8 | 0.471 | **0.841** | 0.864 |
| 16 | 0.384 | 0.526 | 0.744 |
| 32 | 0.284 | 0.287 | 0.375 |

**Gate:** τ rises materially ceil → round (κ=4: 0.719→0.947, κ=8: 0.471→0.841).
All three outcomes point to "proceed". Notably `floor` beats `round` at κ=16,32
— the sign-control hypothesis (floor worse) is rejected.

**Output:** `analysis/20260720_csr_surrogate_fidelity/surrogate_fidelity.csv`

### 2026-07-20 — Phase 1: implementation ✅ COMPLETE

| Item | Status |
| --- | --- |
| Reconstruction fix (assignment+order, `make_semi_active` removed) | already applied (`reconstruct_method_ab`) |
| `coarsen_processing_times(mode=...)` | `ceil`/`round`/`floor` implemented |
| `CoarsenSolveReconstructOption.coarsen_mode` | field + `__post_init__` validation added |
| Call sites (controller ×3, coarsen_solve_reconstruct ×1) | mode threaded |
| Docstring drive-by fix | due-window wording fixed |
| Instance name `_coarsenp` → `_coarsen_k` | renamed |
| Tests | 10 new (formula, ≥1, validation, naming) |
| ruff check + format | clean |
| Test suite | 374 passed |

### 2026-07-20 — Phase 2: experiment ⏳ PENDING

**Config:** `metadata/20260720/csr_coarsen_mode_T06.yaml`
**Grid:** 16 scenarios = k=1 (ceil once) + k∈{2,4,8,16,32} × {ceil, round, floor}
**Data:** 160-instance (T=0.6, R=0.2) subset
**Run dir:** `output/20260720_csr_coarsen_mode/`

Not yet run.
