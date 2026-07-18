# p25–p75 capture-percentile sweep on a shared base — merged analysis

**Date:** 2026-07-18 · **Artifacts:** `analysis/20260718_sw_cp_tl_p_u8_merge/`

---

## 0. Question

Under `unfixed_batch_count_max=8` — the regime the κ=0.005 decision was made in —
is **p60** still the best SW-CP TL capture percentile, and does the U-shape hold
across p25–p75?

The κ values (B2 basis, s/op) were derived offline from a 270-instance u2_pf2
window pool, and the earlier end-to-end validations did not sit on one footing:

| run | `unfixed_batch_count_max` | run mode | scenarios |
|---|---|---|---|
| `20260708_sw_cp_tl_test/20260708T215949_422005` | 12 | FULL_RUN | p25–p75 (7) |
| `20260709_sw_cp_tl_test/20260710T003128_565779` | 8 | RESUME | p50, p60, p70 + κ=0.002–0.008 |
| `20260710_sw_cp_tl_kappa_0.005/20260710T165804_500924` | 8 | RESUME | κ=0.005 |

The κ=0.005 decision came from the max=8 runs, but p25/p30/p40/p75 existed only
at max=12. The gap-fill run below supplies the four missing percentiles at
max=8 on the same base incumbent, putting all seven on equal footing.

## 1. Sources

Three max=8 runs, all `RESUME` from the **same base incumbent**
(`resume_dir: output/20260709T231643_016242/mcf_lb_fmm_neh_cp`,
`flow_resume_idx=3` → only `incremental_sw_cp` + `solve_base_model_cpsat` re-run),
with disjoint scenario names, 1440 instances each:

- `output/20260709_sw_cp_tl_test/20260710T003128_565779` — p50, p60, p70, κ=0.002/0.004/0.006/0.008
- `output/20260717_sw_cp_tl_p25_p75_u8/20260717T012611_015148` — p25, p30, p40, p75 · config `metadata/20260717/sw_cp_tl_p25_p75_u8.yaml`
- `output/20260710_sw_cp_tl_kappa_0.005/20260710T165804_500924` — κ=0.005

One max=12 run for the regime comparison, joined per (scenario, instance):

- `output/20260708_sw_cp_tl_test/20260708T215949_422005/20260708T215949_422005_summary.csv`

κ used by the gap-fill run (B2 basis, s/op, from `k_for_capture.py`):
p25 = 0.000311 · p30 = 0.000388 · p40 = 0.000773 · p75 = 0.031593.

## 2. Reproduction

```bash
uv run python scripts/20260718/analyze_p_sweep.py
```

Defaults point at the four sources above and write to
`analysis/20260718_sw_cp_tl_p_u8_merge/`. BKS join and the symmetric RPDf are
imported from `scripts/20260706/analyze_kappa_sweep.py` (which imports them from
`build_results_index.py`), so this analysis cannot drift from the weekly-review
pipeline. Every instance is scored; none dropped.

## 3. Results

### 3.1 Seven percentiles + the κ family, max=8, shared base

mean RPDf (%), lower is better. `all` = 1440, `T=0.6` = 480, `(T,R)=(0.6,0.2)` = 160.
`mean objVal` is the mean `bestObj` over the same 1440 instances.

| scenario | all | mean objVal (all) | T=0.6 | (T,R)=(0.6,0.2) |
|---|---:|---:|---:|---:|
| p25 | −6.67 | 90 895 | 18.39 | 22.42 |
| p30 | −7.42 | 90 361 | 17.87 | 21.99 |
| p40 | −9.01 | 89 101 | 16.65 | 20.96 |
| p50 | −10.18 | 88 052 | 15.62 | 20.11 |
| **p60** | **−11.50** | **86 727** | **14.07** | **18.86** |
| p70 | −9.16 | 89 635 | 16.72 | 20.69 |
| p75 | −4.14 | 94 667 | 21.24 | 24.64 |
| κ=0.002 | −10.46 | 87 763 | 15.42 | 20.01 |
| κ=0.004 | −11.45 | 86 876 | 14.27 | 19.11 |
| κ=0.005 | **−11.62** | 86 845 | 14.07 | 19.00 |
| κ=0.006 | −11.48 | 87 020 | 14.37 | 19.05 |
| κ=0.008 | −11.19 | 87 288 | 14.41 | 19.12 |

> `mean objVal` is reported for magnitude, not for ranking: the objective scales
> with instance size (n ≤ 200), so its mean is dominated by the large instances
> while RPDf weights every instance equally. The two do disagree — p70 beats p40
> on RPDf (−9.16 vs −9.01) but loses on mean objVal (89 635 vs 89 101).

**The U-shape holds cleanly in all three slices**, with the minimum at p60:
performance improves monotonically p25 → p60, then degrades sharply at p70 and
collapses at p75. p75 is the worst of the seven everywhere — worse than p25.

**p60 is the best percentile in every slice.** Against the κ family, the earlier
per-slice warning is reproduced on the full seven-percentile footing: κ=0.005
edges p60 on the overall mean (−11.62 vs −11.50), but **p60 wins both T=0.6
slices** (14.071 vs 14.072 — a tie for practical purposes; 18.86 vs 19.00).
Reading the overall RPDf mean alone would pick κ=0.005; reading per slice does
not — and on mean objVal p60 is the outright minimum of all twelve scenarios
(86 727 vs κ=0.005's 86 845).

### 3.2 max=8 vs max=12

Per-instance paired comparison (positive Δ = max=8 worse):

| slice | p | mean u8 | mean u12 | Δ | u8 W / T / L |
|---|---:|---:|---:|---:|---:|
| all | 25 | −6.67 | −7.42 | +0.75 | 581 / 139 / 720 |
| all | 30 | −7.42 | −7.79 | +0.36 | 589 / 144 / 707 |
| all | 40 | −9.01 | −8.92 | −0.09 | 635 / 147 / 658 |
| all | 50 | −10.18 | −10.42 | +0.24 | 644 / 146 / 650 |
| all | 60 | −11.50 | −11.81 | +0.31 | 644 / 149 / 647 |
| all | 70 | −9.16 | −9.42 | +0.26 | 634 / 149 / 657 |
| all | 75 | −4.14 | −4.34 | +0.20 | 646 / 145 / 649 |
| T=0.6 | 60 | 14.07 | 13.63 | +0.44 | 236 / 0 / 244 |
| (0.6,0.2) | 60 | 18.86 | 18.53 | +0.33 | 77 / 0 / 83 |

Full table in `p_u8_vs_u12.csv`; plot in `p_sweep_u8_vs_u12.png`.

**The shape and the winner are stable across regimes**: max=12 traces the same
U with the same p60 minimum in all three slices. max=12 is very slightly better
almost everywhere (Δ ≤ 0.77 pp, and the paired win/tie/loss is near 50/50 at
every p except p25/p30), so more windows help marginally but do not move the
decision.

> **Caveat — this comparison is confounded.** The max=12 run is `FULL_RUN`; it
> computed its own base incumbent rather than resuming from
> `20260709T231643_016242`. The two regimes therefore differ in *both* window
> count and starting incumbent, so the small per-p deltas above cannot be
> attributed to window count alone. The qualitative claim the comparison is used
> for — the U-shape and the p60 minimum survive a change of regime — does not
> depend on separating the two.

## 4. Conclusion

Under `unfixed_batch_count_max=8`, **p60 remains the best capture percentile**
and the U-shape holds across p25–p75 with a sharp penalty above p60. The
κ=0.005 vs p60 call is a genuine tie that the overall mean decides one way and
both T=0.6 slices decide the other — consistent with the existing per-slice
guidance in `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` §3.4,
now confirmed on all seven percentiles rather than three.

## 5. Artifacts

`analysis/20260718_sw_cp_tl_p_u8_merge/` (gitignored):

- `p_sweep_by_scenario.csv` — one row per (slice, scenario), u8 p + κ
- `p_u8_vs_u12.csv` — one row per (slice, p), both regimes + paired W/T/L
- `p_sweep_u8_vs_u12.png` — mean RPDf vs p, one panel per slice, both regimes
