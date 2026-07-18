# Resume-from-base: run a shared prefix once, resume each scenario from it

**Date:** 2026-07-09 · **Branch:** `20260708_more_timelimit`
**Status:** ✅ **IMPLEMENTED & VERIFIED (2026-07-09).** Base + resume runs pass
end-to-end; prefix skipped, incumbent + LB restored, TL budget preserved exactly.
See § 5 for the as-run results and § 7 for the one bug found & fixed during the
run. Not committed (per working agreement — awaiting review).

**Goal:** run the common subroutine prefix (`calc_mcf_lb_and_derive_full_sch →
run_flip_makespan_cp_from_incumbent → neh_cp`) **once** as a *base* run, then let
each SW-CP TL scenario **resume** from that base's per-instance incumbent instead
of recomputing the prefix. Port the pattern already implemented in the sibling
repo `../hybridflowshop`.

Reference implementation (verified): `../hybridflowshop/main.py`,
`hfs_single_instance_runner.py` (`_try_apply_resume`, `run`),
`hybridflowshop/controller/controller_core.py::run(flow_resume_idx)`,
`hybridflowshop/resume/validator.py`, `hybridflowshop/io_solution.py`.

---

## 0. Why (cost)

Each case scenario's flow is `[mcf_lb, flip, neh_cp | sw_cp, base_cpsat]`. The
prefix TL budget is `0.009nc (flip) + 0.027nc (neh_cp) ≈ 0.036nc` of the
per-scenario `0.09nc` total — **~40 %**. With 7 scenarios the prefix is currently
recomputed 7× identically. Running it once and resuming removes that waste.

---

## 1. Topology (differs slightly from hybridflowshop)

- **Base run** — `metadata/20260709/sw_cp_tl_test_base.yaml`, **1 scenario**
  (`mcf_lb_fmm_neh_cp`), 3-step prefix flow. Emits per-instance
  `<ins>_solution.json`, `<ins>_obj_log.json`, `<ins>_instance_result.yaml`
  under `output/20260709_sw_cp_tl_test/<base_run_id>/mcf_lb_fmm_neh_cp/<ins>/`.
- **Cases run** — `metadata/20260709/sw_cp_tl_test_cases.yaml`, `run_mode: RESUME`,
  **7 scenarios**, each flow = prefix (3) + tail (`incremental_sw_cp`,
  `solve_base_model_cpsat`). **All 7 resume from the single base scenario dir.**
- So the resume source (`resume_root`) is **one base scenario dir**; there is no
  per-scenario-name indirection (hybridflowshop maps scenario→same-named prior
  scenario; we don't need that). `flow_resume_idx = 3` for every case scenario.

---

## 2. Verified facts about the current code (recon complete)

- **routix already has the scaffold** (vendored dependency): `RunMode.RESUME`,
  `flow_resume_idx` on both runners, `MultiScenarioRunner`/our `reporting.py:610`
  calls `set_flow_resume_idx(scenario_config.get("flow_resume_idx", 0))` in RESUME
  mode, and `SubroutineFlowValidator.validate_subroutine_flow_prefix(resume_flow,
  current_flow) -> int` (returns `len(resume_flow)`).
- **But the FFcDDW side is inert**: (a) `main.py` never sets `run_mode: RESUME`
  path, never injects `resume_root`, and drops any `flow_resume_idx` key when it
  rebuilds `scenario_config` (`main.py:105-113`); (b)
  `FFcDDWSingleInstanceRunner.run()` only runs the controller under `FULL_RUN`
  (`ffcddw_single_instance_runner.py:198`); (c) the controller
  (`controller_core.py::run`, `:332`) ignores `flow_resume_idx` and always runs
  the whole flow; (d) **no JSON→schedule loader exists**.
- **Controller state**: no stored LB attribute — the running LB is derived from
  `solution_manager.best_obj_bound` via `get_current_valid_lb()`
  (`controller_core.py:129`). Registration goes through
  `_register(report, solution, *, progress_log=())` (`:250`), which wraps into
  `FFcDDWSubroutineReport`. Incumbent = `solution_manager.get_incumbent()`.
- **Solution save**: `_save_solution` → `dump_solution_json` →
  `<ins>_solution.json` with keys `jobs`, `stages`, `machinesPerStage`,
  `operations[]` (`{stage,machine,job,start,end}`), `objValue`, `objBound`
  (`io/schedule_json.py:47`, keys in `io/schedule_keys.py`).
- **Schedule rebuild API**: `FFcSchedule(jobs, stages, machines_per_stage)` then
  `add_ops_times_2_mc(stage_id, mc_id, job_id, start_time, end_time)` per op
  (`solution/ffc_schedule.py:29,395`). Feasibility guard available:
  `controller.check_feasibility(schedule.get_jik_2_start_time_map())`
  (`controller_core.py:340`).
- **instance_result.yaml** (`InstanceResult`, `ffcddw_single_instance_runner.py:43`)
  carries `elapsed_time`, `obj_value`, `obj_bound`, `first_obj_value`,
  `first_obj_bound`, `work_status`, … — the fields resume needs for timer/LB.
- **No flow cache is written today.** hybridflowshop writes
  `subroutine_flow.yaml` per scenario; we will add the same (small) so resume can
  validate the prefix. `main.py:67` copies the base config into the run root, but
  relying on its basename is fragile — the flow cache is cleaner.
- **On-disk layout has NO `results/` segment** and uses `.json` (not `.yaml`):
  files live directly in `{scenario_dir}/{ins}/`. So routix's default
  `_check_file_existence` (which expects `{ins}/results/{ins}_*.yaml|csv`)
  **must be replaced** by an override.

### Safety assumption (reasoned pre-run; ✅ confirmed by the § 5 run)

Skipping the prefix and only restoring the incumbent is safe because no tail step
reads controller state produced by a prefix step other than the incumbent + LB:
`incremental_sw_cp` partitions `get_incumbent()`; `solve_base_model_cpsat` seeds
CP-SAT from the incumbent; the global LB is restored by registering the incumbent
report with its `obj_bound` (→ `best_obj_bound`). The mcf_lb diagnostic slots stay
`None` on resume (the resume run's `instance_result.yaml` won't carry mcf_lb
diagnostics — they live in the base run). ⇒ `method_names_to_run_before_resume`
is **empty** for now (attribute added for extensibility only).

---

## 3. Design (KISS; smallest correct change) — as built

Data flow on resume, per instance:

```
base <ins>_solution.json  ──load_schedule_json──▶ FFcSchedule (+objValue/objBound, unused)
base <ins>_instance_result.yaml ──▶ obj_value, obj_bound (global LB), elapsed_time
        │
   FFcDDWMultiInstanceRunner._load_resume_data()  (reads files, injects into SIRs)
     → runner.resume_solution = FFcDDWSolution(schedule, obj_value, obj_bound)
     → runner.resume_elapsed_time = manifest.elapsed_time
        │
   FFcDDWSingleInstanceRunner._apply_resume():
       ctrlr.timer.set_start_time(now - elapsed_time)   # 1) preserve TL budget (first)
       ctrlr.check_feasibility(start_time_map)          # 2) guard
       ctrlr.seed_resume_incumbent(solution, obj_value, obj_bound)  # 3) register incumbent+LB
        │
   ctrlr.run(flow_resume_idx=3)   # skip prefix, run tail only, then post_run_process
        │
   post_run_process → _persist_run_artifacts  (RESUME persists like FULL_RUN)
```

Two refinements vs the first draft of this plan:

- **obj_value / obj_bound come from the manifest, not the solution JSON.** The
  incumbent `<ins>_solution.json` carries `objBound = None` (the global LB lives
  only in the report history, surfaced as `instance_result.yaml:obj_bound`). So
  `_load_resume_data` sources both `obj_value` and `obj_bound` from the manifest
  and stuffs them into the injected `FFcDDWSolution`. `load_schedule_json` returns
  the JSON's obj fields too, but they are ignored for the incumbent.
- **A single registration (the incumbent), via a dedicated controller method.**
  The draft planned two `_register` calls (an init marker + the incumbent) issued
  directly from the SIR. As built there is **one** call —
  `ctrlr.seed_resume_incumbent(...)` — which registers the incumbent with
  `obj_value` + `obj_bound` inside a pushed method context (see § 7 for why the
  context push is load-bearing). `first_obj_value` in the resume manifest is
  therefore the base incumbent's obj (there is no separate init record) — faithful
  enough: the base's true init lives in the base run's artifacts.

**Timer semantics:** back-dating the controller timer by the base's real prefix
`elapsed_time` makes the `timelimit` stopping criterion charge the prefix time, so
the tail gets `timelimit - prefix_elapsed` — identical budget to a single
FULL_RUN of the whole flow. (The resume run's obj_log is a fresh file starting at
`t ≈ prefix_elapsed`; concat with the base obj_log for a full trajectory.)
**Confirmed on the run:** `timelimit 22.5s = base prefix 9.5s + resume tail 13.0s`
(exact) for `Instance_50_5_3_0,6_0,2_10_Rep0` @ p50.

---

## 4. Change list — as built (all ✅ done)

### 4.1 JSON→schedule loader ✅  ·  `src/ffc_ddw_sum_et/io/schedule_json.py`
Added `load_schedule_json(path) -> tuple[FFcSchedule, float|None, float|None]`:
parses `jobs/stages/machinesPerStage/operations`, builds `FFcSchedule`, inserts
each op via `add_ops_times_2_mc`, returns `(schedule, objValue, objBound)`.
Exported via `io/__init__.py`.
**Test:** `tests/io/test_schedule_json_roundtrip.py` — dump→load round-trips
start/end maps, `machines_per_stage`, jobs/stages, obj values; `objBound=None`
case; and the loaded schedule pickles (needed for the process-pool hop). 3 pass.

### 4.2 Controller resume run ✅  ·  `orchestration/controller_core.py`
- Added `self.method_names_to_run_before_resume: set[str] = set()` in `__init__`
  (empty — see § 2 safety assumption).
- `run(self)` → `run(self, flow_resume_idx: int = -1)`, outer wall-clock kept.
  When `flow_resume_idx > 0`: first pass over `self._subroutine_flow[:idx]` runs
  each step via `self._run_flow(step, skip_method_call=<not in run-before set>)`
  (so prefix steps are pure-skipped unless whitelisted), then the tail
  `self._run_flow(self._subroutine_flow[idx:])`, then `self.post_run_process()`.
  Otherwise `super().run()` unchanged. (Uses `SubroutineFlowKeys.parse_step` to
  read the method name — the imported routix helper.)
- Added `seed_resume_incumbent(solution, *, obj_value, obj_bound)`: registers the
  restored incumbent via `_register` **inside a pushed `"resume_seed"` method
  context** so the history entry gets a valid `<idx>-resume_seed` step_label
  (see § 7). `elapsed_time=0` anchors it at the back-dated clock.

### 4.3 Single-instance runner ✅  ·  `ffcddw_single_instance_runner.py`
- Resume slots (class attrs, default `None`): **`resume_solution`,
  `resume_elapsed_time`** (only these two — the manifest carries obj_value/bound
  inside `resume_solution`, so no separate first_obj_* slots were needed).
- `_apply_resume()`: raises if `resume_solution is None` (surfaces a silently
  failed load); back-dates `ctrlr.timer` **first**, then `check_feasibility`, then
  `ctrlr.seed_resume_incumbent(...)`.
- `run()`: `runs_controller = mode in {FULL_RUN, RESUME}` gates the controller
  block; on RESUME it calls `_apply_resume()` then
  `ctrlr.run(flow_resume_idx=self.flow_resume_idx)`.
- `post_run_process()`: persists when `mode in {FULL_RUN, RESUME}` and a controller
  ran; else `_load_instance_result()`.

### 4.4 Multi-instance runner ✅  ·  `ffcddw_multi_instance_runner.py`
Overrode `_load_resume_data(self)`: reads `resume_root` from `output_metadata`;
for each `(instance, runner)` loads `resume_root/<ins>/<ins>_solution.json`
(→ `load_schedule_json`) + `<ins>_instance_result.yaml`; injects
`runner.resume_solution = FFcDDWSolution(schedule, obj_value=manifest.obj_value,
obj_bound=manifest.obj_bound)` and `runner.resume_elapsed_time =
manifest.elapsed_time`. Logs "loaded N/M instances"; raises if any file missing
(routix swallows+logs the raise; § 4.3 guard is the backstop). Parent-side
injection survives to workers because the pool submits `runner.run` by value.

### 4.5 main.py RESUME plumbing ✅  ·  `main.py`
- New config key `resume_dir` (base **scenario** dir), resolved by
  `_resolve_resume_dir` (checks the dir + its `subroutine_flow.yaml` exist).
- On RESUME: loads base flow from `resume_dir/subroutine_flow.yaml`; per scenario
  computes `flow_resume_idx = SubroutineFlowValidator(FFcDDWSubroutineController)
  .validate_subroutine_flow_prefix(from_obj(base_flow), from_obj(scenario_flow))`
  and stores it on the scenario_config dict; `init_run_root` makes a fresh output
  dir for tail results.
- Sets `output_metadata["resume_root"] = str(resume_dir)`.

### 4.6 Flow cache write ✅  ·  `orchestration/reporting.py`
Added module const `SUBROUTINE_FLOW_CACHE_FN = "subroutine_flow.yaml"` (re-exported
via `orchestration/__init__.py`). In
`FFcDDWMultiScenarioRunner._init_multi_instance_runners`, after resolving
`scenario_output_dir`, dumps the scenario's `subroutine_flow` to
`scenario_output_dir/subroutine_flow.yaml` (best-effort). Written on every run, so
the base FULL_RUN produces the cache the resume run validates against.

### 4.7 Config edit ✅  ·  `metadata/20260709/sw_cp_tl_test_cases.yaml`
`run_mode: RESUME`; `resume_dir:
output/20260709_sw_cp_tl_test/20260709T195447_334017/mcf_lb_fmm_neh_cp` (the base
run used for the verification below — **update after each fresh base run**). Each
scenario keeps its full 5-step flow so the prefix validates → `flow_resume_idx=3`.
`ins_index` currently trimmed to `[60, 61, 63]` for a fast smoke (full 10-index
list left commented in both configs).

### 4.8 Full prior-history obj_log ✅  ·  `ffcddw_single_instance_runner.py` (added post-review)
Mirror hybridflowshop's behaviour where a resumed run records the **entire** prior
trajectory (it installs the base `obj_store` before running the tail). FFcDDW
rebuilds the obj_log from `solution_manager.history` at persist time, so a resume
run only had the seed + tail — its progress plot started at the `resume_seed`
marker (~9.5 s), not at t=0.
- `_save_obj_log`: when `mode == RESUME`, call `_merge_base_obj_log(...)` first to
  prepend the base run's `<ins>_obj_log.json` (`obj_value` + `obj_bound` data &
  notes) into the accumulating maps, then append the tail as before.
- The base's controller-frame timestamps live in `[0, prefix_elapsed]` and the
  tail (back-dated clock) continues from `prefix_elapsed`, so the two series
  **concatenate into one continuous trajectory** — no rescaling needed.
- The redundant `resume_seed` note is suppressed when the base merge succeeds
  (the base's real prefix-end note — e.g. `3-neh_cp` — already marks the join);
  its data point is kept for line continuity. Shared step name constant
  `RESUME_SEED_STEP_NAME` in `controller_core.py`.
- Loader-safe: the obj_log loader segments by **timestamp**, not `call_index`, so
  the base's `1/2/3-…` notes and the tail's `2-incremental_sw_cp…` notes coexist
  even though call-index 2 repeats.
- Best-effort: a missing/unreadable base obj_log logs a warning and the resume run
  falls back to tail-only + the `resume_seed` marker.

---

## 5. Verification — as run (2026-07-09, `ins_index=[60,61,63]`)

Two runs, no git commit:

1. **Base** — `uv run python main.py --config
   metadata/20260709/sw_cp_tl_test_base.yaml` → run
   `20260709T195447_334017`, 3 instances, ~10 s. Produced per-instance
   `_solution.json` / `_obj_log.json` / `_instance_result.yaml` and the scenario
   `subroutine_flow.yaml` cache. ✓
2. **Resume** — put that run's `mcf_lb_fmm_neh_cp` dir in `resume_dir`, then
   `uv run python main.py --config metadata/20260709/sw_cp_tl_test_cases.yaml` →
   run `20260709T200108_858180`, 7 scenarios × 3 instances, ~100 s. (First attempt
   hit the § 7 bug; after the fix it exited 0.) ✓

**All assertions held, every scenario × instance:**

| assertion | result |
|---|---|
| prefix not re-run | `calc_mcf_lb = run_flip = neh_cp = 0` ✓ |
| tail ran | `incremental_sw_cp = 1` ✓ |
| seeded from base | resume `first_obj_value` **==** base `obj_value` (50137 / 38753 / 44619) ✓ |
| global LB restored | resume `obj_bound` **==** base `obj_bound` (22291 / 8518 / 15149) ✓ |
| tail improves | resume `obj_value` < base `obj_value` in every case ✓ |
| TL budget preserved | `timelimit 22.5s = base prefix 9.5s + tail 13.0s` (exact) ✓ |
| scenarios differ | p50 / kappa_0.008 / p70 give distinct finals (Rep0: 45370 / 45019 / 45378) — kappa acts on the tail ✓ |
| obj_log = full history (§ 4.8) | resume obj_log spans **t=0.30→22.51** (275 pts) with notes `1-calc_mcf_lb → 2-run_flip → 3-neh_cp → 2-incremental_sw_cp.*`; no `ROOT`, no redundant `resume_seed` ✓ |

**Note — `solve_base_model_cpsat = 0`, not `1` as the draft predicted.** This is
correct, not a bug: `incremental_sw_cp` consumes the remaining tail budget, so the
`timelimit` stopping condition fires before `solve_base_model_cpsat` — exactly what
a single FULL_RUN of the whole flow would do at this budget.

**Full run:** restore the commented 10-index `ins_index` in both configs, re-run
base, update `resume_dir` to the new base run, re-run cases.

---

## 6. Risks / notes

- **Silent resume-load failure**: routix swallows `_load_resume_data` exceptions
  (`multi_instance_runner.py:74-76`). Backstop: §4.3 `_apply_resume` raises when
  `resume_solution is None`, surfacing per-instance in `instance_result.error`.
- **`obj_bound` semantics**: base `instance_result.obj_bound` is the global MCF LB
  (loose). Restored via the incumbent report so `get_current_valid_lb()` keeps it.
- **`resume_dir` is a fixed path**: it names one concrete base run. Every fresh
  base run mints a new timestamped dir, so `resume_dir` must be re-pointed (§ 4.7).
- **Do not run any git command from a subagent** (past incident). No commit until
  the user reviews (per working agreement).
- **ruff** after edits: `uv run ruff check` / `uv run ruff format`.

---

## 7. Bug found & fixed during the run — `ROOT` step_label

**Symptom:** the first resume attempt ran the tail correctly for all instances but
then **failed in post-run reporting** (exit 1): `obj_log_loader._parse_step_label`
raised `obj_log note label does not match '<idx>-<subroutine_name>': 'ROOT'`.

**Cause:** the first draft registered the incumbent by calling `ctrlr._register`
directly from `_apply_resume` — i.e. **outside any subroutine method context**.
`_wrap_report` stamps `step_label = _get_call_context_of_current_method()`, which
returns `"ROOT"` when the method-context stack is empty (routix
`MethodContextManager`). `_save_obj_log` then wrote a note labelled `ROOT`, and the
obj_log loader (`_STEP_LABEL_RE = ^(\d+)-(.+)$`) rejects it.

**Fix:** moved the registration into `controller_core.seed_resume_incumbent`, which
wraps the `_register` in `_method_context_mgr.push("resume_seed")` /
`pop()`. The seed now carries a valid `1-resume_seed` label; the tail steps follow
as `2-incremental_sw_cp.*`. Re-run exited 0 and the progress plots rendered.

**Lesson (worth keeping):** any `_register` issued outside the subroutine-flow
machinery must establish a method context first, or it produces a `ROOT`-labelled
history entry that breaks every downstream obj_log consumer.
