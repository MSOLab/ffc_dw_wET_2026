---
name: add-subroutine
description: Add a new algorithm subroutine (e.g. an LB seeder, a dispatcher integration) to FFcDDWSubroutineController with the full project convention stack — plan file, optional dispatcher port, incumbent registration, two-phase Gantt emission, hfs_summary-style CSV, and a dedicated experiment config wired into main.py.
when_to_use: |
  Invoke when the user asks to "add a new step", "port a dispatcher from
  hybridflowshop", "seed an incumbent from <something>", "integrate <algorithm>
  into run_*", or otherwise introduces a new subroutine to the FFcDDW flow.
  Keywords: subroutine, dispatcher, run_mcf_lb, run_fam, incumbent, LB-init,
  integrate algorithm, new step.
---

# Add a new algorithm subroutine to FFcDDW

Canonical workflow, derived from the MCF-LB-init integration in
`plans/20260419/mcf_lb_init_dispatch.md`. Follow it end-to-end — skipping steps
breaks either the orchestration contract (solution manager / reports) or the
post-run artifact pipeline.

## 0. Gather context first

1. Read `docs/architecture/algorithm-principles.md` and
   `docs/architecture/io-principles.md` — they are the contract.
2. Read `docs/TODO.md` — if the task is deferred there, stop and confirm before
   proceeding.
3. Read the current `orchestration/controller.py` to see the existing step
   methods (`run_fam`, `run_mcf_lb`) and mirror their shape.

## 1. Write the plan (always, even for "small" tasks)

- Location: `plans/<YYYYMMDD>/<short_slug>.md` (project repo, **not**
  `~/.claude/plans/`). Precedent: `plans/20260418/*.md`,
  `plans/20260419/weighted_et_objective.md`,
  `plans/20260419/mcf_lb_init_dispatch.md`.
- Sections: **Context**, **Critical files to modify / create**,
  **Existing functions to reuse** (cite file:line), **Implementation outline**
  (numbered steps), **Verification** (unit + integration + end-to-end + ruff).
- Keep the plan concise but executable — the future reader should not need to
  rederive decisions.

## 2. Extract shared objectives (DRY)

Weighted ET is the FFcDDW objective. When a new step needs to score a
schedule, reuse `ffc_ddw_sum_et.solution.objectives.compute_weighted_earliness_tardiness`. Do
**not** re-inline the formula. If you need a different objective, add it to
`solution/objectives.py` alongside `compute_weighted_earliness_tardiness`.

## 3. Port dispatchers / algorithms (only what you need)

When mirroring code from `../hybridflowshop/`:

- Target directory: `src/ffc_ddw_sum_et/algorithm/<domain>/` (e.g.
  `algorithm/dispatcher/` for dispatch-style heuristics).
- **Drop unused variants.** Example: `MixedDispatcher` was ported without
  CDS/Gupta/Palmer/Johnson helpers because the LB-init path does not use
  them. YAGNI trumps API parity.
- Replace `HybridFlowshopLiteSchedule` with `FFcSchedule`
  (`src/ffc_ddw_sum_et/solution/ffc_schedule.py`). Reuse its existing
  primitives: `dispatch_stage_by_jobs`, `dispatch_job_by_stages`,
  `machine_centric_dispatch_4`, `get_job_priority_queue_for_stage_dispatch`.
  Do not re-implement them.
- Replace makespan-based objective with `compute_weighted_earliness_tardiness` where
  hybridflowshop selects by makespan — the FFcDDW objective is weighted ET.
- Drop `draw_gantt_per_step` hooks from upstream utils — Gantt rendering is
  strictly post-run in this project (see §5).

## 4. Wire the new step into `FFcDDWSubroutineController`

Shape your step method like `run_mcf_lb`:

```python
def run_<new_step>(self, ...) -> SubroutineReport:
    start_elapsed = self.timer.elapsed_sec

    # ... algorithm body produces `schedule: FFcSchedule` ...

    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
    obj_value = float(sum_e + sum_t)
    obj_bound = float(<lb_if_available> or 0.0)

    elapsed = self.timer.elapsed_sec - start_elapsed
    report = SubroutineReport(
        elapsed_time=elapsed, obj_value=obj_value, obj_bound=obj_bound,
    )
    self.solution_manager.register(
        report,
        FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=obj_bound),
    )
    return report
```

Rules:
- Always `register(report, solution)`; never `register(report, None)` unless
  the step genuinely has no feasible schedule.
- `obj_bound` must be ≤ `obj_value` (LB sanity). Add an integration test that
  asserts this for a toy instance — see
  `tests/orchestration/test_controller.py::test_run_mcf_lb_registers_dispatch_incumbent`.
- Never import `matplotlib` from `controller.py` or anything it transitively
  imports. Gantt rendering is deferred (§5).

## 5. Two-phase Gantt emission

**Phase A — during algorithm run (text-only):**
The instance runner (`orchestration/ffcddw_single_instance_runner.py`)
already calls `dump_schedule_yaml(...)` for every incumbent. If your new
step produces additional intermediate schedules worth visualizing, emit more
`*_schedule.yaml` files into `self.working_dir` — nothing else.

YAML schema (see `src/ffc_ddw_sum_et/io/schedule_yaml.py`):
```yaml
instanceName: <str>
objValue: <float|null>
objBound: <float|null>
jobs: [<job_id>, ...]
stages: [<stage_id>, ...]
machinesPerStage:
  <stage_id>: [<mc_id>, ...]
operations:
  - {job: <j>, stage: <i>, machine: <k>, start: <int>, end: <int>}
```

**Phase B — post-run:**
`FFcDDWReporter._generate_gantt_charts` already scans
`self.output_dir.rglob("*_schedule.yaml")` and renders PNGs via
`io/gantt.py::GanttPlotter`. No reporter change is needed when a new step
drops its own YAMLs.

## 6. Summary / report shape

Per-instance rows go through `orchestration/summary.py::FFcDDWSummary`
(`FFcDDWInputSummary` + `FFcDDWOutputSummary`), which mirrors
`hybridflowshop/hfs_summary.py`. If the new step produces a metric worth
surfacing, extend `FFcDDWOutputSummary.to_string_dict()` (do **not** re-invent
CSV writers).

## 7. Experiment config + `main.py`

Create `metadata/<YYYYMMDD>_<slug>_config.yaml` with:

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
output_dir: output
instance_worker_cnt: 48     # default for experiment runs

scenarios:
  - name: <slug>
    timelimit: 300.0
    output_subdir: <slug>
    subroutine_flow:
      - method: run_<new_step>
```

Point `main.py:CONFIG_PATH` at the new file. Keep `instance_worker_cnt: 48`
unless the user specifies otherwise — this is the project default for
throughput runs.

## 8. Verification sequence

Run in this order; do not skip:

1. `uv run ruff check` — must be clean.
2. `uv run ruff format` — apply formatting.
3. `uv run pytest tests/ -q` — all existing tests must still pass. Update
   tests whose invariants you intentionally changed (example: the old
   "bound-only" `run_mcf_lb` test had to become
   `test_run_mcf_lb_registers_dispatch_incumbent`).
4. `uv run python main.py` on a small `ins_index` slice. Verify each
   instance's output subdir contains:
   - `<ins>_solution.json`
   - `<ins>_schedule.yaml`
   - `<ins>_gantt.png`
   - `<ins>_statistics.{json,yaml}`
   - `<ins>_obj_log.yaml`
   and the top-level `<ts>_summary.csv` has the `hfs_summary`-shaped header.
5. For an end-to-end sanity check, read one of the generated Gantt PNGs and
   confirm lanes are `(stage, machine)`, bars are colored per job, durations
   are labeled.

## 9. Commit discipline

- Use `uv run python`, never bare `python`/`python3` (also in scripts and
  docs).
- Do not commit `output/` artifacts.
- Commit message first line: `feat(<area>): <what>` — e.g.
  `feat(controller): seed incumbent from MCF start times`.

## Anti-patterns to refuse

- Writing matplotlib imports into anything under `algorithm/` or
  `orchestration/controller.py` / `orchestration/ffcddw_single_instance_runner.py`.
- Rebuilding a CSV writer inside `reporting.py`; use `FFcDDWSummary`.
- Saving a plan to `~/.claude/plans/` instead of the project's
  `plans/<date>/`.
- Copying `MixedDispatcher`'s CDS/Gupta/Palmer variants when the new step
  only needs the sequence-driven entry point.
- Registering `(report, None)` when a feasible schedule exists.
