# CSR presentation figures — three merged report rebuilds

**Date:** 2026-07-20 · **Status:** executed
**Prior analysis:** [`../20260719/csr_init_k_budget_consolidation.md`](../20260719/csr_init_k_budget_consolidation.md)

---

## Scope — what this document is

**No new solving happened.** Every number here comes from runs that already
existed on 2026-07-19; this is a report-assembly exercise. Three merged run
directories were built so that each slide of `vault/20260719_p3_정리.pdf` is
backed by **one figure on one instance grid**, which the source runs could not
provide because each covered only part of the axis being presented.

The scientific conclusions are the prior document's and are unchanged. What is
new and load-bearing here:

1. A **reporting bug** that silently mixed instance grids inside a single chart,
   found because a slide number looked wrong (§1). This is the reason this
   document exists rather than being a footnote.
2. A **configuration defect** in the 160-instance experiment-1 run that made its
   κ=4 numbers not the κ=4 the slide defines (§3).
3. The **f=25 % column** integrated into the budget sweep, which the original
   sweep skipped (§4).

The prior document's scope caveat carries over in full: every RPDf below measures
**initialization quality under a fixed initialization budget**, not final
solution quality. RPDf is the symmetric form `2(obj−ref)/(obj+ref)`; negative is
better than `BKS_data`.

---

## 1. The bug: charts ignore `ins_index`

**Symptom.** In the first k=1..32 merged report, the two κ=1 scenarios plotted at
−4.44 % while their own rows in `<ts>_rpdf_comparison.csv` said 23.40 %.

**Cause.** The κ=1 scenarios were symlinked from a 1440-instance run, the κ>1
scenarios from a 160-instance run. A `POST_PROCESS_ONLY` config's `ins_index`
filters `summary_csv` and everything derived from it — including the comparison
CSV and the dashboards, which iterate `self.scenario_results` — but
**`write_post_run_subroutine_chart_artifacts` takes no instance list.** It
discovers instances by walking the run directory, so the scatter and
flow-comparison HTMLs averaged over whatever was on disk.

The two artifacts therefore described different experiments, and **nothing in the
output said so.** −4.44 % is the correct full-grid value (it appears in the prior
document's Q4 table); it is simply not the value for the 160-instance subset the
slide was about.

**Fix.** `scripts/build_merged_run_dir.py` gained `--intersect-instances`
(commit `86137f8`), which symlinks only the instances common to every scenario,
making the directory itself the single grid instead of relying on a downstream
filter. Docstring updated in a follow-up commit.

> **Verification lesson, recorded because it was the actual failure.** The bug
> survived a first check that looked only at `rpdf_comparison.csv` (all rows
> n=160) and *inferred* the chart matched. Every verification below instead
> parses the `const payload = {...}` JSON out of the HTML and asserts
> `instance_count` per trace. **Check the deliverable, not a proxy for it.**

**Open production issue — not fixed.** The chart writers still ignore
`ins_index`. `--intersect-instances` fixes the merge path only; any
`POST_PROCESS_ONLY` config pointed at a superset directory can still produce this
divergence. See §6.

---

## 2. Sources and reproduction

All three merged directories are built with the same two-step recipe: assemble
symlinks, then run the committed config. `build_merged_run_dir.py` stamps a fresh
timestamp as the run id, so a rebuild lands in a **new** directory and the
config's `analysis_dir_path` must be repointed (or `--run-id` passed to pin it).

| merged run | scenarios | inst | config | slide |
| --- | --- | --- | --- | --- |
| `output/20260719_merge_csr_k1_32/20260720T154525_895426` | 12 | 160 | `metadata/20260719/merge_csr_k1_32.yaml` | p8 (실험 2) |
| `output/20260720_merge_csr_init_k4/20260720T155723_528668` | 6 | 160 | `metadata/20260720/merge_csr_init_k4.yaml` | p6 (실험 1) |
| `output/20260720_merge_csr_k_f_sweep/20260720T171158_514111` | 48 | 1440 | `metadata/20260720/merge_csr_k_f_sweep.yaml` | p10–12 (실험 3) |

Source runs, with the role each plays:

| source run | inst | feeds |
| --- | --- | --- |
| `output/20260713_csr_init_methods/20260713T091912_833529` | 160 | init_k4: the 4 baselines |
| `output/20260713_csr_init_methods/20260713T195341_009592` | 1440 | slide p12 baselines (not merged; read directly) |
| `output/20260714_csr_higher_k_validation/20260714T154426_711694` | 160 | k1_32: κ=2..32; init_k4: κ=4 |
| `output/20260714_csr_tl_scaling_sweep/20260715T183418_361919` | 1440 | k1_32 and k_f_sweep: κ=1 @ f=25 |
| `output/20260714_csr_full_grid_k248/20260714T184236_642971` | 1440 | k_f_sweep: κ=2,4,8 @ f=25 |
| `output/20260714_csr_tl_scaling_sweep/20260714T234921_531156` | 1440 | k_f_sweep: f=5,10,15,20,30 |

### Build commands

```bash
# (a) k=1..32 on the 160-instance (T,R)=(0.6,0.2) subset.
#     --intersect-instances is REQUIRED: the k=1 source holds 1440 instances,
#     the k>1 source 160. Without it the k=1 traces average the wrong grid.
HK=output/20260714_csr_higher_k_validation/20260714T154426_711694
K1=output/20260714_csr_tl_scaling_sweep/20260715T183418_361919
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260719_merge_csr_k1_32 --intersect-instances \
    $HK/csr_full_d2wp_k2  $HK/csr_full_d2wp_k4  $HK/csr_full_d2wp_k8 \
    $HK/csr_full_d2wp_k16 $HK/csr_full_d2wp_k32 \
    $HK/csr_neh_d2wp_k2   $HK/csr_neh_d2wp_k4   $HK/csr_neh_d2wp_k8 \
    $HK/csr_neh_d2wp_k16  $HK/csr_neh_d2wp_k32 \
    $K1/csr_full_d2wp_k1_tl25=csr_full_d2wp_k1 \
    $K1/csr_neh_d2wp_k1_tl25=csr_neh_d2wp_k1

# (b) experiment 1: 4 baselines + CSR κ=4 at the 25 % inner-TL definition.
#     Both sources are 160-instance, so no flag is needed.
IM=output/20260713_csr_init_methods/20260713T091912_833529
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260720_merge_csr_init_k4 \
    $IM/mcf_lb_fmm $IM/mcf_lb_fmm_25p $IM/neh $IM/neh_25p \
    $HK/csr_full_d2wp_k4 $HK/csr_neh_d2wp_k4

# (c) experiment 3: kappa x f, 48 scenarios, full 1440 grid.
SW=output/20260714_csr_tl_scaling_sweep/20260714T234921_531156
K248=output/20260714_csr_full_grid_k248/20260714T184236_642971
ARGS=""
for f in tl05 tl10 tl15 tl20 tl30; do
  for fl in full neh; do for k in 1 2 4 8; do
    ARGS="$ARGS $SW/csr_${fl}_d2wp_k${k}_${f}"
  done; done
done
for fl in full neh; do
  ARGS="$ARGS $K1/csr_${fl}_d2wp_k1_tl25"                       # f=25, kappa=1
  for k in 2 4 8; do
    ARGS="$ARGS $K248/csr_${fl}_d2wp_k${k}=csr_${fl}_d2wp_k${k}_tl25"
  done
done
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260720_merge_csr_k_f_sweep $ARGS
```

Then, for each, repoint `analysis_dir_path` in the corresponding config and run:

```bash
uv run python main.py --config metadata/20260719/merge_csr_k1_32.yaml
uv run python main.py --config metadata/20260720/merge_csr_init_k4.yaml
uv run python main.py --config metadata/20260720/merge_csr_k_f_sweep.yaml
```

The configs were generated by **reading `subroutine_flow` out of the source
configs**, never by transcription, with generation-time assertions that the
coarsening factor and every inner time limit match the scenario label. A
mislabelled scenario therefore cannot reach a report. (The generators were
throwaway; the assertions they enforce are restated in §3 and §4 so the check can
be redone from this document alone.)

`merge_csr_k1_32.yaml` and `merge_csr_init_k4.yaml` also carry a 160-entry
`ins_index`. After `--intersect-instances` this is redundant, and it is kept only
as a second barrier — it does **not** protect the charts (§1).

---

## 3. Experiment 1 — the 160-instance κ=4 numbers were not the stated κ=4

**Question (slide p5–6).** Is CSR worth using as an initialization method?

The slide defines `CSR(κ=4, 25%)` as *"MCF-LB → FMM → NEH-CP → SW-CP 각각의 시간
제약 25%로 축소"* — every inner step at 25 % of its standard budget.

**The original 160-instance run does not implement that definition.** Verified in
`output/20260713_csr_init_methods/20260713T091912_833529/csr_init_methods.yaml`:

| scenario | inner step | its TL | fraction of standard |
| --- | --- | --- | --- |
| `csr_full_d2wp` | `run_flip_makespan_cp_from_incumbent` | `0.005625nc` | **62.5 %** |
| | `neh_cp` | `0.016875nc` | **62.5 %** |
| `csr_neh_d2wp` | `neh_cp` | `0.0225nc` | **83.3 %** |

(Standards: `0.009nc` and `0.027nc`.) Both scenarios hand the **entire** CSR
budget of `0.0225nc` to the early inner steps, leaving `incremental_sw_cp` — the
step that does the real work — nothing. The consequence is measurable: in that
run `incremental_sw_cp` is reached on only 48/160 instances for `full` and
**1/160** for `neh`, versus 160/160 in the conforming run, where it is always the
winning candidate.

This is why the old slide read 40.1 % / 38.9 % while the correct κ=4 is
34.9 % / 35.0 %. The gap is a starved inner pipeline, not run-to-run noise.

**Rebuild.** The merged run takes the four baselines from the same 160-instance
run (they are unaffected — they contain no CSR step) and the two CSR scenarios
from `20260714T154426_711694`, which does implement 25 % throughout.

**Result** (160 instances, verified from the scatter HTML payload; `time%` is
measured wall-clock as a fraction of the `0.09nc` scenario cap):

| method | mean RPDf | time % |
| --- | --- | --- |
| MCF-LB + FMM (TL 10 %) | 41.54 % | 10.92 |
| MCF-LB + FMM (TL 25 %) | 40.61 % | 22.85 |
| NEH-CP (TL 30 %) | 38.47 % | 30.95 |
| NEH-CP (TL 25 %) | 39.18 % | 25.93 |
| **CSR(κ=4, 25 %) Full** | **34.85 %** | 25.10 |
| **CSR(κ=4, 25 %) No MCF** | **35.00 %** | 25.07 |

**Conclusion.** On the hardest instance set, CSR at the stated definition is
**3.6 %p better than the best existing method while using less time** (25.1 % vs
30.9 %). The old figure supported only "comparable to NEH-CP"; the corrected one
supports "better than NEH-CP, in less time".

> Note the direction of the correction: fixing a defect that *disadvantaged* CSR
> strengthened CSR's case. The defect was found by config audit, not because the
> result looked wrong.

---

## 4. Experiment 3 — the f=25 % column, and the merged κ × f grid

**Question (slide p9–10).** At each time budget, which coarsening factor is best?

The original sweep run covers f ∈ {5, 10, 15, 20, 30} % — **25 % is missing**, and
25 % is the budget experiments 1 and 2 use, so its absence broke the link between
the three experiments. The f=25 % data existed but was scattered across two other
runs (κ=1 in a gap-fill run, κ=2/4/8 in the full-grid run) under a different
naming scheme.

The merge fills it: 48 scenarios = κ ∈ {1,2,4,8} × f ∈ {5,10,15,20,25,30} % ×
{Full, No MCF}, **all 1440 instances**, verified per trace from the HTML payload.
Every scenario's `factor` and inner TLs were asserted against its label at config
generation (κ=4 @ f=25 ⇒ `factor: 4`, `0.00225nc`, `0.00675nc`).

**Full flow** — mean RPDf %, 1440 instances (**bold** = row winner):

| f ＼ κ | **1** | 2 | 4 | 8 |
| --- | --- | --- | --- | --- |
| 5 % | **26.51** | 56.55 | 64.37 | 69.34 |
| 10 % | **6.10** | 33.60 | 41.82 | 47.18 |
| 15 % | **0.55** | 24.91 | 31.57 | 38.08 |
| 20 % | **−2.59** | 19.06 | 25.95 | 33.16 |
| **25 %** | **−4.44** | **15.19** | **21.64** | **29.74** |
| 30 % | **−5.60** | 12.92 | 19.81 | 27.26 |

**No MCF flow** — mean RPDf %, 1440 instances:

| f ＼ κ | 1 | 2 | 4 | 8 |
| --- | --- | --- | --- | --- |
| 5 % | **50.46** | 51.42 | 57.00 | 61.66 |
| 10 % | 35.05 | **34.98** | 38.15 | 43.96 |
| 15 % | 27.50 | **27.36** | 29.87 | 36.61 |
| 20 % | **21.97** | 22.37 | 24.76 | 31.66 |
| **25 %** | 17.96 | **17.77** | 20.05 | 29.00 |
| 30 % | 15.11 | **14.98** | 17.73 | 26.19 |

These reproduce the prior document's Q4 table exactly, which is the intended
cross-check: the merge is a re-presentation, not a re-measurement.

**Reading.** In the `full` flow, κ=1 wins every budget and the curve is monotone
worsening in κ. In `No MCF`, κ=2 edges κ=1 at four of six budgets, but by
**0.07–0.19 %p** — a tie, not a win — while κ≥4 is clearly worse. `Full` at κ=1
beats **every** `No MCF` cell at every budget, so the overall answer is
budget-independent.

> **Slide caveat.** The 160-instance view (§5) shows `No MCF` bottoming at κ=8,
> visibly U-shaped. The full grid shows that is a subset artifact. A slide
> carrying the 160-instance figure should not claim monotonicity for `No MCF`;
> the honest caption for that figure is "coarsening brings no benefit", which is
> what the deck now says.

### Time-matched comparison against the existing methods (slide p12)

| claim | measured | verdict |
| --- | --- | --- |
| CSR(κ=1, 5 %) beats MCF-LB+FMM in all 9 (T,R) cells, **half** the time | 9/9; 5.00 % vs 10.51 % time = **0.476×** | holds |
| CSR(κ=1, 20 %) beats NEH-CP in all 9 cells, **2/3** the time | 9/9; 18.89 % vs 26.53 % time = **0.712×** | holds; ratio is ≈0.71, so "about 70 %" is the safer phrasing |
| CSR(20 %) replaces FMM+NEH-CP at **half** the cost | 18.89 % vs 10.51+26.53 = 37.04 % = **0.51×** | holds |

**Provenance correction for the deck.** The baseline numbers in that comparison
are **not** from `20260720_merge_csr_init_k4` (160 instances, T=0.6 only — it
cannot produce a 9-cell table). They are from
`output/20260713_csr_init_methods/20260713T195341_009592` (1440), matched to six
decimal places: `mcf_lb_fmm` at (T=0.6, R=0.2) is 0.415842 there versus 0.415424
in the 160-instance merge.

---

## 5. Experiment 2 — k=1..32 on the 160-instance subset

**Question (slide p7–8).** At a fixed budget, how does quality vary with κ?

This is the report whose κ=1 traces were wrong before §1's fix. After the rebuild
all 12 traces carry `instance_count: 160` (asserted from the HTML payload).

Mean RPDf %, 160 instances, f=25 %:

| κ | 1 | 2 | 4 | 8 | 16 | 32 |
| --- | --- | --- | --- | --- | --- | --- |
| **Full** | **23.40** | 33.78 | 34.85 | 34.16 | 35.78 | 43.61 |
| **No MCF** | 35.27 | 36.43 | 35.00 | **33.94** | 35.59 | 43.92 |

κ=1 `Full` is the best cell, and κ=32 is catastrophic in both flows — confirming
no upturn hides beyond the κ≤8 range the full grid measures. `No MCF`'s minimum
at κ=8 is the subset artifact discussed in §4.

**Correction applied to the deck.** An earlier draft labelled `No MCF, κ=1` as
18.0 %. That is the *1440-instance* value at f=25 % (17.96 %, §4), carried onto a
160-instance slide. The 160-instance value is **35.27 %**. The two differ by
17 %p and point to opposite conclusions about whether κ=1 is good in the `No MCF`
flow, so this was not a cosmetic error.

---

## 6. Conclusion

1. **The three merged reports back the deck, and the deck's claims survive
   verification.** All figure-level numbers were re-derived from HTML payloads
   and comparison CSVs; the timing claims were checked against measured
   wall-clock rather than nominal budgets.
2. **Two data defects were found and corrected, both of which had reached
   slides**: the 160-instance κ=4 configuration did not implement the stated 25 %
   definition (§3), and a 1440-instance value had been carried onto a
   160-instance slide (§5). Both were found by cross-checking a number against
   its source, not by inspection of the figures.
3. **One tooling defect was found and partly fixed** (§1). The merge path is
   safe; the underlying reporter is not.
4. **No scientific conclusion changed.** The κ × f table reproduces the prior
   document exactly. Experiment 1's conclusion strengthened — from "comparable to
   NEH-CP" to "better, in less time" — purely by removing the configuration
   defect.

### Follow-ups

- **`write_post_run_subroutine_chart_artifacts` ignores `ins_index`** (§1). Any
  `POST_PROCESS_ONLY` config over a superset directory silently reports charts on
  a different grid than its CSVs, with no warning. `--intersect-instances` covers
  only merged dirs. Either thread the instance list into the chart writers or
  have them log the grid they used. **Undecided: fix now vs `TODO.md`.**
  Worth checking whether `metadata/20260718/merge_p_u8_sweep.yaml`'s merged run
  had matching grids — if not, its charts carry the same defect.
- **The `ceil` inflation argument is still ad-hoc.** The mechanism behind κ>1
  being worse — `p → ceil(p/κ)` inflating effective processing time by
  +3.0/+9.5/+23.7/+54.4/+120.8 % at κ=2/4/8/16/32 on the 160-instance subset —
  was computed interactively and is **not committed as a script**. It must not be
  quoted until it is reproducible.
- **CSR draws as a single point in the flow-comparison chart**, so the deck
  cannot show that κ>1's inner solve converges normally (only that its endpoint
  is bad). Plan drafted at
  `plans/experiment/20260720/csr_inner_progress_log.md` (uncommitted, not
  executed); it requires a ~30 min re-run because the needed
  `sec_elapsed_step` column postdates every κ>1 run.
- **Provenance-subject convention drift.** The three run-setting commits for
  these merges use the subject `… output setting`, while 92 prior commits use
  `… run setting` and `CLAUDE.md` documents only the latter. Since `output/` is
  gitignored and the commit subject is the only index, `rg "run setting"` now
  misses these three. Either reword them or document `output setting` as a third
  provenance kind for `POST_PROCESS_ONLY` re-aggregations.
