---
name: pra2017-instance-params
description: Authoritative reference for PRA2017 instance-generation parameters (T, R, n, c, machine count, W), the 1440-instance generation grid, the insIndex ↔ filename encoding, the BKS variants, and the symmetric RPDf definition. Use whenever grouping, filtering, or slicing run results by instance parameters, joining results to BKS, or interpreting an RPDf number.
---

# PRA2017 instance parameters (generation grid & mapping source)

When grouping/filtering results by instance-generation parameters (T, R, n, c,
machine count, W), the **authoritative per-instance source is
`benchmarks/PRA2017/pra2017_bks_table.csv`** — one row per `insIndex`
(`0000`–`1439`, zero-padded 4-digit string). Columns:

`insIndex, n, c, totalMcCount, T, R, W, BKS_data, BKS_calc, BKS_T, BKS_F`.

Parameter meanings and the **full generation grid** (1440 = all combinations ×
5 replicates; each `(n, c, totalMcCount)` cell has exactly 90 instances):

| Param | Meaning | Values |
|-------|---------|--------|
| `n` | job count | 50, 100, 150, 200 |
| `c` | stage count | 5, 10 |
| `totalMcCount` | total machine count = `c × machines-per-stage` | 15, 25, 30, 50 |
| `T` | tardiness factor (due-date tightness) | 0.2, 0.4, 0.6 |
| `R` | due-date range factor | 0.2, 0.6, 1.0 |
| `W` | weight range | 10, 20 |
| (Rep) | replicate id, not a column — see filename | 0–4 |

- **machines-per-stage is uniform** and ∈ {3, 5}: `totalMcCount = c × mps`
  (so `c=5`→{15,25}, `c=10`→{30,50}). It is **not** a `bks_table` column; read
  it from `totalMcCount / c` or the filename.
- **Filename encoding** (via `pra2017_hybrid_match.csv`, `insIndex →
  ffc_ddw_sum_et_filename`): `Instance_{n}_{c}_{mps}_{T}_{R}_{W}_Rep{k}.txt`,
  e.g. `Instance_50_5_3_0,2_0,2_10_Rep0.txt`. **Decimals use a comma**
  (`0,2` = 0.2). Verified: all 1440 filenames' decoded fields match
  `bks_table` exactly.
- **BKS variants** (all "best known solution" objective values):
  - `BKS_T`: objective with `force_job_id_seq=True` (preserves best_seq order)
  - `BKS_F`: objective with `force_job_id_seq=False` (FAM reordering)
  - `BKS_calc`: `min(BKS_T, BKS_F)`
  - `BKS_data`: the paper/data reference BKS — **this is the RPDf denominator**
    used by report tooling (`RPDf_BKS_data`).
  - See `benchmarks/PRA2017/README.md` for how the table is generated.
- **How reports attach these**: `orchestration/post_run_pivot.py` merges each
  run's `instanceName` → `insIndex` (via `pra2017_hybrid_match.csv`) →
  `bks_table` metadata, emitting `<run>_rpdf_comparison.csv` with columns
  `insIndex, scenarioName, n, c, totalMcCount, T, R, W, BKS_data, bestObj,
  RPDf_BKS_data, elapsedTime, timelimit, time%`. **This CSV is the ready-made
  source for any (T, R, size)-grouped RPDf comparison** — no need to re-join.
- **RPDf is symmetric** (`ffc_ddw_sum_et._calc.rpd_f`):
  `2·(obj − ref)/(obj + ref)`, **not** the classic `(obj − ref)/ref`. Range
  (−2, 2); `obj == ref == 0 → 0`.
