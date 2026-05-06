# Plan: scenario `timelimit` accepts `nc`/`n`/`c`/`m` expression

## Goal

Allow `metadata/.../*.yaml` scenario blocks to specify
`timelimit: "0.09nc"` (or `"Xn"` / `"Xc"` / `"Xm"`) in addition to the
current absolute-seconds float. Resolved per-instance via the existing
`resolve_value_expr()` so the grammar matches the subroutine-level
`total_timelimit` field already in use.

## Why this shape (recap of decisions)

- **Single field, expression grammar** (not a separate
  `timelimit_n_by_c_multiplier` scalar). Reuses
  `orchestration/value_resolver.py` already used by `controller.neh_cp` for
  `cp_tl` / `total_timelimit`. Single source of truth for how `nc`/`n`/`c`/`m`
  is parsed.
- **Resolve in `FFcDDWSingleInstanceRunner.__init__`**, not in the controller.
  Orchestration owns IO/expression concerns; algorithm boundary stays clean
  (per `CLAUDE.md` algorithm-principles guidance).
- **Replace dict with a fresh dict** rather than mutating in place. The
  scenario's `stopping_criteria` dict is **shared by reference** across all
  SIRs in the scenario (verified: `reporting.py:327` → routix MIR
  `__init__` → `ffcddw_multi_instance_runner.py:54` loop → routix SIR
  `__init__`, none of which copy). Mutating that shared dict would clobber
  later SIRs — exactly the alias bug the sibling project hit and resolved
  via `StoppingCriteria.from_dict(stopping_criteria.to_obj())` clone.

## Edits

### 1. `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`

Add at the **end of `__init__`** (after `self._layout = self.layout`):

```python
sc = self.stopping_criteria
if isinstance(sc, dict):
    raw_tl = sc.get("timelimit")
    if isinstance(raw_tl, str):
        n = self.instance.job_count
        c = self.instance.stage_count
        m = self.instance.last_stage_mc_count
        resolved = float(resolve_value_expr(raw_tl, n, c, m))
        self.stopping_criteria = {**sc, "timelimit": resolved}
        self.logger.debug(
            "Resolved scenario timelimit '%s' for %s "
            "(n=%d, c=%d, m=%d) -> %.3fs",
            raw_tl, self._ins_name, n, c, m, resolved,
        )
```

Plus the import:

```python
from .value_resolver import resolve_value_expr
```

Notes:

- `isinstance(sc, dict)` guard is necessary because routix permits passing
  an already-built `StoppingCriteria` object too; we only resolve when the
  dict path is in use (which is what `main.py` produces today).
- Uses `**sc` to preserve any future stopping fields without enumerating.
- Float numeric `timelimit` (legacy) skips both branches → unchanged behavior.

### 2. `metadata/20260506/20260506_mcf_lb_neh_cp.yaml` (the file under edit)

Change `timelimit: 2.0` to the user-intended expression once they confirm
the value (e.g. `timelimit: "0.09nc"`). **Do not pre-edit this in the same
PR — the value choice is the user's experiment design call**, separate from
the orchestration plumbing change.

### 3. Test

Add a unit test under `tests/algorithm/` (or a new `tests/orchestration/`
if more appropriate — will check existing layout) that:

- Constructs a fake/minimal `FFcDDWParameters`-like instance (or uses an
  existing fixture — will scan `tests/`) with known `n`, `c`, `m`.
- Builds a `FFcDDWSingleInstanceRunner` with
  `stopping_criteria={"timelimit": "0.5nc"}`.
- Asserts `runner.stopping_criteria["timelimit"] == 0.5 * n * c` (float).
- Asserts the **upstream dict** passed in is **unchanged** (alias-safety
  regression test — this is the whole point).
- Confirms float input (`{"timelimit": 2.0}`) passes through unchanged.

If `tests/` has no SIR-level harness, we'll fall back to a focused unit
test of just the resolution snippet extracted into a tiny helper, but
prefer testing through the runner so the alias check is real.

## Out of scope

- Changing `InstanceResult.timelimit` reporting format (still float seconds
  written by `ffcddw_single_instance_runner.py:352` — already works because
  by the time we get there, the value is float).
- Persisting the original expression string (`"0.09nc"`) anywhere. If
  desired later, would go into the per-scenario manifest, not here.
- Touching `controller_core.py` or `controller.py` — they already see a
  fresh float via the new SIR-level resolution.
- Touching the sibling project pattern's `timelimit_n_by_c_multiplier`
  scalar field — we deliberately use the expression grammar instead.

## Verification after edit

1. `uv run ruff check` (project rule).
2. `uv run pytest tests/...` for the new test plus any SIR-touching tests.
3. Manual smoke: edit the existing yaml to `timelimit: "0.01nc"`, run
   `uv run python main.py ...` against one small instance, confirm INFO
   log shows the resolution and the run actually terminates near the
   expected time.
