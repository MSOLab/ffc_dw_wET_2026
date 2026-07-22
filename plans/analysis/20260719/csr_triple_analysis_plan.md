# CSR 3-Phase Analysis Plan

**Date:** 2026-07-19 · **Status:** **Phases 1–3 executed** — conclusions in
[`csr_init_k_budget_consolidation.md`](csr_init_k_budget_consolidation.md);
**Appendix A executed** (`insert_idle_time` before/after)

---

## Overview

Three-phase analysis of the CSR (Coarsen-Solve-Reconstruct) experiments
conducted 2026-07-13 through 2026-07-15. Each phase has its own script +
output directory under `analysis/20260719_csr_*/`. Phases are sequential:
the init-methods result sets the best `(init_flow, priority)` for the K-range
analysis, which in turn feeds the budget-sweep interpretation.

### Phase dependency graph

```txt
Phase 1 (init methods) ──→ best inner flow ──→ Phase 2 (K range)
    (priority: reported,      (full vs neh)          │
     not a gate)                                     │
Phase 3 (budget sweep) ←──────────────────────────────┘
  (independent execution; interpretation depends on both prior phases)
```

### ⚠️ `d2wp` is pre-committed, not chosen by Phase 1

Every Phase 2 and Phase 3 run exists **only for the `d2wp` (due2-weight-pos)
priority** — there is no `wdp` variant in the K-range or budget-sweep data.
So Phase 1's priority comparison (`d2wp` vs `wdp`) is a
**reported result, not a gate**: it cannot redirect the later phases.

What Phase 1 *does* feed forward is the **inner-flow** axis (`csr_full` vs
`csr_neh`), which Phases 2–3 carry both of.

If Phase 1 finds `wdp` beats `d2wp`, that does **not**
invalidate Phases 2–3 — it means the K/budget sweeps were run on the
runner-up priority, and confirming the conclusion under `wdp` would require
re-running both sweeps. Record that as a follow-up, not a blocker.

### Scope: initialization only (decided 2026-07-19)

Every scenario in Phases 1–3 has a **single-step outer flow**
(`subroutine_flow: [coarsen_solve_reconstruct]`; verified across all three
configs). There is no tail — no outer `incremental_sw_cp`, no
`solve_base_model_cpsat`. The scenario cap is `0.09nc` while CSR consumes
`0.0225nc`, so ~75 % of the nominal budget is deliberately left unspent.

**This is intentional and stays that way.** The question under study is *which
initialization is best per unit of initialization budget*. Running the tail to
fill `0.09nc` is explicitly **out of scope**.

The consequence must be stated in the consolidation rather than left implicit:
every RPDf number here measures **init quality**, not final solution quality.
Appendix A.1 shows downstream steps can absorb upstream differences, so no
claim of the form "CSR init ⇒ better final solution" is supported by this data.
Conclusions must be phrased as "best init under a fixed init budget".

### Run-to-run variance: treated as negligible (settled 2026-07-19)

Solver runs are not bit-reproducible — CP-SAT searches with
`solver_thread_cnt: 8` under a wall-clock budget, so the incumbent at cutoff
depends on thread interleaving and on how much search fits in those seconds.

**This has been measured repeatedly on this project, and at the 1440-instance
mean it narrows to a negligible difference.** Phases 1–3 therefore compare
means directly and **do not** carry a noise-floor caveat: a difference in mean
RPDf% over the full grid is taken at face value. No replicate runs are planned.

Two boundaries on that, both mechanical consequences of "it averages out":

- It holds **at full-grid means**. The 160-instance secondary view (Phase 2)
  has ~1/9 the sample, so the same cancellation is correspondingly weaker —
  read it as directional, and let the 1440-instance primary view settle any
  disagreement between the two.
- It holds **for means, not per-instance**. Per-instance win/tie/loss counts
  still absorb run-to-run variance, so a near-50:50 split is not by itself
  evidence of equivalence (see Appendix A.1).

The dispatch/seed path is separately **fully deterministic** — Appendix A.2
reproduced 21600 rows byte-identically on a different machine (`aigpu0126` vs
the baseline's `calop4`).

---

## Phase 1: CSR Init Methods Comparison

### Question

Among 5 init approaches × 2 NEH priority variants + 2 equal-budget baselines ×
4 CSR inner-flow variants — **which init flow + NEH priority gives the best
mean RPDf on the full PRA2017 1440-instance grid?**

### Sources

| run | instances | scenarios | note |
| --- | --- | --- | --- |
| `output/20260713_csr_init_methods/20260713T195341_009592` | 1440 | 10 | canonical full-grid run |
| `output/20260713_csr_init_methods/20260713T091912_833529` | 160 | 11 | same + `csr_fmm_base`, subset only |

The 160-instance subset here is **identical** to the one used by
`20260714_csr_higher_k_validation` (verified: same `insIndex` set, and a strict
subset of the 1440 grid) — so Phase 2's secondary view is directly comparable
to this run.

That subset is not a random sample: it is **exactly one (T, R) cell —
`(T=0.6, R=0.2)`** (verified: T and R are constant at those values across all
160 rows, while n / c / totalMcCount / W stay balanced). Since 1440 / 9 = 160,
one cell *is* 160 instances. Describe it that way rather than as "a smaller
sample": Phase 2's secondary view lines up with a single cell of the 3×3 table
below, and its ~34 % RPDf level is not comparable to a full-grid mean (~15 %)
because it is the hardest cell, not a different measurement.

**Scenario taxonomy** (from `plans/experiment/20260713/csr_init_methods.md`):

| category | scenario | budget | description |
| --- | --- | --- | --- |
| free baselines | `mcf_lb` | ~0 | MCF LB only (no CP) |
| natural baselines | `mcf_lb_fmm` | 0.009nc | MCF + flip-makespan CP |
| | `neh` | 0.027nc | NEH CP (due2-weight-pos) |
| equal-budget controls | `mcf_lb_fmm_25p` | 0.0225nc | same as CSR budget |
| | `neh_25p` | 0.0225nc | same as CSR budget |
| CSR base | `csr_base` | 0.0225nc | coarsen → default CP-SAT → reconstruct |
| CSR full miniature | `csr_full_d2wp` | 0.0225nc | inner: mcf→flip→neh→sw_cp→base_cp, d2wp |
| | `csr_full_wdp` | 0.0225nc | same, weight-due-pos |
| CSR neh-only | `csr_neh_d2wp` | 0.0225nc | inner: neh→sw_cp→base_cp, d2wp |
| | `csr_neh_wdp` | 0.0225nc | same, weight-due-pos |
| (subset only) | `csr_fmm_base` | 0.0315nc | csr + outer FMM, 160-inst only |

### The baselines already span part of the budget axis

The five non-CSR scenarios are not all at one budget — read as a fraction of
the `0.09nc` scenario cap they give plain init **four points**:

| budget | f | scenario |
| --- | --- | --- |
| ~0 | ~0 % | `mcf_lb` |
| `0.009nc` | **10 %** | `mcf_lb_fmm` |
| `0.0225nc` | **25 %** | `mcf_lb_fmm_25p`, `neh_25p` |
| `0.027nc` | **30 %** | `neh` |

So the CSR-vs-plain question is answerable at f ≈ 0 / 10 / 25 / 30, not only at
the 25 % equal-budget point. Only f = 5 / 15 / 20 lack a plain comparator, and
each baseline family has two points. **No baseline budget sweep is needed** —
an earlier draft proposed one; it is withdrawn.

### Key comparisons

1. **Overall ranking** — mean RPDf of all 10 scenarios (1440 instances).
2. **Equal-budget A/B**: `csr_full_d2wp` vs `mcf_lb_fmm_25p` (same 0.0225nc,
   both full pipeline, CSR vs plain — the CRITICAL test).
3. **NEH priority**: `csr_full_d2wp` vs `csr_full_wdp`,
   `csr_neh_d2wp` vs `csr_neh_wdp`.
4. **Inner-flow**: `csr_full_d2wp` vs `csr_neh_d2wp` (with vs without
   mcf/flip in inner flow).
5. **CSR base vs full**: `csr_base` vs `csr_full_d2wp` (inner flow worth it?).
6. **(T, R) 3×3 decomposition** — see below. Not a footnote: a primary output.
7. **Portfolio synergy** — see below.

### (T, R) 3×3 decomposition — a primary output, not a slice

`T ∈ {0.2, 0.4, 0.6} × R ∈ {0.2, 0.6, 1.0}` gives **9 cells of exactly 160
instances each**. Emit the full 10 scenarios × 9 cells mean-RPDf table, plus the
per-cell winner.

**A 1440-instance mean is not a sufficient basis for discarding an init
method.** The mean averages *over* the cells and therefore erases where a method
wins. A method that is dominant in three cells and poor in six can rank badly
overall while being irreplaceable in those three. Rank within each cell before
drawing any "method X is bad" conclusion, and state which cells any such claim
rests on.

### Portfolio synergy — min over 2 and 3 methods

The intended use is to run several inits and keep the best per instance, so the
quantity that matters for a method is **its marginal contribution to a
portfolio**, not its standalone mean. A method that ranks 5th overall but wins
precisely where the leader loses is worth more than a 2nd-place method whose
wins are a subset of the leader's.

Compute:

- per-instance `min` RPDf over every 2-subset and 3-subset of the 10 scenarios,
  reported overall and per (T, R) cell;
- **marginal contribution**: `mean(min over S) − mean(min over S ∪ {x})` for
  each candidate `x` against the current best subset `S`;
- per-cell and overall counts of how often each method is the **unique** best
  (a method that is never uniquely best is genuinely redundant, whatever its
  mean).

> ⚠️ **Label these as oracle (virtual-best) numbers, and state the budget.** A
> per-instance `min` assumes a perfect selector, and actually running k inits
> costs k × budget — two at `0.0225nc` is `0.045nc`, i.e. f = 50 %, beyond the
> f = 30 % ceiling of anything measured here. So the portfolio table is a
> **complementarity diagnosis and an upper bound**, not a runnable strategy
> compared at matched cost. Deciding an operational portfolio would need a
> budget-matched experiment that does not currently exist.

### Implementation (new script)

```sh
scripts/20260719/analyze_csr_init_methods.py
```

Behavior:

- Read `_rpdf_comparison.csv` from the 1440-instance run (195341).
- Compute mean RPDf% per scenario, overall and per (T) / (T,R) slice.
- Rank scenarios by mean RPDf (lower = better).
- Print comparison tables for the 7 key questions above.
- **10 scenarios × 9 (T,R) cells** mean-RPDf table + per-cell winner + per-cell
  unique-best counts.
- **Portfolio table**: per-instance `min` over all 2- and 3-subsets, overall and
  per cell; marginal contribution of each method to the best subset. Header must
  carry the oracle / budget-multiple caveat.
- Also emit the secondary 160-instance table from the `091912` run, including
  `csr_fmm_base`, to settle the a fortiori check above. Note in the header that
  those 160 instances **are** the `(0.6, 0.2)` cell, so that table is a
  cell-level result, not a small-sample version of the overall one.
- Output: `analysis/20260719_csr_init/csr_init_methods.csv` (+ `_tr_cells.csv`,
  `_portfolio.csv`).

Optional: time-quality scatter (mean elapsed vs mean RPDf) as PNG.

**Data source (all three phases): `<ts>_rpdf_comparison.csv`, using its
precomputed `RPDf_BKS_data` column verbatim.** It already carries the
`(n, c, totalMcCount, T, R, W, BKS_data, elapsedTime)` join, so no re-join is
needed, and it is what the existing Phase 3 script uses — keeping all three
phases on one frame prevents drift between them.

Reusable from `scripts/20260706/analyze_kappa_sweep.py`: the **slice/format
helpers only** (`resolve_slices`, `apply_slice`, `slugify`, `_fmt`,
`aggregate`). Do **not** reuse `load_runs` — it reads `*_summary.csv` and
re-derives the BKS join itself, which is the other frame.

### `csr_fmm_base` (160-subset only, budget-asymmetric)

`csr_fmm_base` gets **0.0315nc** — more than the 0.0225nc every other CSR
scenario gets — so it is not an equal-budget comparison. It is nevertheless
**kept as a comparison target**, on an *a fortiori* reading:

> Equal budget is what a fair head-to-head requires. But if `csr_fmm_base`
> is **worse than (or equal to) the best 0.0225nc scenario despite receiving
> ~40 % more budget**, the asymmetry runs against it — so the conclusion
> "outer FMM does not pay for itself" is *stronger* than an equal-budget loss
> would be, and no budget-matched re-run is needed.

The original intent was to shrink its budget and re-run **only if** 0.0315nc
looked clearly better. Prior recollection is that it came out worse or
comparable — **Phase 1 must verify this**, since it is the sole justification
for not re-running it at matched budget.

Reported as a **secondary 160-instance table** (the only run containing it),
labelled budget-asymmetric. If it *does* beat the 0.0225nc best, the a
fortiori argument collapses and a budget-matched re-run becomes required.

### Decision point

The analysis identifies the **best inner flow** — `csr_full` vs `csr_neh` —
which carries into Phase 2. The **priority** axis (`d2wp` vs `wdp`) is
reported but does not gate the later phases (see the pre-commitment note in
the Overview). If two variants are effectively tied on mean RPDf, both are
reported.

---

## Phase 2: CSR K Range Analysis

### Question

At fixed f=25% budget, how does RPDf vary with coarsening factor
K ∈ {1, 2, 4, 8, 16, 32} × the two init flows? Does RPDf improve
monotonically with K, or is there a U-shape / plateau?

### Sources

| K | run | instances | scenario format |
| --- | --- | --- | --- |
| 1 | `output/20260714_csr_tl_scaling_sweep/20260715T183418_361919` | 1440 | `csr_{full,neh}_d2wp_k1_tl25` |
| 2,4,8 | `output/20260714_csr_full_grid_k248/20260714T184236_642971` | 1440 | `csr_{full,neh}_d2wp_k{2,4,8}` |
| 2,4,8,16,32 | `output/20260714_csr_higher_k_validation/20260714T154426_711694` | 160 | `csr_{full,neh}_d2wp_k{2,4,8,16,32}` |

**K=1 f=25 notes**: the K=1 scenario was run as a gap-fill after the
original TL scaling sweep (which covered K=1 at f=5,10,15,20,30 but not
25). The full-grid gap-fill run is **`20260715T183418_361919`** (2 scenarios ×
1440 instances) — this is the only source of the K=1 @ 25% point.

> ⚠️ `20260715T175237_658738` is **not** a substitute: it holds only **2
> instances** (`insIndex` 0 and 1, 4 rows total) — a smoke test that preceded
> the real gap-fill run. Do not use it for any aggregate.

**Budget parity**: all scenarios in this phase use **f=25%** (`0.0225nc` CSR
budget), confirmed by config inspection:

- K=1: `csr_full_d2wp_k1_tl25` / `csr_neh_d2wp_k1_tl25` (`_tl25` = 25%)
- K=2,4,8 (full_grid_k248): `csr_{full,neh}_d2wp_k{2,4,8}` — same budget structure
- K=2,4,8,16,32 (higher_k_validation): same budget (0.0225nc) × varying factor

### Instance-set caveat

K={16,32} only exist on the 160-instance subset. Two complementary views:

1. **Primary**: K=1,2,4,8 on all 1440 instances (merge K1 gap-fill + K248 run).
   Report the K=1-8 phase-1 curve; note K=16,32 are separate.
2. **Secondary**: K=2,4,8,16,32 on the common 160-instance subset (higher_k run),
   joined against K=1 on the same 160 subset extracted from the gap-fill run.

The primary view has more statistical power; the secondary view extends to
K=32 with reduced precision.

### Implementation (new script)

```sh
scripts/20260719/analyze_csr_k_range.py
```

Behavior:

1. **Primary (1440-inst)**: merge K=1 f=25 from gap-fill + K=2,4,8 from
   full_grid_k248 → one row per (K, flow), mean RPDf%, overall + T=0.6 +
   (T=0.6,R=0.2).
2. **Secondary (160-inst)**: take K=2,4,8,16,32 from the higher_k run and
   K=1 from the gap-fill run restricted to the same 160 `insIndex` →
   one row per (K, flow), same slices.
3. Output CSV: `analysis/20260719_csr_k/csr_k_range.csv`.
4. Plot: RPDf% vs log2(K), one line per flow, 1440-inst panel + 160-inst
   panel side by side (PNG).

> **Scenario-name collision**: `csr_{full,neh}_d2wp_k{2,4,8}` exists in *both*
> `full_grid_k248` (1440) and `higher_k_validation` (160). Keep the two views
> in separate frames — concatenating them would double-count K=2,4,8 on the
> 160 subset. (This is also why `analyze_kappa_sweep.load_runs` cannot be used
> here: it raises on a scenario appearing in more than one run.)

---

## Phase 3: CSR Budget Scaling Sweep

### Question

For K ∈ {1, 2, 4, 8}, flow ∈ {csr_full_d2wp, csr_neh_d2wp}, sweep the CSR
budget fraction f ∈ {5, 10, 15, 20, 25, 30}%. Where does RPDf bottom out,
and what is the marginal value of each +5%p budget increment?

### Sources

| run | description |
| --- | --- |
| `output/20260714_csr_tl_scaling_sweep/20260714T234921_531156` | main sweep: K=1,2,4,8 × f=5,10,15,20,30 |
| `output/20260714_csr_full_grid_k248/20260714T184236_642971` | f=25% baseline: K=2,4,8 |
| `output/20260714_csr_tl_scaling_sweep/20260715T183418_361919` | K=1 f=25 gap-fill |

### Execution

The analysis script already exists. Run as:

```bash
uv run python scripts/analyze_csr_tl_scaling_sweep.py \
    output/20260714_csr_tl_scaling_sweep/20260714T234921_531156 \
    output/20260714_csr_full_grid_k248/20260714T184236_642971 \
    output/20260714_csr_tl_scaling_sweep/20260715T183418_361919
```

This prints 8 blocks to stdout (f→RPDf curves, best f per (flow,K),
T-decomposition, full-vs-neh paired W/T/L, sanity gates, starvation counts,
K=1 optimality counts). No output files currently — capture stdout to
`analysis/20260719_csr_budget_sweep/` for archival.

> The script's docstring (~70 lines) fully documents output semantics.
> It is self-contained — no new code needed for this phase.

---

## Execution order

```txt
Phase 1 → Phase 2 → Phase 3
```

Phase 3 can run before Phase 2 (data is independent), but its interpretation
sheet (the cross-phase conclusion) should be written last, after all three
results are in.

---

## Cross-phase conclusion document

After all three phases complete, consolidate into one analysis document:

```sh
plans/analysis/20260719/csr_init_k_budget_consolidation.md
```

Questions to answer in the consolidation:

1. Which init flow wins overall and at T=0.6? (Phase 1)
2. Is CSR (`csr_full_d2wp`) better than plain init (`mcf_lb_fmm_25p`) at
   equal budget? (Phase 1)
3. At f=25%, what is the best K? Monotonic improving, U-shape, or plateau?
   (Phase 2)
4. At the best K, what is the best f? Where does diminishing return start?
   (Phase 3)
5. What `(init_flow, K, f)` triple minimizes mean RPDf% across slices, at the
   pre-committed `d2wp` priority? (cross-phase join)
6. Does outer FMM (`csr_fmm_base`) pay for itself even at +40 % budget?
   (Phase 1 secondary table — note those 160 instances are the `(0.6,0.2)` cell.)
7. Which method wins in each of the 9 (T, R) cells, and does any cell's winner
   differ from the overall winner? (Phase 1 — a method may be worth keeping for
   a few cells despite a poor 1440-mean.)
8. Which 2- and 3-method portfolios minimise per-instance `min` RPDf, and which
   methods carry non-zero marginal contribution? (Phase 1 — oracle bound at
   k × budget; see the caveat in that section.)

---

## Appendix A — `insert_idle_time` before/after (executed 2026-07-19)

**Why this belongs here.** The three phases above join runs from 2026-07-13
(before commit `9b7ad2a`, coarse-exact `insert_idle_time`) with runs from
07-14/07-15 (after it). Anyone reading the consolidation will ask whether that
is legitimate. This appendix is the answer, and it is already executed — the
result is recorded below, not deferred.

Two independent measurements, because neither alone is conclusive.

### A.1 Paired 1440-instance A/B (noisy, end-to-end)

Two runs whose CSR scenario configs are **byte-identical** (verified by parsing
both YAMLs and comparing every key except `name` / `output_subdir`), on the same
1440 instances, same machine (`calop4`), with **exactly one `src/` commit
between them** (`9b7ad2a`):

| | run | scenarios |
| --- | --- | --- |
| before | `output/20260713_csr_init_methods/20260713T195341_009592` | `csr_full_d2wp`, `csr_neh_d2wp` (K=4) |
| after | `output/20260714_csr_full_grid_k248/20260714T184236_642971` | `csr_full_d2wp_k4`, `csr_neh_d2wp_k4` |

Invariant warnings — the thing the fix targeted:

| scenario | `left E/T on the table` | `post-process > CP-SAT` |
| --- | --- | --- |
| `csr_full_d2wp` | 341 → **0** | 29 → **0** |
| `csr_neh_d2wp` | 337 → **0** | 27 → **0** |

(The 27 matches commit `0930c31`'s "CpsatAdapter warning 27→0" exactly,
confirming this is the pair that was looked at contemporaneously.)

Solution quality — essentially unmoved:

| scenario | mean RPDf% | Δ | win/tie/loss |
| --- | --- | --- | --- |
| `csr_full_d2wp` | 21.738 → 21.640 | −0.098 %p | 680 / 110 / 650 |
| `csr_neh_d2wp` | 20.223 → 20.047 | −0.176 %p | 659 / 112 / 669 |

Read these as **"invariants fixed at essentially no cost to quality"**, not as
a quality gain: −0.098 / −0.176 %p over 1440 instances is a real but very small
mean improvement, and the ~50:50 per-instance win/loss shows the fix helps and
hurts individual instances in roughly equal measure rather than shifting the
whole distribution. A.2 is what explains *why* the effect is this small — see
its interpretation point 3 (reconstruction absorbs the coarse-layer gain).

### A.2 Deterministic seed-only re-derivation (the conclusive one)

`solve: false` means no CP-SAT runs at all — only the v4 dispatch seed and
`insert_idle_time`. Fully deterministic, so **any difference is 100 % code**.
It also reports the objective at two layers, `coarse_obj` (before reconstruction)
and `recon_obj` (after), which lets the "reconstruction washes the fix out"
hypothesis be tested directly.

```bash
uv run python scripts/dump_csr_coarse_obj.py \
    --config metadata/20260702/csr_idle_modes_v4_config.yaml \
    --out <out>.csv --workers 96      # ~5 min, 21600 rows
```

Baseline to diff against (survives on disk, produced at commit `bdc36cb`):
`analysis/20260702T013931_438875/csr_idle_modes_v4_full_20260702.csv`
— 21600 rows = 1440 instances × 3 modes × factor {1,2,4,8,16}.

Reproducibility checks done first: `dump_csr_coarse_obj.py` is **unchanged**
since `bdc36cb`; the config differs only by a doc path in a comment; `solve`,
`seed_dispatch: v4` and all three `idle_mode` values still resolve in current
code.

**Result 1 — `K > 1`: `idle_mode` is fully dead, and lookahead was already optimal.**

| 3 modes agree on `coarse_obj` | f=2 | f=4 | f=8 | f=16 |
| --- | --- | --- | --- | --- |
| baseline (07-02) | 469/1440 | 368/1440 | 391/1440 | 438/1440 |
| today | **1440** | **1440** | **1440** | **1440** |

| mode, `coarse_obj` mean | f=2 | f=4 | f=8 | f=16 |
| --- | --- | --- | --- | --- |
| flooring | −5.59 % | −5.28 % | −3.76 % | −1.72 % |
| ceiling | −0.00 % | −0.00 % | −0.01 % | −0.01 % |
| **lookahead** | **`max\|diff\| = 0`** | **0** | **0** | **0** |

lookahead is byte-identical to the new exact gate at every factor: the old
heuristic was **already exactly optimal** on v4 seeds, not merely dominant as
the 07-02 report concluded. `9b7ad2a`'s quality gain went entirely to flooring.

**Result 2 — `K == 1`: a control-group invariant broke.**

`metadata/20260702/csr_idle_modes_v4_config.yaml` states that at `factor=1` all
three modes are byte-identical (`ceil(d/1) == floor(d/1)`), making f=1 a clean
control. That held in the baseline (1440/1440) but holds on only **1308/1440**
today. Cause is *not* `9b7ad2a` (its new block is gated `if K > 1:` and
`continue`s past the mode branches) but `c36fa5e`, which deliberately changed
the lookahead tie-break `block_obj(db) <` → `<=` ("prefer the larger shift on
objective ties"). Since `K > 1` now bypasses `idle_mode` entirely, that `<=`
survives **only at `K == 1`** — precisely where lookahead should degenerate to
flooring. The 132 divergent instances tie on `coarse_obj` but differ in
schedule, which reconstruction turns into a different `recon_obj`: 82 better /
50 worse, net **−0.003 %**. Tracked in `TODO.md` §"Drop the `idle_mode` knob".

### A.3 Bearing on Phases 1–3

- **Cross-commit joins are legitimate.** On the seed path the fix changed
  nothing for lookahead, and the 07-13 `csr_*` scenarios ran `idle_mode:
  "lookahead"`. So the 07-13 and 07-14/15 runs are comparable, and Phase 2's
  merge of a K=1 gap-fill with the K=2,4,8 grid is sound.
- **Scope limit — this is a lower bound, not the whole effect.** The seed-only
  dump never exercises CP-SAT-produced coarse schedules, which is exactly where
  the 341 warnings originated. The two results together read as: lookahead was
  optimal *on seeds* and not optimal *on CP-SAT output* — which is why the exact
  gate was needed at all.
- **Anyone re-running the 07-02 idle-mode experiment gets a broken f=1 control**
  until the `K == 1` path is settled.

---

## Result

One entry per execution, newest last. Artifacts live under
`output/20260719_csr_analysis/<timestamp>/` (gitignored — these entries are the
tracked record).

### `20260719` — Phases 1–3 executed

All three phases ran on 2026-07-19. Conclusions, tables and follow-ups live in
[`csr_init_k_budget_consolidation.md`](csr_init_k_budget_consolidation.md);
this entry records only what was run and where the artifacts are.

| phase | script | artifacts (gitignored) |
| --- | --- | --- |
| 1 | `scripts/20260719/analyze_csr_init_methods.py` (new) | `analysis/20260719_csr_init/` |
| 2 | `scripts/20260719/analyze_csr_k_range.py` (new) | `analysis/20260719_csr_k/` |
| 3 | `scripts/analyze_csr_tl_scaling_sweep.py` (existing) + `scripts/20260719/analyze_csr_equal_budget.py` (new) | `analysis/20260719_csr_budget_sweep/` |

Headline: **at every fixed budget f, `csr_full_d2wp` at K=1 is the best setting**
(winner in all 18 slice × f columns) — coarsening does not pay at equal budget,
the CSR *inner flow* is what carries the gain, and `d2wp` beats `wdp` decisively
(so the pre-commitment was sound). Three corrections to the plan's framing
surfaced during execution:

- **Phase 1 is a `K = 4` result** — every `csr_*` scenario in
  `metadata/20260713/csr_init_methods.yaml` carries `factor: 4`. The plan did not
  state this, and it matters: Phase 1's inner-flow verdict (`neh` wins) is a K=4
  verdict, which Phases 2–3 show reverses at K≤2. The phases do not conflict;
  they measure different K.
- **The `(T,R)` decomposition changed a headline.** `mcf_lb_fmm_25p` ranks 5th of
  10 overall and loses the equal-budget A/B by 23.5 %p, yet is the outright
  winner in 3 of the 9 cells (all low-R) and the single most valuable portfolio
  partner (+12.42 %p oracle). The plan's insistence that a 1440-mean is not
  sufficient grounds for discarding a method was load-bearing, not a formality.

- **Phase 3's question was posed the wrong way round.** §Phase 3 asks "where does
  RPDf bottom out, and what is the marginal value of each +5 %p?" — but more
  budget is monotonically better, so "best f" is always the largest f measured
  and is a fact about the sweep's range, not the algorithm. The discriminating
  read is the transpose: **fix f (= fix cost) and rank the settings down the
  column.** `analyze_csr_tl_scaling_sweep.py`'s own docstring says this;
  `analyze_csr_equal_budget.py` was added to answer it directly, with
  winner→runner-up gaps and paired tests. Consolidation Q4 is now written that
  way, and the marginal-Δ reading demoted to a secondary "budget efficiency"
  note.

`csr_fmm_base` verdict: **worse by 31.5 %p despite +40 % budget** (paired
2/0/158) — the *a fortiori* argument holds, no budget-matched re-run needed.

One caveat that the equal-budget read surfaced and the original framing would
have hidden: **`F_k1`'s dominance erodes as f grows on hard instances.** On
T=0.6 the winner→runner-up gap falls 17.70 → 10.15 → 2.17 %p at f=5/15/30, and
at f=30 `F_k1` wins the mean while *losing* the per-instance count 233/0/247. A
crossover just past f=30 % is plausible and untested — so "K=1 always wins" is
budget-bounded, not general.

**vs the existing initialization methods** (`analyze_csr_vs_baseline.py`, added
after the first pass): the plan's §"The baselines already span part of the budget
axis" is right that no baseline sweep is needed — `mcf_lb_fmm` / `neh` / `_25p`
cover f ≈ 0/10/25/30. But Phase 1 only ever met them at **K=4**. Redone at K=1
and matched on measured wall-clock, **CSR beats every existing method in every
slice and in all 9 (T,R) cells** (`mcf_lb_fmm` −40.33 %p, `mcf_lb_fmm_25p`
−49.67, `neh_25p` −35.83, `neh` −34.79), and needs **~5× less budget to match
`neh`**. This also retracts the "uneven advantage" caveat above: at K=1 the
3-cell loss to `mcf_lb_fmm_25p` disappears entirely. Honest limit: at f=5 % vs
the NEH family CSR wins the mean but loses the per-instance count (628/83/729),
so that particular diagonal comparison is parity, not victory.

### `20260719T223053` — `insert_idle_time` deterministic re-dump

**Setting**

| | |
| --- | --- |
| machine | `aigpu0126` (baseline was `calop4` — irrelevant, path is deterministic) |
| config | `metadata/20260702/csr_idle_modes_v4_config.yaml` (copied into the artifact dir) |
| command | `uv run python scripts/dump_csr_coarse_obj.py --config metadata/20260702/csr_idle_modes_v4_config.yaml --out <dir>/20260719T223053_csr_idle_modes_v4_dump.csv --workers 96` |
| scope | 1440 instances × 3 idle modes × factor {1,2,4,8,16} = 21600 rows |
| solver | **none** — `solve: false`, seed-only (v4 dispatch + `insert_idle_time`) |
| wall clock | 5 m 04 s |
| baseline | `analysis/20260702T013931_438875/csr_idle_modes_v4_full_20260702.csv` (commit `bdc36cb`) |

**Artifacts**

- `20260719T223053_csr_idle_modes_v4_dump.csv` — raw 21600-row dump
- `20260719T223053_baseline_vs_today.csv` — per (mode, factor) baseline-vs-today means, % change, changed-instance counts, both layers
- `20260719T223053_mode_agreement.csv` — 3-mode agreement counts per factor

**Result — coarse layer (`coarse_obj`, before reconstruction)**

| mode | f=2 | f=4 | f=8 | f=16 |
| --- | --- | --- | --- | --- |
| flooring | −5.59 % | −5.28 % | −3.76 % | −1.72 % |
| ceiling | −0.00 % | −0.00 % | −0.01 % | −0.01 % |
| lookahead | 0 (0/1440 changed) | 0 | 0 | 0 |

**Result — 3-mode agreement (of 1440)**

| | f=1 | f=2 | f=4 | f=8 | f=16 |
| --- | --- | --- | --- | --- | --- |
| baseline coarse | 1440 | 469 | 368 | 391 | 438 |
| today coarse | 1440 | **1440** | **1440** | **1440** | **1440** |
| baseline recon | 1440 | 675 | 543 | 499 | 495 |
| today recon | **1308** | 1440 | 1440 | 1440 | 1440 |

**Interpretation**

1. **`9b7ad2a` made `idle_mode` genuinely dead at `K > 1`** — all three modes
   now coincide on 1440/1440 at every factor. Its quality gain went entirely to
   flooring; ceiling was already near-optimal.
2. **lookahead was already exactly optimal on v4 seeds** — byte-identical to the
   new exact gate at every factor. The 07-02 report's "lookahead dominates 40/40"
   understated it: not merely dominant, optimal.
3. **Reconstruction absorbs most of the coarse-layer gain.** flooring's −5.59 %
   at the coarse layer becomes only −0.51 % after reconstruction (f=2; likewise
   −5.28→−0.35, −3.76→−0.26, −1.72→−0.16). Measured on a fully deterministic
   path, this is the mechanism behind Appendix A.1's near-zero end-to-end Δ:
   the fix's gain is real at the coarse layer and then largely absorbed by
   reconstruction.
4. **Regression at `K == 1`.** The f=1 3-mode identity — asserted by the config
   as a clean control group — broke: recon agreement 1440 → 1308. Cause is
   `c36fa5e`'s deliberate lookahead tie-break change (`<` → `<=`), which the
   `K > 1` exact gate now shields everywhere *except* `K == 1`. Net effect
   −0.003 % (82 better / 50 worse of 132). Recorded in `TODO.md` §"Drop the
   `idle_mode` knob"; the no-op premise for hardcoding lookahead is retracted.

**Bearing on Phases 1–3** — cross-commit joins (07-13 runs with 07-14/15 runs)
are legitimate: the 07-13 `csr_*` scenarios ran `idle_mode: "lookahead"`, for
which the fix changed nothing on the seed path. Scope limit: this exercises only
dispatch seeds, never CP-SAT-produced coarse schedules — the origin of the
341→0 warnings — so it bounds the seed-path impact only.

### `20260719T231308` — pilot: widen the exact gate to `K >= 1`

**Question.** `insert_idle_time`'s exact block-shift gate is `if K > 1:`, so
`K == 1` still runs the older `sum_e > sum_t` weight-sum heuristic. Two things
follow, and both had to be measured before any decision:

1. `reconstruct_coarse_schedule` (`solution/schedule_build.py:112`) calls
   `insert_idle_time` with **no `time_factor` and no `idle_mode`** — defaults
   `1` / `"flooring"`. That is the final original-scale post-process of **every
   CSR scenario at every K**. So the `K == 1` path is not K=1-specific: changing
   it changes all K. "Fix and re-run only K=1" is therefore unsound — it would
   leave K=1 on new code and K≥2 on old, relocating the asymmetry rather than
   removing it. A real fix implies re-running Phases 2+3 (~69 000 instance-runs).
2. Nothing measured so far bounds what the exact gate would *buy* at `K == 1`.
   The −0.003 % in the previous entry is the tie-break's effect, not the
   heuristic-vs-exact effect.

**Setting** — identical to `20260719T223053` (same config, same command, same
machine, 21600 rows, 5 m 06 s) except for a one-line source change,
`if K > 1:` → `if K >= 1:`, saved as
`20260719T231308_pilot_code_change.diff`. Reverted after measuring; 38 schedule
/ CPSAT-adapter tests pass under the pilot. Compared against
`20260719T223053` (same code except that line).

**Result**

| | coarse changed | recon changed | recon Δ |
| --- | --- | --- | --- |
| every mode, factor {2,4,8,16} | 0 | 0 | 0 |
| flooring / ceiling, factor 1 | 0 | 0 | 0 |
| lookahead, factor 1 | **0** | 132 | **+0.0034 %** |

f=1 three-mode agreement: **1308 → 1440/1440**.

**Interpretation**

1. **No re-run is justified.** `coarse_obj` is unchanged on all 21600 rows —
   including `factor=1`. The `K == 1` heuristic was **already producing the
   exact optimum**, exactly as lookahead already did at `K > 1` (previous
   entry). The exact gate buys nothing in objective terms, so the ~69 000-run
   re-run implied by point 1 above has **zero expected benefit**. Phases 1–3
   proceed on existing data; the `K == 1` algorithmic asymmetry is real in code
   but empirically void.
2. **It does repair the broken control group.** Widening the gate restores the
   f=1 three-mode identity to 1440/1440 — the exact path lands on `da`, i.e. it
   reproduces the pre-`c36fa5e` choice and makes the `<=` tie-break unreachable.
3. **Cost is a rounding error, and slightly negative.** The 132 lookahead
   instances move +0.0034 % on `recon_obj` (50 better / 82 worse) — the exact
   mirror of the previous entry's −0.003 %. So `c36fa5e`'s `<=` is a hair
   *beneficial* downstream while breaking the invariant; the pilot trades that
   hair back for the invariant.

**Consequence for `TODO.md` §"Drop the `idle_mode` knob".** The recommendation
there (widen to `K >= 1` and delete all three branches) is now measured, not
just argued: it is objective-neutral at the coarse layer, costs +0.003 % at the
reconstructed layer, and restores the invariant. It remains a **deferred code
cleanup, not an analysis blocker** — nothing in Phases 1–3 depends on it.
