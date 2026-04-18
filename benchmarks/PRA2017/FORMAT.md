# PRA2017 HFSDDW Instance Format

## Filename

`Instance_{n}_{c}_{m}_{beta1}_{beta2}_{cv}_Rep{rep}.txt`

| field    | meaning                                   | example |
|----------|-------------------------------------------|---------|
| n        | number of jobs                            | 50      |
| c        | number of stages                          | 5       |
| m        | machines per stage (uniform)              | 3       |
| T_factor | due-date tightness factor (comma=decimal) | 0,2     |
| R_factor | due-date range factor (comma=decimal)     | 0,2     |
| W_factor | due window width factor                   | 10      |
| rep      | replication index (0–4)                   | 0       |

## File Structure

```plaintext
HFSDDW
n  total_m  c
stage_0 p0  stage_1 p1  …  stage_{c-1} p_{c-1}   ← job 0
…                                                ← n rows total
LBCmax: <value>
RELDUE
r_j  d_j  w^{-}_j  w^{+}_j                       ← job 0
…                                                ← n rows total
DDW
d^{-}_j  d^{+}_j                                 ← job 0
…                                                ← n rows total
```

### Header line

`n  total_m  c`
`total_m = m × c`

### Processing times block (n rows)

Each row: alternating `stage_k  p_k` pairs (k = 0 … c−1).
Extract every odd-indexed element to get the n × c processing time matrix.

### LBCmax

Lower bound on makespan (Cmax).

### RELDUE (n rows)

| field     | meaning                                                 |
|-----------|---------------------------------------------------------|
| r_j       | release time; always −1 in this dataset (no constraint) |
| d_j       | nominal due date                                        |
| $w^{-}_j$ | earliness weight                                        |
| $w^{+}_j$ | tardiness weight                                        |

### DDW (n rows)

| field     | meaning                        |
|-----------|--------------------------------|
| $d^{-}_j$ | earliest acceptable completion |
| $d^{+}_j$ | latest acceptable completion   |

Note: $d_j = (d^{-}_j + d^{+}_j) / 2$ (symmetric window) in all instances.

## Generation parameters

The 1440 large instances follow a full factorial design:

| parameter | values |
|-----------|--------|
| n (jobs)  | {50, 100, 150, 200} |
| c (stages)| {5, 10} |
| m (machines/stage) | {3, 5} |
| T_factor  | {0.2, 0.4, 0.6} |
| R_factor  | {0.2, 0.6, 1.0} |
| W_factor  | {10, 20} |
| rep       | {0, 1, 2, 3, 4} |

4 × 2 × 2 × 3 × 3 × 2 × 5 = 1440 instances.

`InstanceParams` is available via `params.generation_params` on every loaded instance (returns `None` for non-standard filenames).

## Matching key

Processing times matrix (n × c integers), lines 3 … n+2.
LBCmax, RELDUE, DDW sections are **not needed** for instance matching.
