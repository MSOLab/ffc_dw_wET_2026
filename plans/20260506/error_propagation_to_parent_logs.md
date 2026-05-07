# Plan: Propagate per-instance errors to MIR/MSR/main logs

## Goal

Today, when a `SubroutineController` raises (e.g.
`MCF global LB exceeds incumbent UB; LB or UB is inconsistent.`), the error
is only visible in
`<scenario>/<instance>/<run_id>_SingleInstanceRunner.log`. All upstream logs
(`<scenario>/<run_id>_MultiInstanceRunner.log`,
`<run_id>_MultiScenarioRunner.log`, `<run_id>_main.log`) are silent, and
`main.log` even prints `Experiment run completed successfully.`

We want:

1. Every per-instance ERROR is mirrored into the parent process's currently
   active log (MIR.log when MIR is running, MSR.log during reporting,
   main.log at end).
2. `main.log` distinguishes "ran clean" vs "had instance errors" without a
   reader needing to grep N nested files.

This is observability only — we do **not** change the
catch-and-continue behavior (one failed instance must not kill the rest).

## Why the error vanishes today

- `FFcDDWSingleInstanceRunner.run()` (`ffcddw_single_instance_runner.py:163`)
  catches the exception, stores `traceback.format_exc()` in `_run_error`,
  calls `self.logger.exception(...)`. But that runs in a
  `ProcessPoolExecutor` worker, where `setup_logging(sir_log_path, …)`
  (line 176) has already replaced the worker's root handlers with the
  per-instance file handler — so the record only reaches the SIR log.
- `routix.MultiInstanceConcurrentRunner.run()` collects
  `future.result()` (an `InstanceResult` carrying `error`) but never
  inspects the `error` field. The parent process's MIR.log handler never
  sees the failure.
- `FFcDDWMultiScenarioRunner.run()` (`reporting.py:345`) only logs
  `--- Starting/Finished Scenario … ---` per iteration; no aggregation of
  instance errors.
- `main()` (`main.py:141`) wraps `runner.run()` in `try/finally` but logs
  "successfully" whenever `runner.run()` returns without raising — which
  it always does, because every layer below catches.

## Design

Three mirror points, each at the layer where the relevant log handler is
already active in the parent process:

### M1. `FFcDDWMultiInstanceRunner.post_run_process()` — MIR.log

After the parent's `post_run_process()` collects results, iterate
`self.results` and for each `InstanceResult` with `error`, log:

```
self.logger.error(
    "Instance %s failed:\n%s", ir.instance_name, ir.error
)
```

`self.logger` is `ffc_ddw_sum_et.orchestration.FFcDDWMultiInstanceRunner`,
and during `multi_instance_runner.run()` the root handler points at
`<scenario>/<run_id>_MultiInstanceRunner.log` (set by MSR.run line 355).
Records propagate to root → MIR.log.

This single change covers the user's requirement (1) for the failing
case at hand: the SIR's stored traceback now appears in MIR.log too.

### M2. `FFcDDWMultiScenarioRunner.run()` — MIR.log (per scenario, summary)

After `multi_instance_runner.run()` returns and before
`--- Finished Scenario … ---`, count instance errors and log:

```python
n_err = sum(1 for ir in (result or []) if getattr(ir, "error", None))
if n_err:
    logger.error(
        "Scenario %s: %d/%d instances finished with errors",
        scenario_name, n_err, len(result),
    )
```

Goes to MIR.log because root handler is still the MIR one. Also caught by
the existing `except Exception` block above for the (rare) case where the
scenario itself raises before producing a result list.

### M3. `main()` — main.log final summary

Replace the unconditional `"Experiment run completed successfully."` with:

```python
final = runner.run()
setup_logging(*main_logging_args, is_main=True)
total_err = sum(
    1
    for sr in (final.scenario_results if final else [])
    for ir in sr.instance_results
    if getattr(ir, "error", None)
)
if total_err:
    logger.error(
        "Experiment run finished with %d instance error(s); "
        "see per-scenario MIR logs for tracebacks.",
        total_err,
    )
else:
    logger.info("Experiment run completed successfully.")
```

`final` is the `FinalResult` returned by `FFcDDWMultiScenarioRunner.run()`
(currently discarded). The `try/finally` and elapsed-time log stay.

## Files touched

| File | Change |
|---|---|
| `src/ffc_ddw_sum_et/orchestration/ffcddw_multi_instance_runner.py` | M1: extend `post_run_process` to log instance errors before returning. |
| `src/ffc_ddw_sum_et/orchestration/reporting.py` | M2: in `FFcDDWMultiScenarioRunner.run`, after each scenario, count `result` errors and log if any. |
| `main.py` | M3: capture `runner.run()` return, count errors across `FinalResult`, branch the final log. |

## Out of scope

- **Live propagation of every WARNING/INFO from a worker to the parent.**
  That requires the `multiprocessing.QueueHandler` listener pattern and is
  a larger refactor. Today's fix only mirrors recorded errors at result-
  aggregation time, which is what the user's example log demands.
- **Failing the run on instance error.** Catch-and-continue is intentional
  (large benchmark sweeps). main.log's branching message + nonzero error
  count is the observable signal.
- **Changing SIR.run's catch logic.** Re-raising would kill sibling
  instances inside the pool — not what we want.

## Verification

1. Re-run the failing config (`metadata/20260506/20260506_mcf_lb_neh_cp.yaml`,
   single instance with the LB>UB inconsistency):
   - SIR log: traceback (unchanged, expected).
   - **MIR.log**: should now contain
     `[ERROR] … Instance Instance_100_10_3_0,2_0,6_10_Rep1 failed: …` plus
     scenario-level `1/1 instances finished with errors`.
   - **main.log**: should now contain
     `[ERROR] Experiment run finished with 1 instance error(s); …`
     instead of `completed successfully`.
2. A clean run (no failing instance) should still log
   `Experiment run completed successfully.` in main.log.
3. `uv run ruff check` clean on the touched files.

## Non-goals / open questions

- The SIR currently logs at `self.logger.exception(...)` which prints the
  full traceback into the SIR log. M1 logs `ir.error` (already a captured
  traceback string), so the parent-side message is one record carrying the
  same text — no double traceback formatting.
