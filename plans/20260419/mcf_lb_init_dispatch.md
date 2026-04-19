# Plan: MCF-seeded dispatch for `run_mcf_lb` + Gantt/report fixes

## Context

`run_mcf_lb` currently computes a preemptive last-stage lower bound via min-cost
flow but discards the per-job start times, registers `None` for the
`FFcDDWSolution`, and leaves `obj_value=None` in the `SubroutineReport`. The
MCF LP already contains a dispatch-order signal that we want to exploit: sort
jobs by the ascending MCF last-stage start time, then dispatch them from the
first stage using a port of hybridflowshop's `MixedDispatcher`. The resulting
`FFcSchedule` becomes an incumbent candidate and its weighted ET value replaces
the missing `obj_value`.

The current Gantt chart inside `FFcDDWReporter._generate_gantt_charts` is
visually incorrect (ad-hoc matplotlib block inside the reporter) and the
summary output does not match the hybridflowshop CSV "inputs + outputs" layout.
We port hybridflowshop's `GanttPlotter` and `HfsSummary` shape.

Gantt rendering is split into two phases so the algorithm path stays
matplotlib-free:

1. **At algorithm/step time** — emit a text-only `*_schedule.yaml`.
2. **At post-run-process time** — the reporter walks the output tree, finds
   each conforming YAML, and renders PNGs via `GanttPlotter`.

This removes matplotlib from the hot path and lets visualization be rerun or
skipped independently.

Finally, we add a new metadata config dedicated to this LB-init flow and wire
`main.py` to consume it.

## Critical files to modify / create

**Modify:**

- `src/ffc_ddw_sum_et/orchestration/controller.py` — extend `run_mcf_lb` to sort
  by MCF start time, dispatch via `MixedDispatcher`, compute ET obj, register
  a real `FFcDDWSolution`.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py` — also
  write `<ins>_schedule.yaml` next to the existing `_solution.json` for the
  incumbent.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` — drop the inline Gantt
  matplotlib block; replace with a post-run pass that scans for
  `*_schedule.yaml` files and renders them via `GanttPlotter`. Switch summary
  writer to the ported `HfsSummary`-style CSV.
- `src/ffc_ddw_sum_et/algorithm/fam.py` — reuse the extracted ET helper
  (drop the private `_calculate_window_et`).
- `src/ffc_ddw_sum_et/io/__init__.py` — export the new `GanttPlotter` and the
  schedule-YAML read/write helpers.
- `main.py` — point `CONFIG_PATH` to the new LB-init config.

**Create:**

- `src/ffc_ddw_sum_et/algorithm/dispatcher/__init__.py`
- `src/ffc_ddw_sum_et/algorithm/dispatcher/base.py` — minimal `BaseDispatcher`
  (job/stage lists, processing-time maps, machines-per-stage, empty-schedule
  factory). **Drops** `get_cds_sequence` / `get_gupta_sequence` /
  `get_palmer_sequence` / `get_johnsons_rule_sequence` — YAGNI for LB-init.
- `src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py` — `MixedDispatcher`
  with only `_get_np_candidates` and `get_best_mixed_schedule_by_sequence`
  (drop `get_schedule_by_cds` / `_by_gupta` / `_by_palmer`). Candidate
  selection uses weighted ET, not makespan.
- `src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py` —
  `from_job_sequence_get_schedule_mixed` adapted from
  `hybridflowshop/dispatcher/utils.py:678-890`, using `FFcSchedule` directly.
  Drop the `draw_gantt_per_step` branch.
- `src/ffc_ddw_sum_et/solution/objectives.py` — extract
  `compute_window_et(schedule, instance) -> (sum_earliness, sum_tardiness)` from
  `fam.py:205-220` so both FAM and LB-init reuse it (DRY).
- `src/ffc_ddw_sum_et/io/gantt.py` — `GanttPlotter` class ported from
  `hybridflowshop/hybridflowshop/painter/gantt.py`. Inputs use the
  `(job, stage, machine)→time` map format already produced by
  `FFcSchedule.get_jik_2_{start,end}_time_map()`.
- `src/ffc_ddw_sum_et/io/schedule_yaml.py` — `dump_schedule_yaml` and
  `load_schedule_yaml`. Schema:
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
  Text-only — no matplotlib touched during the algorithm run.
- `src/ffc_ddw_sum_et/orchestration/summary.py` — `FFcDDWSummary` class shaped
  like `hybridflowshop/hfs_summary.py:8-64` with `header()` /
  `comma_separated_values()` / `save()`. Inputs: ins name, job/stage count,
  machines per stage, timelimit. Outputs: `initObj`, `initBound`, `bestObj`,
  `bestBound`, `elapsedTime`, `improvementRatio`, `workStatus`.
- `metadata/20260419_lb_init_config.yaml` — FULL_RUN, one scenario running only
  `run_mcf_lb`, with `instance_worker_cnt: 48`.

## Existing functions to reuse

- `FFcSchedule.dispatch_stage_by_jobs`, `dispatch_job_by_stages`,
  `machine_centric_dispatch_4`, `get_job_priority_queue_for_stage_dispatch`
  (`src/ffc_ddw_sum_et/solution/ffc_schedule.py`) — the dispatcher port
  collapses to thin wiring around these.
- `ParallelMachinePreemptionMcf.get_job_2_start_time_map`
  (`src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py:196-203`).
- `FFcDDWParameters.{job_id_list, stage_id_list, stage_2_machines_map,
  stage_2_job_2_p_map, job_2_stage_2_p_map, machine_count_per_stage,
  job_2_due_window_map, job_2_ewt_map, job_2_twt_map}`.

## Implementation outline

### 1. Dispatcher port (`algorithm/dispatcher/`)

Minimal `BaseDispatcher` extracts the instance fields `MixedDispatcher` needs
and returns fresh `FFcSchedule` instances.

`MixedDispatcher` keeps only `_get_np_candidates` and
`get_best_mixed_schedule_by_sequence`; best-schedule selection uses
`compute_window_et` instead of `makespan`.

`utils.from_job_sequence_get_schedule_mixed` reuses `FFcSchedule`'s built-in
dispatch primitives directly; the `draw_gantt_per_step` hooks are dropped.

### 2. `run_mcf_lb` extension (`orchestration/controller.py:67-88`)

```
mcf = ParallelMachinePreemptionMcf.from_instance(instance); mcf.solve()
start_map = mcf.get_job_2_start_time_map()
job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
job_sequence = sorted(
    instance.job_id_list,
    key=lambda j: (start_map[j] is None,
                   start_map[j] if start_map[j] is not None else 0,
                   job_2_pos[j]),
)
dispatcher = MixedDispatcher(instance)
schedule = dispatcher.get_best_mixed_schedule_by_sequence(job_sequence)
assert schedule is not None
e_sum, t_sum = compute_window_et(schedule, instance)
obj_value = float(e_sum + t_sum)
obj_bound = float(mcf.get_obj_value())
report = SubroutineReport(elapsed_time=elapsed, obj_value=obj_value,
                          obj_bound=obj_bound)
self.solution_manager.register(
    report,
    FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=obj_bound),
)
```

### 3. Gantt split — write-YAML phase + render-PNG phase

**Phase A (algorithm/step time):** extend
`FFcDDWSingleInstanceRunner._post_run_process_inner` so that when an incumbent
exists and `working_dir` is set, it writes
`<ins_name>_schedule.yaml` alongside the existing `_solution.json`.

**Phase B (post-run-process):** `FFcDDWReporter._generate_gantt_charts` (lines
237-338) is replaced with:

```python
def _generate_gantt_charts(self) -> None:
    try:
        from ..io import GanttPlotter, load_schedule_yaml
    except ImportError:
        logger.warning("matplotlib not available, skipping Gantt charts")
        return
    for yaml_path in self.output_dir.rglob("*_schedule.yaml"):
        data = load_schedule_yaml(yaml_path)
        start_map = {(op["job"], op["stage"], op["machine"]): op["start"]
                     for op in data["operations"]}
        end_map   = {(op["job"], op["stage"], op["machine"]): op["end"]
                     for op in data["operations"]}
        png_path = yaml_path.with_name(
            yaml_path.stem.replace("_schedule", "_gantt") + ".png"
        )
        GanttPlotter().export(
            png_path, start_map, end_map,
            job_list=data["jobs"], stage_list=data["stages"],
            machine_list_per_stage=data["machinesPerStage"],
            all_job_list=data["jobs"],
        )
```

Consequences: algorithm runs never import matplotlib; POST_PROCESS_ONLY reruns
regenerate PNGs from the frozen YAMLs; any future subroutine that wants a
Gantt just drops another `*_schedule.yaml` — no reporter changes needed.

### 4. Summary/report shape (`orchestration/summary.py` + `reporting.py`)

Port `HfsSummary` as `FFcDDWSummary`. `FFcDDWReporter._write_summary_csv`
delegates to `FFcDDWSummary.save(...)` per instance (append mode). The existing
JSON/YAML/Excel writers are unchanged.

### 5. New config + `main.py` wiring

`metadata/20260419_lb_init_config.yaml`:

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
ins_index: [0, 1, 2]
output_dir: output
instance_worker_cnt: 48  # parallel workers for faster experimentation

scenarios:
  - name: mcf_lb_init
    timelimit: 300.0
    output_subdir: mcf_lb_init
    subroutine_flow:
      - method: run_mcf_lb
```

`main.py:21`: `CONFIG_PATH = Path("metadata/20260419_lb_init_config.yaml")`.

## Verification

1. **Unit — objective helper:** `compute_window_et` on a hand-built
   `FFcSchedule` matches the inline calculation it replaces (one tardy job,
   one early job, one on-time).
2. **Unit — dispatcher:** on a small toy instance,
   `MixedDispatcher.get_best_mixed_schedule_by_sequence([...])` returns a
   non-null schedule and its ET matches a manually computed value.
3. **Integration — `run_mcf_lb`:** after the step,
   `solution_manager.get_incumbent()` is not `None`,
   `report.obj_value == compute_window_et(schedule, ins)`,
   `report.obj_bound == mcf.get_obj_value()`, and `obj_value >= obj_bound`.
4. **End-to-end run:** `uv run python main.py` on `ins_index: [0]`; check that
   `*_solution.json`, `*_schedule.yaml`, `*_gantt.png`, and the new summary CSV
   are produced.
5. **POST_PROCESS_ONLY rerun:** rerun with `run_mode: POST_PROCESS_ONLY`
   pointing at the prior dir; the PNG is regenerated from the existing YAML
   without the algorithm running.
6. **Lint/format:** `uv run ruff check` and `uv run ruff format`.
