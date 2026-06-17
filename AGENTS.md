# Repository Guidance

This repository keeps long-lived coding rules in Markdown so future conversations
can pick up the same architectural intent.

## Working conventions

- Prefer `uv run ...` for Python execution.
  - Use `uv run python` instead of `python3` or just `python`.
- Run `uv run ruff check` after code changes.
- Run `uv run ruff format` when formatting is needed.

## Planning conventions

- Save implementation plans under `plans/<YYYYMMDD>/<slug>.md`.
- Structure a plan so it can be dispatched as **one subagent per file to modify**
  (the user opens a new conversation and says "이 markdown 참조해서, 수정 파일
  하나당 subagent 하나씩 시켜 수정하게 만들어"). Concretely:
  - A top "How to dispatch" note: one subagent per work order; each reads the
    background + shared contracts + its own work order only, edits only its one
    file, and keeps the default/unchanged path byte-identical.
  - A **Shared Contracts** section that pins every cross-file interface exactly
    (new option names/defaults, function signatures, dataclass field lists, types
    crossing file boundaries) — the single source of truth that keeps independent
    edits compatible (contract-first).
  - **One Work Order per file** (including new files and test files): file path,
    depends-on, precise changes, reference code as `path:line`, invariants,
    acceptance check.
  - A dependency graph / dispatch-order section.

## Architecture Docs

- **Problem definition** (parameters, variables, constraints, objective):
  `docs/problem-description.md`
- IO extraction and import rules:
  `docs/io-principles.md`
- Algorithm execution contract rules:
  `docs/algorithm-principles.md`
- Output directory schema (`ArtifactLayout`) and SC log lifecycle:
  `docs/io/20260429_artifact_manager.md`

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
  `algorithm.flip_makespan_cp.*`, `algorithm.pw_cp.*`.

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

- `docs/TODO.md` collects refactor ideas that are deliberately deferred
  (YAGNI today but worth capturing so the reasoning isn't re-derived).
- Before proposing a refactor, check `docs/TODO.md` to see if it has
  already been considered — respect the "When to act" condition.
- When a design idea is agreed to be deferred rather than acted on,
  append it to `docs/TODO.md` with **Why** and **When to act** fields.
- Do not execute TODO items autonomously — they are deferred by intent.
