# `<instance>_obj_log.json` — schema and how to read it

How to read a run's objective trajectory correctly. The **producer** is
`orchestration/ffcddw_single_instance_runner._save_obj_log` (that docstring owns
the on-disk layout); this document owns the *reader* side — the traps a consumer
has to get right and which API answers which question.

## Layout

One line, no whitespace:

```json
{"obj_value":{"name":"obj_value","data":{"<t>":v,…},"notes":{"<t>":"<step_idx>-<subroutine>",…}},
 "obj_bound":{"name":"obj_bound","data":{…},"notes":{…}}}
```

- `t` — controller-frame elapsed seconds, `repr(float)` string keys.
- `data` — every progress point the step's `progress_log` emitted.
- `notes` — marks the **endpoint** of a controller step. The label format is
  `"<step_idx>-<subroutine_name>"`, set by routix
  `_get_call_context_of_current_method`. `step_idx` is 1-based and counts *flow
  positions*, not registrations.
- Duplicate timestamps: first writer wins.
- `RunMode.RESUME` prepends the base run's whole trajectory, so a resume log
  reads as one continuous series from `t = 0`.

Artifact keys: `obj_log_json`, and `csr_inner_obj_log_json` for the coarse-scale
CSR inner-solve trajectory.

## Three traps

**1. A step that never registered is simply absent.**
Numbering passes by *order of appearance* therefore slides pass k+1 into the
`pass{k}` slot whenever the flow ended early. Always key on the `step_idx`
parsed from the label, never on position. This only bites flows that repeat the
same step, which is why it went unnoticed until the 2026-08-01 NEH pass-chain
analysis.

**2. A note's value is that step's own output, not the running incumbent.**
It *rises* whenever a step lands worse than what the solution manager already
held — 775 of 1440 instances in run `20260801T183302_770739`. "What did this
step produce?" and "what did the next step inherit?" are different series and
both are legitimate, but nothing in the payload says which one a reader picked.
`StepRegistration` carries both (`own_obj` / `incumbent`) so the choice has to
be written down at the call site.

**3. `notes` is per-series, and the structured loader drops a series that has
none.** `_build_calls_for_series` returns `()` on empty notes, so a series
carrying no per-point notes vanishes entirely — `sw_cp`'s `obj_bound` is exactly
that case, and the LB silently disappears from anything built on
`load_instance_progression`. For the same reason, `data` points *after* the last
note are dropped: the segment cursor stops at the final endpoint.

## Which reader to use

| you need | use |
|---|---|
| step boundaries: what each step produced / what the next inherited | `report.obj_log_loader.build_step_registrations(payload)` |
| per-step segmented trajectory for the chart writers | `report.obj_log_loader.load_instance_progression(...)` |
| the full raw trajectory, a series without notes, or per-point inner-step labels | `load_raw_obj_log(path)` + your own parsing |

The third row is a legitimate choice, not a shortcut — but say **why** in the
consuming module's docstring, because the default expectation is that a reader
uses the loader.

## Current consumers

| consumer | reader | note |
|---|---|---|
| `scripts/20260801/analyze_neh_pass_chain.py` | `build_step_registrations` | needs both `own_obj` and `incumbent`, explicitly |
| `scripts/20260801/analyze_neh_step_quality.py` | `build_step_registrations` | the step's own output |
| `report/post_run_chart_writer.py` | `iter_scenario_instance_progressions` | segmented per-step chart input |
| `scripts/build_cross_run_flow_chart.py` | `load_instance_progression` | same, across runs — its own discovery helper, no `ArtifactLayout` |
| `scripts/20260706/analyze_tl_policy.py` | raw | notes are a time anchor for step_log YAMLs; needs the raw UB map |
| `scripts/20260706/plot_ub_lb_vs_time.py` | raw | plots the LB, which trap 3 would erase |
| `scripts/20260726/analyze_csr_init_tl_curve.py` | raw | reads per-point `inner-NN-<idx>-<step>` notes |

## See also

- Optimality judgment for a run (`obj_value` vs `obj_bound` in
  `<instance>_instance_result.yaml`) — `AGENTS.md` §"Optimality-judgment field".
- Output directory schema and artifact lifecycle —
  `docs/io/20260429_artifact_manager.md`.
