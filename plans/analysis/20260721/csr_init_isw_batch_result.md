# CSR κ=1 initializer + ISW-CP `m+2` batch — full-grid result

- **Date:** 2026-07-21
- **Experiment plan:** [`../../experiment/20260721/csr_init_isw_cp_batch_size.md`](../../experiment/20260721/csr_init_isw_cp_batch_size.md)
- **Verdict:** the proposal **loses** to the unmodified C5 baseline. Do not adopt.
  The `m+2` batch widening is a clean, uniform loss; the CSR κ=1 initializer swap
  is roughly neutral-to-mildly-helpful once isolated from it.

## Question

Does replacing the C5 initializer (mcf→flip→neh_cp) with `coarsen_solve_reconstruct(factor=1)`
at f=20 %/30 %, plus widening the ISW-CP window from `m` to `m+2`, beat C5
end-to-end on the full 1440-instance PRA2017 large grid at outer budget 0.09nc?

## Source run

- `output/20260721_csr_init_isw_batch/20260721T015603_278451/` (machine calop4,
  run setting commit `9fb8868`; 4 scenarios × 1440 instances = 5760 rows,
  0 errors, wall 10:15:15)
- Config: `metadata/20260721/csr_init_isw_batch.yaml`
- Four arms (see plan §6):
  - **A** — C5 as-is (initializer 0.036nc, ISW-CP batch `m`)
  - **C** — A with ISW-CP batch `m+2` (batch effect, isolated)
  - **B20** — CSR κ=1 f=20 % init (0.018nc) + ISW-CP batch `m+2`
  - **B30** — CSR κ=1 f=30 % init (0.027nc) + ISW-CP batch `m+2`

## Reproduction

```
uv run python scripts/20260721/analyze_csr_init_isw_batch.py \
    output/20260721_csr_init_isw_batch/20260721T015603_278451
```

Full console output is archived at `analysis/20260721_csr_init_isw_batch/results.txt`
(gitignored). RPDf is `RPDf_BKS_data × 100` (percentage points, symmetric, lower
is better); contrasts are signed `x−y`, so **positive = worse than the baseline**.

## Result

### Pooled mean RPDf (%p, all 1440)

| arm | mean RPDf | vs A |
| --- | --- | --- |
| **A** (C5) | **−11.22** | — |
| C  (A, batch m+2) | −8.69 | **+2.54** |
| B20 (CSR f=20 %, m+2) | −8.84 | **+2.39** |
| B30 (CSR f=30 %, m+2) | −9.85 | **+1.37** |

A is the best arm. Every modified arm is worse. Per-instance win rate against A
is 25.6 % (C), 27.8 % (B20), 33.8 % (B30) — A wins the majority of instances,
though medians (`C−A` +1.30, `B30−A` +0.36) are much smaller than the
tail-driven means.

### The loss decomposes cleanly: the batch widening is the culprit

| contrast | meaning | mean %p |
| --- | --- | --- |
| `C−A` | batch `m`→`m+2`, isolated | **+2.54** |
| `B20−C` | init swap at fixed `m+2`, f=20 % | −0.15 |
| `B30−C` | init swap at fixed `m+2`, f=30 % | **−1.16** |
| `B30−B20` | f=30 % vs f=20 % | −1.01 |

`B30−A = (B30−C) + (C−A) = −1.16 + 2.54 = +1.37` ✓. So:

- **`m+2` widening costs +2.54 %p** and is remarkably stable — +2.44…+2.58 across
  all four `n`, +2.17 on the (0.6,0.2) hard slice. This is the §7 gate's
  "`C > A` materially → widening hurts" branch, unambiguously.
- **The CSR κ=1 initializer swap, at fixed batch width, is neutral (f=20 %) to
  mildly helpful (f=30 %, −1.16 %p).** Richer init beats cheaper (`B30−B20`
  −1.01), consistent with §3's monotone-in-f ordering — just far smaller after
  the pipeline.
- Both proposal arms lose end-to-end **only because the `m+2` penalty they carry
  outweighs the initializer gain.**

### §3's 13 %p initializer margin is erased downstream

§3 measured CSR κ=1 f=20 % beating the C5 initializer by 13.15 %p **on the
initializer output**. After the full 0.09nc pipeline the sign flips: the
initializer swap alone (`B*−C`) is worth at most −1.16 %p. This is exactly the
§7 "the head start is erased downstream" outcome the plan pre-committed to not
re-litigating as a measurement failure — and here it over-corrects into a net
loss once bundled with `m+2`.

Mechanistically this is expected (plan §5 correction): `incremental_sw_cp`'s
`total_timelimit` is per inner `sw_cp` call, so ISW-CP runs to the wall clock in
every arm and the freed initializer budget is spent on more ISW-CP iterations,
whose job is precisely to erase starting-point differences.

### Stratified by T (required — ladder span differs 3.1× across slices)

| T | A | C | B20 | B30 |
| --- | --- | --- | --- | --- |
| 0.2 | −48.18 | −46.60 | −47.78 | −48.09 |
| 0.4 | 0.10 | 3.76 | 2.85 | 1.53 |
| 0.6 | 14.41 | 16.78 | 18.42 | 17.01 |

A wins at every T. The proposal is closest to A at T=0.2 (`B30−A` +0.08) and
loses most at T=0.6 (`B20−A` +4.01). On the (T,R)=(0.6,0.2) hard slice (160
instances) B30 is the least-bad modified arm (`B30−A` +0.68, win 42.5 %) but
still loses.

## Gate reading (plan §7) and recommendation

1. **No arm beats A by ≥2 %p → do not adopt the proposal as specified.**
2. **`C > A` materially (+2.54 %p, uniform) → the `m+2` widening hurts.** The gate
   says "rerun the winning initializer at `m` before adopting." That is the
   decisive missing measurement: this run never tests CSR init with batch `m`.
3. **`B30 < B20` materially-ish (−1.01) and `B30−C` −1.16 → the initializer
   direction is not dead.** The CSR κ=1 swap helps at fixed batch, and richer f
   helps more.

**Recommended follow-up (one run):** CSR κ=1 at f=20 % and f=30 % initializers
with ISW-CP batch held at **`m`** (drop `extra_batch_size_expr`), tail as
remainder — i.e. arms A, "B20@m", "B30@m". Under an independence approximation
(subtract the +2.54 batch penalty) this predicts B20@m ≈ A (−0.15) and
**B30@m ≈ A − 1.16 %p**, i.e. a small win — but the approximation must be
measured directly, since the batch penalty need not be additive across
initializers. If B30@m confirms a ≥1 %p win, sweep f upward (35 %, 40 %) per the
`B30 < B20` branch.

**Do not** pursue `m+2` (or wider) batches with this pipeline: the penalty is
large, uniform across size and tightness, and independent of the initializer.
