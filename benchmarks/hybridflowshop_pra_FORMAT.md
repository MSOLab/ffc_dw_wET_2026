# hybridflowshop/resources/pra Instance Format

Source repo: `~/code/hybridflowshop`
Path: `resources/pra/{1..1440}.txt`
`0.txt` is a small toy example, not a benchmark instance.

## Filename

Sequential integer: 1 – 1440.

### Ordering

| files      | n   | c       |
|------------|-----|---------|
| 1–360      | 50  | 5 or 10 |
| 361–720    | 100 | 5 or 10 |
| 721–1080   | 150 | 5 or 10 |
| 1081–1440  | 200 | 5 or 10 |

Within each n-group: c=5 subgroup first, c=10 second.
Within each (n, c) subgroup: ordered by (beta1, beta2, cv, rep) numerically.

## File Structure

```plaintext
n
s
m_0  m_1  …  m_{c-1}     ← machines per stage (all equal in this dataset)
p_0  p_1  …  p_{c-1}     ← job 0 processing times (one per stage)
…                         ← n rows total
```

## Matching key

Processing times matrix (n × c integers), lines 4 … n+3.
