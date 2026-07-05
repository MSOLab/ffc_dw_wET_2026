# SW-CP time-limit policy investigation — full handoff

**Date:** 2026-07-05 · **Branch:** `20260705_rj_scope_option`
**Purpose of this doc:** self-contained context so a *fresh* conversation can
continue the analysis without re-deriving anything. Read top to bottom.

---

## 0. One-line goal & current status

**Goal:** produce empirical evidence for a *size-aware* per-sub-CP time-limit
(TL) policy for SW-CP: `TL = f(#unfixed_ops, #profile_fixed_ops)`, replacing the
current size-independent constant/linear policy.

**Status:** instrumentation done + unit-tested; profiling configs written;
pipeline smoke-validated. **Next up:** run the profiling sweep and build the
analysis. No git add/commit has been done (per user); all changes are unstaged.

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
- **Why a generous cap (300 s) at all, if not for convergence?** Two reasons:
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
  size-proportional idea to justify here. **Open question:** should `unfixed` and
  `profile_fixed` get *separate* coefficients? (profile-fixed ops are retime-only
  with fixed precedence → likely cheaper per op).
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
- **`sw_cp_tl_profile.yaml`** — 10-instance smoke subset ×
  **5 star-design scenarios**, `cp_tl: 300.0`, `batch_tl_mode: constant`,
  `timelimit: 100000.0` (so the global deadline never clamps the per-sub-CP cap),
  `instance_worker_cnt: 10`. Seed = `calc_mcf_lb_and_derive_full_sch`.
  - Star design (breaks `#unfixed`/`#pfixed` collinearity — otherwise both `=k·m·c`):
    - `u1_pf2` (unfixed=1, lpf=rpf=2), `u2_pf2` (2,2/2), `u4_pf2` (4,2/2) — vary unfixed
    - `u2_pf0` (2, 0/0), `u2_pf2`, `u2_pf4` (2, 4/4) — vary profile-fixed
  - Both configs already have `record_sub_cp_trajectory` **removed** (that key no
    longer exists on `SwCpOption`; leaving it in crashes the run).
- **Instances** = `[60,61,63,64,68,150,152,155,246,248]` — all **n=50, T=0.6,
  R=0.2**; c∈{5,10}, m∈{3,5}, W∈{10,20}. This is a **pipeline-validation cluster,
  NOT a real (n,c,T,R) grid**. Expand to a real grid later (fix m,W,rep; sweep
  n,c,T,R).

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
  (verified: smoke = 1 scenario `u2_pf2` @ cp_tl 60; full = 5 scenarios @ cp_tl
  300; no `record_sub_cp_trajectory` key anywhere). Runs `20260705T230701` and any
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
3. **Bump nothing** — `sw_cp_tl_profile.yaml` cap is already 300 s. Launch the
   full sweep (background).
4. **Analysis** (`analysis/20260705_sw_cp_tl_profile/`), framed as **objective
   improvement only**:
   - Per window: define achievable improvement = `UB(0) − UB(cap)`; compute
     **time-to-p%** of it for p∈{50,80,90,95,99}. Regress time-to-p% on
     `(#unfixed, #pfixed)` (from `_step_log.yaml`) → coefficients `a, b (+c)`.
   - **"Cut at τ = a·#unfixed + b·#pfixed + c"**, equal-total-budget comparison:
     constant vs proportional; report **total objective improvement captured**
     (needs the 300 s curves so proportional can give big windows > constant
     baseline). This is the headline evidence.
   - Aggregate per (n,c,T,R); emit CSV + HTML.
5. If evidence supports it: implement a **size-proportional TL mode** in
   `step_tl_resolver.py`/dispatcher (port hybridflowshop's op-count policy,
   possibly two-coefficient), then **end-to-end A/B** (constant vs proportional at
   equal total budget) measuring final objective.
6. Expand from the smoke cluster to a real **(n,c,T,R) representative grid**.

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
