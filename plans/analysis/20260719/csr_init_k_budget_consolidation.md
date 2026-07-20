# CSR init / K / budget — cross-phase consolidation

**Date:** 2026-07-19 · **Status:** Phases 1–3 executed
**Plan:** [`csr_triple_analysis_plan.md`](csr_triple_analysis_plan.md)

---

## Scope — what these numbers are, and are not

Every scenario in all three phases has a **single-step outer flow**
(`subroutine_flow: [coarsen_solve_reconstruct]`). The scenario cap is `0.09nc`
while CSR consumes at most `0.027nc`, so ~70 % of the nominal budget is
deliberately left unspent. There is no tail — no outer `incremental_sw_cp`, no
`solve_base_model_cpsat`.

**Consequently every RPDf number below measures *initialization quality under a
fixed initialization budget*, not final solution quality.** The plan's Appendix
A.1 shows downstream steps can absorb upstream differences, so no conclusion of
the form "CSR init ⇒ better final solution" is supported by this data. Every
claim here is phrased as "best init at budget f".

Run-to-run variance is treated as negligible at 1440-instance means (settled
2026-07-19, plan §Overview); differences in full-grid means are taken at face
value. The 160-instance view has ~1/9 the sample and is read as directional only.

RPDf is the **symmetric** form `2(obj−ref)/(obj+ref)`, range (−2, 2). Negative
means better than `BKS_data`.

### ⚠️ Phase 1 is a **K = 4** result

Verified in `metadata/20260713/csr_init_methods.yaml`: every `csr_*` scenario in
Phase 1 carries `factor: 4`. This is load-bearing and was not stated in the plan.
Phase 1's inner-flow verdict is therefore a *K=4* verdict, and Phase 2/3 show the
flow winner is K-dependent — so Phase 1's ranking must not be read as the
unconditional answer. The two phases do not conflict; they are measurements at
different K.

Cross-check confirming the mapping: Phase 1 `csr_full_d2wp` = 21.738 % and Phase
2 `K=4, full` = 21.640 % — the pair identified in plan Appendix A.1 as the same
config across the `9b7ad2a` commit boundary.

### ⚠️ At K=1 the coarsening is a **no-op** — verified in code

`factor: 1` makes the coarsen/reconstruct *scaling* an exact identity, so the
winning setting is not really "CSR" in the coarsening sense. Verified directly
(not inferred from config):

| step | at `factor=1` | check |
| --- | --- | --- |
| `FFcDDWParameters.coarsen_processing_times` | **exact identity** — `ceil(p/1) = p`, due windows already preserved at original scale | on `Instance_50_5_3_0,2_0,2_10_Rep0`: `p` map and `due_window` map compare equal; `factor=4` differs (sanity) |
| `reconstruct_raw_coarse_schedule` | **identity on starts** — `start * 1` | reconstructed start map == input start map |
| `reconstruct_coarse_schedule` | **NOT identity** — additionally runs `make_semi_active` + `insert_idle_time` | a gapped test schedule moves `50→10, 60→20, 70→30` |

So `csr_full_d2wp` at K=1 is exactly:

```txt
[mcf_lb → flip_makespan_cp → neh_cp → incremental_sw_cp → solve_base_model_cpsat]
    ↓  (at ORIGINAL scale — no coarsening happens)
make_semi_active → insert_idle_time     (final global ET-realignment)
```

**The final ET-realignment is not an unfair extra**, which was the obvious worry:
`insert_idle_time` is called inside the baselines' own dispatchers too
(`mcf_lb/full_sch_builder.py:267`, `flip_makespan_cp/dispatcher.py:287`,
`neh_cp/dispatcher.py:215`), so both `mcf_lb_fmm` and `neh` also end ET-aligned.
The comparison is not confounded by one side getting E/T alignment the other
lacks.

**Consequence for how these results are described.** The win belongs to the
**5-step inner pipeline**, not to coarsen-solve-reconstruct. The baselines are
1–2 steps (`mcf_lb`+`fmm`, or `neh` alone) and lack `incremental_sw_cp` and
`solve_base_model_cpsat` entirely. Combined with Q7's finding that `csr_base`
(the CSR skeleton *without* an inner flow) is dead last, the honest summary is:

> Coarsening contributes nothing or less than nothing; the CSR wrapper at K=1 is
> a pass-through plus a final realign. What beats the existing initialization is
> the inner pipeline it carries.

Writing this up as "CSR beats the baselines" would misattribute the effect.

### `d2wp` is pre-committed

Phases 2 and 3 exist only for the `d2wp` priority. Phase 1's `d2wp` vs `wdp`
comparison is a **reported result, not a gate** — and it came out in favour of
the pre-committed choice (below), so nothing is stranded.

---

## Sources and reproduction

| phase | script | artifacts |
| --- | --- | --- |
| 1 | `scripts/20260719/analyze_csr_init_methods.py` | `analysis/20260719_csr_init/` |
| 2 | `scripts/20260719/analyze_csr_k_range.py` | `analysis/20260719_csr_k/` |
| 3 | `scripts/analyze_csr_tl_scaling_sweep.py` | `analysis/20260719_csr_budget_sweep/` |
| 3′ | `scripts/20260719/analyze_csr_equal_budget.py` | `analysis/20260719_csr_budget_sweep/` |
| — | `scripts/20260719/analyze_csr_vs_baseline.py` | `analysis/20260719_csr_init/` |

```bash
uv run python scripts/20260719/analyze_csr_init_methods.py
uv run python scripts/20260719/analyze_csr_k_range.py
uv run python scripts/20260719/analyze_csr_equal_budget.py
uv run python scripts/20260719/analyze_csr_vs_baseline.py
uv run python scripts/analyze_csr_tl_scaling_sweep.py \
    output/20260714_csr_tl_scaling_sweep/20260714T234921_531156 \
    output/20260714_csr_full_grid_k248/20260714T184236_642971 \
    output/20260714_csr_tl_scaling_sweep/20260715T183418_361919 \
  > analysis/20260719_csr_budget_sweep/csr_budget_sweep.txt
```

Source run directories:

| run | inst | role |
| --- | --- | --- |
| `output/20260713_csr_init_methods/20260713T195341_009592` | 1440 | Phase 1 primary (K=4, f=25) |
| `output/20260713_csr_init_methods/20260713T091912_833529` | 160 | Phase 1 secondary (`csr_fmm_base`) |
| `output/20260714_csr_full_grid_k248/20260714T184236_642971` | 1440 | K=2,4,8 @ f=25 |
| `output/20260714_csr_tl_scaling_sweep/20260715T183418_361919` | 1440 | K=1 @ f=25 gap-fill |
| `output/20260714_csr_higher_k_validation/20260714T154426_711694` | 160 | K=2..32 |
| `output/20260714_csr_tl_scaling_sweep/20260714T234921_531156` | 1440 | budget sweep f=5..30 |

All three phases read `<ts>_rpdf_comparison.csv` and its precomputed
`RPDf_BKS_data` column verbatim, so they cannot drift apart.

---

## Answers to the eight consolidation questions

### 1. Which init flow wins overall and at T=0.6?

**It depends on K — and the plan's phrasing presumed a K-independent answer that
does not exist.**

| regime | overall | T=0.6 |
| --- | --- | --- |
| K=4 (Phase 1) | `csr_neh_d2wp` 20.22 % | `csr_neh_d2wp` (wins all 3 T=0.6 cells) |
| K=1, f=25 (Phase 2) | **`csr_full_d2wp` −4.44 %** | **`csr_full_d2wp` 21.87 %** |
| K=1, f=30 (Phase 3) | **`csr_full_d2wp` −5.60 %** | **`csr_full_d2wp` 20.74 %** |

Phase 3's paired analysis states the rule directly: **`full` wins at K≤2, `neh`
wins at K≥4** (by K, all f: K=1 gap −24.59 %p → full; K=2 −1.11 → full; K=4
+2.93 → neh; K=8 +2.62 → neh).

So Phase 1's "neh wins" is correct *at K=4* and irrelevant at the K that
actually wins. **At the best setting the answer is `csr_full_d2wp`**, and its
margin at K=1 is enormous (−24.6 %p paired, 67/9/24 win/tie/loss).

### 2. Is CSR better than plain init at equal budget?

**Yes, decisively — and this is the phase's critical test.**

`csr_full_d2wp` vs `mcf_lb_fmm_25p`, both `0.0225nc`, both full pipeline, paired
on 1440 instances:

| | mean RPDf% |
| --- | --- |
| `csr_full_d2wp` (K=4) | **21.74** |
| `mcf_lb_fmm_25p` | 45.23 |
| Δ | **−23.50 %p** |
| win/tie/loss | 917 / 18 / 505 |

This holds at the *worst* CSR K measured in Phase 1; at K=1 the same flow reaches
−4.44 % (f=25), widening the gap to ~50 %p.

#### At the *best* K, the answer is far stronger — and the cell caveat vanishes

The comparison above is against CSR at **K=4**. Repeating it at **K=1** (the
setting that actually wins every equal-budget column) is the comparison that
matters for adoption. Artifact:
`scripts/20260719/analyze_csr_vs_baseline.py` →
`analysis/20260719_csr_init/csr_vs_baseline{,_cells}.csv`.

The baseline family already spans f ≈ 0 / 10 / 25 / 30, so **no new baseline
sweep is needed** — CSR can be met head-to-head at three of its six budgets.
Budget parity is verified on measured wall-clock, not assumed:

| f | existing method | CSR K=1 | Δ | paired w/t/l |
| --- | --- | --- | --- | --- |
| 10 % | `mcf_lb_fmm` 46.43 % (9.5 s) | **6.10 %** (8.1 s) | **−40.33 %p** | 1401 / 22 / 17 |
| 25 % | `mcf_lb_fmm_25p` 45.23 % (20.3 s) | **−4.44 %** (19.6 s) | **−49.67 %p** | 1420 / 17 / 3 |
| 25 % | `neh_25p` 31.39 % (18.9 s) | **−4.44 %** (19.6 s) | **−35.83 %p** | 1184 / 126 / 130 |
| 30 % | `neh` 29.18 % (22.4 s) | **−5.60 %** (23.5 s) | **−34.79 %p** | 1179 / 126 / 135 |

**CSR K=1 beats every existing initialization method at matched budget, in every
slice, and — unlike at K=4 — in all 9 (T,R) cells against all four baselines.**
On T=0.6 vs `mcf_lb_fmm_25p` the sweep is total: **480 / 0 / 0**.

This resolves the K=4 caveat rather than carrying it: `mcf_lb_fmm_25p`'s wins in
three cells (Q7) are an artifact of comparing against the wrong K. At K=1 the
per-cell margins against it run −9.54 to −180.02 %p, with no cell lost.

#### CSR needs ~5× less budget to match the best existing method

Because the baselines sit at different budgets, the frontier can be read
diagonally. `csr_full_d2wp` K=1 at **f=5 % (4.2 s)** reaches 26.51 %:

| vs | its budget | Δ (CSR@f=5 − baseline) | paired w/t/l | budget ratio |
| --- | --- | --- | --- | --- |
| `mcf_lb_fmm` | 9.5 s | **−19.92 %p** | 1079 / 74 / 287 | 2.3× less |
| `mcf_lb_fmm_25p` | 20.3 s | **−18.72 %p** | 972 / 73 / 395 | 4.8× less |
| `neh_25p` | 18.9 s | −4.87 %p | 648 / 83 / 709 | 4.5× less |
| `neh` | 22.4 s | −2.67 %p | 628 / 83 / 729 | 5.3× less |

Against the **mcf_lb family this is outright dominance** at a fifth of the cost —
better on the mean *and* on the per-instance count, and **in all 9 cells**.
Against the **NEH family the honest reading is parity, not victory**: CSR wins
the mean by 2.7–4.9 %p while *losing* the per-instance count (628/83/729 vs
`neh`).

The cell view shows exactly what that mean is hiding — **a sign flip across R**:

`csr_full_d2wp_k1` @ f=5 % minus `neh` @ f=30 % (negative = CSR better):

| | R=0.2 | R=0.6 | R=1.0 |
| --- | --- | --- | --- |
| T=0.2 | −64.88 | −37.56 | **+63.33** |
| T=0.4 | −33.88 | −19.51 | **+42.61** |
| T=0.6 | −2.45 | **+12.90** | **+15.41** |

**`neh` still wins 4 of 9 cells** — the whole R=1.0 column plus (0.6, 0.6). CSR's
−2.67 %p mean is a large win on tight due dates (R=0.2) cancelling a large loss
on loose ones. So the budget-efficiency claim must be scoped:

- **vs the mcf_lb family at f=5 %: dominant everywhere**, 2.3–4.8× less budget.
- **vs the NEH family at f=5 %: better only for R ≤ 0.6 and mostly at low T**;
  at R=1.0 `neh` is decisively better even with 5× less budget than CSR would
  need to catch up.

This does **not** touch the equal-budget result: at f=25 % and f=30 % CSR K=1
wins all 9 cells against the NEH family too. The R=1.0 weakness is specific to
starving CSR to f=5 %.

### 3. At f=25 %, what is the best K? Monotone, U-shape, or plateau?

**Coarsening hurts. K=1 (no coarsening) is best; the curve is monotone worsening
in K for `full` and effectively flat-then-worsening for `neh`.**

Primary view, 1440 instances, f=25 %, mean RPDf%:

| K | `csr_full_d2wp` | `csr_neh_d2wp` |
| --- | --- | --- |
| **1** | **−4.44** | 17.96 |
| 2 | 15.19 | **17.77** |
| 4 | 21.64 | 20.05 |
| 8 | 29.74 | 29.00 |

- `full`: strictly monotone worsening — K=1 wins by **19.6 %p** over K=2.
- `neh`: a very shallow U with its minimum at K=2 (17.77 vs 17.96 at K=1, a
  0.19 %p difference), then worsening. Read as a plateau over K∈{1,2}.
- Secondary view (160 inst, the (0.6,0.2) cell) extends the axis: K=16 (35.8 /
  35.6) and **K=32 (43.6 / 43.9) are clearly worse**, confirming no upturn hides
  beyond K=8.

**No U-shape at the level that matters.** The best cell of the whole table is
`full` at K=1, and it is the best at every f in every slice (Phase 3's
equal-budget setting comparison marks `F_k1` the column winner in all 18
columns).

### 4. At each fixed budget f, which setting is best?

> **This is the question the budget sweep exists to answer, and the plan's
> original phrasing ("what is the best f? where do diminishing returns start?")
> is not it.** More budget is monotonically better, so "best f" is always the
> largest f measured — a fact about the sweep's range, not about the algorithm.
> The discriminating read is the **transpose**: fix f (= fix cost) and rank the
> settings *down* the column. `analyze_csr_tl_scaling_sweep.py`'s docstring
> already says this; this document's first draft buried it.

Artifact: `scripts/20260719/analyze_csr_equal_budget.py` →
`analysis/20260719_csr_budget_sweep/csr_equal_budget{,_gaps}.csv`, which adds the
winner→runner-up gap and a per-instance paired test to the existing table.

**Answer: `F_k1` (`csr_full_d2wp`, K=1) wins all 18 (slice × f) columns.**
The setting ranking is budget-independent over the measured range — there is no
crossover, so the choice of f never changes which setting to pick.

Mean RPDf% by setting × f, **overall** (read each column down; **bold** = winner):

| setting | f=5 | f=10 | f=15 | f=20 | f=25 | f=30 |
| --- | --- | --- | --- | --- | --- | --- |
| **F_k1** | **26.51** | **6.10** | **0.55** | **−2.59** | **−4.44** | **−5.60** |
| F_k2 | 56.55 | 33.60 | 24.91 | 19.06 | 15.19 | 12.92 |
| F_k4 | 64.37 | 41.82 | 31.57 | 25.95 | 21.64 | 19.81 |
| F_k8 | 69.34 | 47.18 | 38.08 | 33.16 | 29.74 | 27.26 |
| N_k1 | 50.46 | 35.05 | 27.50 | 21.97 | 17.96 | 15.11 |
| N_k2 | 51.42 | 34.98 | 27.36 | 22.37 | 17.77 | 14.98 |
| N_k4 | 57.00 | 38.15 | 29.87 | 24.76 | 20.05 | 17.73 |
| N_k8 | 61.66 | 43.96 | 36.61 | 31.66 | 29.00 | 26.19 |

The margin is large and paired-confirmed on the full grid — at every f, `F_k1`
wins ≥70 % of individual instances:

| f | winner → runner-up | gap | paired w/t/l |
| --- | --- | --- | --- |
| 5 | F_k1 → N_k1 | 23.94 %p | 1036 / 84 / 320 |
| 10 | F_k1 → F_k2 | 27.49 %p | 1119 / 114 / 207 |
| 15 | F_k1 → F_k2 | 24.36 %p | 1075 / 113 / 252 |
| 20 | F_k1 → F_k2 | 21.65 %p | 1059 / 116 / 265 |
| 25 | F_k1 → F_k2 | 19.63 %p | 1024 / 117 / 299 |
| 30 | F_k1 → F_k2 | 18.53 %p | 1006 / 121 / 313 |

#### ⚠️ The dominance erodes with budget on the hard slices

The overall column hides a systematic trend: **`F_k1`'s advantage shrinks as f
grows**, and it shrinks fastest exactly where the problem is hard.

| slice | gap @ f=5 | @ f=15 | @ f=30 | paired w/t/l @ f=30 |
| --- | --- | --- | --- | --- |
| overall | 23.94 %p | 24.36 | **18.53** | 1006 / 121 / 313 |
| T=0.6 | 17.70 %p | 10.15 | **2.17** | **233 / 0 / 247** |
| (T,R)=(0.6,0.2) | 17.33 %p | 13.57 | **7.50** | 97 / 0 / 63 |

At **T=0.6, f=30 % — the narrowest of the 18 columns — `F_k1` wins the mean by
2.17 %p while *losing* the per-instance count 233/0/247.** Its mean win there is
carried by margin size, not by winning more often. Combined with a 2.17 %p gap,
that corner is the one place where the "K=1 dominates" claim is genuinely weak,
and it should not be quoted as a uniform result.

The mechanism is visible in the table: on T=0.6 the runner-up identity migrates
from `N_k1` (f=5) to `N_k8`/`N_k4` (f≥10) as budget grows. So while the *winner*
is budget-independent, the ranking *among the losers* is not — higher K becomes
relatively more competitive as f grows, consistent with coarsening needing budget
before it pays. Extrapolating the T=0.6 trend, a crossover beyond f=30 % is
plausible and untested.

#### Budget efficiency (the secondary read)

Given `F_k1`, the marginal value of each +5 %p:

| f | 5 | 10 | 15 | 20 | 25 | 30 |
| --- | --- | --- | --- | --- | --- | --- |
| mean RPDf% | 26.51 | 6.10 | 0.55 | −2.59 | −4.44 | −5.60 |
| Δ per +5 %p | — | −20.4 | −5.5 | −3.1 | −1.9 | −1.2 |

Diminishing returns begin immediately after f=10 %: the first increment buys
−20.4 %p, the last only −1.2 %p. **f≈15 % captures most of the value** (0.55 %,
roughly BKS parity) at half the budget of f=30. The curve is still descending at
f=30, so the measured optimum is a range limit, not a true one.

### 5. What `(init_flow, K, f)` triple minimizes mean RPDf%?

**`(csr_full_d2wp, K=1, f=30 %)` → −5.60 % mean RPDf, overall** — but note this
"triple" collapses to a two-part answer, because per Q4 the setting choice does
not interact with f. The real content is: **pick `F_k1` regardless of budget**,
then spend whatever budget is available (with returns tailing off past f≈15 %).

Per slice at f=30, with the caveat from Q4 attached:

| slice | `F_k1` @ f=30 | runner-up | gap |
| --- | --- | --- | --- |
| overall | **−5.60 %** | `N_k2` 14.98 % | 20.58 %p |
| T=0.6 | **20.74 %** | `N_k4` 22.91 % | 2.17 %p ⚠️ w/t/l 233/0/247 |
| (T,R)=(0.6,0.2) | **22.83 %** | `N_k4` 30.32 % | 7.50 %p |

Caveat carried from Q1: this is at the pre-committed `d2wp` priority. The K and
budget sweeps have no `wdp` variant, so the triple is optimal *within* `d2wp`.
Phase 1 shows `d2wp` beats `wdp` decisively at K=4 (below), which makes the
pre-commitment look sound but does not prove it at K=1.

**Priority axis (Phase 1, K=4, 1440 inst):**

| comparison | Δ (d2wp − wdp) | win/tie/loss |
| --- | --- | --- |
| `csr_full_d2wp` vs `csr_full_wdp` | **−28.28 %p** | 1073 / 103 / 264 |
| `csr_neh_d2wp` vs `csr_neh_wdp` | **−33.29 %p** | 1136 / 99 / 205 |

`d2wp` wins both, by very large margins. **The pre-commitment was correct** and
no re-run of Phases 2–3 under `wdp` is warranted.

### 6. Does outer FMM pay for itself even at +40 % budget?

**No — and it loses badly enough that the *a fortiori* argument is conclusive.**

160-instance secondary table (= exactly the (T=0.6,R=0.2) cell; the only run
containing `csr_fmm_base`):

| scenario | budget | mean RPDf% |
| --- | --- | --- |
| `csr_neh_wdp` (best 0.0225nc) | 0.0225nc | 38.84 |
| … | | |
| **`csr_fmm_base`** | **0.0315nc (+40 %)** | **70.34** |
| `csr_base` | 0.0225nc | 73.04 |

`csr_fmm_base` is **31.5 %p worse** than the best equal-budget scenario *despite
receiving ~40 % more budget*, and loses the paired comparison **2 / 0 / 158**.

The asymmetry runs against it, so the conclusion "outer FMM does not pay for
itself" is **stronger** than an equal-budget loss would be. **No budget-matched
re-run is required** — this was the sole open justification for not re-running
it, and it is now settled.

(Its near-tie with `csr_base` (73.04) suggests `csr_fmm_base` inherits
`csr_base`'s weakness — the default-CP-SAT inner flow — rather than being harmed
by outer FMM as such. Both are ~2× worse than every scenario with a real inner
flow.)

### 7. Which method wins in each of the 9 (T,R) cells?

**Four different methods win cells, and the overall winner takes only 4 of 9.**
This is the phase's most consequential nuance.

Mean RPDf% per cell, rows in overall-rank order (**bold** = cell winner):

| scenario | T.2R.2 | T.2R.6 | T.2R1 | T.4R.2 | T.4R.6 | T.4R1 | T.6R.2 | T.6R.6 | T.6R1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `csr_neh_d2wp` | 44.47 | **0.58** | −59.47 | 53.62 | 48.60 | 15.50 | **33.39** | **26.04** | **19.27** |
| `csr_full_d2wp` | 46.82 | 3.25 | −57.56 | 54.03 | 53.36 | **14.23** | 34.09 | 26.07 | 21.37 |
| `neh` | 63.09 | 32.32 | −62.80 | 62.86 | 62.85 | 15.03 | 38.07 | 29.49 | 21.75 |
| `neh_25p` | 64.32 | 38.14 | **−62.98** | 65.28 | 65.72 | 19.77 | 38.84 | 30.30 | 23.11 |
| `mcf_lb_fmm_25p` | **−0.83** | 18.35 | 110.65 | **30.98** | **47.53** | 72.04 | 40.58 | 46.84 | 40.97 |
| `mcf_lb_fmm` | 0.17 | 20.01 | 110.81 | 32.35 | 49.67 | 73.84 | 41.58 | 47.59 | 41.87 |
| `csr_full_wdp` | 55.08 | 73.19 | −24.18 | 56.57 | 75.27 | 95.88 | 34.77 | 40.38 | 43.16 |
| `csr_neh_wdp` | 58.56 | 86.69 | −21.83 | 57.88 | 80.45 | 102.62 | 33.85 | 40.31 | 43.11 |
| `mcf_lb` | 5.40 | 27.92 | 125.78 | 38.40 | 58.03 | 84.88 | 46.09 | 52.23 | 46.55 |
| `csr_base` | 98.84 | 115.90 | 159.10 | 91.67 | 107.01 | 102.50 | 73.11 | 78.57 | 71.43 |

Cell winners: `csr_neh_d2wp` ×4, **`mcf_lb_fmm_25p` ×3**, `csr_full_d2wp` ×1,
`neh_25p` ×1.

**The headline finding here is `mcf_lb_fmm_25p`.** It ranks **5th of 10 overall**
(45.23 %, and the loser of Q2's critical test by 23.5 %p) — yet it is the
**outright best method in three cells**: `(0.2,0.2)`, `(0.4,0.2)`, `(0.4,0.6)`.
Its per-cell rank swings from **1st to 7th**. The pattern is systematic: it wins
where due dates are tight (low R) and loses badly where they are loose
(R=1, where it is ~110 % vs CSR's ~−58 %).

**A 1440-mean is therefore not a sufficient basis for discarding it**, exactly as
the plan warned.

> ⚠️ **But this whole cell table is a `K=4` result, and the 3-cell win does not
> survive at the recommended setting.** Against `csr_full_d2wp` at **K=1** at
> matched budget, `mcf_lb_fmm_25p` loses **all 9 cells** (margins −9.54 to
> −180.02 %p; see Q2). So the correct statement is *not* "CSR's advantage is
> uneven across the parameter space" — it is **"CSR *at K=4* has an uneven
> advantage, and choosing K=4 is itself the mistake."** The cell decomposition
> earned its keep by exposing a weakness that turned out to be a symptom of the
> wrong K, not a limitation of CSR.

The methodological lesson stands regardless: had the analysis stopped at the
1440-mean of Phase 1, `mcf_lb_fmm_25p`'s cell wins would have been invisible, and
they were the clue that Phase 1's K was not the right one to judge CSR at.

Symmetrically, two methods are dominated: **`csr_base` is last in 8 of the 9
cells** (in `(0.4,1)` it is 9th of 10 — `csr_neh_wdp`, 102.62 %, is worse there)
and `mcf_lb` never wins a cell. `csr_base` (default CP-SAT inner flow, 99.79 %
overall, 0/3/1437 paired against `csr_full_d2wp`, 0 unique-best instances, 0.00
marginal contribution) is the one method this analysis can discard on every
axis it was measured on — **the CSR inner flow is essential, not an
optimization.**

### 8. Which portfolios minimize per-instance `min` RPDf?

> ⚠️ **Oracle (virtual-best) numbers — an upper bound, not a runnable strategy.**
> A per-instance `min` assumes a perfect selector, and running k inits costs
> k × budget: two at `0.0225nc` is `0.045nc` (f = 50 %), beyond the f = 30 %
> ceiling of anything measured here. This is a **complementarity diagnosis**.
> Deciding an operational portfolio needs a budget-matched experiment that does
> not currently exist.

| k | best subset | oracle mean RPDf% |
| --- | --- | --- |
| 1 | `csr_neh_d2wp` | 20.22 |
| 2 | **`csr_neh_d2wp` + `mcf_lb_fmm_25p`** | **7.81** |
| 3 | **`csr_neh_d2wp` + `mcf_lb_fmm_25p` + `neh`** | **5.02** |

Marginal contribution to the best single (`csr_neh_d2wp`):

| candidate | oracle with it | gain |
| --- | --- | --- |
| **`mcf_lb_fmm_25p`** | 7.81 | **+12.42 %p** |
| `mcf_lb_fmm` | 8.23 | +11.99 |
| `mcf_lb` | 9.38 | +10.84 |
| `neh` | 16.90 | +3.33 |
| `csr_full_d2wp` | 17.03 | +3.19 |
| `csr_base` | 20.22 | **0.00** |

**This is the same story as Q7, quantified.** The single most valuable partner to
the best method is `mcf_lb_fmm_25p` — the method that *loses* the equal-budget
head-to-head by 23.5 %p. Adding it cuts oracle RPDf by more than half (20.2 →
7.8). It is valuable precisely *because* its wins are disjoint from CSR's: the
mcf_lb family occupies the top three marginal slots, while `csr_full_d2wp` — the
2nd-best standalone method — contributes only +3.19 %p because its wins largely
duplicate `csr_neh_d2wp`'s.

Extending to the best pair, `neh` adds +2.79 %p and `mcf_lb_fmm` adds only
+0.02 %p (redundant once `mcf_lb_fmm_25p` is in).

**Redundant methods:** `csr_base` and `mcf_lb` have **exactly 0.00 marginal
contribution** and are **never uniquely best on any instance** (unique-best
counts: `csr_neh_d2wp` 319, `csr_full_d2wp` 254, `mcf_lb_fmm_25p` 179, `neh` 126,
`neh_25p` 90, `csr_neh_wdp` 50, `csr_full_wdp` 39, `mcf_lb_fmm` 24, **`csr_base`
0, `mcf_lb` 0**). Both are genuinely dominated, whatever the slice.

---

## Overall conclusion

1. **At every fixed budget, `csr_full_d2wp` at K=1 is the best setting** — the
   winner in all 18 (slice × f) columns, so the setting choice is
   budget-independent over f ∈ [5, 30] %. Best measured point:
   `(csr_full_d2wp, K=1, f=30 %)` at −5.60 %. **Exception to quote alongside it:**
   at T=0.6, f=30 % the margin is only 2.17 %p and the per-instance count is
   233/0/247 *against* it — the dominance erodes with budget on hard instances.
2. **Coarsening does not pay at equal budget.** K=1 beats every K≥2 in the
   `full` flow by ~20 %p, and K=32 is catastrophic. The coarsen-solve-reconstruct
   machinery's value is *not* in the coarsening.
3. **The CSR inner flow is what matters — coarsening itself contributes nothing.**
   `csr_base` (no inner flow) is 99.79 % vs `csr_full_d2wp`'s 21.74 % at identical
   budget — 0/3/1437 paired, the largest single effect in the analysis. And at the
   winning setting `factor=1` makes coarsening a verified no-op (see Scope), so
   what wins is the 5-step inner pipeline, not coarsen-solve-reconstruct. Both
   halves point the same way: **the skeleton is inert; the pipeline is the
   result.** Describe these findings as "the inner pipeline beats the existing
   init", not "CSR beats the existing init".
4. **`d2wp` ≫ `wdp`** (−28 to −33 %p), vindicating the pre-commitment.
5. **Outer FMM does not pay for itself**, conclusively, even at +40 % budget.
6. **CSR at K=1 beats every existing init method at matched budget** —
   `mcf_lb_fmm` (−40.33 %p), `mcf_lb_fmm_25p` (−49.67), `neh_25p` (−35.83), `neh`
   (−34.79) — in every slice and **all 9 (T,R) cells**. Starved to f=5 % it still
   **dominates the mcf_lb family in all 9 cells at 4.8× less budget**, but
   against the NEH family it then wins only R ≤ 0.6 and **loses the whole R=1.0
   column** (4 of 9 cells) — that comparison is parity, not victory.
7. **`mcf_lb_fmm_25p`'s 3-cell win is a K=4 artifact.** It ranks 5th overall and
   is the top portfolio partner (+12.42 %p oracle), but at K=1 it loses all 9
   cells. The cell decomposition was still what exposed that Phase 1's K was
   wrong — the diagnosis was right, the implied limitation was not.
8. **The budget curve has not saturated at f=30 %**, though marginal returns
   past f≈15 % are small.

### Follow-ups (none are blockers)

- **Extend the budget sweep past f=30 %.** Two distinct reasons, and the second
  is the interesting one: (a) the `F_k1` curve is still descending, so the
  measured optimum is a range limit; (b) **on T=0.6 the `F_k1` → `N_k*` gap is
  collapsing with budget** (17.70 → 10.15 → 2.17 %p at f=5/15/30) and the paired
  count has already flipped against it. A crossover just beyond f=30 % is
  plausible, which would make the "K=1 always wins" conclusion budget-bounded
  rather than general. Sweep `F_k1` against `N_k4`/`N_k8` at f=35/40/50 on the
  T=0.6 slice specifically.
- **The tail is unmeasured.** Everything here is init-only. Whether CSR's init
  advantage survives an outer `incremental_sw_cp` tail to `0.09nc` is the
  question this data cannot answer, and plan Appendix A.1 gives direct reason for
  doubt (downstream steps absorbed a coarse-layer gain almost entirely). **This
  is the single most important next experiment.**
- **A budget-matched portfolio experiment** — Q8's numbers are oracle bounds at
  k × budget; whether `csr + mcf_lb_fmm` beats a single method at *equal total*
  cost is untested.
- **`wdp` at K=1** is unmeasured; the priority verdict is a K=4 result.
- **`K == 1` idle-mode code asymmetry** (plan Appendix A, `20260719T231308`)
  remains a deferred cleanup in `TODO.md`, empirically void.
