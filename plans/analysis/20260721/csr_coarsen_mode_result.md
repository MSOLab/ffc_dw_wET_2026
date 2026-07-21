# CSR coarsen_mode sweep (T=0.6 / R=0.2 slice) — result

- **Date:** 2026-07-21
- **Verdict:** on this slice, coarsen-solve-reconstruct is a **uniform loss** at
  every factor and every mode; the no-coarsen baseline (`csr_k1`) wins. Among
  coarsen modes the ranking is `ceil > round ≈ cumulative > floor`, and the
  newly added **`cumulative` mode brings no benefit** — it never beats `ceil`
  and degrades sharply at large factors. Do not adopt coarsening on this slice;
  `cumulative` is not worth keeping on its current evidence.

## Question

At a fixed coarsen budget, does `coarsen_solve_reconstruct` help, and which
`coarsen_mode` (how the rational coarse processing times are snapped back to
integers) is best? The sweep is the 17-scenario grid
`csr_k1` (factor=1, no coarsening) + factor {2,4,8,16} × mode {ceil, round,
floor, cumulative}, each running the full C5-style `solve_flow`
(mcf_lb → flip_makespan_cp → neh_cp → incremental_sw_cp → base_model_cpsat).

## Source run

- `output/20260721_csr_coarsen_mode/20260721T194407_731892/` (machine calop4,
  run setting commit `177a5a0`; 17 scenarios × 160 instances = 2720 rows,
  0 errors, wall 1:20:37)
- Config: `metadata/20260721/csr_coarsen_mode_T06_2.yaml`
- Instances: PRA2017 large, the **T=0.6 / R=0.2 slice only** — 160 instances
  (`insIndex` 60–69, 150–159, … 1410–1419), all sizes n∈{50,100,150,200},
  c∈{5,10}, mps∈{3,5}, W∈{10,20}, 5 reps.

## Reproduction

```
uv run python scripts/20260721/analyze_csr_coarsen_mode.py \
    output/20260721_csr_coarsen_mode/20260721T194407_731892
```

Full console output is archived at
`analysis/20260721_csr_coarsen_mode/results.txt` (gitignored). RPDf is
`RPDf_BKS_data × 100` (percentage points, symmetric, lower is better; positive =
worse than `BKS_data`). Per-instance deltas are signed `scenario − baseline`, so
**positive = worse than `csr_k1`**.

## Fixed-compute caveat (why the levels look high)

Each scenario has a 0.09nc limit, but the CSR budget is `0.0225nc` and it binds
first: **`time%` ≈ 0.25 and mean wall time ≈ 21.17 s are identical across all 17
scenarios**. So this is a *fixed-compute* comparison at ¼ of the outer budget —
it isolates the coarsening effect, and the absolute RPDf level (~23–53 %p) is
high only because the pipeline stops early. The ranking, not the level, is the
result. Coarsening also buys **no** wall-time saving here.

## Result

### Per-scenario mean RPDf (%p, all 160)

| scenario | mean RPDf | median | vs csr_k1 |
| --- | --- | --- | --- |
| **csr_k1 (baseline)** | **23.09** | 24.01 | — |
| csr_k2_ceil | 34.16 | 36.69 | +11.07 |
| csr_k8_ceil | 34.47 | 30.55 | +11.38 |
| csr_k2_cumulative | 34.59 | 34.68 | +11.50 |
| csr_k2_floor | 34.78 | 34.77 | +11.69 |
| csr_k2_round | 34.81 | 36.13 | +11.72 |
| csr_k4_ceil | 35.60 | 33.28 | +12.52 |
| csr_k16_ceil | 36.27 | 33.04 | +13.19 |
| csr_k4_cumulative | 36.51 | 34.72 | +13.42 |
| csr_k4_round | 36.80 | 34.53 | +13.71 |
| csr_k8_round | 36.85 | 35.66 | +13.76 |
| csr_k4_floor | 37.86 | 38.42 | +14.77 |
| csr_k8_cumulative | 37.95 | 37.00 | +14.86 |
| csr_k16_round | 40.47 | 37.90 | +17.38 |
| csr_k8_floor | 42.50 | 40.63 | +19.41 |
| csr_k16_cumulative | 44.46 | 43.28 | +21.37 |
| csr_k16_floor | 53.18 | 51.20 | +30.10 |

**Baseline is the single best scenario.** Every coarsen scenario is worse.

### factor × mode pivot (mean RPDf %p) — baseline = 23.09

| factor | ceil | round | floor | cumulative |
| --- | --- | --- | --- | --- |
| 2 | **34.16** | 34.81 | 34.78 | 34.59 |
| 4 | **35.60** | 36.80 | 37.86 | 36.51 |
| 8 | **34.47** | 36.85 | 42.50 | 37.95 |
| 16 | **36.27** | 40.47 | 53.18 | 44.46 |

- Mode ranking is stable across factors: **`ceil` best, `floor` worst**,
  `round`/`cumulative` in between.
- `floor` collapses as factor grows (k16 = 53.18) — flooring the coarse times
  systematically underestimates load, so the reconstruction starts from an
  infeasibly compressed skeleton.
- **`cumulative` (new) never beats `ceil`** at any factor and blows up at k16
  (44.46), so its "cumulative rounding fixes the drift" premise does not pay off
  on this slice.

### Coarsening penalty grows with instance size (mean RPDf %p, ceil arms)

| n | csr_k1 | k2_ceil | k4_ceil | k8_ceil | k16_ceil |
| --- | --- | --- | --- | --- | --- |
| 50 | 11.98 | 13.16 | 13.16 | 13.80 | 18.16 |
| 100 | 25.77 | 29.58 | 27.57 | 27.39 | 29.01 |
| 150 | 26.61 | 41.23 | 42.66 | 37.48 | 37.53 |
| 200 | **27.99** | **52.66** | 59.02 | 59.20 | 60.39 |

The gap widens monotonically with n — the **opposite** of the "coarsening helps
big instances at fixed budget" intuition. The weighted-E/T objective is
sensitive to exact due-window timing, and the larger the instance the more that
temporal resolution matters, so the reconstruction cannot recover it.

### Per-instance win rate & oracle

- Even the best coarsen scenario (`csr_k2_ceil`) beats baseline on only
  **35/160** instances; `csr_k16_floor` beats it on **0/160**.
- **Oracle** (pick the best of all 16 coarsen scenarios per instance): beats
  baseline on 70/160 instances but still averages **+4.35 %p worse**. Even
  post-hoc best-case selection over the whole coarsen grid loses to plain
  `csr_k1`.

## Conclusion

On the T=0.6 / R=0.2 slice, `coarsen_solve_reconstruct` is a net loss at equal
compute, worsening with instance size; `ceil` is the least-bad mode and the new
`cumulative` mode adds nothing. **Scope caveat:** this is a single (T, R) slice —
tighter/looser due-date factors (T∈{0.2,0.4}) or other R may shift the picture,
so the "coarsening loses" verdict should not be generalized to the full grid
without a broader run.
