# Plan — method-mean scatter: carry-forward (last observed value for unreached instances)

**Purpose:** written self-contained so a *fresh* conversation can read this file
alone and execute it. Fix a bug in the
`multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html` chart where the
endpoint (mean RPDf / mean Time% at each method's end) is averaged over **only the
instances that reached that method**. When the last method is skipped by many
instances due to tight time limits, the endpoint drifts far from the true
full-sample mean. Switch to a **carry-forward (intent-to-treat)** scheme: at each
method point, instances that did not reach it carry forward the **last observed
value (obj, time, rpdf) from the previous method** into the average.
If told "do what this file says", execute.

**Do NOT:** `git add`/commit (keep changes unstaged).
Do not let subagents run git (a stray checkout once deleted work).
Use `uv run python`; after editing code run `uv run ruff check` / `uv run ruff format` if needed.
**Do NOT run experiments (multi-instance runs)** — only regenerate charts from existing run dirs.

---

## 0. Locked decisions

- **Fix approach: carry-forward.** When computing each method (call_index) point,
  instances that reached the method use that method's (time_pct, rpdf, obj);
  instances that did not reach it **carry forward the last observed value from
  the most recent method they did reach** into the average. This is the
  intent-to-treat semantics of an RCT — "follow every allocated instance to the
  end" — rather than a per-protocol analysis that only looks at reachers.
- **Keep the `drop_non_improving_methods` parameter, but flip its default from
  `True` → `False`.** A non-improving method still shows up as a horizontal
  segment ("spends time, quality flat") which is a useful diagnostic signal —
  exactly the "spending time without improving" case worth seeing. The parameter
  is retained as a knob so a caller that wants less noise can pass `True`. The
  sole call site (`post_run_chart_writer.py:234`) does not pass the argument, so
  the default flip alone switches the chart to showing every reached method. The
  `improves` judgment uses **only the obj of instances that actually reached**
  the method, never carry-forward values (a carry-forward value equals the
  previous method's obj, so it is never an "improvement").
- **Unify `instance_count` as the carry-forward total** (= the number of
  instances that entered the flow). The hover `instance_cnt` now means "instances
  still being tracked at this point", not "instances that reached this method".
  Keep the meaning consistent. (A separate `reached_count` column could be added
  later, but is out of scope here; keep a single count. Hover label stays
  `instance_cnt`.)
- **Formula/symbol consistency:** RPDf formula `rpd_f(obj, ref) = 2*(obj-ref)/(obj+ref)`
  (`ffc_ddw_sum_et._calc.rpd_f`) is unchanged. Baseline ref = BKS_data (the
  `BKS_data` column of `benchmarks/PRA2017/pra2017_bks_table.csv`; identical to
  the `BKS` column of `pra2017_instance_table.csv` — verified all 1440 match).
- **time% = `global_end_sec / timelimit_sec`** (0–1 ratio, chart x-axis). On
  carry-forward, time% is also carried forward unchanged. (Verification:
  p25 endpoint mean time% = 91.68%, p50 endpoint = 91.54% → matches the expected
  91.7% / 91.6%.)

---

## 1. Current bug (verified)

**Target run:** `output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386`
(scenarios `s0_c5_p25`, `s0_c5_p50`; 1440 instances each)

The current `load_method_mean_metrics`
(`src/ffc_ddw_sum_et/report/method_mean_scatter.py`) averages each method's
(mean_time_pct, mean_rpdf) over **only the instances that reached it**. The last
method `solve_base_model_cpsat` has low reach rate under tight TL, so the endpoint
is distorted:

| Scenario | Endpoint reach count | Current mean RPDf | Current mean Time% | Expected (full mean) RPDf | Expected Time% |
|---|---|---|---|---|---|
| s0_c5_p25 | 1316/1440 | +1.6% | 99.3% | **-6.305%** | **91.7%** |
| s0_c5_p50 | 398/1440  | -22.6% | 97.4% | **-10.354%** | **91.6%** |

The expected values equal the 1440-instance mean of the per-scenario RPDf / time%
columns in the `analysis_wide` / `analysis_long` sheets of `*_report.xlsx`.
Recomputing with carry-forward makes the endpoint match the expected values **to
4 decimal places**:

- p25 endpoint → RPDf **-6.3051%**, time **91.6772%**
- p50 endpoint → RPDf **-10.3539%**, time **91.5415%**

Intermediate method points also shift slightly under carry-forward (few unreached
instances: ci=2 → 14, ci=3 → 17, ci=4 → 117/118), but the endpoint is where the
correction is largest.

---

## 2. Code change

### File to edit (1)

`src/ffc_ddw_sum_et/report/method_mean_scatter.py` —
the `load_method_mean_metrics` function (currently lines 28–115).

**Current structure (summary):**

```python
def load_method_mean_metrics(
    progressions: list[InstanceProgression],
    *,
    baseline_obj_by_instance: dict[str, float],
    drop_non_improving_methods: bool = True,   # <-- flip default to False
) -> list[dict[str, Any]]:
    ...
    prev_obj_by_instance: dict[str, float] = {}  # for improves judgment + carry
    candidates = []
    for ci in sorted_ci:
        method_name = method_order[ci]
        contributions = []  # (ins_id, t_pct, r, obj) — reached instances only
        improves = False
        for ins_id, methods in instance_data.items():
            for m_ci, m_name, t_pct, r, obj in methods:
                if m_ci == ci:
                    prior = prev_obj_by_instance.get(ins_id)
                    if prior is None or obj < prior:
                        improves = True
                    contributions.append((ins_id, t_pct, r, obj))
                    break
        if not contributions:
            continue
        for ins_id, _, _, obj in contributions:
            prev_obj_by_instance[ins_id] = obj
        time_pcts = [t for _, t, _, _ in contributions]
        rpdfs = [r for _, _, r, _ in contributions]
        candidates.append({
            "method": method_name,
            "improves": improves,
            "mean_time_pct": sum(time_pcts) / len(time_pcts),
            "mean_rpdf": sum(rpdfs) / len(rpdfs),
            "instance_count": len(time_pcts),  # reach count
        })
    if drop_non_improving_methods and candidates:   # now default-off
        last_idx = len(candidates) - 1
        kept = [c for i, c in enumerate(candidates)
                if c["improves"] or i == last_idx]
        candidates = kept
    return [{k: v for k, v in c.items() if k != "improves"} for c in candidates]
```

**After (carry-forward + default `drop_non_improving_methods=False` + `*` moved):**

```python
def load_method_mean_metrics(
    progressions: list[InstanceProgression],
    baseline_obj_by_instance: dict[str, float],
    *,
    drop_non_improving_methods: bool = False,   # flipped: show time-wasting steps
) -> list[dict[str, Any]]:
    ...
    # Per-instance last observed (time_pct, rpdf, obj) — carry-forward source
    prev_state_by_instance: dict[str, tuple[float, float, float]] = {}
    # Instances that entered the flow at least once (carry-forward eligible)
    active_instances: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for ci in sorted_ci:
        method_name = method_order[ci]
        reached: list[tuple[str, float, float, float]] = []  # actually reached
        improves = False
        for ins_id, methods in instance_data.items():
            found = None
            for m_ci, m_name, t_pct, r, obj in methods:
                if m_ci == ci:
                    found = (t_pct, r, obj)
                    break
            if found is not None:
                t_pct, r, obj = found
                prior_obj = prev_state_by_instance.get(ins_id, (None, None, None))[2]
                if prior_obj is None or obj < prior_obj:
                    improves = True
                reached.append((ins_id, t_pct, r, obj))
                active_instances.add(ins_id)
        if not reached:
            # No instance reached this method → no new info; a carry-forward
            # point would duplicate the previous point exactly. Skip to avoid
            # duplicate dots (distinct from "non-improving but reached").
            continue
        # carry-forward average: reached values + prev_state for unreached active
        time_pcts: list[float] = []
        rpdfs: list[float] = []
        reached_ids = {i for i, *_ in reached}
        # First, record the reached instances with their new values
        for ins_id, t_pct, r, obj in reached:
            time_pcts.append(t_pct)
            rpdfs.append(r)
            prev_state_by_instance[ins_id] = (t_pct, r, obj)
        # Then, carry forward prev_state for active instances not in reached
        for ins_id in active_instances:
            if ins_id not in reached_ids:
                ps = prev_state_by_instance.get(ins_id)
                if ps is not None:
                    time_pcts.append(ps[0])
                    rpdfs.append(ps[1])
        candidates.append({
            "method": method_name,
            "improves": improves,
            "mean_time_pct": sum(time_pcts) / len(time_pcts),
            "mean_rpdf": sum(rpdfs) / len(rpdfs),
            "instance_count": len(time_pcts),  # carry-forward total = active count
        })
    if drop_non_improving_methods and candidates:   # now opt-in, default off
        last_idx = len(candidates) - 1
        kept = [c for i, c in enumerate(candidates)
                if c["improves"] or i == last_idx]
        candidates = kept
    return [{k: v for k, v in c.items() if k != "improves"} for c in candidates]
```

**Caveats:**

1. **`active_instances` accumulates from the first method reached.** Instances
   that never reach `calc_mcf_lb_and_derive_full_sch` (ci=1) are treated as
   never having entered the flow and are excluded from the average — this
   matches current behavior (such instances are absent from `instance_data` in
   the first place because they lack a baseline ref or an empty obj_log).
2. **`improves` is judged from `reached` only.** A carry-forward value equals
   the previous method's obj, so `obj < prior_obj` cannot hold; including
   carry-forward in `improves` would only ever vote non-improving, which is
   meaningless. Judging improvement on reachers only preserves the semantics of
   the `drop_non_improving_methods=True` opt-in.
3. **`prev_state_by_instance` update order:** the `reached` loop updates
   `prev_state` with new values first; the carry-forward loop then reads
   `prev_state` only for ins_ids not in `reached`, so there is no double-count
   and no read-after-write hazard.
4. **Set computation:** build `reached_ids = {i for i, *_ in reached}` once and
   reuse, instead of recomputing the comprehension each iteration. (Readability
   over micro-perf — 1440 instances is negligible either way.)
5. **`if not reached: continue` (replaces the old `if not contributions`).**
   A method that no instance reached produces a carry-forward point identical
   to the previous method's point (same obj, same time, same rpdf for every
   active instance) → a duplicate dot. Skip it. This is distinct from a
   "non-improving but reached" method, which *does* produce a new (later time,
   same rpdf) horizontal segment — that one is kept under the new default.
6. **`instance_count` meaning changes:** it is now "tracked instances
   (carry-forward included)" rather than "reached instances". The hover label
   stays `instance_cnt=%{customdata[2]}`. (No chart HTML template change needed.)
7. **Default flip of `drop_non_improving_methods`.** The filter block and the
   `improves` field stay in the code so a caller can still opt into the old
   behavior by passing `True`. With the new default `False`, every reached
   method appears — including non-improving ones that spend time without
   improving quality (horizontal segment). The call site
   `post_run_chart_writer.py:234` passes no argument, so it automatically
   switches to the show-all behavior.

### Also changed

- `src/ffc_ddw_sum_et/report/post_run_chart_writer.py` — move `*` so all
  required args are positional-or-keyword (policy: required args before `*`,
  options after). The call site becomes
  `load_method_mean_metrics(progressions, baseline_map)` (positional
  `baseline_obj_by_instance`). `drop_non_improving_methods` stays keyword-only.

### Unchanged

- `src/ffc_ddw_sum_et/report/obj_log_loader.py` — `build_endpoint_df` unchanged.
- `src/ffc_ddw_sum_et/_calc.py` — `rpd_f` unchanged.
- Chart HTML template (`_HTML_TEMPLATE`) — unchanged.

---

## 3. Verification

### 3A. Code checks (not an experiment)

```bash
uv run ruff check src/ffc_ddw_sum_et/report/method_mean_scatter.py
# if needed
uv run ruff format src/ffc_ddw_sum_et/report/method_mean_scatter.py
```

### 3B. Existing unit tests

```bash
uv run pytest tests/report/test_post_run_chart_writer.py -x
```

`test_writes_both_html_artifacts` builds its fixture so both instances reach both
methods, so the carry-forward change does not affect it (every instance reaches
every method → carry-forward path never activates).
The skip-case tests are also unaffected.

### 3C. Regenerate charts from an existing run (post-process only, not an experiment)

```bash
uv run python scripts/build_subroutine_flow_charts.py \
    output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386
```

Parse the regenerated
`output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386/20260708T014624_039386_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`
payload and confirm the endpoints match the expected values:

```bash
uv run python -c "
import re, json
p = 'output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386/20260708T014624_039386_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html'
m = re.search(r'const payload = ({.*?});', open(p).read(), re.S)
pl = json.loads(m.group(1))
for t in pl['traces']:
    print(t['scenario'], 'endpoint RPDf=%.4f%%' % (t['y'][-1]*100), 'time=%.4f%%' % (t['x'][-1]*100), 'n=%d' % t['instance_count'][-1])
"
```

**Expected result:**
- `s0_c5_p25` endpoint: RPDf ≈ -6.3051%, time ≈ 91.68%, n=1440
- `s0_c5_p50` endpoint: RPDf ≈ -10.3539%, time ≈ 91.54%, n=1440

### 3D. (Optional) Cross-check against the analysis_wide sheet

Confirm the 1440-instance mean of the `RPDf_s0_c5_p25` / `RPDf_s0_c5_p50`
columns in `*_report.xlsx`'s `analysis_wide` sheet matches the chart endpoint
mean_rpdf (= -6.3051% / -10.3539%). For time%, compare with the mean of the
`time%` column in `analysis_long` (= 91.68% / 91.56%). Read the xlsx with
`openpyxl` (already added via `uv add --dev`).

### 3E. (Optional) Verify non-improving methods now appear

Find a run whose flow contains a mid-flow non-improving method (e.g. one
produced from `metadata/20260505/mcf_lb_init_37_config.yaml`, which chains
`apply_lb_by_mcf` / `heuristic_last_stage_only_sch_from_mcf_lb` /
`build_full_sch_from_last_stage_only_sch`). Regenerate its chart and confirm
those methods now appear as horizontal segments (time increases, RPDf flat)
instead of being dropped. If no such run exists under `output/`, skip this
check — it is informational, not blocking.

---

## 4. Done criteria

1. `load_method_mean_metrics` computes endpoints via carry-forward (code change done).
2. `drop_non_improving_methods` default is `False` and `*` is moved so
   `baseline_obj_by_instance` is positional; the call site at
   `post_run_chart_writer.py` passes it positionally and shows every reached
   method (including non-improving ones).
3. `uv run ruff check` passes.
4. `uv run pytest tests/report/test_post_run_chart_writer.py -x` passes
   (no regression in existing tests).
5. After regenerating charts for the existing run
   `output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386`, the endpoints match
   the expected values (-6.3051% / -10.3539%, 91.68% / 91.54%).
6. All changes remain **unstaged** (no `git add`/commit).

---

## 5. TODO.md update (optional)

This change is executed immediately, not deferred, so it is not recorded in
TODO.md. However, after introducing carry-forward, if a "reached count vs
tracked count" display split (adding a separate `reached_count` column) is
desired, that is a follow-up that can be recorded in TODO.md.
This scope unifies on a single count (`instance_count` = carry-forward total).
