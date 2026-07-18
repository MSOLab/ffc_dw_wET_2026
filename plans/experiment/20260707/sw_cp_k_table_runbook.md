# Runbook — build the combined SW-CP k-for-capture table (u2_pf2, 270 instances)

**Purpose:** self-contained instructions so a *fresh* conversation can, once the
background profiling run finishes, produce the combined `(p) × (A/B1/B2)` k-table
across the whole n∈{50,100,150,200} grid under the **u2_pf2** scenario and record
it. Say "이 파일 내용대로 해" and follow top to bottom.

**Scope note (2026-07-07):** the table covers **u2_pf2 only**. The u4_pf2
full-grid computation was attempted twice on bigN and abandoned both times, and
is NOT being resumed (deferred until a separate decision). Two partial/stale
u4_pf2 dirs exist — do not pool either:
- `…_bigN/20260707T022259_167411/u4_pf2` — 29/190 (original run; its tmux window
  was killed). **This is the same dir whose `u2_pf2` (190) we DO use**, so the
  u4_pf2 here rides along unless filtered out (see §2 `--scenario`).
- `…_bigN/20260707T140332_583918/u4_pf2` — 15/190 (deliberate re-run, then
  stopped by the user). Not referenced anywhere below.

Only the u2_pf2 scenario is in scope. The §2 commands MUST pass
`--scenario u2_pf2`; the n=50 dir also holds a complete out-of-scope `u4_pf2`
(68).

**Representative-instance policy (new, 2026-07-07):** one instance per
`(n, c, m, T, R, W)` cell, scanning `rep0..rep4` and taking the **first
non-optimal rep** (`obj_value > obj_bound`); a cell is dropped only if **all 5
reps** are optimal. This yields **270 instances** (vs the old rep0-only policy's
258): 258 cells still use rep0, **12 "rescued" cells** use a later rep (rep0 was
optimal but a later rep wasn't), and 18 cells remain dropped (all 5 reps optimal,
all at `T=0.2, R=1.0`). The 12 rescued instances are computed in a separate
top-up run because the original n=50 and bigN runs were already done under the
old rep0-only selection.

**Parent context (read if unfamiliar):**
`plans/experiment/20260705/sw_cp_tl_policy_investigation.md` (full investigation; see §1
philosophy, §3.2 results, §8 remaining work) and
`scripts/20260706/k_capture_methods.md` (what A / B1 / B2 mean, in Korean).

---

## 0. One-liner

Pool the SW-CP windows from **three thread=8 u2_pf2 runs** (n=50 rep0 + n≥100
rep0 + 12 rescued non-rep0) and, for each capture target `p ∈ {50,80,90,95,99}`,
report the size-proportional multiplier `k` (in
`TL = k · non_time_fixed_op_count`, seconds/op) under three aggregation bases
**A / B1 / B2**. Operational choice is **B2**; A and B1 are reference. Then
record the table in the investigation doc.

---

## 1. Preconditions — confirm ALL THREE runs are complete

Three run directories feed the table (all **u2_pf2 only**, thread=8, worker=12
= 96 PHYSICAL cores, cp_tl=120, step_size=1, unfixed=2, pf=2/2, PF1, batch=m,
rj=rtf_only):

| run | path | instances | note |
|---|---|---|---|
| n=50 t8 (rep0) | `output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/` | 68 | already done |
| n≥100 t8 (rep0) | `output/20260705_sw_cp_tl_profile_t8_bigN/20260707T022259_167411/` | 190 | u2_pf2 done; u4_pf2 abandoned at 29/190 (out of scope) |
| rescued-12 t8 | `output/20260707_sw_cp_tl_profile_t8_rescued12/20260707T150918_895893/` | 12 | the run being waited on |

Total pooled = 68 + 190 + 12 = **270** u2_pf2 instances. They MUST all be
thread=8 for the pooled table to be consistent (do not mix in the 1-thread run
`20260706T005339_880205`).

**Check all three runs' u2_pf2 scenario is complete:**

```sh
cd /home/hjt/code/ffc_dw_wET_2026
pgrep -cf 'rescued12' && echo "rescued12 STILL RUNNING — wait" || echo "rescued12 process gone"
for pair in \
  "output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214:68" \
  "output/20260705_sw_cp_tl_profile_t8_bigN/20260707T022259_167411:190" \
  "output/20260707_sw_cp_tl_profile_t8_rescued12/20260707T150918_895893:12"; do
  R="${pair%:*}"; want="${pair#*:}"
  n=$(find "$R/u2_pf2" -name '*_instance_result.yaml' 2>/dev/null | wc -l)
  echo "u2_pf2: $n / $want  ($R)"
done
```

- If `pgrep` still finds the rescued12 process, or any u2_pf2 count is below its
  target → **not done, stop and wait.**
- Only proceed when all three show their target count and the rescued12 process
  is gone.
- Do **not** check or wait for u4_pf2 — it is out of scope (bigN u4_pf2 is
  intentionally incomplete).

---

## 2. Produce the table

```sh
cd /home/hjt/code/ffc_dw_wET_2026
uv run python scripts/20260706/k_for_capture.py --scenario u2_pf2 \
  output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/ \
  output/20260705_sw_cp_tl_profile_t8_bigN/20260707T022259_167411/ \
  output/20260707_sw_cp_tl_profile_t8_rescued12/20260707T150918_895893/
```

⚠️ **`--scenario u2_pf2` is REQUIRED, not optional.** `k_for_capture.py` pools
*every scenario physically present* in each run dir it is given. Two of these
dirs also contain `u4_pf2` results that are OUT OF SCOPE here: the n=50 dir has a
complete `u4_pf2` (68 instances), and the bigN dir has a stale/partial `u4_pf2`
(29 instances, from the abandoned run). Without the filter the pool becomes
u2_pf2 (270) **+ u4_pf2 (97) = 367 instances**, silently contaminating the table.
With `--scenario u2_pf2` the pool is exactly the 270 u2_pf2 instances (verify the
printed `scenario filter: 'u2_pf2' … pooled instances: 270` line).

`k_for_capture.py` accepts multiple run dirs and, after the scenario filter,
**pools all windows** (it also filters to `I>0` internally). It prints one table
row per `p`, columns:
`A k | A TL@med | B2 k | B2 TL@med | B1 medk | B1 P75k | B1 P90k | B1 TL@med(P90)`.

**Also capture per-cohort context** so size effects are visible (the pooled
`median_ntf` differs from n=50-only, so `TL@med` shifts — the *k in s/op* is the
size-invariant number, report that as primary):

```sh
# n=50 rep0 only (for comparison with the already-recorded n=50 table):
uv run python scripts/20260706/k_for_capture.py --scenario u2_pf2 \
  output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/
# n>=100 rep0 only:
uv run python scripts/20260706/k_for_capture.py --scenario u2_pf2 \
  output/20260705_sw_cp_tl_profile_t8_bigN/20260707T022259_167411/
# rescued-12 only (small set, expect noisy k — context only):
uv run python scripts/20260706/k_for_capture.py --scenario u2_pf2 \
  output/20260707_sw_cp_tl_profile_t8_rescued12/20260707T150918_895893/
```

---

## 3. How to read it (A / B1 / B2, and the units)

Per window: `ntf_i` = `non_time_fixed_op_count`; `I_i` = achievable UB
improvement at the 120 s cap; `captured_i(τ)` = improvement by time τ; policy
`τ_i = k · ntf_i`.

- **A (full-sweep total objective, I-weighted):** k s.t.
  `Σ captured_i(k·ntf_i) / Σ I_i = p`. Big windows dominate → smallest k.
- **B1 (per-window required-k distribution):** `k_i = t_p^i / ntf_i`; report
  median / P75 / P90. P90 ⇒ 90% of windows reach their own p%.
- **B2 (per-window unweighted-mean fraction):** k s.t.
  `mean_i[captured_i(k·ntf_i)/I_i] = p`. Every window equal → largest k.
  **This is the operational choice** (preserves the hardest-to-squeeze windows).

`k` is in **seconds per op**. `TL@med = k · median_ntf` is only an illustrative
translation to seconds for a typical window (vs the current fixed 120 s).
**Report `k` (s/op) as the primary number**, TL@med as secondary.

⚠️ **Measurement limit:** `I_i` is defined at the 120 s cap, so any target whose
implied TL exceeds 120 s (typically p=95, 99) means "you basically cannot reduce
the cap" — the true requirement may be larger than shown (not observable beyond
120 s). p ≤ 90 (esp. p ≤ 80) is the trustworthy, TL-reduction regime.

⚠️ **Offline replay:** all three bases replay the observed within-window curve
under a hypothetical shorter clock; they ignore that cutting window i early
shifts window i+1's start (sequential coupling). Directional evidence only — real
proof is the end-to-end A/B (investigation §8 item 5).

---

## 4. Record the result

Append a **§3.3 "Full-grid k-for-capture table (n=50–200, thread=8, u2_pf2)"** to
`plans/experiment/20260705/sw_cp_tl_policy_investigation.md`, containing:

1. The pooled table (p × {A k, B2 k, B1 med/P75/P90 k}, in s/op) + the pooled
   `median_ntf` and window count, over **270 u2_pf2 instances** (68 n=50 rep0 +
   190 n≥100 rep0 + 12 rescued non-rep0). State the new representative policy
   (rep0..rep4 scan, first non-optimal rep) and that u4_pf2 is out of scope.
2. One line each for n=50-rep0-only vs n≥100-rep0-only (does the required `k`
   grow with n? i.e. is `k` roughly size-invariant, which would *support* a
   size-proportional `TL = k·ntf`, or does it drift with difficulty, echoing
   §3.1 obs 1 / §3.2 that difficulty—not size—drives budget need?). The
   rescued-12 cohort is too small for a standalone k reading — mention it only
   as a sanity check that those 12 cells' windows fall within the pooled
   distribution.
3. A one-sentence takeaway for the **operational B2 k at p=80 and p=90** — the
   "how small can the TL be" answer.

Do **not** git commit (user commits manually). Leave changes unstaged.

---

## 5. Reference — n=50-rep0-only result already computed (thread=8)

From the n=50 t8 run alone (the 68 rep0 instances; 1446 I>0 windows,
median_ntf=180). The 3 rescued n=50 instances (idx 23, 112, 206) are in the
separate rescued-12 run, not here. Use to sanity-check that the combined run
reproduces these for the n=50-rep0 slice:

| p% | A k | A TL@med | B2 k | B2 TL@med | B1 medk | B1 P75k | B1 P90k |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 0.002 | 0.3 s | 0.004 | 0.8 s | 0.005 | 0.025 | 0.195 |
| 80 | 0.078 | 14 s | 0.151 | 27 s | 0.050 | 0.500 | 0.964 |
| 90 | 0.500 | 90 s | 0.500 | 90 s | 0.231 | 0.627 | 1.000 |
| 95 | 0.800 | 144 s | 0.899 | 162 s | 0.379 | 0.667 | 1.001 |
| 99 | 1.333 | 240 s | 1.333 | 240 s | 0.400 | 0.671 | 1.001 |

n=50 reading: **~80% of improvement captured with TL ≈ 15–27 s (median window)**
vs the current 120 s — a ~5–8× reduction; **90% needs ~90 s; 95%+ exceeds the
120 s cap** (can't reduce). Big B1 spread (P90 ≫ median) = window difficulty
varies a lot.

---

## 6. Key facts / environment (for the fresh conversation)

- `uv run python …` for all Python; `uv run ruff check` after any edit.
- **96 physical cores** (nproc shows 192 logical via hyperthreading). Never size
  runs against 192 — hyperthreaded timings are non-comparable (a past run did this
  and had to be discarded). The two runs above both use 12 workers × 8 threads.
- Analysis toolkit lives in `scripts/20260706/`
  (`k_for_capture.py`, `analyze_tl_policy.py`, `plot_ub_lb_vs_time.py`,
  `ANALYSIS_DESIGN.md`, `k_capture_methods.md`); the scripts import each other
  and must stay in the same dir. Generated CSV/PNG go to the gitignored
  `analysis/20260705_sw_cp_tl_profile/`.
- Optimality judgment (if needed): `instance_result.yaml` `obj_value` (UB) vs
  `obj_bound` (LB); LB is loose (AGENTS.md "Optimality-judgment field").
- If a subagent is used for edits: **never let it run git** (a stray git checkout
  once destroyed uncommitted work).
