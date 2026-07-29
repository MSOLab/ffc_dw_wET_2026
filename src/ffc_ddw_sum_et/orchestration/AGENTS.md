# Orchestration

## Subroutine step contract (controller.py)

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

    A **composite step whose inner sub-steps also register on the same
    controller** (e.g. `incremental_job_contrib_cp` whose inner
    `job_contrib_cp` calls `_register`) must still call `_register` itself
    exactly once after all inner work completes. The inner registrations are
    per-subroutine history entries; the composite's own registration adds the
    parent endpoint — needed so charts show a top-level marker closing the
    composite's flow section (matching `coarsen_solve_reconstruct`'s
    convention). Pass `obj_value=self.solution_manager.best_obj_value`,
    `obj_bound=None`, and **the current incumbent as `solution`** —
    `self.solution_manager.get_incumbent()`.

    Do **not** pass `solution=None` for such a tail entry: `work_status`
    (`controller_core.py`) reads `history[-1]` and returns `None` when that
    record carries no solution, so a successful run would be written to
    `<instance>_instance_result.yaml` / the summary CSV as status-unknown.
    Re-registering the incumbent is safe — routix `SolutionManager.register`
    swaps the incumbent only on a strictly better objective, and its
    consistency check compares `solution.obj_value` against the reported
    `obj_value`, which match by construction.

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
