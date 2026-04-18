# PRA2017 HFSDDW Instance Format

## Filename

`Instance_{n}_{c}_{m}_{beta1}_{beta2}_{cv}_Rep{rep}.txt`

| field  | meaning                                   | example |
|--------|-------------------------------------------|---------|
| n      | number of jobs                            | 50      |
| c      | number of stages                          | 5       |
| m      | machines per stage (uniform)              | 3       |
| beta1  | due-date tightness factor (comma=decimal) | 0,2     |
| beta2  | due-date window width factor              | 0,2     |
| cv     | coefficient of variation × 100            | 10      |
| rep    | replication index (0–4)                   | 0       |

## File Structure

```plaintext
HFSDDW
n  total_m  c
stage_0 p0  stage_1 p1  …  stage_{c-1} p_{c-1}   ← job 0
…                                                   ← n rows total
LBCmax: <value>
RELDUE
r_j  d_j  aux1  aux2                               ← job 0
…                                                   ← n rows total
DDW
E_j  L_j                                           ← job 0
…                                                   ← n rows total
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

| field      | meaning                                                 |
|------------|---------------------------------------------------------|
| r_j        | release time; always −1 in this dataset (no constraint) |
| d_j        | nominal due date                                        |
| aux1, aux2 | auxiliary generation parameters (not used for scheduling) |

### DDW (n rows)

| field | meaning                         |
|-------|---------------------------------|
| E_j   | earliest acceptable completion  |
| L_j   | latest acceptable completion    |

Note: d_j = (E_j + L_j) / 2 (symmetric window) in all instances.

## Matching key

Processing times matrix (n × c integers), lines 3 … n+2.
LBCmax, RELDUE, DDW sections are **not needed** for instance matching.
