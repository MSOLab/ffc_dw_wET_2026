# Problem Description: FFcDDW

**Flexible(Hybrid) Flowshop Scheduling with Due Windows**
Notation: ((PM(i))_{i=1}^m) // TWET^dw

Source: Pan, Ruiz, Alfaro-Fernández (2017). *Computers and Operations Research* 80, 50–60.

---

## Parameters

| Symbol | Description |
| ------ | ----------- |
| $i \in \mathcal{I}$ | Stage index; $\mathcal{I} = \{1, \ldots, c\}$, $c \ge 2$ |
| $j \in \mathcal{J}$ | Job index; $\mathcal{J} = \{1, \ldots, n\}$ |
| $m \in \mathcal{M}_i$ | Machine index at stage $i \in \mathcal{I}$; $\mathcal{M}_i = \{1, \ldots, \lvert\mathcal{M}_i\rvert\}$, $\lvert\mathcal{M}_i\rvert \ge 1$, $\exists i: \lvert\mathcal{M}_i\rvert > 1$ |
| $p_{ij}$ | Processing time of job $j$ at stage $i$ (positive integer) $\forall i \in \mathcal{I}, j \in \mathcal{J}$ |
| $d^-_j$ | Lower bound of due window for job $j$ (earliest on-time completion) $\forall j \in \mathcal{J}$ |
| $d^+_j$ | Upper bound of due window for job $j$ (latest on-time completion) $\forall j \in \mathcal{J}$ |
| $w^-_j$ | Earliness weight of job $j$ $\forall j \in \mathcal{J}$ |
| $w^+_j$ | Tardiness weight of job $j$ $\forall j \in \mathcal{J}$ |

---

## Variables

| Symbol | Description |
| ------ | ----------- |
| $C_{ij}$ | Completion time of job $j \in \mathcal{J}$ at stage $i \in \mathcal{I}$ $\forall i \in \mathcal{I}, j \in \mathcal{J}$ |
| $C_j = C_{cj}$ | Completion time of job $j$ at the last stage $c$ (used in objective) $\forall j \in \mathcal{J}$ |
| $E^-_j$ | Earliness of job $j$; $E^-_j = \max\{d^-_j - C_j,\ 0\}$ $\forall j \in \mathcal{J}$ |
| $E^+_j$ | Tardiness of job $j$; $E^+_j = \max\{C_j - d^+_j,\ 0\}$ $\forall j \in \mathcal{J}$ |

---

## Constraints

1. **Stage order**: Each job $j \in \mathcal{J}$ visits stages $\mathcal{I}$ in sequence.
2. **Single machine per stage**: At each stage $i \in \mathcal{I}$, job $j$ is processed by exactly one machine $m \in \mathcal{M}_i$.
3. **No preemption**: Jobs are processed without interruption once started.
4. **Machine capacity**: Each machine $m \in \mathcal{M}_i$ processes at most one job at a time.

---

## Objective

Minimize the **Total Weighted Earliness and Tardiness from the due window**:

$$\text{TWET}^{\text{dw}} = \sum_{j \in \mathcal{J}} \left( w^-_j \cdot E^-_j + w^+_j \cdot E^+_j \right)$$

A job $j$ is **on time** if $d^-_j \le C_j \le d^+_j$, incurring zero penalty.
Different weights $w^-_j$ (earliness) and $w^+_j$ (tardiness) allow asymmetric penalties per job.
