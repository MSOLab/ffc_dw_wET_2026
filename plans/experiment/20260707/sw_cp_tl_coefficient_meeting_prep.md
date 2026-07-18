# Plan — SW-CP TL-coefficient meeting material (size-proportional per-CP time limit)

**Purpose:** a self-contained plan so a *fresh* conversation can produce the
meeting material for **work item #2 of this week: switching the SW-CP per-CP
time-limit policy on the due-window problem to a size-proportional
`TL = k · non_time_fixed_op_count`**. Read this top to bottom and execute.
Say "이 파일 내용대로 해" to run it.

The k-for-capture profiling that produces the coefficient is **already done**;
this plan is mostly (B) tracing the existing policy from code, (C/D) building
the per-case comparison, and (E) assembling the presentation artifact. Do **not**
git commit (user commits manually); leave changes unstaged.

---

## 0. One-liner

We profiled SW-CP with a generous uniform 120 s per-CP cap and derived a
size-proportional multiplier **k** (`TL = k · non_time_fixed_op_count`, s/op)
from 270 representative instances (u2_pf2). The meeting presents: **(1)** the
`k` computation method, **(2)** how the resulting per-CP TL compares to the
*existing* due-window policy across the case space (unfixed batch count 2–6 ×
problem size), and **(3)** one chosen `k` and its comparison vs the existing TL.

---

## 1. Context — what is already done

**Week's work item #1 (DONE, committed): SW-CP right-justify scope fix.**
`rj_right_justify_scope` option — right-justify only the right-time-fixed
operations (was: all ops), so both left and right dummy operations get minimal
length. Result: multipliers 501/63/876 → 530/63/847, RPDf 19.2/9.8 → 20.0/8.7;
mean RPDf improved in every (T,R) region but **T=0.6 still loses**. (Not the
subject of this plan — background only.)

**Week's work item #2 (THIS plan): SW-CP TL-policy change.** Today two problems
use *different* SW-CP per-CP time-limit policies:
- **makespan problem:** per-CP TL ∝ (profile-fixed + unfixed op count) — i.e.
  already a `k · ntf` shape.
- **due-window problem:** ~**20 % of the total time limit per single pass**,
  split among the CPs in that pass (see §5) — NOT size-proportional.
The goal is to port the makespan (size-proportional) policy to the due-window
problem and pick a good constant `k`.

**The coefficient is already profiled (2026-07-07).** k-for-capture over **270
u2_pf2 instances** (new representative policy). Full results recorded in
`plans/experiment/20260705/sw_cp_tl_policy_investigation.md` **§3.3**, with artifacts under
`output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/analysis/`:
- `k_for_capture_270_u2_report.md` — pooled + cohort tables + takeaways
- `k_for_capture_270_u2_pooled.csv` — the pooled table (p × A/B1/B2 k)
- `k_for_capture_270_cohort_*.csv` — n=50 rep0 / n≥100 rep0 / rescued-12
- `k_for_capture_270_by_TR.csv` — (T,R) breakdown

**Operational coefficient (basis B2, s/op), pooled 270:**

| p target | B2 k (s/op) | ≈ TL@median window (ntf≈150) |
|---:|---:|---:|
| 80 % | **0.080** | ≈ 12 s |
| 90 % | **0.400** | ≈ 60 s |

(vs the current fixed 120 s profiling cap. p≥95 implies TL@med ≥ 120 s = beyond
the observable cap — not a candidate.) These two are the **primary k
candidates** for §3/Task D.

---

## 2. The three meeting deliverables

1. **Method** — how the per-CP time limit is computed from window size:
   `TL = k · non_time_fixed_op_count` (s/op), and how `k` was derived
   (k-for-capture, basis B2, p-target). Reference §3.3; no new computation.
2. **Computed k vs existing TL, across the case space.** One `k` is derived from
   270 (all u2, unfixed=2) and will be applied to all 1440 (unfixed 2–6). Because
   the per-CP TL under `k · ntf` depends on `ntf` (which grows with unfixed batch
   count and with problem size), build a **per-case comparison table**: for each
   (unfixed batch count ∈ {2,3,4,5,6}, problem size) the TL that `k · ntf` would
   assign vs the TL the **existing due-window policy** assigns.
3. **One chosen k → comparison vs existing.** The user picks ONE `k` (from the
   candidates). Present that choice's per-case TL vs existing side by side, plus
   the offline-replay expected capture (from §3.3). The empirical end-to-end A/B
   run is a *follow-up* (see §8; investigation §8 item 5), not required for the
   meeting doc unless time allows.

---

## 3. Accepted scope decisions (do NOT re-litigate)

- **k derived from unfixed=2 only is acceptable.** The 270 set is all u2_pf2
  (unfixed=2). We know from the n=50 u2-vs-u4 comparison that unfixed=4 needs a
  larger k per op (p=80: 0.31 vs 0.04) — so a single u2-derived k under-budgets
  high-unfixed CPs. **User's call: if this k beats the existing policy, ship it;
  if not, recompute later (possibly with higher-unfixed data).** Do not build a
  per-unfixed k model now — just state the limitation.
- **Difficulty (T) dependence is acknowledged and deferred.** §3.3's (T,R)
  breakdown shows k@80 grows ~10–20× from T=0.2 to T=0.6. Mention as a known
  simplification cost; do **not** build a T-aware k now.
- **u4_pf2 is out of scope** (deferred; bigN u4_pf2 abandoned partial).

---

## 4. Task A — method & candidates (no new compute)

Pull the method statement and the two candidate k's from §3.3 / the 270 report.
Restate: per-CP `TL = k · non_time_fixed_op_count`, `ntf = unfixed_op_count +
profile_fixed_op_count`; `k` chosen so basis-B2 (per-window unweighted mean
captured fraction) hits target p at the 120 s-cap trajectory. Candidates:
**k=0.080 (p80)**, **k=0.400 (p90)** s/op.

---

## 5. Task B — trace the EXISTING per-CP TL policies from code

Two policies to document precisely (read code, quote the formula):

**(B1) Existing due-window per-CP TL — the baseline being replaced.** Chain:
- `src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py :: resolve_per_step_tl` —
  when `total_seconds` (= `option.total_timelimit_seconds`) is set and
  `batch_tl_mode="constant"`, per-CP TL = **`total_seconds / batch_count`**
  (flat), where `batch_count = len(iteration_idxs)` = number of SW-CP windows in
  the pass. `"linear"` mode = `offset + (i+1)·x` summing to `total_seconds`.
- Call site: `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py:~130`
  (`total_seconds=option.total_timelimit_seconds`, `num_batches=None`,
  `batch_count=len(iteration_idxs)`).
- **Where `total_timelimit_seconds` / the "20 % per pass" is set:** trace back
  from `src/ffc_ddw_sum_et/orchestration/controller.py:~2126` (the
  `total_timelimit` resolution) to the step/loop that invokes `sw_cp` /
  `incremental_sw_cp` and decides the per-pass budget fraction (the "20 %").
  Confirm the exact fraction and how many passes/CPs; grep for the caller that
  passes `total_timelimit=` into the sw_cp step. **This is the number needed for
  the §7 comparison denominator.**

**(B2) Makespan proportional per-CP TL — the template being ported.** The user
says the makespan CP already sets per-CP TL ∝ op count. Locate it (start:
`src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/` and any makespan SW-CP path) and
quote its exact per-CP TL formula and constant, so the ported due-window policy
matches its shape. If it is literally `k · op_count`, note what `k` / op-count
definition it uses (compare to our `non_time_fixed_op_count`).

Write the two formulas into the meeting doc (Task E) as the "before" side.

---

## 6. Task C — characterize `ntf` per case (unfixed 2–6 × size)

`non_time_fixed_op_count = unfixed_op_count + profile_fixed_op_count`
(`sw_cp/dispatcher.py:410-419`), recorded per window in the step logs
(`<instance>_*-sw_cp_step_log.yaml`, field `non_time_fixed_op_count`; also
`unfixed_op_count`, `profile_fixed_op_count`). Config: unfixed batch count varies
**2–6**, profile-fixed fixed at **lpf=2 / rpf=2**, `batch_size="m"` (ops per
batch scale with problem size).

Build the ntf distribution per case:
- **unfixed=2:** actual, from the 270 u2_pf2 step logs (the profiling runs).
- **unfixed=4:** actual, from the n=50 u4_pf2 step logs
  (`…_t8/20260706T015554_738214/u4_pf2`, 68 instances).
- **unfixed=3,5,6:** not profiled. Derive `ntf` by formula — `ntf ≈
  (unfixed_batch_count + 2·pfixed_batch) · ops_per_batch`; get `ops_per_batch`
  (≈ m per batch) and the per-window `profile_fixed_op_count` from the u2/u4 step
  logs and scale the unfixed term. Cross-check the unfixed=4 formula against the
  actual n=50 u4 ntf to validate the extrapolation.

Report, per (unfixed count × representative problem size n∈{50,100,150,200}), a
representative `ntf` (e.g. median) — this feeds Task D.

The window-level data is already parsed by `scripts/20260706/analyze_tl_policy.py
:: collect_rows` (returns rows with `non_time_fixed_op_count`, `unfixed_op_count`,
`profile_fixed_op_count`, `n`, `scenario`, `instance`, window index). Reuse it;
do not re-parse YAML by hand.

---

## 7. Task D — build the comparison table (existing vs `k · ntf`)

For each candidate `k` (0.080, 0.400) and each case (unfixed ∈ {2..6} × size):
- **new per-CP TL** = `k · ntf(case)` (median ntf from Task C).
- **existing per-CP TL** = the due-window baseline from Task B1
  (`total_timelimit_seconds / batch_count`; plug the actual per-pass budget and a
  representative `batch_count` for that size). Also show the makespan template
  value (B2) if it differs.

Emit a CSV + a markdown table, e.g. columns:
`unfixed | n | median_ntf | batch_count | existing_TL_s | k=0.080 TL_s | k=0.400 TL_s | Δ vs existing`.
Highlight where `k · ntf` diverges most from existing (expected: large-ntf /
high-unfixed / large-n cases get much more time under `k · ntf`; tiny cases get
much less). This IS the deliverable-(2) evidence.

Optionally add the offline-replay expected capture per candidate from §3.3
(B2 p=80 ≈ 80 % of achievable UB improvement at ≈12 s/median window, p=90 ≈ 90 %
at ≈60 s) so the user can weigh "how much cheaper vs how much captured."

---

## 8. Task E — assemble the meeting artifact; (appendix) execution path

**Meeting artifact (required):** one concise, presentation-ready doc covering
deliverables (1)(2)(3): the method + candidate k's (Task A), the two existing
formulas (Task B), the per-case comparison table (Task D), and a one-line
recommendation for the user to confirm which k. Put it under
`output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/analysis/` (with the
other artifacts) or a `plans/experiment/20260707/` companion; a Markdown file is enough. If
the user wants slides, offer the `md-to-html` skill (deck mode) — do not build
HTML unless asked.

**Appendix — actual A/B execution (follow-up, only if asked / time allows):**
running the new policy requires a CODE CHANGE — there is currently **no
size-proportional `batch_tl_mode`**. `resolve_per_step_tl` supports only
`"constant"` and `"linear"`, and it does not even receive per-window `ntf`. To
run `TL = k · ntf` you must: add a `"proportional"` (or similar) `BatchTlMode` in
`step_tl_resolver.py` + `sw_cp/option.py`, pass the per-window `ntf` list into
the resolver from `sw_cp/dispatcher.py`, and add a `k` option. Then A/B: new
policy (chosen k) vs existing due-window policy on the 270 (or a subset),
comparing final objective / RPDf — this is investigation §8 item 5 (the real
proof; offline replay is directional only). Flag this scope to the user before
doing it; it is NOT needed for the meeting doc itself.

---

## 9. Caveats to state in the meeting (from §3.3)

- k is derived from **unfixed=2 only**; single global k under-budgets high-unfixed
  CPs (accepted — revisit if it loses).
- k varies strongly with **T** (difficulty), weakly with size; single k is a
  deliberate simplification.
- All k-for-capture numbers are **offline-replay** (ignore sequential window
  coupling) — directional; real proof is the end-to-end A/B.
- p ≥ 95 targets exceed the 120 s cap (not observable) — not candidates.

---

## 10. Environment / conventions

- `uv run python …` for all Python; `uv run ruff check` after any code edit.
- **96 physical cores** (nproc shows 192 logical — never size runs against 192).
- Do **not** git commit; leave changes unstaged (user commits manually).
- If a subagent is used for edits: **never let it run git** (a stray git checkout
  once destroyed uncommitted work).
- Analysis toolkit: `scripts/20260706/` (`k_for_capture.py` now has a
  `--scenario` filter — REQUIRED when pooling run dirs that contain other
  scenarios, e.g. leftover u4_pf2; `analyze_tl_policy.py :: collect_rows` is the
  window-row parser to reuse).

## 11. Key file map

- Coefficient results: `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` §3.3;
  `output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/analysis/k_for_capture_270_*.{md,csv}`.
- Existing per-CP TL: `src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py`;
  call site `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py:~130`;
  per-pass budget origin `src/ffc_ddw_sum_et/orchestration/controller.py:~2126`.
- ntf definition: `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py:410-419`;
  per-window data via `scripts/20260706/analyze_tl_policy.py :: collect_rows`.
- Makespan proportional template: `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/`
  (locate exact formula).
- SW-CP options / batch_tl_mode: `src/ffc_ddw_sum_et/algorithm/sw_cp/option.py`.
