# Pre-merge review follow-up commits — `20260705_rj_scope_option` → `main`

**Provenance.** A pre-merge review of the whole branch (`origin/main...HEAD`, 71
commits / 202 files) was run on 2026-07-08. Verdict: **mergeable, 0 critical
findings** — the core scheduling / CSR / dispatch logic was verified sound
(objective signs, coarsen↔reconstruct index mapping, right-justify,
idle-insertion, `obj_bound=None`, step-contract invariants). What remains is a
short list of latent defects, misleading docstrings, an analysis-tooling
robustness gap, and test-coverage gaps. This file turns that list into
**independent, individually-green commits** so each is easy to review.

Nothing here changes the objective or any solver result. Two items (WP1, WP2)
are latent defects that don't fail today but disarm future foot-guns; the rest
are docs/tests/housekeeping.

---

## Execution conventions (read first)

- Python via `uv run ...` (never bare `python`). See project `CLAUDE.md`.
- **One work package = one commit.** WPs are independent; do them in any order,
  but the listed order (WP1 → WP9) keeps each commit self-contained and green.
- After **each** WP, before committing:
  - `uv run ruff check` (and `uv run ruff format` if it reports formatting).
  - Run the WP's targeted `uv run pytest ...` (given per-WP).
- Commit on the **current feature branch** `20260705_rj_scope_option` (already
  checked out — it is *not* `main`). **Do not push** unless the user asks. Get
  the user's go-ahead before the first commit.
- Commit messages: Conventional-Commits style (title ≤ ~50 chars), and end the
  message body with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Final gate before declaring merge-ready: `uv run ruff check` +
  `uv run ruff format --check` + `uv run pytest -q` (full suite) all green.

> Root cause note (for context, **no action**): the branch's `pyproject.toml`
> enabled isort (`[tool.ruff.lint] extend-select = ["I"]`). That is an
> intentional branch change — keep it. It is the reason for the WP2 fix and for
> the pure-reformat churn in several `scripts/*.py`. WP2 only protects the one
> import-order-sensitive file from it.

---

## WP1 — `fix(neh-cp): reject proportional batch_tl_mode`  *(latent defect)*

**Why.** `step_tl_resolver.py:10` widened the shared
`BatchTlMode = Literal["constant","linear","proportional"]`. `SwCpOption`
guards `"proportional"` (needs a `kappa`), but `NehCpOption`
(`algorithm/neh_cp/option.py:48`) reuses the type with **no `__post_init__`
guard and no `kappa` field**. `resolve_per_step_tl` returns `None` for
proportional (correct — sw_cp computes `kappa·ntf` inline in its own
dispatcher), and `neh_cp/dispatcher.py:226` treats `None` as *"no per-batch
limit"*. Net effect: `NehCpOption(batch_tl_mode="proportional",
total_timelimit_seconds=T)` is type-valid but silently runs **every NEH-CP batch
with no CP time limit** (the first solve can eat the whole budget). Before this
branch, `"proportional"` was not a valid value at all. Latent today (no config
wires it), so fail loudly instead.

**Change.** `src/ffc_ddw_sum_et/algorithm/neh_cp/option.py` — add a
`__post_init__` after the field block (after the `objective_lower_bound` field,
before the `coerce_skip_pf_below_obj` classmethod):

```python
def __post_init__(self) -> None:
    if self.batch_tl_mode == "proportional":
        raise ValueError(
            "batch_tl_mode='proportional' is not supported by NehCpOption "
            "(NEH-CP has no per-op time-limit multiplier); "
            "use 'constant' or 'linear'."
        )
```

> First check `algorithm/base/alg_option.py` (`AlgOption`): if it already
> defines `__post_init__`, call `super().__post_init__()` as the first line.
> `NehCpOption` is `frozen=True, slots=True` — raising in `__post_init__` is
> fine (no attribute writes).

**Test.** New file `tests/algorithm/neh_cp/test_option.py`:

```python
"""Validation tests for NehCpOption."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.neh_cp import NehCpOption  # fall back to .option if needed


def test_proportional_batch_tl_mode_rejected() -> None:
    with pytest.raises(ValueError, match="proportional.*not supported"):
        NehCpOption(batch_tl_mode="proportional")


def test_constant_and_linear_accepted() -> None:
    assert NehCpOption(batch_tl_mode="constant").batch_tl_mode == "constant"
    assert NehCpOption(batch_tl_mode="linear").batch_tl_mode == "linear"
```

> Confirm the import path: `tests/algorithm/sw_cp/test_option.py` imports
> `from ffc_ddw_sum_et.algorithm.sw_cp import SwCpOption`, so the neh_cp package
> likely re-exports `NehCpOption` the same way. If not, import from
> `...algorithm.neh_cp.option`.

**Verify.** `uv run pytest tests/algorithm/neh_cp/test_option.py -q` and
`uv run pytest tests/algorithm/neh_cp -q` (make sure `test_tl_schedule.py` still
passes — it tests the resolver directly and is unaffected).

---

## WP2 — `fix(io): pin load-bearing import order against isort`  *(latent defect)*

**Why.** `src/ffc_ddw_sum_et/io/__init__.py` documents (lines 1-8) a deliberate
**io-internal-first, then cross-package** import order to avoid a partial-init
`ImportError` (the `parameters → io → algorithm → parameters` cycle documented
in `TODOS.md`). Enabling isort auto-sorted the file into pure alphabetical,
reversing that order — so the comment now *contradicts* the code. It doesn't
crash today only because `parameters/__init__.py` and `algorithm/__init__.py`
are import-empty; it will crash the moment any sorter/module gains a
`from ffc_ddw_sum_et.io import ...` at module load. (Confirmed this is the only
order-sensitive file isort touched.)

**Change.** `src/ffc_ddw_sum_et/io/__init__.py` — (a) add `# isort: skip_file`
as the **very first line**, and (b) restore the internal-first grouping:

```python
# isort: skip_file
# Import order matters here.
#
# Several modules in ``parameters/`` and ``algorithm/`` reach back into this
# package (``from ffc_ddw_sum_et.io import TextDataParser, Table2DManager,
# NumericTV, ...``) during their own initialization. If we trigger those
# foreign packages before this package's namespace is populated, we get a
# partial-init ImportError. So io-internal-only modules are imported first,
# THEN io modules with cross-package deps.
from .typing import NumericTV, ScalarTV, numeric_type_set, scalar_type_set
from .text_data_parser import TextDataParser
from .df_manager import DfManager
from .table_2d_manager import Table2DManager

from .parallel_mc_cost_heatmap import (
    HeatmapSort,
    SignedCostHeatmapData,
    build_signed_cost_matrix,
    dump_signed_cost_heatmap_yaml,
    heatmap_title,
    load_signed_cost_heatmap_yaml,
    make_figure,
)
from .schedule_json import dump_preemptive_schedule_json, dump_solution_json
from .schedule_yaml import (
    dump_preemptive_schedule_yaml,
    dump_schedule_yaml,
    load_preemptive_schedule_yaml,
    load_schedule_yaml,
)
```

Leave `__all__` unchanged.

> If ruff does not honor `# isort: skip_file` in your version, use a file-level
> `# ruff: noqa: I001` at the top instead. Either directive must survive
> `ruff check --fix`.

**Verify.**
- `uv run ruff check src/ffc_ddw_sum_et/io/__init__.py` → passes (no `I001`).
- `uv run ruff check --fix src/ffc_ddw_sum_et/io/__init__.py` then `git diff` →
  the order is **not** re-sorted (proves the directive holds).
- `uv run python -c "import ffc_ddw_sum_et.io"` → no error.

---

## WP3 — `docs: fix two misleading docstrings`  *(no behavior change)*

**Why.** Two docstrings actively mislead a maintainer.

**Change 3a.** `src/ffc_ddw_sum_et/solution/objectives.py` — the
`compute_weighted_earliness_tardiness` docstring (lines ~29-36) says the CSR
caller passes the *original* instance and that "Passing the *coarsened* instance
with `time_factor=factor` … **is a bug**." Both halves are false: the CSR seed
path passes the **coarsened** instance (`coarsen_solve_reconstruct.py:200-201`,
`229-230`, `time_factor=factor`), and that is correct because
`coarsen_processing_times` preserves the **original-scale** due window on the
coarsened instance (SSOT). Replace that invariant paragraph with:

```
    Invariant (caller's responsibility, not enforced): ``time_factor * C`` and
    *instance*'s due window must be in the **same time unit**. The CSR seed path
    passes the **coarsened** instance with ``time_factor=factor``; this is
    correct because ``coarsen_processing_times`` preserves the
    **original-scale** due window on the coarsened instance (SSOT), so
    ``factor * C^c`` is measured against the original window. (This is the same
    scale-consistency requirement the ``time_factor=1`` path already imposes
    between *schedule* and *instance*; ``time_factor`` only generalises it.)
```

> Re-read `coarsen_solve_reconstruct.py:188-230` and `:479` first and match the
> wording to what the code actually does (`:479` passes the original `instance`
> at the default `time_factor=1` for the final objective; the seed evals pass
> `coarsened` at `time_factor=factor`).

**Change 3b.** `scripts/build_rpdf_tr_tables.py` — the module docstring (lines
~11-13) claims its two-sided `best = min(Alg, BKS)` RPDf "matches the convention
in … `scripts/build_results_index.py`." `build_results_index.py:335` is a
**one-sided signed** variant (`(bestObj - BKS_data) / ((bestObj + BKS_data)/2)`,
no `min()`). They agree only when `Alg ≥ BKS`. Qualify it:

```
0). This is the symmetric two-sided form used in ``metrics_ffc_ddw_wET.py`` of
the Juntaek-PhD-Thesis scripts. NOTE: ``scripts/build_results_index.py``'s
``RPDf_BKS_data`` is a *one-sided signed* variant (no ``min(Alg, BKS)``
substitution); the two agree only when ``Alg >= BKS`` and diverge (0 here vs a
negative value there) whenever the algorithm beats BKS. Do not cross-reference
them as the same metric on Alg-beats-BKS instances.
```

**Verify.** `uv run ruff check src/ffc_ddw_sum_et/solution/objectives.py
scripts/build_rpdf_tr_tables.py`. (Docstring-only; no runtime test.)

---

## WP4 — `fix(scripts): surface partial coverage in csr idle-mode cross-check`

**Why.** `scripts/analyze_csr_idle_modes.py:127-131` inner-joins the run summary
onto the dump on `(instanceName, mode, factor)` with **no coverage check**, then
prints `max |diff| = {max_abs} (0 => dump reproduces the run exactly)`. If the
summary is partial/older, or a scenario name doesn't reduce cleanly (the
`re.match(r"(\w+?)_f(\d+)", …)` mode-parse absorbs underscores, since `\w`
includes `_`), unmatched rows are silently dropped and `max_abs` is computed
over an unverified subset — a clean-looking `0` over incomplete coverage.

**Change.** In `scripts/analyze_csr_idle_modes.py`, between the `merged = …`
inner-join (ends ~line 131) and the `max_abs = …` line (132), insert:

```python
        if len(merged) != len(df):
            n_missing = len(df) - len(merged)
            print(
                f"WARNING: summary matched {len(merged)}/{len(df)} dump rows "
                f"({n_missing} unmatched on (instanceName, mode, factor)); "
                "the cross-check below is PARTIAL, not a full reproduction."
            )
```

> Optional (same commit): add a one-line comment above the `re.match` noting the
> `\w+?` group absorbs underscores, so scenario names must be exactly
> `<mode>_f<int>`. Not required for correctness of the warning.

**Verify.** `uv run ruff check scripts/analyze_csr_idle_modes.py`. (CLI
diagnostic; no unit test. If a `*_summary.csv` + dump pair is handy, a manual
`uv run python scripts/analyze_csr_idle_modes.py … --summary …` smoke run is a
bonus, not required.)

---

## WP5 — `test(csr): cover multi-machine reconstruct path`

**Why.** Every instance in `tests/algorithm/test_coarsen_solve_reconstruct.py`
uses `stage_2_machine_count=(1, 1)` (`_make_small_ddw_instance` default at line
43; `_make_tiny_2job_2stage_instance` at 181). So the greedy machine-assignment
loop in reconstruct — the **only** path that can raise "No free machine" — is
never exercised with `|M_i| > 1`. Logic was verified correct in review, but the
highest-risk branch has no coverage.

**Change.** Add one test mirroring
`test_run_returns_feasible_or_optimal_on_tiny_instance` (line 185), but with a
stage that has ≥2 machines, e.g. `_make_small_ddw_instance(...,
stage_2_machine_count=(2, 2))` (enough jobs that a stage genuinely runs ops in
parallel). Assert: the full CSR `run(...)` completes without raising, returns a
feasible/optimal status, and the reconstructed final schedule is queryable for
every `(stage_id, job_id)` end time (and, if a helper exists, that no two ops
share a machine-time overlap).

**Verify.** `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py -q`.

---

## WP6 — `test(controller): cover initialize_by_dispatch_v4`

**Why.** `initialize_by_dispatch_v4` (`controller.py:1730`, default
`V4_PRIORITY_SET`, which uniquely exercises `wxd7`) has **no test**, while
`initialize_by_dispatch_v3` has three (`test_controller.py:469, 493, 512`).

**Change.** Add v4 twins of the three v3 tests in
`tests/orchestration/test_controller.py`:
- `..._v4_registers_single_incumbent` — one `_register`, `obj_bound is None`,
  `len(history) == 1`, incumbent obj matches report.
- `..._v4_picks_min_of_N` — iterate `V4_PRIORITY_SET` (import it — see how the
  test module imports `V3_PRIORITY_SET`/priority constants at the top), compute
  each sd/rd obj via `controller._dispatch_by_simple_sequence_with_iit` /
  `_dispatch_by_reversed_sequence_with_iit`, assert `report.obj_value` equals the
  min.
- `..._v4_is_deterministic` — two controllers, same `obj_value`.

**Verify.** `uv run pytest tests/orchestration/test_controller.py -q -k dispatch_v4`.

---

## WP7 — `test(sw-cp): cover rj all_ops scope option + validation`

**Why.** `rj_right_justify_scope` (`sw_cp/option.py:99`,
`Literal["rtf_only","all_ops"]`) has runtime validation
(`__post_init__:139-142`) but **no test** exercises the `"all_ops"` value or the
rejection path (`test_option.py` covers kappa/proportional/step_size only).

**Change 7a (required).** Add to `tests/algorithm/sw_cp/test_option.py`:

```python
def test_rj_scope_default_is_rtf_only() -> None:
    assert SwCpOption().rj_right_justify_scope == "rtf_only"


def test_rj_scope_all_ops_is_valid() -> None:
    assert (
        SwCpOption(rj_right_justify_scope="all_ops").rj_right_justify_scope
        == "all_ops"
    )


def test_rj_scope_invalid_rejected() -> None:
    with pytest.raises(ValueError, match="rj_right_justify_scope must be one of"):
        SwCpOption(rj_right_justify_scope="everything")  # type: ignore[arg-type]
```

**Change 7b (recommended, same commit).** Add a behavior test to
`tests/algorithm/sw_cp/test_dispatcher.py` that runs the sw_cp dispatcher with
`rj_right_justify_scope="all_ops"` on a small instance and asserts the produced
schedule is valid and its rj-objective does not exceed the pre-rj incumbent
(mirror the existing dispatcher tests' setup; the consumer at
`sw_cp/dispatcher.py:223` already asserts `rj_obj <= inc_obj + 1e-6` at runtime,
so this pins the `all_ops` branch specifically).

**Verify.** `uv run pytest tests/algorithm/sw_cp -q`.

---

## WP8 — `test(solution): cover delay_operations_latest_leq_obj_contrib`  *(lowest priority)*

**Why.** `FFcSchedule.delay_operations_latest_leq_obj_contrib` (`ffc_schedule.py`
~1533), the RTF-only right-justify primitive, has **no direct unit test** — only
an indirect runtime assert in its sole consumer (`sw_cp/dispatcher.py:223`).

**Change.** Add a unit test in `tests/solution/test_ffc_schedule.py` that builds
a small schedule with known start/end times and per-op obj-contrib caps, calls
the method, and asserts the post-conditions the review proved hold:
- every selected op only moves **right** (`new_end >= old_end`);
- last-stage ops are capped at `d⁺` (never pushed into tardiness);
- no precedence/overlap violation is introduced.

Read the method body and the sibling `delay_job_latest_leq_obj_contrib_all_stages`
first to get the exact argument shapes (the method takes `(job, stage, mc)`
tuples). This is the most involved WP; if the setup proves large, it is
acceptable to land WP1-WP7 first and do WP8 separately.

**Verify.** `uv run pytest tests/solution/test_ffc_schedule.py -q`.

---

## WP9 — `chore(todos): mark EDDUB-ordering + register-decorator deferrals as triggered`

**Why.** This branch *realized* two deferred `TODOS.md` items. Per project
`CLAUDE.md`, **do not auto-execute deferred TODOs** — just record that their
"when to act" trigger is now met, so the user can decide.

**Change.** `TODOS.md`:
- *"SSOT: consolidate the EDDUB+w⁺ dispatch-seed ordering"* — note both sites now
  exist live and were **verified identical** in the 2026-07-08 review:
  `_dispatch_seed_job_sequence` (`coarsen_solve_reconstruct.py:135`, key
  `(dw_ub[j], -twt[j], given_index[j])`) and
  `FFcDDWParameters.get_eddub_twt_job_sequence`
  (`ffc_ddw_params.py:632`, key `(dw_ub[j], -twt[j], job_2_pos[j])`). The
  "when to act" precondition (getter exists) is satisfied → consolidation is now
  actionable (see optional WP10).
- *"Decorator for `solution_manager.register` boilerplate"* — the "when to act"
  threshold (step-method count) is clearly crossed: the controller now has ~20+
  step methods and ~29 `_register` call sites (was 2 when the TODO was written).

**Verify.** `uv run ruff check` (markdown-only change; nothing to test).

---

## WP10 (OPTIONAL — user opt-in only) — `refactor(csr): route dispatch seed through get_eddub_twt_job_sequence`

**Only do this if the user explicitly approves** (it is a deferred `TODOS.md`
item; `CLAUDE.md` forbids executing those autonomously). The two orderings are
byte-for-byte equivalent, so this is a pure DRY consolidation guarded by the
existing CSR tests.

**Change.** In `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`:
delete the module-local `_dispatch_seed_job_sequence` (line 135) and replace its
call site (line 180, `seq = _dispatch_seed_job_sequence(coarsened)`) with
`seq = coarsened.get_eddub_twt_job_sequence()`. Then update the corresponding
`TODOS.md` entry to "done."

**Verify.** `uv run pytest tests/algorithm/test_coarsen_solve_reconstruct.py -q`
(these tests guard the seed ordering) + full `uv run pytest -q`.

---

## Out of scope (considered, deliberately **not** planned)

- **`reporting.py:719` CP-gap report hard-wired to `init_filter="v3"`.** v4/mixed
  *solving* runs get no CP-gap artifacts. This is by design (the feature was
  built for the v3 paired-dispatch experiment; `test_v3_filter` encodes it). If
  v4 CP-gap artifacts are ever wanted, thread `init_filter` from run/reporter
  config instead of the literal. No change now.
- **`controller.py:1335` `last_stage_rebuild_config_used` records `"best"`**, not
  the variant that won the tiebreak. Consistent with the field's documented
  value domain; observability nicety only, no functional impact.
- **`_generate_gantt_charts` `"%d heatmap"` log under-counts** CSR-trajectory
  graphs (count captured before they're appended). Cosmetic log line only — fix
  in passing if you happen to touch that function, otherwise skip.

---

## Suggested commit sequence (recap)

| # | Commit | Kind | Priority |
|---|--------|------|----------|
| WP1 | `fix(neh-cp): reject proportional batch_tl_mode` | code + test | high (latent defect) |
| WP2 | `fix(io): pin load-bearing import order against isort` | code | high (latent defect) |
| WP3 | `docs: fix two misleading docstrings` | docs | medium |
| WP4 | `fix(scripts): surface partial coverage in csr idle-mode cross-check` | code | medium |
| WP5 | `test(csr): cover multi-machine reconstruct path` | test | medium |
| WP6 | `test(controller): cover initialize_by_dispatch_v4` | test | medium |
| WP7 | `test(sw-cp): cover rj all_ops scope option + validation` | test | medium |
| WP8 | `test(solution): cover delay_operations_latest_leq_obj_contrib` | test | low |
| WP9 | `chore(todos): mark deferrals as triggered` | docs | low |
| WP10 | `refactor(csr): route dispatch seed through get_eddub_twt_job_sequence` | refactor | optional (opt-in) |

WP1-WP9 are independent and safe to land as-is. WP10 needs explicit user
approval. Run the full-suite gate (`ruff check` + `ruff format --check` +
`pytest -q`) once at the end before calling the branch merge-ready.
