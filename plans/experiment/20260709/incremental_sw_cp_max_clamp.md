# Plan: clamp `incremental_sw_cp` unfixed_batch_count_max to the instance batch count

Date: 2026-07-09
Scope: `src/ffc_ddw_sum_et/orchestration/controller.py` (`incremental_sw_cp`).

## 1. Problem

`incremental_sw_cp` iterates `unfixed_batch_count` over
`[unfixed_batch_count_min, unfixed_batch_count_max]` and calls `sw_cp` once per value
(`controller.py:2555-2618`). The upper bound is **not** compared against the instance's
actual batch count. When `unfixed_batch_count_max` exceeds the batch count, the extra
iterations are pure no-ops:

- `batch_count = ceil(job_count / batch_size_resolved)` (equals the dispatcher's
  `max_batch_cnt`; each stage has exactly `job_count` ops so batch count is
  `ceil(job_count / batch_size)` — see `sw_cp/partition.py:143-148` and
  `validate_and_get_batch_count` at `partition.py:193-203`).
- For `unfixed_batch_count > batch_count`, `SwCpDispatcher` short-circuits at
  `sw_cp/dispatcher.py:114-127` (`"max_batch_cnt=%d < unfixed_batch_count=%d; nothing
  to do."`) and returns the incumbent unchanged.

### Observed in real data
Run `output/20260708_sw_cp_tl_test/20260708T215949_422005/p30/Instance_50_5_5_0,2_0,2_10_Rep0`
(n=50, c=5, 5 machines/stage → `batch_count=10`) with config
`metadata/20260708/sw_cp_tl_test.yaml` (`unfixed_batch_count_max: 12`):

- counts 2..9  → normal sliding window (iterations 9..2)
- count 10     → `iterations=1`, single full-window solve (`batch_count == count` boundary)
- count 11, 12 → `"nothing to do."` no-ops (log lines 198, 201, 1 ms apart)

Counts 11 and 12 are dead configuration for this instance shape. Because the batch count
varies per instance across the PRA2017 grid while one config is reused, a fixed
`unfixed_batch_count_max` inevitably produces such dead iterations on the smaller shapes.

## 2. Goal

Clamp `unfixed_batch_count_max` down to the instance's actual batch count so no iteration
runs above `batch_count`. **No new option** — the clamp is unconditional.

Key property: clamping to exactly `batch_count` **never removes a productive pass**. Every
count in `(batch_count, unfixed_batch_count_max]` is already a guaranteed dispatcher no-op
(`dispatcher.py:114`); counts in `[unfixed_batch_count_min, batch_count]` are untouched.
The change only drops wasted loop iterations (each ~1 ms + a spurious no-op step report).

## 3. Design

Inside `incremental_sw_cp`, after `batch_size_resolved` is computed
(`controller.py:2505-2517`) and before the loop (`controller.py:2555`):

```python
batch_count = math.ceil(instance.job_count / batch_size_resolved)
effective_max = min(unfixed_batch_count_max, batch_count)
```

The clamp is surfaced by folding it into the **single** existing policy log line
(`controller.py:2563-2568`) — no separate clamp log — so the effective range and the
requested/batch context appear on one line:

```python
self.logger.info(
    "incremental_sw_cp: policy=%s, unfixed_batch_count=[%d, %d] "
    "(requested_max=%d, batch_count=%d)",
    increment_unfixed_batch_count_flag,
    unfixed_batch_count_min,
    effective_max,
    unfixed_batch_count_max,
    batch_count,
)
```

Then change the loop bound (`controller.py:2555-2556`) from `unfixed_batch_count_max` to
`effective_max`:

```python
for unfixed_batch_count in range(unfixed_batch_count_min, effective_max + 1):
```

Notes:
- The policy log's range upper bound is the **effective** `effective_max`; the
  **requested** `unfixed_batch_count_max` and `batch_count` are shown in parentheses on
  the same line for traceability.
- No signature change, no YAML change, no validation change. Existing guards
  (`controller.py:2484-2494`: `min >= 1`, `max >= min`) are unchanged.
- **Edge case (`batch_count < unfixed_batch_count_min`)** needs no special handling:
  `effective_max < unfixed_batch_count_min` makes `range(min, effective_max + 1)` empty,
  so `incremental_sw_cp` runs no `sw_cp` pass. This matches today's behavior — every
  count `>= min > batch_count` was already a no-op — so nothing productive is lost, only
  the wasted iterations. (Optionally add a `debug`/`info` log when the clamped range is
  empty; low priority.)

## 4. Implementation steps

1. Add the `batch_count` / `effective_max` computation + clamp log after
   `batch_size_resolved` in `incremental_sw_cp`.
2. Change the loop upper bound to `effective_max`.
3. Update the `incremental_sw_cp` docstring (`controller.py:2464-2483`): note that
   `unfixed_batch_count_max` is clamped to the instance's `batch_count`, so above-batch
   no-ops (previously absorbed only by `dispatcher.py:114`) are avoided up front.
4. `uv run ruff check` and `uv run ruff format`.
5. Regression: `uv run pytest tests/orchestration tests/algorithm/sw_cp`.

### Testing note (TDD tradeoff)
The change is a single `min(...)` plus a log line, so the substantive logic is just the
`batch_count = ceil(job_count / batch_size)` derivation. Per the global TDD rule a test
would be written first, but a unit test over `min()` is a pointless abstraction (KISS /
YAGNI). The meaningful invariant to guard is *"our closed-form `batch_count` equals the
dispatcher's `max_batch_cnt`"*. Options, in order of preference:

- **(Recommended, light)** Verify via the controller log on a small instance: after
  running a flow that reaches `incremental_sw_cp` with `unfixed_batch_count_max` above the
  instance's batch count, assert the clamp info line fires and no `sw_cp` runs above
  `batch_count`. Reuse the `_make_instance` / `_make_controller` helpers in
  `tests/orchestration/test_controller.py:18-42`.
- **(Optional, stronger)** A targeted test asserting
  `ceil(job_count / batch_size_resolved)
   == validate_and_get_batch_count(build_stage_2_batch_list(incumbent, batch_size_resolved))`
  for a seeded incumbent, pinning the invariant the closed form relies on.

Flagging the tradeoff per the global rule; recommend the light log-based check and
skipping a dedicated helper/unit test for the `min()` itself.

## 5. Out of scope
- No change to `sw_cp` / `SwCpDispatcher`: the `dispatcher.py:114` guard stays as the
  last-line safety net (direct `sw_cp` calls, and the still-valid
  `count == batch_count` full-window pass).
- No experiment YAML edits.
- `step_size > 1` interaction unchanged: clamping only lowers the loop's upper bound; the
  inner `sw_cp` iteration math (`dispatcher.py:129-131`) is untouched.

## 6. References
- `controller.py:2436-2618` — `incremental_sw_cp` (target).
- `sw_cp/dispatcher.py:112-131` — batch-count guard + iteration range.
- `sw_cp/partition.py:120-203` — `build_stage_2_batch_list`, `validate_and_get_batch_count`.
- `metadata/20260708/sw_cp_tl_test.yaml:47-59` — example `incremental_sw_cp` config entry.
- `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` — related SW-CP TL work.
