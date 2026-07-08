# SW-CP TL-policy analysis — design spec

Goal (plan §8 item 4): quantify, per SW-CP window, **how fast objective (UB)
improvement is captured over time**, and use it to compare a **constant** vs a
**size-proportional** per-window time-limit at equal total budget.

Framed as objective-improvement only (plan §1) — LB is not used as a metric.

## Inputs (per instance, per scenario)

For a run dir `output/.../<run_id>/<scenario>/<instance>/`:

- `<instance>_obj_log.json` — raw maps `obj_value.data` (UB, `{t_str: ub}`),
  `obj_value.notes` (`{t_str: "<idx>-<subroutine>"}` step-ends). Read RAW (not
  `obj_log_loader.py`; it drops LB — but we only need UB here).
- `progress/<N>-sw_cp_step_log.yaml` — YAML list, one row per window, fields:
  `step`, `unfixed_batch_start_idx`, `unfixed_op_count`,
  `profile_fixed_op_count`, `non_time_fixed_op_count`, `sub_job_count`,
  `incumbent_obj_before`, `cp_obj`, `incumbent_obj_after`, `accepted`, `status`,
  `wall_seconds`, `elapsed_time` (**cumulative** algorithm-frame secs to window
  end), `TL`.

Cell params (n,c,m,T,R,W,rep) parse from the instance name
`Instance_{n}_{c}_{m}_{T}_{R}_{W}_Rep{rep}` (T,R comma-decimal). Reuse the regex
in `scripts/select_representative_instances.py`.

## Step A — per-window UB(t) segmentation

Map each window's controller-frame time span (same anchor as the plotter):
1. `N` = int prefix of the step_log filename; `note_t` = time of the
   `obj_value.notes` entry labeled `"{N}-sw_cp"` (the sw_cp step end).
2. `offset = note_t - rows[-1].elapsed_time`.
3. window i span = `[offset + rows[i-1].elapsed_time, offset + rows[i].elapsed_time]`
   (window 0 starts at `offset + 0`). Collect obj_log UB points with `t` in that
   half-open span; prepend the window-start incumbent
   (`t=window_start, ub=incumbent_obj_before`) so every window curve starts at
   its true UB(0).

Result: for each window, a within-window curve `(τ, ub)` with `τ` = seconds
since window start, `τ ∈ [0, wall_seconds]`.

## Step B — time-to-p% per window

Per window: `I = incumbent_obj_before - incumbent_obj_after` (achievable
improvement; `>= 0`, `accepted` rows only — for rejected/no-improvement windows
set `I=0`).
- For `p ∈ {50,80,90,95,99}`: `target = incumbent_obj_before - (p/100)*I`;
  `t_p` = first `τ` in the window curve with `ub <= target`; report `t_p`
  (absolute secs) and `t_p / wall_seconds` (fraction).
- Also record `reached_cap` = `wall_seconds >= 0.98*TL` (did the window use its
  budget) and `I` itself.
- Windows with `I == 0` contribute to the `reached_cap`/regime analysis but are
  excluded from time-to-p% rows (undefined).

Emit **per-window CSV**: one row per (scenario, instance, window) with all cell
params, `unfixed_op_count`, `profile_fixed_op_count`, `non_time_fixed_op_count`,
`step`/window_index, `wall_seconds`, `TL`, `reached_cap`, `I`, and `t_50..t_99`
(abs + fraction).

## Step C — regressions (report coefficients + R²)

Rows = windows with `I>0`. Use statsmodels OLS if available, else numpy lstsq.
1. **Single-coefficient (decision §2):** `t_90 ~ non_time_fixed_op_count`.
2. **Diagnostic (two-coefficient):** `t_90 ~ unfixed_op_count +
   profile_fixed_op_count` — report whether the two coefficients differ
   materially (informs whether to ever split k).
3. **Difficulty-augmented (plan §3.1 obs 1):** `t_90 ~ non_time_fixed + T + m +
   window_index` — does instance difficulty / window index explain time-to-p%
   beyond op-count? Report each coefficient's sign/size.
Repeat headline (1) for p ∈ {80,90,95} to show stability.

Also a **regime model:** logistic (or just a fraction table) of
`reached_cap ~ T + m + non_time_fixed` — plan §3.1 obs 1 says difficulty (T,m),
not size, sets whether a window uses its budget.

## Step D — equal-budget captured-improvement comparison (headline)

Per instance (fixing a per-instance budget `B = C * num_windows`, with `C` = the
current constant cap, e.g. 120 s):
- **constant policy:** `τ_i = C` for every window.
- **proportional policy:** `τ_i = k * non_time_fixed_i`, `k = B / Σ
  non_time_fixed_i` (so Σ τ_i = B, equal total budget).
- For each window, `captured_i(τ)` = UB improvement achieved by time `τ` within
  that window, read from the Step-A curve (clamp to `[0, wall_seconds]`; if
  `τ >= wall_seconds` the window captures its full `I`).
- Report **Σ captured_i** for constant vs proportional, per instance and
  aggregated by (n,c,scenario). Headline = does proportional capture more total
  improvement at equal budget?

**CAVEAT to state in output:** this is an OFFLINE approximation — SW-CP windows
are sequential, so cutting window i early shifts window i+1's starting
incumbent. Per-window captured sums ignore that coupling. The real proof is the
end-to-end A/B (plan §8 item 5); this comparison is directional evidence only.
Emit this caveat into the report text/HTML.

## Outputs (all under `analysis/20260705_sw_cp_tl_profile/`)

- `window_metrics.csv` (Step B table) — the primary artifact.
- `regression_summary.txt` (or `.md`) — Step C coefficients + R².
- `captured_comparison.csv` — Step D per-instance + aggregate.
- PNGs: (a) `t_90` vs `non_time_fixed` scatter colored by scenario w/ fit line;
  (b) captured-improvement constant-vs-proportional bar per (n,c). Reuse
  matplotlib, PNG dpi ~120.

## CLI

`uv run python scripts/20260706/analyze_tl_policy.py <run_dir> [<run_dir> ...] [--out-dir DIR] [--constant-cap 120]`
Discover every `<scenario>/<instance>/` with both an obj_log and a step_log;
skip (with a `log`/print) any instance missing either. Dependency-light
(json, csv, yaml, numpy, matplotlib; statsmodels optional).
