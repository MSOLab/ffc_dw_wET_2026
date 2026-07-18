# CSR: NBM look-ahead for coarse-grid `insert_idle_time` (pre-plan / explainer)

- Status: **PRE-PLAN — explainer for review only.** No implementation plan, no
code change yet. This document exists so the *reasoning* (esp. the math) is
agreed before a TDD plan is written.
- Date: 2026-06-30
- Builds on: `csr-floor-shift-overshoot-safe.md` (the currently-shipped floor rule;
commit `0eb8d27 fix(csr): floor shift to prevent overshoot`).
- Touches (eventually): `insert_idle_time` in `solution/ffc_schedule.py:1566`,
coarse path only (`time_factor = K > 1`).

---

## 0. One-paragraph summary

On the coarse grid the right-only NBM greedy decides "keep moving" from a **slope
sign** ($W_E > W_T$); on the fine grid that slope is exact, but on the coarse grid
it **undercounts the last partial-cell step**, so the shipped **floor** rule stops
one step short of the true objective minimum (while staying overshoot-safe).

- **The reconciliation (§2–§3).** Read the partition *marginally* — by what the
  **next** coarse cell does ($K(C^K_j + s + 1)$ vs the window). Then an early job
  leaves $S_E$ exactly at the floor cell $\lfloor d^-_j/K \rfloor$, so **floor-only
  is internally consistent and terminating — no $\Delta_1 = 0$ guard, no
  look-ahead** (the user's point). *But* that stop cell is usually still **truly**
  early ($K\lfloor d^-_j/K\rfloor \le d^-_j$), and the marginal slope cannot see
  the remaining partial-cell improvement. So floor is clean *and* leaves a residual
  $\le K-1$ per early job; the two facts do not contradict.
- **Two options (§4), presented for decision:**
  - **Option F — floor (marginal refactor):** trust the marginal slope; accept the
    single-job residual. Simplest. **Not** a pure cosmetic refactor — it is
    $K=1$-identical but at $K>1$ it *fixes a latent merged-block stall* in the
    shipped guard (see §3). Residual repaired downstream by the final $K=1$ pass.
  - **Option L — look-ahead:** stop on the **actual objective**. When floor can no
    longer advance, evaluate the straddling step $\min\{\Delta_1 + 1, \Delta_2\}$
    and take it iff it lowers $F$. Recovers the residual (examples a, c);
    generalizes the floor doc's "option C". **NBM unchanged** — same net-benefit
    idea, discretized on the true objective rather than the marginal slope.

---

## 1. Setup and notation

Last-stage idle insertion shifts a contiguous **block** of operations
(no idle between them on one machine) rightward as a unit.

- $K$ `(= time_factor)`: coarsening factor; $K = 1$ is the fine/original grid
- $d^-_j, d^+_j$: original due window (lower, upper) of job $j$ (preserved at fine
  scale, see `coarsen_processing_times`, `ffc_ddw_params.py:289`)
- $C^K_j$: coarse completion time of operation $j$ on the last stage (at shift
  $s$ it is $C^K_j + s$; its fine completion is $K\,(C^K_j + s)$)
- $w^-_j, w^+_j$: earliness / tardiness weights
- $s\in \mathbb{Z}_{\geq 0}$: the block's rightward shift, in **coarse** units.

Per the earliness/tardiness definitions in `objectives.py:50`, after shift $s$:

$$E_j(s) = w^-_j \cdot \max\!\bigl(0,\; d^-_j - K(C^K_j + s)\bigr), \qquad
T_j(s) = w^+_j \cdot \max\!\bigl(0,\; K(C^K_j + s) - d^+_j\bigr).$$

The block-shift objective is

$$F(s) \;=\; \sum_{j \in \text{block}} \Bigl[\, w^-_j \max\!\bigl(0,\, d^-_j - K(C^K_j + s)\bigr)
\;+\; w^+_j \max\!\bigl(0,\, K(C^K_j + s) - d^+_j\bigr) \Bigr].$$

We minimize $F$ over integer $s \in [0, \Delta_2]$, where $\Delta_2$ is the coarse
distance to the next block (collision / merge bound; $\Delta_2 = \infty$ if the
block is last). $\Delta_1$ denotes the coarse distance to the next **window**
event (the floor-rounded breakpoint the shipped rule lands on).

---

## 2. Structure of F: convex, piecewise-linear

Each $E_j$/$T_j$ is a clipped linear function of $s$; $F$ is a finite sum of them, so

> **$F(s)$ is convex and piecewise-linear in the real variable $s$.**

Partition the block by what the **next** coarse step would do (the marginal /
right-derivative partition — this is the `+1` reading; current cell is
$C^K_j + s$, next cell is $C^K_j + s + 1$):

$$S_E = \{\, j : K\,(C^K_j + s + 1) \le d^-_j \,\} \quad\text{(next cell still } \le d^-_j \text{ — a right step still reduces earliness)},$$
$$S_T = \{\, j : K\,(C^K_j + s + 1) > d^+_j \,\} \quad\text{(next cell would exceed } d^+_j \text{ — a right step creates/adds tardiness)},$$
$$S_D = \text{otherwise: the next cell lands in } (d^-_j,\, d^+_j] \text{ — a right step is slope-0}.$$

> **The marginal vs. true-state gap (the hinge of this whole doc).** $S_E$/$S_T$
> above are defined by the **next** cell ($C^K_j + s + 1$), but the *actual*
> earliness/tardiness in $F$ is set by the **current** cell ($C^K_j + s$). On the
> fine grid the two coincide (a cell and "its breakpoint" are one step apart). On
> the coarse grid they diverge: a job can have $K(C^K_j+s) < d^-_j$ (**currently
> early**, $E_j > 0$) yet $K(C^K_j+s+1) > d^-_j$ (**marginally $S_D$**). The
> marginal slope $K(W_T - W_E)$ then reports "slope 0" for that job even though a
> right step still strictly lowers its $E_j$ by a *partial* cell
> ($d^-_j - K(C^K_j+s) \in [1, K-1]$). **This single gap is what separates the two
> options below:** floor trusts the marginal slope (stops, leaving the partial
> residual); look-ahead checks $F$ directly (takes the partial step when it helps).

Define the weighted sums (these are `sum_e` / `sum_t` in the code):

$$W_E = \sum_{j \in S_E} w^-_j, \qquad W_T = \sum_{j \in S_T} w^+_j.$$

Then the right-derivative of $F$ is

$$\frac{dF}{ds} = K\,(W_T - W_E).$$

So **moving right reduces $F$ iff $W_E > W_T$** — exactly the NBM net-benefit test
$UNB > 0$. The slope is constant between **breakpoints**, which occur where some
job enters or leaves its window:

$$\text{$j$ enters window:}\quad s = \frac{d^-_j}{K} - C^K_j, \qquad\qquad
\text{$j$ leaves window:}\quad s = \frac{d^+_j}{K} - C^K_j.$$

Because $F$ is convex, its real minimizer $s^{*}$ is at a breakpoint (or anywhere
on a flat bottom), and the **integer** minimizer is one of $\lfloor s^{*} \rfloor$,
$\lceil s^{*} \rceil$, clamped to $[0, \Delta_2]$.

---

## 3. Why the fine grid needs no search, but the coarse grid does

**Fine grid ($K = 1$):** the next cell *is* the next integer, so the marginal
partition coincides with the true state — there is no marginal-vs-true gap. The
greedy steps right while $W_E > W_T$, each step lands exactly on a breakpoint, the
job crosses $S_E \to S_D \to S_T$, and it stops at the convex minimizer. **Exact;
no search needed.** (True for the 2008 due-date problem and the due-window problem
alike.)

**Coarse grid ($K > 1$) — the floor reading (user's claim).** Run the right-only
greedy on the **marginal** partition: keep stepping while $W_E > W_T$. An early
job stays in $S_E$ exactly while $K(C^K_j + s + 1) \le d^-_j$, and **leaves $S_E$
precisely at the floor cell** $C^K_j + s = \lfloor d^-_j/K \rfloor$ (its next cell
would exceed $d^-_j$). So the greedy **stops cleanly at $\lfloor d^-_j/K \rfloor$
— no $\Delta_1 = 0$ termination guard, no look-ahead, overshoot-safe**. This is
exactly the user's point: *with the marginal partition, floor-only is internally
consistent and the "bug" disappears.* (For an **isolated** early job the shipped
code reaches the same cell via the current-cell partition + the $\Delta_1 = 0$
guard at `ffc_schedule.py:1653-1655`. But the two are **not** equivalent for a
**merged block**: when a right member already sits at its own floor it contributes
$\Delta_1 = 0$, and the shipped guard then stalls the *entire* block, leaving left
members badly under-shot. A standalone sim confirms the divergence — a left job
stuck at cell 1 ($wET = 2510$) where the marginal partition reaches cell 6/7
($wET = 150$). So the marginal refactor is **equal-or-better, never worse**, and
strictly fixes this stall. At $K = 1$ the marginal partition coincides with the
current-cell partition, so both are a no-op for every non-CSR caller — the
fine-grid invariance is preserved.)

**Coarse grid ($K > 1$) — the true-objective reading (the residual).** By the
marginal-gap note in §2, that stop cell is usually **still truly early**:
$K\lfloor d^-_j/K \rfloor \le d^-_j$, so $E_j = d^-_j - K\lfloor d^-_j/K \rfloor
\in [0, K-1]$, often $> 0$. The marginal slope called this "done," but $F$ is
**still strictly decreasing** across the one remaining partial-cell step. The
candidate that step lands on is $s' = \lfloor d^-_j/K \rfloor + 1 =
\lceil d^-_j/K \rceil$ (capped at $\Delta_2$):

- if $K\,s' < d^+_j$: job $j$ lands **in the window**, $E_j = T_j = 0$ —
  a **guaranteed strict improvement** floor leaves on the table;
- if $K\,s' \ge d^+_j$: job $j$ crosses **into tardy**, trading
  $w^-_j\,(d^-_j - K\lfloor \cdot \rfloor)$ of earliness for
  $w^+_j\,(K\,s' - d^+_j)$ of tardiness — a weighted trade whose sign must be
  **evaluated**, not assumed.

So both readings are correct and they do **not** contradict: floor is clean and
overshoot-safe *and* leaves a residual $\le K-1$ per early job. The residual is
precisely the partial-cell step the marginal slope cannot see. The two options in
§4 differ only in whether that residual is left (floor) or recovered (look-ahead).

---

## 4. The two options

Both start from §3's reconciliation; they differ only on the residual $\le K-1$.

### Option F — floor (marginal partition, residual accepted)

Keep the shipped floor behavior, but justify it cleanly via the marginal
partition of §2 (optionally refactor the code to drop the `Δ₁ == 0` guard in
favor of the marginal `S_E` test — same result, less hack). Properties:

- **Simplest**; no per-stall objective evaluation; overshoot-safe by construction.
- Leaves residual earliness $\le K-1$ per **isolated** early job (the partial-cell
  step the marginal slope can't see). Misses examples (a) and (c) in §5.
- $K=1$-identical, but at $K>1$ **not** behavior-preserving: it removes the shipped
  guard's **merged-block stall** (a right member at its floor freezing the whole
  block), so it is equal-or-better than today on multi-job blocks. The final $K=1$
  reconstruct repairs the residual in the *final* schedule regardless; only the
  **seed / coarse incumbent** carries it.

### Option L — look-ahead (stop on the objective)

Stop on the **objective**, not the marginal slope. Keep the efficient
breakpoint-jumping skeleton; only augment the `Δ₁ == 0` dead-end:

```
# reached when floor can no longer advance but slope is still favourable (K>1 only)
else:  # Δ₁ == 0
    step = 1                                   # straddle the fractional breakpoint
    if step ≤ Δ₂ and F(s + step) < F(s):       # actual block objective, objectives.py form
        shift block by step;  keep j           # re-evaluate (partition/slope changed)
    else:
        j -= 1                                  # genuine local min on the grid → advance
```

Because `F` is convex in integer `s`, "step while it strictly improves, else stop"
converges to the integer block optimum on `[0, Δ₂]`. The look-ahead realises the
in-window step when one exists, and performs the weighted early-vs-tardy
comparison when no in-window cell exists — automatically.

### Relation to the floor doc's "option C"

Option C was: *use ceil when an in-due cell exists, else floor.* The look-ahead is
a **strict generalization**:

| case | option C | NBM look-ahead |
|---|---|---|
| in-due coarse cell reachable | ceil → in-window (optimal) | step taken → in-window (optimal) |
| no in-due cell, $w^-_j$ large | floor (stay early) | compares; may step into tardy if cheaper |
| no in-due cell, $w^+_j$ large | floor (stay early) | declines step (floor is optimal) |
| $K = 1$ | n/a (guard unreachable) | n/a (guard unreachable) → byte-identical |

So the look-ahead matches option C wherever option C is optimal, and additionally
optimises the case option C left on the table ($w^-_j$-dominant, sub-grid window).

---

## 5. Worked micro-examples (`K = 50`, single-job block, `Δ₂ = ∞`)

**(a) in-window cell reachable.** $(d^-, d^+) = (110, 220)$, $C^K = 1$,
$w^- = 1$, $w^+ = 1$.

- **Option F (floor):** at $C^K = 1$, next cell $K(1+1) = 100 \le 110$ → $S_E$,
  step right. At $C^K = 2$, next cell $K(2+1) = 150 > 110$ → leaves $S_E$ →
  **clean stop at $C^K = 2$**. But $K\,C^K = 100 < 110$, so it is still truly
  early: residual $E = 1\cdot(110 - 100) = 10$.
- **Option L (look-ahead):** at $C^K = 2$, check $F$ of stepping to $C^K = 3$:
  $K\,C^K = 150 \in [110, 220)$ → $F = 0 < 10$ → **take it. Final cost 0.** (The
  wide-window under-shoot, recovered.)

**(b) no in-window cell, tardiness-dominant.** $(d^-, d^+) = (110, 120)$,
$C^K = 2$, $w^- = 1$, $w^+ = 100$. $K\,C^K = 100$.

- look-ahead at $C^K = 2$: step to $C^K = 3$, $K\,C^K = 150 \ge 120$ →
  $T = 100\cdot(150 - 120) = 3000$ vs current $E = 1\cdot(110 - 100) = 10$.
  $3000 > 10$ → **decline. Stay at $C^K = 2$.** (Floor was already optimal here —
  look-ahead confirms it.)

**(c) no in-window cell, earliness-dominant.** $(d^-, d^+) = (110, 120)$,
$C^K = 2$, $w^- = 100$, $w^+ = 1$. $K\,C^K = 100$.

- current $E = 100\cdot 10 = 1000$. step to $C^K = 3$: $T = 1\cdot 30 = 30 < 1000$
  → **take it.** Floor would have stayed at cost 1000; look-ahead reaches 30.
  *(This is the case neither floor nor option C optimises.)*

---

## 6. Scope and impact (unchanged from floor doc, restated)

- **Coarse-path only.** `insert_idle_time(time_factor=K>1)` is used solely in seed
  construction (`coarsen_solve_reconstruct.py:167,178` and the v3/v4 paired
  builders). The **final** schedule is produced at `time_factor=1`
  (`schedule_build.py:112`), where the guard is unreachable → **no change**.
- Therefore this is a **seed / CP-warm-start quality** improvement (and a fairer
  `dispatch_seed_obj` metric), **not** a final-objective correctness fix. Expected
  value: a tighter incumbent handed to the coarse CP, most visible under tight time
  limits / wide-window-heavy instances (e.g. the 200/10/5 family).
- `K = 1` byte-identity (the tested no-op guarantee for all non-CSR callers) is
  preserved *for free* because the patched branch is unreachable at `K = 1`.

---

## 7. Correctness obligations (to discharge in the real plan)

**If Option F (floor + marginal refactor):**

0. **$K=1$ identity + overshoot-safety + no-stall (NOT full equivalence).** Prove
   (i) at $K=1$ the marginal `S_E` test coincides with the current-cell partition →
   byte-identical for all non-CSR callers; (ii) overshoot-safety still holds
   ($\Delta \le \Delta_1$ ⟹ no early job crosses to tardy); (iii) the marginal
   greedy never stalls (when $W_E > W_T$, $\Delta_1 \ge 1$ always, so the
   `Δ₁ == 0` guard is dead and removable). Do **not** assert cell-for-cell
   equivalence with the shipped guard at $K>1$ — it is intentionally **better**
   (removes the merged-block stall; see §3). Lock the divergence as a regression
   fixture, not a violation.

**If Option L (look-ahead):**

1. **Optimality of the integer line search.** $F$ convex PL in $s$ ⟹ stepping
   while strictly improving reaches the integer min on $[0, \Delta_2]$; straddling
   integers suffice. (Needs a clean statement + proof in the plan.)
2. **Termination.** Each iteration either strictly lowers $F$ (bounded below by 0,
   integer-valued ⟹ finitely many improving steps) or decrements `j`. No hang.
3. **$\Delta_2$ cap / block merge.** When the improving step reaches $\Delta_2$,
   the block merges with the next block (existing `block_end` expansion). Confirm
   the look-ahead respects `step ≤ Δ₂` and that a step *to* $\Delta_2$ hands off to
   the merge path rather than double-counting.
4. **Marginal-vs-objective discipline.** The §2 partition ($S_T$:
   $K(C^K_j + s + 1) > d^+_j$) is for the *slope*; the look-ahead decision must
   compare the **objective** $F$ (which uses the current cell,
   $\max(0, K(C^K_j+s) - d^+_j)$, so $K\,C^K_j = d^+_j \Rightarrow T_j = 0$). Keep
   the two separate so the marginal-vs-true gap is closed by $F$, not re-encoded in
   the partition. Verify on a $K \mid d^+_j$ fixture.

---

## 8. Open questions for review (answer before I write the TDD plan)

1. **Option F vs Option L** (the central call). F = floor, marginal-justified,
   = shipped behavior, residual $\le K-1$ accepted (repaired by the final $K=1$
   pass). L = look-ahead, recovers the residual on the coarse seed at the cost of
   one $F$-evaluation per stall (O(block size)). Given impact is **seed-only**, is
   L's extra coarse-optimality worth the complexity?
2. **If F: also do the marginal-partition refactor?** Option F can either keep the
   current `Δ₁ == 0` guard or be rewritten to the clean marginal `S_E` test (same
   result, drops the guard). Recommend the refactor for clarity; confirm.
3. **Gate by measurement first?** A/B (F vs L: coarse incumbent quality, CP
   time-to-incumbent, final obj) on a wide-window instance set **before**
   committing — is L worth doing at all, given the final $K=1$ pass already repairs
   the *final* timing and only the seed carries the residual?
4. **If L: `step` granularity.** `step = 1` straddles a single breakpoint;
   convexity + the loop handles multi-breakpoint blocks via re-evaluation. Confirm
   we prefer the simple unit step over computing $\lceil s^{*} \rceil$ in closed
   form.

---

*Next artifact (after this is reviewed): a TDD implementation plan with file-by-file
edits, red→green test order, and the A/B harness — mirroring the structure of
`csr-floor-shift-overshoot-safe.md`.*
