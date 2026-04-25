# Plan: `total_timelimit` kwarg for `neh_cp`

## Context

In commit `5bb35cc` (`metadata/20260425/neh_cp_config_9.yaml`), the user
removed an extra `b15` scenario where `cp_tl` had been mis-derived. The
intent of the sweep is to keep the **total CP-SAT budget per instance**
constant across `added_batch_size`:

    cp_tl_per_batch * (n / added_batch_size) ≈ 0.024 · n · c

i.e. the per-batch limit must be `cp_tl = 0.024 · c · added_batch_size`.
Computing that by hand is error-prone (the dropped scenario used `0.2c`
instead of `0.36c` for `b=15`). Adding a `total_timelimit` kwarg lets
users specify the budget once and have `neh_cp` derive `cp_tl`
automatically.

`cp_tl_2nd_obj` is intentionally out of scope for this change.

## Design

Add `total_timelimit: float | str | None = None` as a new kwarg to:

- `NehCpConstructor.run` — `src/ffc_ddw_sum_et/orchestration/neh_cp.py:72`
- `FFcDDWSubroutineController.neh_cp` — `src/ffc_ddw_sum_et/orchestration/controller.py:917`

Same grammar as `cp_tl` (uses the existing `resolve_cp_tl` helper at
`src/ffc_ddw_sum_et/orchestration/tl_resolver.py:6`, which already
handles `"<x>nc"` / `"<x>c"` / float / None).

### Resolution logic in `NehCpConstructor.run` (replaces lines 172–181)

```python
cp_tl_from_arg = resolve_cp_tl(cp_tl, n, stage_count)

if total_timelimit is not None:
    total_seconds = resolve_cp_tl(total_timelimit, n, stage_count)
    derived = total_seconds * added_batch_size / n
    cp_tl_seconds = (
        min(cp_tl_from_arg, derived) if cp_tl_from_arg is not None else derived
    )
else:
    cp_tl_seconds = cp_tl_from_arg

cp_tl_2nd_obj_seconds = (
    resolve_cp_tl(
        cp_tl_2nd_obj if cp_tl_2nd_obj is not None else cp_tl,
        n,
        stage_count,
    )
    if minimize_makespan_lex
    else None
)
```

Notes:

- `n == 0` already raises before this block (line 169–170), so the
  division is safe.
- The derived formula uses the simple `n / added_batch_size` — it does
  not subtract the larger first batch (`max(added_batch_size,
  2·max_machines)`). The user specified this exact formula; mismatch
  with the actual batch count is acceptable as a small over-budget.
- Per user direction: when both `cp_tl` and `total_timelimit` are
  given, take `min` silently (no warning, no error).
- `cp_tl_2nd_obj` fallback is left untouched (deferred work). When only
  `total_timelimit` is set and `cp_tl_2nd_obj` is `None` under
  `minimize_makespan_lex=True`, the secondary solve will get **no time
  limit** — same as today's behavior when both `cp_tl` and
  `cp_tl_2nd_obj` are `None`.

### Signature placement

Add `total_timelimit` immediately after `cp_tl` in both signatures so
the related parameters are colocated. Mirror in the controller wrapper
delegation call.

### Docstring

In `NehCpConstructor.run` docstring, add an entry for `total_timelimit`
that mirrors `cp_tl`'s grammar wording, and append a sentence to the
`cp_tl` entry along the lines of:
> "When `total_timelimit` is also set, the per-batch limit becomes
> `min(cp_tl, total_timelimit · added_batch_size / n)`."

## Files to modify

- `src/ffc_ddw_sum_et/orchestration/neh_cp.py` — add kwarg, resolution
  logic, docstring entry.
- `src/ffc_ddw_sum_et/orchestration/controller.py` — add kwarg to
  `FFcDDWSubroutineController.neh_cp` and forward it.

No YAML config update in this change. Once merged, the user can choose
to switch existing configs (e.g. `metadata/20260425/neh_cp_config_9.yaml`)
to use `total_timelimit: "0.024nc"` instead of hand-tuned `cp_tl`.

## Existing utilities reused

- `resolve_cp_tl` at `src/ffc_ddw_sum_et/orchestration/tl_resolver.py:6`
  — handles `"0.024nc"`, `"0.36c"`, floats, and `None` already.
- The existing `apply_cumulative_tl` machinery at lines 274–304 needs
  no change: it already operates on the resolved `cp_tl_seconds`,
  whatever its origin.

## Verification

1. `uv run ruff check` and `uv run ruff format` after edits.
2. Spot-check semantics with a small Python session (or a tiny pytest):

   ```python
   # n=100, c=5, added_batch_size=15, total_timelimit="0.024nc"
   # → total_seconds = 0.024 * 100 * 5 = 12.0
   # → derived cp_tl = 12.0 * 15 / 100 = 1.8 = 0.36c (since c=5)  ✓
   ```

   For `added_batch_size=5` → derived 0.6 = 0.12c ✓
   For `added_batch_size=10` → derived 1.2 = 0.24c ✓

3. Existing test `tests/orchestration/test_controller.py::test_neh_cp_registers_full_schedule`
   should still pass unchanged (it calls `controller.neh_cp(cp_tl=1.0)`).

4. Optional: run one scenario from
   `metadata/20260425/neh_cp_config_9.yaml` after rewriting it with
   `total_timelimit: "0.024nc"` and confirm the per-batch CP-SAT TL
   matches `0.36c` in the logs.
