# CSR reconstruct_mode AB — `active` vs `semi_active` (+ prior-run baseline)

## Question

For the coarsen–solve–reconstruct (CSR) subroutine on the PRA2017 large grid,
does projecting the coarse solution back to the original scale with
`reconstruct_mode="active"` beat the default `semi_active`, across
κ ∈ {1,2,4,8} × inner-TL f ∈ {5,10,15}% (all `coarsen_mode=cumulative`)?

Secondary: do the new `_semi` scenarios reproduce the prior run recorded before
the `active` option existed (a cross-run / machine-offset + CP-noise sanity
check)?

## Verdict

**`active` is decisively worse than `semi_active` on the aggregate weighted
E/T objective — keep `semi_active` (the default).** Overall mean RPDf penalty
**+29.19 pp** (17,280 paired instances; active loses on 10,550, wins 6,549).
The `_semi` path reproduces the prior run within noise (**−0.12 pp**, mean Δobj
−310, within the ±350 CP noise floor). Net effect of `active` vs the historical
baseline: **+29.07 pp worse**.

## Source runs (full paths)

- **Current run** (24 scenarios, `csr_k{K}_tl{f}_{semi,active}`, full 1440 grid):
  `output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252`
  (config committed a5a92e7; ran 00:57→07:33, calop4)
- **Prior baseline** (12 scenarios, `csr_k{K}_tl{f}`, default `semi_active`,
  full 1440 grid):
  `output/20260721_csr_k_f_cumulative/20260721T215135_772079`
- **Merged synthetic run** (36 scenarios, symlinks; POST_PROCESS_ONLY target):
  `output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861`

## Reproduction

```bash
# 1. merge the two runs into one synthetic POST_PROCESS_ONLY run dir + config
uv run python scripts/20260724/build_recon_ab_merge.py \
  --cur-run  output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252 \
  --prior-run output/20260721_csr_k_f_cumulative/20260721T215135_772079 \
  --dest     output/20260724_merge_recon_ab_vs_prior \
  --config-out metadata/20260724/merge_recon_ab_vs_prior.yaml
# (reuse an existing merged dir with --merged-dir <dir> to skip re-symlinking)

# 2. regenerate the 36-scenario comparison reports (no solving)
uv run python main.py --config metadata/20260724/merge_recon_ab_vs_prior.yaml

# 3. three-mode analysis (prior / semi / active) per κ×f cell
uv run python scripts/20260724/analyze_recon_ab.py \
  output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861
```

Per-cell CSVs land in `analysis/20260724_recon_ab/` (gitignored). Run-level
dashboards (`*_rpdf_dashboard.html`, `*_win_tie_dashboard.html`,
`*_multi_scenario_*`) land in the merged run dir.

All RPDf figures are percentage points (`RPDf_BKS_data × 100`); lower is better.
RPDf is the symmetric variant `2·(obj−ref)/(obj+ref)` against `BKS_data`.

## Results

### Mean RPDf by mode (pp) — active is worse in every cell

| κ | f% | prior | semi | active | active−semi |
|---|----|-------|------|--------|-------------|
| 1 | 5  | 28.26 | 27.86 | 61.12 | **+33.26** |
| 1 | 10 |  6.59 |  6.45 | 53.78 | **+47.33** |
| 1 | 15 |  0.15 |  0.24 | 50.38 | **+50.14** |
| 2 | 5  | 56.27 | 55.69 | 78.16 | +22.46 |
| 2 | 10 | 34.79 | 34.43 | 67.59 | +33.16 |
| 2 | 15 | 25.49 | 25.26 | 61.06 | +35.80 |
| 4 | 5  | 63.92 | 64.12 | 83.30 | +19.18 |
| 4 | 10 | 40.56 | 40.86 | 69.92 | +29.06 |
| 4 | 15 | 30.92 | 31.02 | 62.91 | +31.89 |
| 8 | 5  | 72.54 | 71.81 | 83.16 | +11.35 |
| 8 | 10 | 49.99 | 50.17 | 67.65 | +17.48 |
| 8 | 15 | 42.40 | 42.54 | 61.66 | +19.11 |

**Overall mean RPDf: semi 37.54 · active 66.72 · prior 37.66.**

### Primary AB — active vs semi (paired, dRPDf = active − semi)

Overall: mean **+29.19 pp**, mean Δobj **+4476**, win/tie/loss **6549 / 181 /
10550** (n=17280).

Trends (mean dRPDf):
- **by κ**: +43.58 (κ1) → +30.47 (κ2) → +26.71 (κ4) → +15.98 (κ8) — the
  penalty *shrinks* as κ grows.
- **by f**: +21.56 (5%) → +31.76 (10%) → +34.24 (15%) — the penalty *grows*
  with more inner solve time.

**Distribution nuance (important):** the mean is inflated by a heavy right tail,
not a uniform shift. Medians are small (0 to +12 pp) and at κ=8 go slightly
negative (−2.47 pp at f=5%, where active in fact wins 932/1440 by count). So on
a *majority* of instances at high κ, active is a hair better — but a minority of
instances are catastrophically worse under active (mean Δobj +10k–14.6k at κ=1),
dragging the aggregate mean far positive. Judged by the reported metric (mean
RPDf), semi wins everywhere.

### Reproducibility — semi vs prior (paired, dRPDf = semi − prior)

Overall: mean **−0.12 pp**, mean Δobj **−310** (inside the ±350 CP noise floor),
median dRPDf = 0.000 in every cell, win/tie/loss 7754 / 2185 / 7341 (balanced).
→ The `_semi` scenarios **faithfully reproduce** the prior run; adding the
`active` option did not perturb the `semi_active` path, and the cross-run /
machine offset is negligible.

## Mechanism (why active loses) — it manufactures EARLINESS

Machine assignment is **not** what the inner solver decides (assignment is a
downstream post-step), so the loss is not "active discards the solver's machine
assignment". The real cause is that active reconstruction **left-packs**, and a
left-packed schedule is antithetical to an E/T objective.

Verified by splitting the objective into weighted earliness vs tardiness on the
κ1_tl15 cell (`scripts/20260724/et_split_check.py`, 1440 instances):

| mode | sum_E | sum_T | obj | E-share |
|------|------:|------:|------:|--------:|
| semi | 6,634,210 | 134,477,122 | 141,111,332 | 4.7% |
| active | **26,308,475** | 135,784,968 | 162,093,443 | **16.2%** |

Tardiness is nearly identical (+1.0%); **earliness ~quadruples (+297%)** and
accounts for ~94% of the obj gap. active over-produces earliness.

Why:
- `active` (`reconstruct_active_coarse_schedule` → `build_active_from_reference`,
  `solution/schedule_build.py`) keeps only the coarse per-stage operation
  **start-order** and re-dispatches every op onto the **earliest-available
  machine** — a makespan / left-packing rule. Every op starts as early as
  feasible, so jobs **complete well before their due windows** → large earliness.
- `semi` (`reconstruct_raw_coarse_schedule`) carries the coarse solution's
  per-machine **order and assignment verbatim** and only re-derives times,
  preserving the coarse solve's already-E/T-aware spacing.
- Both then run `insert_idle_time`, but it only **right-shifts blocks on the
  last stage** (no re-sequencing, no re-assignment; `ffc_schedule.py:1648`) and
  each shift is bounded by the next op's start. active's earliest-machine
  dispatch yields dense, back-to-back last-stage sequences with no slack, so the
  block right-shifts cannot move individual jobs to their windows — the
  left-packed earliness survives.

This matches both trends: the penalty grows with inner TL f (a better coarse
E/T structure for semi to preserve, hence more lost by re-packing) and shrinks
with κ (a cruder coarse structure is worth less to preserve).

## Conclusion / action

- Keep `reconstruct_mode="semi_active"` as the CSR default.
- The `active` option, as implemented (earliest-available-machine left-packing),
  is not competitive on aggregate weighted E/T and should not be promoted. The
  left-packing is the component to change: it manufactures earliness that the
  last-stage-only `insert_idle_time` cannot remove. A reconstruction that places
  ops toward their due windows (or a global, not last-stage-only, idle
  insertion) would be needed to make an assignment-free projection competitive.
