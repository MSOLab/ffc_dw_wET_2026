# p25–p75 capture-percentile sweep — `unfixed_batch_count_max=8` gap-fill

**Date:** 2026-07-18 · **Config:** `metadata/20260717/sw_cp_tl_p25_p75_u8.yaml`
**Prerequisite:** `metadata/20260709/sw_cp_tl_test_cases.yaml` (kappa sweep RESUME run, `output/20260709_sw_cp_tl_test/20260710T003128_565779`)

---

## 0. Motivation

The SW-CP TL κ values (B2 k, s/op) for p25–p75 were derived offline from a
270-instance u2_pf2 window pool. Two end-to-end validations exist on the full
1440-instance grid:

| run | config | `unfixed_batch_count_max` | scenarios |
|---|---|---|---|
| `20260708_sw_cp_tl_test/20260708T215949_422005` | `sw_cp_tl_test.yaml` | **12** | p25–p75 (7) |
| `20260709_sw_cp_tl_test/20260710T003128_565779` | `sw_cp_tl_test_cases.yaml` | **8** | p50, p60, p70 + κ=0.002–0.008 |

The kappa sweep (max=8) was the one used for the final κ=0.005 decision. But the
p25/p30/p40/p75 scenarios from the standalone run (max=12) use a different SW-CP
window count and are not directly comparable to the kappa sweep's p50/p60/p70
numbers.

**Goal:** run the missing p25, p30, p40, p75 with `unfixed_batch_count_max=8`,
RESUME from the **same base incumbent** as the kappa sweep, so all 7
capture-percentile scenarios sit on equal footing.

## 1. Experiment design

### Config

- `run_mode: RESUME`
- `resume_dir: output/20260709T231643_016242/mcf_lb_fmm_neh_cp` (same base as kappa sweep)
- `unfixed_batch_count_max: 8` (same as kappa sweep, not 12)
- 4 scenarios: p25, p30, p40, p75
- κ values as derived from `k_for_capture.py` B2:

| scenario | κ (B2, s/op) |
|---|---|
| p25 | 0.000311 |
| p30 | 0.000388 |
| p40 | 0.000773 |
| p75 | 0.031593 |

### Full subroutine flow (identical to kappa sweep)

```
mcf_lb_and_derive_full_sch → flip_makespan_cp_from_incumbent → neh_cp
→ incremental_sw_cp (proportional, κ·ntf) → solve_base_model_cpsat
```

`flow_resume_idx=3`: only `incremental_sw_cp` + `solve_base_model_cpsat` re-run.

## 2. Analysis plan

### 2.1 Merge with existing kappa sweep scenarios

The existing kappa sweep run provides p50, p60, p70. Merge the 4 new scenarios
with those 3 to produce a **unified 7-scenario capture-percentile table** on a
shared base incumbent and identical `unfixed_batch_count_max=8`.

Two run directories to feed into `analyze_kappa_sweep.py` (or a variant):

- `output/20260709_sw_cp_tl_test/20260710T003128_565779` (p50, p60, p70, κ=0.002–0.008)
- `output/20260717_sw_cp_tl_p25_p75_u8/<ts>/` (p25, p30, p40, p75)

### 2.2 Expected outputs

1. **p-only 7-scenario table** (p25–p75, max=8, shared base):
   mean RPDf by capture percentile, per `all` / `T=0.6` / `(T,R)=(0.6,0.2)` slices.
   Confirm the U-shape and identify the best p% under max=8.

2. **Combined p + κ table**: place the 7 percentile scenarios alongside the
   κ=0.002–0.008 family on the same axes. κ=0.005 was selected from the κ sweep
   alone; this gives the full portrait where p% and κ intersect.

3. **Compare max=8 vs max=12**: the standalone 7-scenario run
   (`20260708T215949_422005`, max=12) already covers all 7. A side-by-side table
   shows whether the U-shape and best p% are stable across window-count regimes.

### 2.3 Key question to answer

> Under `unfixed_batch_count_max=8` (the kappa sweep's regime), is p60 still the
> best capture percentile, and does the U-shape hold across p25–p75?

## 3. Execution

```bash
# validate resume config (dry-run)
uv run python scripts/validate_resume_config.py metadata/20260717/sw_cp_tl_p25_p75_u8.yaml

# launch experiment
uv run python main.py
# or with nohup for background:
nohup uv run python main.py > output/20260717_sw_cp_tl_p25_p75_u8/nohup.out 2>&1 &
```

## 4. Completion criteria

- [ ] 4 scenarios × 1440 instances complete (14400 ≈ each)
- [ ] Post-run pivot + report generated
- [ ] Merged analysis: 7 p-scenarios on equal footing
- [ ] Plan updated with results
