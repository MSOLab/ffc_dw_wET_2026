# Review fixes — `20260708_rj_scope_option_pre_PR` → `main`

**Provenance.** A pre-merge review of the branch (`origin/main...HEAD`, 208 files)
was run on 2026-07-08. Verdict: **8 issues (2 critical, 6 informational)** — all
latent defects or housekeeping, no objective/solver-result changes. This file
turns that list into **two cohesive, individually-green commits** (the six work
packages below are grouped by theme — see *Suggested commit sequence*).

Nothing here changes the objective or any solver result. WP2 is the substantive
fix (validate option fields at construction); WP1 is a cheap internal-consistency
safe-guard layered on top of it. The rest are type-safety, magic-number
extraction, docstring updates, and test gaps.

> **Note on WP1 vs WP2.** `strategy` reaches `_build_dispatch_seed_schedule`
> *only* via `option.seed_dispatch` (call chain: `run_…` →
> `_solve_coarsened_model` / `_seed_and_obj` → `_build_dispatch_seed_schedule`).
> So WP2 alone fully closes the user-facing hole — an invalid `seed_dispatch`
> is rejected at construction and can never reach the dispatch function. WP1
> is therefore **not** needed for user-input safety; its enduring value is
> catching a *future maintenance* inconsistency (a new strategy added to the
> `Literal` + `__post_init__` valid-set but not wired into the dispatch
> branches would silently fall through to `"mixed"` — WP2 cannot catch this).
> Hence WP1 is a one-line guard, not a critical fix.

---

## Execution conventions (read first)

- Python via `uv run ...` (never bare `python`). See project `AGENTS.md`.
- **The six WPs land as two commits** (grouping in *Suggested commit sequence*).
  The WP sections below are the units of *work*; the two commits are the units
  of *history*. Do the WPs within a commit together, then commit once.
  - **Commit A** = WP1 + WP2 + WP6 — the seed_dispatch enum surface.
  - **Commit B** = WP3 + WP4 + WP5 — non-behavioral type/constant cleanups.
- Commit A must land before Commit B (both touch `coarsen_solve_reconstruct.py`
  and `controller.py` in different regions — sequential, not reorderable).
- Before **each commit**:
  - `uv run ruff check` (and `uv run ruff format` if it reports formatting).
  - Run the union of the constituent WPs' targeted `uv run pytest ...`.
- Commit messages: Conventional-Commits style (title ≤ ~50 chars).
- Final gate: `uv run ruff check` + `uv run ruff format --check` + `uv run pytest -q` all green.

---

## WP1 — `fix(csr): guard unknown seed_dispatch strategy` *(safe-guard)*

**Why.** `_build_dispatch_seed_schedule` (`coarsen_solve_reconstruct.py:156-198`)
dispatches on `strategy` via early-returns (`"v3"` → return, `"v4"` → return,
`"job_wise"` → return) and then **unconditionally falls through** to the
`"mixed"` code path. With WP2 in place, an invalid *user* value can no longer
reach here — but if a future strategy is added to the option's `Literal` and
`__post_init__` valid-set yet not wired into these branches, it would silently
execute mixed-dispatch. This WP adds a loud failure for that case. It is
defense-in-depth, not the primary fix (see the note at the top); keep it to a
single line and do not add a dedicated test (WP2's tests cover the value space).

**Change.** `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py` — add a
guard at the **top** of `_build_dispatch_seed_schedule`, before the `if
strategy == "v3":` check. (A top guard is used rather than a trailing
`else: raise` because the function uses early-returns with shared setup between
the `"job_wise"` and `"mixed"` branches, so there is no single `if/elif` chain
to close.)

```python
def _build_dispatch_seed_schedule(
    coarsened, factor, strategy, idle_mode="flooring",
):
    if strategy not in {"job_wise", "mixed", "v3", "v4"}:
        raise ValueError(f"Unknown seed_dispatch strategy: {strategy!r}")
    if strategy == "v3":
        ...
```

**Verify.** `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py -q`
(no new test; existing suite must stay green)

---

## WP2 — `fix(csr): validate seed_dispatch and idle_mode in CoarsenSolveReconstructOption` *(critical)*

**Why.** `CoarsenSolveReconstructOption` (`coarsen_solve_reconstruct.py:76-105`)
declares `seed_dispatch: Literal["job_wise", "mixed", "v3", "v4"]` and
`idle_mode: Literal["flooring", "ceiling", "lookahead"]` but has no
`__post_init__` to validate these. Compare with `SwCpOption.__post_init__`
(sw_cp/option.py:114-159) which validates its Literal fields. An invalid
`seed_dispatch` silently falls through to `"mixed"` (see WP1); an invalid
`idle_mode` crashes deep inside `insert_idle_time`.

**Change.** `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py` — add
`__post_init__` to `CoarsenSolveReconstructOption` (after the field block):

```python
def __post_init__(self) -> None:
    valid_dispatch = {"job_wise", "mixed", "v3", "v4"}
    if self.seed_dispatch not in valid_dispatch:
        raise ValueError(
            f"seed_dispatch must be one of {valid_dispatch}, got {self.seed_dispatch!r}"
        )
    valid_idle = {"flooring", "ceiling", "lookahead"}
    if self.idle_mode not in valid_idle:
        raise ValueError(
            f"idle_mode must be one of {valid_idle}, got {self.idle_mode!r}"
        )
```

> Check if `CoarsenSolveReconstructOption` inherits from a base with
> `__post_init__` — if so, call `super().__post_init__()` first.

**Test.** New tests in `tests/algorithm/test_coarsen_solve_reconstruct.py` (or
a new `tests/algorithm/test_csr_option.py`):

```python
def test_seed_dispatch_invalid_rejected() -> None:
    with pytest.raises(ValueError, match="seed_dispatch"):
        CoarsenSolveReconstructOption(seed_dispatch="mied")

def test_idle_mode_invalid_rejected() -> None:
    with pytest.raises(ValueError, match="idle_mode"):
        CoarsenSolveReconstructOption(idle_mode="turbo")

def test_valid_defaults_accepted() -> None:
    opt = CoarsenSolveReconstructOption()
    assert opt.seed_dispatch == "mixed"
    assert opt.idle_mode == "flooring"
```

**Verify.** `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py -q`

---

## WP3 — `fix(solution): type idle_mode as Literal across call sites` *(informational)*

**Why.** `insert_idle_time(idle_mode: str = "flooring")` (`ffc_schedule.py:1655`)
is compared against bare strings `"flooring"`, `"ceiling"`, `"lookahead"`. The
same weak `str` typing propagates to 10+ call sites across
`coarsen_solve_reconstruct.py`, `dispatcher/paired.py`, `controller.py`. Typos
silently raise at runtime instead of being caught by type checkers. Only
`CoarsenSolveReconstructOption` correctly declares `Literal`.

**Change.** Update the type annotation at each declaration site:

1. `src/ffc_ddw_sum_et/solution/ffc_schedule.py:1655` — `idle_mode: str` →
   `idle_mode: Literal["flooring", "ceiling", "lookahead"]`
2. `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py` — function
   signatures at ~lines 139, 205, 232: `idle_mode: str` → `Literal[...]`
3. `src/ffc_ddw_sum_et/algorithm/dispatcher/paired.py` — function signatures
   at ~lines 43, 74, 168, 209: `idle_mode: str` → `Literal[...]`
4. `src/ffc_ddw_sum_et/orchestration/controller.py:2628` — method parameter:
   `idle_mode: str` → `Literal[...]`

Add `from __future__ import annotations` if not already present, and import
`Literal` from `typing` where needed.

**Verify.** `uv run ruff check src/` (type annotations only; no behavior change)

---

## WP4 — `refactor: extract DEFAULT_COARSEN_FACTOR constant` *(informational)*

**Why.** `factor: int = 50` appears as the default in both
`coarsen_solve_reconstruct.py:82` and `controller.py:2621`. Should be a single
named constant so changing the experiment default doesn't require synchronizing
two files.

**Change.** In `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`,
add at module level:

```python
DEFAULT_COARSEN_FACTOR: int = 50
```

Then reference it in both `CoarsenSolveReconstructOption.factor` default and
`controller.py`'s `coarsen_solve_reconstruct` method parameter default.

**Verify.** `uv run ruff check src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py src/ffc_ddw_sum_et/orchestration/controller.py`

---

## WP5 — `refactor: extract FP_TOLERANCE constant` *(informational)*

**Why.** `1e-6` is used as a floating-point comparison tolerance in
`sw_cp/dispatcher.py:223-224` without a named constant.

**Change.** In `src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`, add at
module level:

```python
_FP_TOLERANCE: float = 1e-6
```

Replace the bare `1e-6` literals in the assertion and conditional.

**Verify.** `uv run ruff check src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`

---

## WP6 — `docs: fix stale docstrings for CSR seed_dispatch` *(informational)*

**Why.** Two docstrings are stale after `v4` was added:

1. Module docstring (`coarsen_solve_reconstruct.py:11-12`) says `seed_dispatch`
   is `"job_wise"` or `"mixed"` — should list all four values.
2. Controller method docstring (`controller.py:2651-2655`) lists three values,
   omitting `"v4"`.

**Change.** Update both docstrings to list `{"job_wise", "mixed", "v3", "v4"}`.

**Verify.** `uv run ruff check src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py src/ffc_ddw_sum_et/orchestration/controller.py`

---

## Suggested commit sequence (recap)

The six WPs collapse into **two commits**. Land **A before B** (both edit
`coarsen_solve_reconstruct.py` / `controller.py` in disjoint regions, so the
order is sequential, not reorderable).

### Commit A — `fix(csr): validate & guard seed_dispatch, refresh docs`

| WP | Contents | Kind |
|----|----------|------|
| WP2 | validate `seed_dispatch` + `idle_mode` in `__post_init__` | code + test |
| WP1 | one-line guard for unknown `seed_dispatch` in dispatch fn | code |
| WP6 | list all four `seed_dispatch` values in module + controller docstrings | docs |

One concern — the `seed_dispatch` enum surface. WP2 is the substantive fix; WP1
is defense-in-depth; WP6 documents the validated value set.
Gate: `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py -q`.

### Commit B — `refactor: tighten idle_mode typing & extract constants`

| WP | Contents | Kind |
|----|----------|------|
| WP3 | `idle_mode: str` → `Literal[...]` across all declaration sites | code |
| WP4 | extract `DEFAULT_COARSEN_FACTOR` (CSR + controller) | code |
| WP5 | extract `_FP_TOLERANCE` (`sw_cp/dispatcher.py`) | code |

All non-behavioral (type annotations + named constants); runtime results
unchanged. Gate: `uv run ruff check src/`.

Run the full-suite gate (`ruff check` + `ruff format --check` + `pytest -q`)
once after Commit B.

---

## Out of scope (considered, deliberately **not** planned)

- **Test gaps** (#8 in review): `build_cp_gap_comparison_df`,
  `write_cp_gap_artifacts`, `error_if_infeasible=True`, `seed_dispatch="v4"`
  through CSR adapter — these are coverage improvements for new code, not
  defects. Can be addressed in a follow-up PR.
- **`docs/algorithms/pw_cp.md` pw_cp→sw_cp renames** — already done in this
  branch.
- **`TODOS.md` status updates** — already done in this branch (WP9 of the
  earlier plan).
