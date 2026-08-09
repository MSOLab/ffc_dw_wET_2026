# ffc_ddw_sum_et

Solver and experiment harness for the **Flexible(Hybrid) Flowshop Scheduling with Due Windows (FFcDDW)** problem.

## Problem

$n$ jobs are processed through $c$ stages in series. Each stage $i \in \mathcal{I}$ has $\lvert\mathcal{M}_i\rvert \ge 1$ identical parallel machines (at least one stage has more than one). Each job $j \in \mathcal{J}$ has a due window $[d^-_j,\ d^+_j]$: finishing inside is on time, finishing outside incurs a weighted earliness ($w^-_j$) or tardiness ($w^+_j$) penalty.

**Objective** — minimize Total Weighted Earliness and Tardiness from the due window:

$$\text{TWET}^{\text{dw}} = \sum_{j \in \mathcal{J}} \left( w^-_j \cdot E^-_j + w^+_j \cdot T_j \right)$$

where $E^-_j = \max\{d^-_j - C_j,\ 0\}$ and $T_j = \max\{C_j - d^+_j,\ 0\}$.

Full parameter/variable/constraint definition: [`docs/problem-description.md`](docs/problem-description.md).

Reference: Pan, Ruiz, Alfaro-Fernández (2017). *Computers and Operations Research* 80, 50–60.

## Docs

- [`docs/problem-description.md`](docs/problem-description.md) — problem definition
- [`docs/io-principles.md`](docs/io-principles.md) — IO extraction and import rules
- [`docs/algorithm-principles.md`](docs/algorithm-principles.md) — algorithm execution contract
- [`TODO.md`](TODO.md) — deferred design notes

## Algorithms (step method names for config)

| Step method | Description |
|---|---|
| `run_fam` | Fast dispatching (EDD, WEDD, ECT) |
| `calc_mcf_lb_and_derive_full_sch` | MCF-based lower bound and full schedule construction |
| `neh_cp` | NEH heuristic + CP-SAT refinement |
| `neh_cp_midpoint_seq` | NEH-CP with incumbent-derived midpoint job order |
| `neh_cp_first_stage_seq` | NEH-CP with incumbent-derived first-stage-start job order |
| `neh_cp_completion_seq` | NEH-CP with incumbent-derived completion job order |
| `run_flip_makespan_cp_from_incumbent` | Makespan-flipping CP-SAT for last stage |
| `incremental_sw_cp` | Sliding-window CP with coarsened grid |
| `coarsen_solve_reconstruct` | CSR pipeline (coarsen → CP solve → reconstruct) |
| `job_contrib_cp` | D&C: remove top-contributing jobs, let CP-SAT re-insert |
| `incremental_job_contrib_cp` | Ramp over jd values calling job_contrib_cp |
| `initialize_by_dispatch_v4` | EDD dispatch with v4 ordering + optional active reconstruction |

## Results

- output/20260509/20260510T033853_487093/mcf_lb_best_neh_cp_best_base_cpsat/
  - MCF-LB -> NEH-CP -> base CP
- output/20260509/20260510T222326_509362/mcf_lb_best_neh_cp_best_flip_makespan_cp_base_cpsat/
  - MCF-LB -> NEH-CP -> flip makespan -> base CP
