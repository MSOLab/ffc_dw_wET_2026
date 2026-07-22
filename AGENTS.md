# Repository Guidance

This repository keeps long-lived coding rules in Markdown so future conversations
can pick up the same architectural intent.

## Working conventions

- Prefer `uv run ...` for Python execution.
  - Use `uv run python` instead of `python3` or just `python`.
- Run `uv run ruff check` after code changes.
- Run `uv run ruff format` when formatting is needed.
- **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/):
  - Type: `feat`, `fix`, `refactor`, `docs`, `perf`, `test`, `chore`
  - Scope: derive from changed files (e.g. `pw_cp.py` → `pw-cp`)
  - Title: ≤49 chars (including `<type>(<scope>): ` prefix), imperative mood, no trailing period
  - Body: bullet points (`- ` prefix); each bullet is a **single line** (no hard-wrapping within a bullet)

### Plan & analysis documents (`plans/`)

`plans/` is split by intent. Both halves are tracked; date subdirectories use
`YYYYMMDD`.

| path | holds | written |
|---|---|---|
| `plans/experiment/<date>/` | code-change and experiment-execution plans | **before** the work |
| `plans/analysis/<date>/` | cross-run / post-hoc analysis write-ups | **after** the runs exist |

An analysis document is the tracked **single source of truth** for a merged
analysis: the question, the source run directories (full paths), the exact
reproduction command, the result tables, and the conclusion. Bulk artifacts
(CSV / PNG / HTML) stay in `analysis/<id>/`, which is gitignored — the document
must stand on its own without them.

### Provenance commits

`output/` and `analysis/` are gitignored, so results are discoverable only
through commit messages. Two commit kinds name an untracked directory in their
subject and are **exempt from the Conventional Commits format above**:

- **run setting** — commit the config that produced a run:
  `<run_dir>/<timestamp> run setting`, body naming the machine
  (e.g. `19521ec`).
- **merged analysis** — commit the `plans/analysis/` document (plus any new
  script) for a cross-run analysis:
  `analysis/<id> merged analysis`, body listing the source run directories,
  the reproduction command, and a one-line conclusion.

`git log --oneline | rg "merged analysis"` then indexes every cross-run
analysis, and each commit body says which runs fed it and where its artifacts
live.

## Architecture Docs

- **Problem definition** (parameters, variables, constraints, objective):
  `docs/problem-description.md`
- IO extraction and import rules:
  `docs/io-principles.md`
- Algorithm execution contract rules:
  `docs/algorithm-principles.md`
- Output directory schema (`ArtifactLayout`) and SC log lifecycle:
  `docs/io/20260429_artifact_manager.md`

### Optimality-judgment field (per-instance result)

When deciding whether a run reached a proven optimum for an instance, read the
per-instance `<instance>_instance_result.yaml` and compare its two top-level
fields: **`obj_value`** (final incumbent, the UB) and **`obj_bound`** (best
lower bound, the LB). Optimal ⇔ `obj_value == obj_bound` (within float
tolerance). Notes:

- `obj_bound` here is the **global** LB carried by the run (e.g. the MCF LB
  seeded by `calc_mcf_lb_and_derive_full_sch`), not a per-window sub-CP bound.
  It is **loose**: across the 1440-instance PRA2017 large grid only ~10 % of
  instances reach `obj_value == obj_bound`, and non-optimal gaps have a median
  of ~100 %+. Do not treat a large gap as a solver failure.
- `work_status` (`optimal` / `feasible` / …) reflects the *last algorithm
  step's* status, not a global proof; prefer the `obj_value == obj_bound`
  comparison for a global optimality judgment.
- The same UB/LB pair is also emitted per progress point in
  `<instance>_obj_log.json` (`obj_value.data` / `obj_bound.data`) — see the
  SW-CP TL-policy note in `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` for the
  loader caveat (the structured loader drops LB points that carry no note).

### PRA2017 instance parameters (generation grid & mapping source)

When grouping/filtering results by instance-generation parameters (T, R, n, c,
machine count, W), the **authoritative per-instance source is
`benchmarks/PRA2017/pra2017_bks_table.csv`** — one row per `insIndex`
(`0000`–`1439`, zero-padded 4-digit string). Columns:

`insIndex, n, c, totalMcCount, T, R, W, BKS_data, BKS_calc, BKS_T, BKS_F`.

Parameter meanings and the **full generation grid** (1440 = all combinations ×
5 replicates; each `(n, c, totalMcCount)` cell has exactly 90 instances):

| Param | Meaning | Values |
|-------|---------|--------|
| `n` | job count | 50, 100, 150, 200 |
| `c` | stage count | 5, 10 |
| `totalMcCount` | total machine count = `c × machines-per-stage` | 15, 25, 30, 50 |
| `T` | tardiness factor (due-date tightness) | 0.2, 0.4, 0.6 |
| `R` | due-date range factor | 0.2, 0.6, 1.0 |
| `W` | weight range | 10, 20 |
| (Rep) | replicate id, not a column — see filename | 0–4 |

- **machines-per-stage is uniform** and ∈ {3, 5}: `totalMcCount = c × mps`
  (so `c=5`→{15,25}, `c=10`→{30,50}). It is **not** a `bks_table` column; read
  it from `totalMcCount / c` or the filename.
- **Filename encoding** (via `pra2017_hybrid_match.csv`, `insIndex →
  ffc_ddw_sum_et_filename`): `Instance_{n}_{c}_{mps}_{T}_{R}_{W}_Rep{k}.txt`,
  e.g. `Instance_50_5_3_0,2_0,2_10_Rep0.txt`. **Decimals use a comma**
  (`0,2` = 0.2). Verified: all 1440 filenames' decoded fields match
  `bks_table` exactly.
- **BKS variants** (all "best known solution" objective values):
  - `BKS_T`: objective with `force_job_id_seq=True` (preserves best_seq order)
  - `BKS_F`: objective with `force_job_id_seq=False` (FAM reordering)
  - `BKS_calc`: `min(BKS_T, BKS_F)`
  - `BKS_data`: the paper/data reference BKS — **this is the RPDf denominator**
    used by report tooling (`RPDf_BKS_data`).
  - See `benchmarks/PRA2017/README.md` for how the table is generated.
- **How reports attach these**: `orchestration/post_run_pivot.py` merges each
  run's `instanceName` → `insIndex` (via `pra2017_hybrid_match.csv`) →
  `bks_table` metadata, emitting `<run>_rpdf_comparison.csv` with columns
  `insIndex, scenarioName, n, c, totalMcCount, T, R, W, BKS_data, bestObj,
  RPDf_BKS_data, elapsedTime, timelimit, time%`. **This CSV is the ready-made
  source for any (T, R, size)-grouped RPDf comparison** — no need to re-join.
- **RPDf is symmetric** (`ffc_ddw_sum_et._calc.rpd_f`):
  `2·(obj − ref)/(obj + ref)`, **not** the classic `(obj − ref)/ref`. Range
  (−2, 2); `obj == ref == 0 → 0`.

## Working Agreement

- Before any domain-level work (objective, scheduling logic, algorithm design),
  read `docs/problem-description.md` to understand the main problem and confirm
  symbol usage.
- If a change touches `src/ffc_ddw_sum_et/io/` or code that imports from it,
  read `docs/io-principles.md` first.
- If a change touches `src/ffc_ddw_sum_et/algorithm/` or code that imports from
  it, read `docs/algorithm-principles.md` first.
- Treat the IO subtree as an extractable package candidate. Avoid introducing
  new dependencies from `io` into parent or sibling domain packages.
- Treat the algorithm boundary as a stable execution contract candidate. Avoid
  introducing `Launcher`, `Reporter`, or report-orchestration concerns into
  `Algorithm`, `AlgSpec`, or `AlgRecord` code before those contracts are
  defined.
- Prefer changing public imports through `ffc_ddw_sum_et.io` instead of
  importing deep internal modules from outside the IO subtree.
- The `ffc_ddw_sum_et.algorithm` package surface is intentionally empty
  (see `src/ffc_ddw_sum_et/algorithm/__init__.py` — re-exports caused a
  circular import at package init). Until that is resolved, import contract
  types from their owning submodules: `algorithm.base.alg_spec`,
  `algorithm.base.alg_record`, `algorithm.base.alg_option`,
  `algorithm.base.algorithm`, `algorithm.options.*`, `algorithm.fam`,
  `algorithm.dispatcher.*`, `algorithm.mcf_lb.*`, `algorithm.neh_cp.*`,
  `algorithm.flip_makespan_cp.*`, `algorithm.sw_cp.*`.
- **Naming note:** the algorithm now called `sw_cp` (sliding-window CP,
  classes `SwCp*`, controller steps `sw_cp` / `incremental_sw_cp`) was
  formerly named `pw_cp` (`PwCp*`, `incremental_pw_cp`) to match the paper.
  Older artifacts still carry the old name: on-disk `output/` results,
  `algorithm_id`/step-labels in historical runs, and file/directory names
  such as `metadata/**/pw_cp_*.yaml`, `docs/algorithms/pw_cp.*`, and
  `plans/**/pw_cp_*` were intentionally left unrenamed. When reading old
  data or docs, treat `pw_cp` as the same algorithm as today's `sw_cp`.

### Subroutine step contract (controller.py)

Each step method on `FFcDDWSubroutineController` must follow these two
invariants. They are load-bearing for the per-instance `_obj_log.json`
aggregator (`_save_obj_log` in `ffcddw_single_instance_runner.py`), which
re-bases each step's algorithm-frame trajectory onto the controller clock
using `start_time = self.timer.elapsed_sec - report.elapsed_time`.

1. **At most one register per step call.** A step body either calls
   `self._register(report, sol, ...)` exactly once before returning, or
   returns a stop-report from `_make_stop_report` without registering.
Composite steps (e.g. `calc_mcf_lb_and_derive_full_sch`) delegate to
    a pure algorithm pipeline function and call `self._register` exactly
    once with the synthesized final report. Multiple registers per call would make
   `solution_manager.history` ambiguous about which trajectory belongs to
   which step.

2. **`elapsed_time` is measured `monotonic()` from step entry to
   `_register` call, with no work in between.** Pattern:

   ```python
   def my_step(self, ...):
       start_elapsed = time.monotonic()
       ...                                        # all the actual work
       elapsed = time.monotonic() - start_elapsed # measure here
       report = SubroutineReport(elapsed_time=elapsed, ...)
       self._register(report, sol, ...)           # immediately
       return report
   ```

   Wedging non-trivial work between `elapsed = ...` and `_register`
   skews the derived `start_time` and shifts the step's obj_log
   timestamps. If a step needs post-work that should not count toward
   the trajectory, do it after `_register` (the controller has already
   captured the trajectory at that point).

## Deferred Design Notes

- `TODO.md` (repository root) collects refactor ideas that are
  deliberately deferred (YAGNI today but worth capturing so the
  reasoning isn't re-derived).
- Before proposing a refactor, check `TODO.md` to see if it has
  already been considered — respect the "When to act" condition.
- When a design idea is agreed to be deferred rather than acted on,
  append it to `TODO.md` with **Why** and **When to act** fields.
- Do not execute TODO items autonomously — they are deferred by intent.
