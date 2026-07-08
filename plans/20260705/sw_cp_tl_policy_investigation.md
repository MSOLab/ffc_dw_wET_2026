# SW-CP time-limit policy investigation — full handoff

**Date:** 2026-07-05 · **Branch:** `20260705_rj_scope_option`
**Purpose of this doc:** self-contained context so a *fresh* conversation can
continue the analysis without re-deriving anything. Read top to bottom.

---

## 0. One-line goal & current status

**Goal:** produce empirical evidence for a *size-aware* per-sub-CP time-limit
(TL) policy for SW-CP: `TL = f(#unfixed_ops, #profile_fixed_ops)`, replacing the
current size-independent constant/linear policy.

**Status (2026-07-06):** instrumentation done + unit-tested; analysis tool built
(`analyze_tl_policy.py`); **n=50 representative stage run (1- and 8-thread) and
analysed.** Preliminary n=50 result: **size-proportional TL shows no benefit
over constant** at equal budget (§3.2) — but this is *sample-only* and must be
confirmed on the full (n,c,T,R) grid before concluding. Thread count 1→8 is a
real lever (+10.7 % UB); **solver_thread_cnt=8 adopted**. No git add/commit (per
user); all changes unstaged. **Next up:** expand the sweep to the (n,c,T,R) grid
(8-thread) to firm up / overturn the size-vs-difficulty finding; optionally
re-run the analysis on the 8-thread trajectories.

---

## 1. Analysis philosophy (UPDATED — read this first)

Per the user's explicit direction (2026-07-05):

- **Abandon optimality/gap judgment.** Sub-CPs will **not** close to `UB = LB`
  even with 300 s / 600 s / more — the CP lower bound is very loose (smoke showed
  per-window gaps of 8–44 % at 60 s; step 2 was 43.6 %). Do **not** classify
  windows as OPTIMAL / not, and do **not** use the gap as the metric.
- **The metric is objective-value (UB) improvement over time.** Everything is
  framed as "how much objective improvement does a window achieve by time τ",
  and "how much of the generous-time improvement does a cheaper TL policy
  capture."
- **LB is context-only.** We still record it (it's in `_obj_log.json`'s
  `obj_bound` series), but it only illustrates that the bound is loose; it is not
  central to any conclusion.
- **Why a generous cap (120 s) at all, if not for convergence?** Two reasons:
  (a) capture enough of the diminishing-returns UB curve to see where objective
  improvement plateaus; (b) have curve length *beyond* the constant baseline so
  we can simulate redistributing budget to larger windows in the equal-budget
  comparison.

---

## 2. Background (verified in code)

- **Current policy** — `src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py`
  `resolve_per_step_tl` (lines 13–70) builds the per-window TL list in only two
  modes: `constant` (same TL every window) and `linear` (`offset + (i+1)·x`,
  linear in **step index** `i`). Neither depends on the number of ops in the
  window. Applied in `algorithm/sw_cp/dispatcher.py` via
  `solver.parameters.max_time_in_seconds` (`_apply_tl_and_deadline`, ~line 524).
  The window op-count is logged but never fed back into TL.
- **Target policy (already in sibling repo `../hybridflowshop`)** — PW-CP (== this
  repo's SW-CP) uses `sub_CP_TL = non_time_fixed_op_count × multiplier`
  (`hybridflowshop/hybridflowshop/controller/pw_cp.py::_resolve_batch_time_limit`,
  ~line 256), where `non_time_fixed = unfixed + left_profile_fixed +
  right_profile_fixed`, clamped by remaining global budget. This is the
  size-proportional idea to justify here. **Decision (2026-07-05):** use a
  **single coefficient** `TL = k · non_time_fixed_op_count` (== hybridflowshop's
  form; simplest port). The earlier open question — whether `unfixed` and
  `profile_fixed` deserve *separate* coefficients (profile-fixed ops are
  retime-only with fixed precedence, plausibly cheaper per op) — is **not
  closed, only deferred**: keep it as a *regression diagnostic*. Fit
  `time-to-p% ~ unfixed + pfixed` and, only if the two coefficients come out
  significantly different, revisit a two-coefficient policy. Until then a single
  `k` is the working assumption (interaction terms ignored — consistent with the
  reduced 2-scenario design in §6, which cannot see the unfixed×pfixed
  interaction).
- **SW-CP window mechanics:** each stage's ops are cut into fixed-size batches
  (`batch_size`, default `"m"` = last-stage machine count). Per step a 5-region
  partition `LTF | LPF | UNFIXED | RPF | RTF` slides right by `step_size` batches.
  `non_time_fixed = LPF + UNFIXED + RPF` are the CP decision variables;
  `unfixed = UNFIXED`; `profile_fixed = LPF + RPF`. Attributes on the partition
  object: `p.unfixed`, `p.left_profile_fixed`, `p.right_profile_fixed`,
  `p.non_time_fixed`. Controlled by option fields `unfixed_batch_count`,
  `left_profile_fixed_batch_count`, `right_profile_fixed_batch_count`,
  `step_size`, `pf_method`.
- **Problem/instance params:** `n` jobs {50,100,150,200}, `c` **stages** {5,10},
  `m` machines/stage {3,5} (separate from c), `T` due-date tightness {0.2,0.4,0.6},
  `R` due-date range {0.2,0.6,1.0}, `W` window width {10,20}, `rep` {0..4}.
  1440 instances in `benchmarks/PRA2017/large/`, filename
  `Instance_{n}_{c}_{m}_{T}_{R}_{W}_Rep{rep}.txt` (T/R comma-decimal). Index map:
  `benchmarks/PRA2017/pra2017_hybrid_match.csv` (insIndex→filename);
  `pra2017_instance_table.csv` has (insIndex,n,c,totalMcCount,T,R,W,BKS).

---

## 3. Key finding so far (smoke, `u2_pf2`, `Instance_50_5_3_0,6_0,2_10_Rep0`, 60 s cap)

Even the **smallest** instances (n=50) do **not** converge: 7/8 windows consumed
the 60 s cap. (Gap shown for context only — we do NOT use it as a metric.)

| step | unfix | pfix | ntf | status | wall_s | UB | (LB) | (gap%) |
|---:|---:|---:|---:|:--|---:|---:|---:|---:|
| 0 | 30 | 30 | 60 | OPTIMAL | 0.01 | 5814 | 5814 | 0.0 |
| 1 | 30 | 60 | 90 | FEASIBLE | 60.3 | 10373 | 9595 | 8.1 |
| 2 | 30 | 60 | 90 | FEASIBLE | 60.0 | 15470 | 10771 | 43.6 |
| 3 | 30 | 60 | 90 | FEASIBLE | 60.0 | 20782 | 18018 | 15.3 |
| 4 | 30 | 60 | 90 | FEASIBLE | 60.0 | 28704 | 24203 | 18.6 |
| 5 | 30 | 60 | 90 | FEASIBLE | 60.0 | 34507 | 30323 | 13.8 |
| 6 | 30 | 60 | 90 | FEASIBLE | 59.8 | 31122 | 28248 | 10.2 |
| 7 | 30 | 40 | 70 | FEASIBLE | 60.3 | 22539 | 20814 | 8.3 |

The run-level obj_log for that run captured **1523** obj_value (UB) points across
the sw_cp step — plenty of trajectory density. (`unfix`=30 constant because
unfixed_batch_count=2 × m=3 × c=5; `pfix` varies at boundaries.)

### 3.1 n=50 stage observations (2026-07-06, u2_pf2, cp_tl=120, step_size=1)

First real sweep stage (run `20260706T005339_880205`, u2_pf2 = 68 instances,
rep0). u2_pf2 wall ≈ 29 min at `instance_worker_cnt=68`; per-instance algorithm
elapsed min 89 s / median 987 s / max 1740 s; 9–16 windows/instance. Eyeballing
UB/LB-vs-time plots for a fast / median / max-elapsed instance surfaced two
observations that should shape the regression (§8):

1. **Two solve regimes, set by instance structure — not window size.** Some
   instances close every window **well under** the 120 s cap (e.g.
   `Instance_50_5_3_0,2_0,6_10_Rep0`: 16 windows in 89 s total — CP finds each
   window's optimum fast, so a generous TL is wasted); others hit the cap on
   **every** window (`..._0,4_0,2...`: 16 windows × ~110 s = 1740 s). The
   discriminator is instance difficulty (T tightness, m), **not** the per-window
   op-count. Consequence for the policy: within one `u2_pf2` instance
   `non_time_fixed ≈ 90` is nearly constant across windows, so `TL =
   k·non_time_fixed` is **essentially constant per window inside an instance**
   (≈ today's constant policy). The size-proportional signal lives mostly
   **across** scenarios (u2 vs u4) and across the `(n,c,m)` grid — so the
   regression should include instance-difficulty features (T, m, …), not just
   op-count, to predict whether a window will even use its budget.
2. **Diminishing returns runs along the window-*index* axis.** Later windows
   contribute progressively less UB improvement (max-elapsed instance is nearly
   flat after ~ub8 / t≈1000 s). That argues for **less** budget on late windows —
   i.e. the step-index *linear* policy — which is a **different axis** from
   size-proportional TL. The two ideas can be in tension; keep them separate in
   the analysis (a window can be large *and* late).

These are qualitative reads to be quantified by the §8 time-to-p% regression
once the stage completes; noted here so the framing isn't re-derived.

### 3.2 n=50 stage RESULTS (2026-07-06) — analysis + a headline correction

Analysis tool: `scripts/20260706/analyze_tl_policy.py` (spec in
`scripts/20260706/ANALYSIS_DESIGN.md`; per-window `k` targets via
`scripts/20260706/k_for_capture.py`; plotter
`scripts/20260706/plot_ub_lb_vs_time.py`. Generated CSV/PNG artifacts still land
under the gitignored `analysis/20260705_sw_cp_tl_profile/`). Run on the
1-thread n=50 stage (`20260706T005339_880205`, 1550 windows, 1417 with I>0).

**⚠️ These are n=50-only reads — NOT firm conclusions.** The user's explicit
caveat: the sample stage cannot settle the size-vs-difficulty question; it must
be re-confirmed on the (n,c,T,R) grid (§8). Recorded so the direction isn't
re-litigated.

1. **Size-proportional TL shows NO benefit over constant (tentative).** Equal
   total budget, `constant (tau=cap)` vs `proportional (tau=k·non_time_fixed)`,
   captured fraction of achievable UB improvement:

   | per-window cap | constant | proportional | delta |
   |---:|---:|---:|---:|
   | 15 s | 53.0 % | 52.9 % | −0.1 pp |
   | 30 s | 61.3 % | 60.9 % | −0.4 pp |
   | 60 s | 71.0 % | 71.4 % | +0.4 pp |
   | 120 s *(degenerate)* | 100.0 % | 94.2 % | −5.8 pp |

   Sub-cap deltas are noise (±0.4 pp). Mechanism: within an instance
   `non_time_fixed` is nearly constant across windows (§3.1 obs 1), so
   "proportional" allocates almost the same τ as "constant" — they *can't*
   differ much. `cap == TL` (120 s) is DEGENERATE: constant == the actual run ⇒
   100 % by construction.

2. **HEADLINE CORRECTION — the first "+10.3 pp for proportional" was a
   measurement artifact.** The initial analysis reported constant capturing only
   84 % (and proportional 94 %) at the 120 s cap. Cause: CP-SAT logs its final
   accepted incumbent a few hundredths of a second *past* the nominal cap
   (`wall_seconds` 120.0–120.8 for 74 % of windows), and `captured_at` only
   credited full `I` when `tau >= wall_seconds`. So evaluating constant at
   exactly `tau=120` dropped every window's end-of-window jump, penalising
   constant and inflating proportional. **Fix:** credit full `I` when the granted
   budget reaches the window's actual solve budget `TL` (`tau >= TL`). After the
   fix constant@120 = 100 % (correct) and the sub-cap comparison is unchanged
   (that regime was never affected). Lesson: never evaluate a policy budget at
   exactly the cap that generated the data.

3. **The real driver of budget-need is difficulty, not size (tentative).**
   `t_90 ~ non_time_fixed`: slope ~0.12 but **R²≈0.03** (size explains ~3 % of
   time-to-90% variance). Difficulty-augmented: `T` dominates (coef ~46 vs ~0.13
   for op-count); `reached_cap` fraction rises monotonically in both `T` and `m`
   (0.49 → 0.93). Diagnostic split of unfixed vs pfixed coefficients differed
   ~73 % — weak support for a two-coefficient TL *if* a size policy were pursued,
   but (1) says size barely matters anyway.

4. **Thread count is a real lever (n=50): 1 → 8 threads/sub-CP improved final UB
   by median +10.7 %** (120/136 instances better, mean +15.3 %, 16 worse) at the
   same 120 s cap — run `20260705_sw_cp_tl_profile_t8/20260706T015554_738214`
   (config `sw_cp_tl_profile_t8.yaml`, solver_thread_cnt=8, instance_worker_cnt
   =12 = 96 cores). **Decision: use solver_thread_cnt=8 going forward.** Threads
   matter more than the (null) size-proportional TL. Follow-up: re-run the
   time-to-p%/capture analysis on the 8-thread trajectories — the faster
   convergence may reshape the within-window curve.

---

### 3.3 Full-grid k-for-capture table (n=50–200, thread=8, u2_pf2) — 2026-07-07

Full-grid confirmation of the §3.1/§3.2 direction, on the **8-thread** runs
(§3.2 obs 4 decision). Scenario **u2_pf2 only** (u4_pf2 deferred — the bigN
u4_pf2 was abandoned partial and is out of scope). Pool of **270 instances**
under the NEW representative policy: per `(n,c,m,T,R,W)` cell scan `rep0..rep4`,
take the first non-optimal rep, drop a cell only if all 5 reps are optimal
(258 rep0 + 12 rescued non-rep0; 18 cells stay dropped, all at `T=0.2,R=1.0`).
Runs pooled = 68 (n=50 rep0) + 190 (n≥100 rep0) + 12 (rescued). Method:
`k_for_capture.py --scenario u2_pf2` over the three run dirs (offline replay,
`I>0` windows). Artifacts: `…_t8/20260706T015554_738214/analysis/`
(`k_for_capture_270_u2_report.md`, `…_u2_pooled.csv`, `…_cohort_*.csv`,
`…_by_TR.csv`). `k` is in **s/op** (`TL = k·non_time_fixed_op_count`) — the
size-normalised primary number; `TL@med = k·median_ntf` is a seconds illustration
vs the fixed 120 s cap.

**Pooled table — 270 u2_pf2 (7627 I>0 windows, median_ntf=150):**

| p% | A k | B2 k | B1 medk | B1 P75k | B1 P90k | B2 TL@med |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 0.002 | 0.002 | 0.002 | 0.024 | 0.319 | 0.3 s |
| 80 | 0.225 | 0.080 | 0.016 | 0.400 | 0.800 | 12 s |
| 90 | 0.479 | 0.400 | 0.033 | 0.400 | 0.800 | 60 s |
| 95 | 0.800 | 0.800 | 0.051 | 0.405 | 0.800 | 120 s |
| 99 | 1.333 | 1.333 | 0.076 | 0.552 | 0.800 | 200 s |

**`k` is roughly size-invariant (supports size-proportional `TL = k·ntf`).**
Operational B2 `k` barely drifts from n=50 to n≥100:

| p% | B2 k n=50 (68) | B2 k n≥100 (190) | n≥100 − n=50 |
|---:|---:|---:|---:|
| 80 | 0.044 | 0.091 | +0.047 |
| 90 | 0.470 | 0.400 | −0.070 |

(median_ntf n=50=150, n≥100=180; `k` is s/op so it is directly comparable.) The
rescued-12 cohort (median_ntf=90, 208 windows) is too small for a standalone `k`
read — it sits inside the pooled distribution and moved the pooled median_ntf/k
negligibly (sanity only).

**But budget-need tracks difficulty (T), not size — `(T,R)` breakdown.**
B2 `k` and the `reached_cap` fraction both rise monotonically with **T**
(tardiness tightness); **R** is a weak secondary axis. This directly confirms
§3.2 obs 3 on the full grid.

| T \ R | B2 k@80 (0.2 / 0.6 / 1.0) | reached_cap (0.2 / 0.6 / 1.0) |
|---:|---|---|
| 0.2 | 0.031 / 0.006 / 0.011 | 0.33 / 0.18 / 0.12 |
| 0.4 | 0.115 / 0.057 / 0.152 | 0.42 / 0.38 / 0.32 |
| 0.6 | 0.195 / 0.143 / 0.220 | 0.50 / 0.52 / 0.57 |

From T=0.2 to T=0.6 the required B2 `k@80` grows ~10–20×. The easy corner
`(T=0.2, R=1.0)` is thin by construction — 14 instances (vs 32 elsewhere), 166
I>0 windows (vs ~900), `reached_cap`=0.12 — because 18 of its cells are all-rep
optimal and dropped. So a single global `k` over-budgets easy (low-T) windows and
under-budgets hard (high-T) ones; a difficulty-aware (T-aware) `k` would fit
better than a pure size-proportional one — echoing §3.1 obs 1 / §3.2 obs 3.

**Operational takeaway (B2, trustworthy p ≤ 90):** ~80% of the achievable UB
improvement is captured at **B2 k=0.080 s/op** (≈12 s at a median window) and 90%
at **k=0.400 s/op** (≈60 s) — both well under the current fixed **120 s** cap.
p ≥ 95 implies TL@med ≥ 120 s (beyond the cap, "cannot reduce"): not a
trustworthy TL-reduction target. Real proof still requires the end-to-end A/B
(§8 item 5) — these are offline-replay, directional.

---

## 4. Instrumentation — what changed (final form)

Design principle (from user feedback): **do not invent a bespoke artifact**;
reuse the standard `<instance>_obj_log.json`. An earlier verbose
`_sub_cp_trajectory.json` (per-point t/ub_cp/lb_cp/ub_full/lb_full + per-step
metadata) was built, judged **too big / redundant with obj_log**, and **removed**.

Changes (all unstaged working-tree edits; sw_cp tests pass — 23 passed):

1. **`src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`** — the `ProgressLogEntry`
   built from CP-SAT solution-callback points now sets
   `obj_bound = float(vb.bound) + full_offset` (line ~309) instead of `None`.
   This makes the standard obj_log carry the LB series. (`vb.value`/`vb.bound` are
   the sub-CP's UB/LB; `full_offset` maps them to full-instance objective space.
   The LB is a **per-window local** lower bound, not a global one.)
2. **`src/ffc_ddw_sum_et/algorithm/sw_cp/step_log.py`** — `SwCpStepEntry` gained
   `unfixed_op_count` and `profile_fixed_op_count` (invariant:
   `unfixed + profile_fixed == non_time_fixed`). These are the compact per-window
   size features for the regression. Written to `_step_log.yaml`.
3. **`dispatcher.py`** computes those two counts from the (post-promotion)
   partition and threads them into `SwCpStepEntry`, with an invariant assertion.
4. Removed: the `record_sub_cp_trajectory` option field
   (`sw_cp/option.py`), all `_sub_cp_trajectory.json` accumulation/writing
   (`dispatcher.py`, `controller.py`), the now-unused `import json` in
   `controller.py`, and the two obsolete tests. Added
   `test_dispatcher_progress_log_carries_obj_bound` (asserts progress_log has an
   entry with `obj_bound is not None`, and the count invariant).

**`_save_obj_log`** (`orchestration/ffcddw_single_instance_runner.py:515–578`) was
**not** changed — it already reads `entry.obj_bound` into the obj_log `obj_bound`
series (lines 542–543). It only writes a bound *note* when the step's
report-level `obj_bound` is set (lines 552–555); SW-CP's report-level obj_bound is
`None`, so **no bound note is written for sw_cp** — see the loader caveat below.

---

## 5. Data formats & how to read them (for the analysis)

### `<instance>_obj_log.json` (compact, single line)

```json
{"obj_value":{"name":"obj_value","data":{"<t_str>":UB,...},"notes":{"<t_str>":"<idx>-<step>"}},
 "obj_bound":{"name":"obj_bound","data":{"<t_str>":LB,...},"notes":{...}}}
```

- `t_str` = controller-frame elapsed seconds (`repr(float)`), string keys.
- `obj_value.data` = UB trajectory (every CP solution-callback point across all
  steps); `obj_bound.data` = LB trajectory (now populated for sw_cp).
- `notes` mark step-ends with label `"<idx>-<subroutine>"` (e.g. `"2-sw_cp"`).
- **obj_log_loader caveat:** `report/obj_log_loader.py::load_instance_progression`
  segments a series by its `notes`; `_build_calls_for_series` returns `()` if a
  series has no notes (lines 87–88). Because sw_cp writes no `obj_bound` note, the
  loader's `obj_bound_calls` will **drop** the sw_cp LB points even though the raw
  `obj_bound.data` map is fully populated. → **For the UB/LB line plot, read the
  raw obj_log maps directly.** (Follow-up option: write a per-step bound note or
  relax `_save_obj_log` so LB becomes first-class in the loader.)
- The loader works fine for the **UB** series (`obj_value_calls`), which has notes.

### `<instance>_step_log.yaml` (per sw_cp step call; one row per window)

Fields (from `SwCpStepEntry`): `step`, `unfixed_batch_start_idx`,
`unfixed_op_count`, `profile_fixed_op_count`, `non_time_fixed_op_count`,
`sub_job_count`, `incumbent_obj_before`, `cp_obj`, `incumbent_obj_after`,
`accepted`, `status`, `wall_seconds`, `cp_divergence_count`. This is the primary
per-window (size → wall_seconds → objective) table for the regression.

### Per-window segmentation

To get per-window UB(t) curves, segment obj_log points by window boundaries using
`_step_log.yaml` cumulative `wall_seconds` (each window's controller-frame span).
Fiddly but doable; alternatively add per-window notes (follow-up).

---

## 6. Experiment configs (`metadata/20260705/`)

- **`sw_cp_tl_profile_smoke.yaml`** — 1 instance (index 60), 1 window config
  (`u2_pf2`), `cp_tl: 60.0`. Pipeline validation.
- **`sw_cp_tl_profile.yaml`** — representative-grid instances ×
  **2 window scenarios**, `cp_tl: 120.0`, `step_size: 1`,
  `batch_tl_mode: constant`, `timelimit: 100000.0` (so the global deadline never
  clamps the per-sub-CP cap), `instance_worker_cnt: 10`.
  Seed = `calc_mcf_lb_and_derive_full_sch`.
  - **Scenarios (REDUCED 2026-07-05):** vary **only `unfixed_batch_count`**,
    profile-fixed held at `lpf=rpf=2`:
    - `u2_pf2` (unfixed=2, lpf=rpf=2), `u4_pf2` (unfixed=4, lpf=rpf=2).
  - Design history / rationale: the original **star design** varied unfixed over
    `{1,2,4}` *and* profile-fixed over `{0,2,4}` to break the
    `#unfixed`/`#pfixed` collinearity (both otherwise `=k·m·c`). Per user
    direction this was pruned: `unfixed=1` exploration dropped, and the
    profile-fixed axis frozen at 2 (`u1_pf2`, `u2_pf0`, `u2_pf4` removed).
    Consistent with the **single-coefficient** decision (§2) — we no longer
    need the pfixed axis to identify a separate coefficient. The residual
    unfixed leverage (2 vs 4, plus natural `m,c` variation across the grid)
    still varies `non_time_fixed` enough to fit `TL = k · non_time_fixed`. NOTE:
    with pfixed frozen, the §2 "separate-coefficient diagnostic" is now weak
    (only the u2-vs-u4 contrast gives a differing unfixed:pfixed ratio); revisit
    the design if that diagnostic is ever needed.
  - `step_size: 1` (was 2) — window slides one batch per step, so window count
    (and thus per-instance sweep cost) roughly doubles vs step_size=2, but
    trajectory resolution improves. With `step_size < unfixed_batch_count`
    windows **overlap** (u2, u4), so a given op is re-optimised in multiple
    windows — account for this when attributing UB improvement to ops.
  - Both configs already have `record_sub_cp_trajectory` **removed** (that key no
    longer exists on `SwCpOption`; leaving it in crashes the run).
- **Instances (smoke)** = `[60,61,63,64,68,150,152,155,246,248]` — all **n=50,
  T=0.6, R=0.2**; c∈{5,10}, m∈{3,5}, W∈{10,20}. This is a **pipeline-validation
  cluster, NOT a real grid**. It is superseded for the real sweep by the
  representative-grid selection below.

### Representative-grid selection (DECIDED 2026-07-05)

Pick **one instance per `(n,c,T,R,m,W)` cell, `rep=0` only**, dropping cells
whose rep0 is already optimal. Rationale and rule:

- **One per cell, rep0 fixed** — deterministic and reproducible; no runtime-based
  tiebreak. (Earlier idea "not-optimal + fastest" was rejected: in a
  timelimit-capped source run, non-optimal wall-times all pile at the cap so
  "fastest" has no discriminating power, and picking the extreme yields an
  outlier, not a cell representative. Sweep cost is set by window count ≈
  `n·c/batch × W`, not by the source run's convergence time, so a "fast"
  instance does not make the sweep cheaper.)
- **Exclude optimal (`obj_value == obj_bound`) cells** — their UB(t) curve is
  flat and uninformative. **We do NOT use the LB gap as a selection metric
  beyond this binary exclude**, because in this problem the (MCF global) LB is
  too loose to be a meaningful reference (across the 1440-instance PRA2017 large
  grid only ~10 % of instances reach `obj_value == obj_bound`; non-optimal gaps
  have a median of ~110 %). LB is context-only (cf. §1).
- **Source of the optimality flag** — the per-instance
  `<instance>_instance_result.yaml` fields `obj_value` (UB) and `obj_bound` (LB)
  from an existing full-grid run (e.g.
  `output/20260704/20260704T164349_114896/s0_c5_base/`). See AGENTS.md
  "Optimality-judgment field" for the canonical rule.
- **Grid size** — `n{50,100,150,200} × c{5,10} × T{0.2,0.4,0.6} ×
  R{0.2,0.6,1.0} × m{3,5} × W{10,20}` = **288 cells**; minus ~10 % optimal
  ⇒ ~260 instances, `rep=0`. ⚠️ At 2 scenarios × 120 s cap × (window count
  per instance) this is a **large** sweep — consider staging (e.g. run the
  `n∈{50,100}` half first) rather than all 260 at once. TODO: generate the
  concrete `ins_index` list with a selection script and write it into
  `sw_cp_tl_profile.yaml`.

### How to run

```sh
uv run python main.py --config metadata/20260705/sw_cp_tl_profile_smoke.yaml  # ~min
uv run python main.py --config metadata/20260705/sw_cp_tl_profile.yaml        # full sweep, ~1.5–3 h
```

Output → `output/20260705_sw_cp_tl_profile[_smoke]/<run_id>/<scenario>/<instance>/`
with `..._obj_log.json`, `progress/<N>-sw_cp_step_log.yaml`, etc.

---

## 7. Output state / what's on disk

- `output/20260705_sw_cp_tl_profile_smoke/` has several runs. Only trust runs that
  have a `progress/*step_log.yaml` (means sw_cp completed). Run `20260705T223004`
  is a valid **pre-pivot** sample (obj_log has 1523 UB points, but LB empty).
  Run `20260705T230701` is **garbage** (crashed at sw_cp because it used a stale
  config still containing `record_sub_cp_trajectory`) — ignore it.
- **Config status: FIXED and validated.** The key-removal edit briefly left a
  YAML malformation (`batch_tl_mode: "constant"` merged with the next line); both
  `sw_cp_tl_profile_smoke.yaml` and `sw_cp_tl_profile.yaml` now parse cleanly
  (verified: smoke = 1 scenario `u2_pf2` @ cp_tl 60; full = 2 scenarios
  `u2_pf2`/`u4_pf2` @ cp_tl 120, step_size 1; no `record_sub_cp_trajectory` key
  anywhere). Runs `20260705T230701` and any
  older ones that lack a `step_log.yaml` are **garbage** (crashed on the dead key
  / broken YAML) — ignore them; only `20260705T223004` is a valid pre-pivot
  sample.
- **Pivot validated end-to-end.** Post-pivot smoke run
  `20260705T232111_573620/u2_pf2/Instance_50_5_3_0,6_0,2_10_Rep0/` has an obj_log
  with **UB 1523 pts AND LB 1522 pts** (`obj_bound.data` fully populated across
  the sw_cp step; UB objective improved 78935 → 55137). This is the good sample
  for building the plotter. (Unit test `test_dispatcher_progress_log_carries_obj_bound`
  also passes.)
- Note on launching runs: `nohup ... &` makes the Bash tool return immediately
  (its "exit 0" is the launcher shell, not the Python run). Poll for the
  `_obj_log.json` + a sibling `step_log.yaml`, or `pgrep -f <config>.yaml`, to
  know when a run actually finished.

---

## 8. Remaining work (ordered)

1. ~~**Verify fresh smoke obj_log** has `obj_bound.data` populated for the sw_cp
   step.~~ **Done** (see §7: post-pivot smoke `20260705T232111_573620` has
   UB 1523 + LB 1522 pts).
2. **UB/LB-vs-time plotter** — thin script reading the **raw** obj_log maps
   (`obj_value.data`, `obj_bound.data`) → one HTML/PNG line chart per instance:
   UB (primary) and LB (faint, context) over controller time, with step-boundary
   markers from `obj_value.notes`. Put under `analysis/20260705_sw_cp_tl_profile/`.
   (Delegate to a sonnet subagent to keep context small.)
3. **Build the representative `ins_index` list** (selection rule in §6:
   one per `(n,c,T,R,m,W)` cell, `rep=0`, drop optimal). Script reads
   `instance_result.yaml` (`obj_value`/`obj_bound`) from an existing full-grid
   run + `pra2017_instance_table.csv` for the cell keys, emits the index list,
   and writes it into `sw_cp_tl_profile.yaml` (`ins_index`). Then **launch the
   sweep** (background; cap set to 120 s). Consider staging by `n` (§6 warning).
4. **Analysis** (`analysis/20260705_sw_cp_tl_profile/`), framed as **objective
   improvement only**:
   - Per window: define achievable improvement = `UB(0) − UB(cap)`; compute
     **time-to-p%** of it for p∈{50,80,90,95,99}. Regress time-to-p% on
     `(#unfixed, #pfixed)` (from `_step_log.yaml`) → coefficients `a, b (+c)`.
   - **"Cut at τ = a·#unfixed + b·#pfixed + c"**, equal-total-budget comparison:
     constant vs proportional; report **total objective improvement captured**
     (needs the 120 s curves so proportional can give big windows > constant
     baseline). This is the headline evidence.
   - Aggregate per (n,c,T,R); emit CSV + HTML.
5. If evidence supports it: implement a **size-proportional TL mode** in
   `step_tl_resolver.py`/dispatcher (port hybridflowshop's op-count policy,
   possibly two-coefficient), then **end-to-end A/B** (constant vs proportional at
   equal total budget) measuring final objective.
6. ~~Expand from the smoke cluster to a real representative grid.~~ **Decided
   & folded into step 3** — see §6 "Representative-grid selection" (one per
   `(n,c,T,R,m,W)` cell, rep0, drop optimal).

### Code cleanup (deferred, from the 2026-07-05 staged-diff review)

- **`assert` in production path** — `dispatcher.py` uses a runtime `assert` for
  the `unfixed + profile_fixed == non_time_fixed` invariant. It is stripped under
  `python -O`. Decide: move the check into the test only, or replace with an
  explicit raise, and drop the production `assert`.
- **Strengthen the LB test** — `test_dispatcher_progress_log_carries_obj_bound`
  only asserts `obj_bound is not None`. Strengthen to also assert the bound is
  sane (e.g. `obj_bound <= obj_value` for each progress entry, within tolerance)
  so a mis-mapped `full_offset` would be caught.

---

## 9. Working agreements / environment (for the next session)

- **No `git add` / `git commit`** — the user reviews changes manually. Leave
  everything unstaged.
- **Use sonnet subagents for code edits** to keep the main conversation's context
  small. **Never let a subagent run any git command** (a past subagent's stray
  git checkout destroyed uncommitted work).
- **96 physical cores** (nproc shows 192 logical via hyperthreading) — size
  parallelism against 96.
- `uv run ...` for all Python; `uv run ruff check` / `ruff format` after edits.
- Read `docs/problem-description.md` + `docs/algorithm-principles.md` before
  algorithm work; respect the controller subroutine step contract in
  `AGENTS.md`.

---

## 10. Key file map

| Path | Role |
|---|---|
| `src/.../algorithm/sw_cp/dispatcher.py` | SW-CP driver; obj_bound fix (~L309); per-window counts; TL apply |
| `src/.../algorithm/sw_cp/option.py` | `SwCpOption` (window + TL fields) |
| `src/.../algorithm/sw_cp/step_log.py` | `SwCpStepEntry` (per-window row; new count fields) |
| `src/.../algorithm/step_tl_resolver.py` | current constant/linear TL policy (extend here for proportional) |
| `src/.../orchestration/controller.py` | `sw_cp(...)` step (~L2267); resolves cp_tl/total_timelimit |
| `src/.../orchestration/ffcddw_single_instance_runner.py` | `_save_obj_log` (L515) — writes obj_value/obj_bound |
| `src/.../report/obj_log_loader.py` | structured obj_log reader (UB ok; LB dropped — see caveat) |
| `metadata/20260705/sw_cp_tl_profile{,_smoke}.yaml` | experiment configs |
| `../hybridflowshop/.../controller/pw_cp.py` | reference op-count TL policy to port |
| `plans/20260705/sw_cp_tl_policy_investigation.md` | **this doc** |
