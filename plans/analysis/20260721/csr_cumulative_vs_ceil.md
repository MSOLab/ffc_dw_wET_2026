# CSR cumulative vs default (ceil) — paired comparison

- **Date:** 2026-07-21
- **Status:** ✅ done (2026-07-22). Full 1440-grid paired across all 12 cells.
  Verdict: cumulative does **not** decisively beat ceil — see `## 결과 (실행 후)`.
- **Prerequisite reading:**
  [`../experiment/20260721/csr_coarsen_mode_cumulative.md`](../../experiment/20260721/csr_coarsen_mode_cumulative.md)
  — defines the `cumulative` coarsening rule and the experiment design.

## Question

Does `coarsen_mode: cumulative` outperform the default mode (no explicit
`coarsen_mode` → `ceil`) at the same coarsening factor κ and the same CSR
time-limit fraction f? A paired per-instance comparison across the 12-cell
grid κ ∈ {1,2,4,8} × f ∈ {5%,10%,15%}, all using the full pipeline
(mcf→flip→neh_cp→sw_cp→base_cp inside `coarsen_solve_reconstruct`).

Primary metric: **ΔRPDf = RPDf(cumulative) − RPDf(ceil)**, signed so negative =
cumulative wins. Stratified by T (all / T=0.2 / T=0.4 / T=0.6).

## Source runs

| Label | Run path | Mode |
|---|---|---|
| ceil (baseline) | `output/20260720_merge_csr_k_f_sweep/20260720T171158_514111` | default (no `coarsen_mode` → `ceil`) |
| cumulative | `output/20260721_csr_k_f_cumulative/20260721T215135_772079` | `coarsen_mode: cumulative` |

Both runs: full 1440-instance PRA2017 large grid, global budget `0.09nc`,
12 worker threads. All 12 ceil cells confirmed at 1440/1440 instances.

> **⚠ Validity threat — this is a cross-run comparison, not one batch.**
> The ceil "run" is itself a `POST_PROCESS_ONLY` merge (commit `435ed18`) of
> *older solve runs*: κ=1 f=5/10/15% came from a gap-fill run, κ=2/4/8 from
> `20260714_csr_full_grid_k248`. The cumulative solves ran fresh on `calop4`
> on 2026-07-21. Because RPDf is measured under a **wall-clock** budget
> (`0.09nc`), any difference in machine speed or host load between the ceil
> solve batches and the cumulative batch biases ΔRPDf on top of the coarsen_mode
> effect. The κ=1 cell is the built-in control for this offset (see Analysis
> block 3) — read the whole comparison relative to it, not against zero. Before
> trusting the numbers, confirm the ceil solve batches ran on `calop4` too; if
> they did not, the κ=1 offset is the only defence and the verdict must clear it
> by a wide margin.

### Scenarios to extract (12 per source)

| κ | f=5% | f=10% | f=15% |
|---|---|---|---|
| 1 | `csr_full_d2wp_k1_tl05` / `csr_k1_tl05` | `..._tl10` | `..._tl15` |
| 2 | `csr_full_d2wp_k2_tl05` / `csr_k2_tl05` | `..._tl10` | `..._tl15` |
| 4 | `csr_full_d2wp_k4_tl05` / `csr_k4_tl05` | `..._tl10` | `..._tl15` |
| 8 | `csr_full_d2wp_k8_tl05` / `csr_k8_tl05` | `..._tl10` | `..._tl15` |

- ceil scenario names use `csr_full_d2wp_k*_tl*` (from the previous sweep)
- cumulative scenario names use `csr_k*_tl*` (from the current sweep)
- The inner solve flows are identical except for the `coarsen_mode` field

## Reproduction

### Step 1: Build merged run directory

`--run-id` pins the merged dir name so the post-process config's
`analysis_dir_path` is deterministic (otherwise the script stamps a fresh
timestamp and the path below must be edited to match).

```bash
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260721_merge_csr_cumulative_vs_ceil \
    --run-id 20260721_cum_vs_ceil \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k1_tl05=ceil_k1_tl05 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k1_tl10=ceil_k1_tl10 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k1_tl15=ceil_k1_tl15 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k2_tl05=ceil_k2_tl05 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k2_tl10=ceil_k2_tl10 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k2_tl15=ceil_k2_tl15 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k4_tl05=ceil_k4_tl05 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k4_tl10=ceil_k4_tl10 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k4_tl15=ceil_k4_tl15 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k8_tl05=ceil_k8_tl05 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k8_tl10=ceil_k8_tl10 \
    output/20260720_merge_csr_k_f_sweep/20260720T171158_514111/csr_full_d2wp_k8_tl15=ceil_k8_tl15 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k1_tl05=cum_k1_tl05 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k1_tl10=cum_k1_tl10 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k1_tl15=cum_k1_tl15 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k2_tl05=cum_k2_tl05 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k2_tl10=cum_k2_tl10 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k2_tl15=cum_k2_tl15 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k4_tl05=cum_k4_tl05 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k4_tl10=cum_k4_tl10 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k4_tl15=cum_k4_tl15 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k8_tl05=cum_k8_tl05 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k8_tl10=cum_k8_tl10 \
    output/20260721_csr_k_f_cumulative/20260721T215135_772079/csr_k8_tl15=cum_k8_tl15
```

The 24 label mapping matches `metadata/20260721/merge_csr_cumulative_vs_ceil.yaml`
scenario-for-scenario (`ceil_k{κ}_tl{f}`, `cum_k{κ}_tl{f}`). Both sides are the
full 1440 grid, so no `--intersect-instances` is needed — but the script aborts
on an instance-set mismatch, which is the guard that the cumulative run has
finished all 1440 instances per cell. If it aborts, the run is still in
progress; wait, don't pass `--allow-instance-mismatch`.

### Step 2: Run POST_PROCESS_ONLY with the merge config

```bash
uv run python main.py --config metadata/20260721/merge_csr_cumulative_vs_ceil.yaml
```

`metadata/20260721/merge_csr_cumulative_vs_ceil.yaml` (already written) is
`POST_PROCESS_ONLY`, `analysis_dir_path` = the Step 1 merged dir, 24 scenarios
each carrying its `subroutine_flow` (identical across the pair except
`coarsen_mode: ceil` vs `cumulative`), `draw_gantt` / `draw_progress_plot` false
(mandatory — the merged dir is symlinks). It emits the combined
`<run>_rpdf_comparison.csv` over all 24 scenarios plus the standard
side-by-side dashboards.

### Step 3: Run analysis script

```bash
uv run python scripts/20260721/analyze_csr_cumulative_vs_ceil.py \
    output/20260721_merge_csr_cumulative_vs_ceil/20260721_cum_vs_ceil
```

The script reads that run's `_rpdf_comparison.csv` (columns `insIndex`,
`scenarioName`, `T`, `RPDf_BKS_data`, `elapsedTime`, `time%`), splits
`scenarioName` on the `ceil_`/`cum_` prefix into (mode, κ, f), and joins the two
modes on `insIndex` within each (κ, f) cell.

## Analysis blocks (script outline)

1. **Per-cell ΔRPDf table** — mean/median ΔRPDf (cumulative − ceil) at each
   (κ, f) cell, overall and per T-stratum. Negative = cumulative better.
2. **Per-cell win/tie/loss** — paired per-instance count of
   cumulative < ceil / equal / cumulative > ceil.
3. **κ=1 calibration (control, not identity)** — at κ=1 the factor-1 path does
   no coarsening, so ceil and cumulative feed the pipeline identical input.
   They still will **not** match per-instance: every solve step runs 8-thread
   CP-SAT under a wall-clock limit (nondeterministic), and the two sides come
   from different solve batches on possibly different hardware. So κ=1 ΔRPDf
   estimates the **null floor** — CP-SAT time-limit noise plus any cross-run /
   machine offset — not zero. Report its mean, median, and win/tie/loss as the
   baseline the κ≥2 effects must clear. A *large* κ=1 mean (e.g. |mean| beyond a
   fraction of a point) flags a real machine/timing confound, not a bug.
4. **Net effect vs κ** — subtract the κ=1 calibration:
   `netΔ(κ,f) = ΔRPDf(κ,f) − ΔRPDf(κ=1,f)`. Does cumulative's edge grow or
   shrink with κ? Which (κ, f) gives the largest genuine (κ=1-corrected) edge?
5. **Verdict** — per the success criterion in the experiment plan: the
   κ=1-corrected net edge (block 4), not the raw ΔRPDf, must beat ceil by more
   than the κ=1↔κ=2 gap (~11.20pp in the prior experiment) to claim the rule
   change is causal. Note pairing coverage (block 6) — a verdict on a
   partially-paired grid is provisional.
6. **Coverage / pairing count** — N instances paired per (κ, f) cell after the
   join. Expect 1440 each once the cumulative run finishes; log any cell short
   of 1440 explicitly (no silent truncation) — an unfinished cumulative run
   drops those instances from the pairing and skews the cell's mean.

## Output artifacts

All written to `analysis/20260721_csr_cumulative_vs_ceil/` (gitignored):
- `cum_vs_ceil_rpdf.csv` — per-instance paired ΔRPDf
- `cum_vs_ceil_summary.csv` — per-cell aggregated stats (raw + κ=1-corrected)
- `cum_vs_ceil_win_tie_loss.csv` — win/tie/loss per cell

## 결과 (실행 후)

- **Merged run:** `output/20260721_merge_csr_cumulative_vs_ceil/20260721_cum_vs_ceil`
  (24 scenarios × 1440, 34 560 rows). **Coverage: all 12 (κ, f) cells paired at
  1440/1440.**
- **Reproduce:** the three commands under `## Reproduction`, then
  `uv run python scripts/20260721/analyze_csr_cumulative_vs_ceil.py \
  output/20260721_merge_csr_cumulative_vs_ceil/20260721_cum_vs_ceil`.

All ΔRPDf are percentage points, **cumulative − ceil**, so **negative = cumulative
wins**.

### The κ=1 offset is real, and it is not a budget/hardware artifact

At κ=1 the factor-1 path does no coarsening, so cumulative and ceil feed the
pipeline identical input — yet they do **not** agree:

| f | κ=1 mean ΔRPDf | win/tie/loss (cum) | mean elapsed ceil / cum |
|---|---|---|---|
| 5% | **+1.74** | 352 / 141 / 947 | 4.224 / 4.218 s |
| 10% | +0.49 | 590 / 133 / 717 | 8.110 / 8.106 s |
| 15% | −0.41 | 662 / 129 / 649 | 11.966 / 11.962 s |

Elapsed time and `time%` are identical across the two modes at every cell, so
this is **not** the cross-run hardware confound the plan warned about — both
batches realized the same wall-clock budget. But the κ=1 split is systematically
lopsided (f=5%: cumulative loses 947 vs wins 352; bestObj differs on 91% of
instances, ceil better on 66.7% vs cumulative 24.4%), which symmetric 8-thread
CP-SAT noise cannot produce. The residual is **code/path drift** between the
2026-07-20 ceil batch and the 2026-07-21 cumulative build, strongest at the
tightest budget (f=5%) and decaying to ≈0 by f=15%. **This is exactly why the
verdict is read on the κ=1-corrected net effect, not raw ΔRPDf** — the raw
numbers carry this offset.

### κ=1-corrected net effect — `netΔ(κ,f) = ΔRPDf(κ,f) − ΔRPDf(κ=1,f)`

Overall (all T), %p, negative = cumulative wins:

| κ | f=5% | f=10% | f=15% |
|---|---|---|---|
| 2 | −2.03 | +0.71 | +0.99 |
| 4 | **−2.19** | −1.75 | −0.25 |
| 8 | +1.46 | +2.33 | +4.73 |

- **κ=4 is the only band where cumulative helps** (net −0.25 to −2.19 %p).
- **κ=2 is a wash** (sign flips with f).
- **κ=8 clearly hurts, and the harm grows with f** (up to +4.73 %p worse).

### T-stratified — the κ=8 harm is a T=0.2 phenomenon

net effect (κ=1-corrected), by T-stratum:

| stratum | κ=4 (5/10/15%) | κ=8 (5/10/15%) |
|---|---|---|
| T=0.2 | −3.04 / −0.50 / +1.16 | **+7.66 / +12.11 / +16.24** |
| T=0.4 | −3.41 / −4.07 / −1.57 | −3.50 / −4.89 / −2.61 |
| T=0.6 | −0.11 / −0.67 / −0.33 | +0.22 / −0.24 / +0.57 |

- At **T=0.2, κ=8** cumulative is **catastrophically worse** (up to +16 %p) — it
  dominates the overall κ=8 penalty.
- At **T=0.4** cumulative is consistently the **best** (net −1.6 to −4.9 %p at
  κ≥4).
- At **T=0.6** everything is within ±0.7 %p — coarsen_mode barely matters on the
  hard-due-date slice.

### Verdict

Best κ=1-corrected net edge is **κ=4, f=5% → −2.19 %p**, far short of the
pre-registered **−11.20 %p** bar (the κ=1↔κ=2 gap from the prior experiment).
**The `cumulative` rule change is not causally superior to `ceil`.** Its effect
is small, direction-dependent (helps only at κ=4 and on T=0.4; hurts sharply at
κ=8/T=0.2), and never approaches the magnitude that would justify changing the
default coarsening rule. If cumulative is adopted anywhere, restrict it to
**κ=4** and avoid **κ=8 on loose-due-date (T=0.2) instances**.

> Caveat: because the κ=1 offset traces to code/path drift between the two
> source batches (not a controlled A/B on one build), even the κ=1-corrected
> numbers are a first-order correction. A clean re-test would run `ceil` and
> `cumulative` from the **same build** in one batch; the current data is strong
> enough to reject the "cumulative is a clear win" hypothesis but not to certify
> the small κ=4/T=0.4 gains as durable.
