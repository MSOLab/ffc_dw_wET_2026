# Plan: split `_only_sch` vs `_only_pmtn_sch` adjust flags (breaking-change fix)

## Context

The currently-staged changes silently changed the meaning of two existing
controller kwargs:

- `adjust_p_by_full_sch_and_last_stage_only_sch`
- `adjust_r_by_full_sch_and_last_stage_only_sch`

Originally these read `ls_only_makespan` from
`self.last_stage_only_sol.schedule.makespan` (the **non-preemptive
last-stage-only heuristic schedule**). The staged diff
(`controller.py` L529 / L1019) rewires them to
`self.mcf_preemptive_schedule.makespan` (the **preemptive MCF
relaxation schedule**) and adds a `makespan` property on
`MCFPreemptiveSchedule`.

Flag name still says `_only_sch`, but the semantics now refer to a
different schedule — a breaking change for any caller / config that
relies on the original meaning. Recent experiments
(`metadata/20260504/mcf_lb_init_*_config.yaml`, 5 files) were
authored under the new (preemptive) semantics and depend on it; the
original semantics should still be reachable for older / future
callers under a flag that accurately names the schedule it consults.

Goal: keep both semantics, each behind an accurately-named flag.

## Decisions (confirmed with user)

1. **YAML migration**: rewrite all `_only_sch: true` lines in the 5
   YAML configs to `_only_pmtn_sch: true` so recent experiment
   semantics are preserved.
2. **Mutual exclusion**: a call where any `_only_sch=True` knob and
   any `_only_pmtn_sch=True` knob are simultaneously set raises
   `ValueError`.
3. **Diagnostic schema**: keep the existing
   `adjust_params_last_stage_only_makespan` field (populated only on
   `_only_sch` path) and add a sibling
   `adjust_params_last_stage_only_pmtn_makespan` (populated on
   `_only_pmtn_sch` path). CSV gets a parallel
   `lastStageOnlyPmtnMakespan` column.
4. **Naming convention**: the new flag uses `_only_pmtn_sch` (not
   `_preemptive_sch`) — `pmtn` matches the project's existing
   shorthand (e.g. `parallel_mc_pmtn`, `pm_pmtn`, `1_rj_prmp_rel_dev`)
   and keeps the `last_stage_only_*_sch` shape consistent across
   the two flags.

## Files to modify

- `src/ffc_ddw_sum_et/orchestration/controller.py`
  - `apply_lb_by_mcf` — kwargs (L426-435), inner `_ensure_makespans`
    closure (L506-530), p/r adjust branches (L532-583), guard / diag
    write block (L573-583).
  - `heuristic_last_stage_only_sch_from_mcf_lb` — kwargs (L912-921),
    inner `_ensure_makespans` (L996-1020), p/r branches (L1022-1059),
    guard / diag write block (L1061-1073).
- `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`
  - Add `adjust_params_last_stage_only_pmtn_makespan: int | None = None`
    next to the existing `adjust_params_last_stage_only_makespan`
    (L59-63). Update the docstring (L49-58) to explain the two
    reference-schedule paths.
- `src/ffc_ddw_sum_et/orchestration/reporting.py`
  - `_write_adjust_params_by_makespan_delta_csv` (L786-870):
    - Read `adjust_params_last_stage_only_pmtn_makespan` alongside
      the existing field; add column `lastStageOnlyPmtnMakespan`
      to the row + header.
    - Emit `""` (empty string) for whichever reference field is
      `None` (mirrors how `pIncrementAdded` / `rIncrementAdded` are
      handled today).
    - Update the docstring (L786-802) to mention both flags and the
      new column.
- `metadata/20260504/mcf_lb_init_35_config.yaml`,
  `metadata/20260504/mcf_lb_init_36_config.yaml`,
  `metadata/20260504/mcf_lb_init_adjust_pj_debug_config.yaml`,
  `metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml`,
  `metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml`,
  `metadata/20260504/mcf_lb_init_adjust_rj_debug_config.yaml`
  - Replace every `adjust_p_by_full_sch_and_last_stage_only_sch: true`
    with `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: true`,
    and the analogous `_r_` flag. (38 occurrences total per
    `grep`.) Leave any `adjust_r_by_half: true` lines alone.

## Implementation steps

### 1. Diagnostic dataclass

In `algorithm/mcf_lb/diagnostic.py`, add the new field and update
the comment block above to clarify:

- `adjust_params_last_stage_only_makespan` is populated when an
  `_only_sch` knob fires (reads from
  `last_stage_only_sol.schedule.makespan`).
- `adjust_params_last_stage_only_pmtn_makespan` is populated when
  an `_only_pmtn_sch` knob fires (reads from
  `mcf_preemptive_schedule.makespan`).
- The two are mutually exclusive within a single call (enforced by
  the controller).
- `adjust_params_makespan_delta` records the chosen delta regardless
  of source.

### 2. Controller — `apply_lb_by_mcf`

a. **Kwargs** (L426-435): add two new kwargs, defaulting to `False`,
   placed immediately after the existing pair so the order reads
   `_only_sch (p, r)`, `_only_pmtn_sch (p, r)`, `adjust_r_by_half`.

```python
adjust_p_by_full_sch_and_last_stage_only_sch: bool = False,
adjust_r_by_full_sch_and_last_stage_only_sch: bool = False,
adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
adjust_r_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
adjust_r_by_half: bool = False,
```

b. **Mutual-exclusion guard** before any of the closure / branches
   run (next to the existing `p_increment / r_multiplier / r_increment`
   validation at L495-504):

```python
uses_only = (
    adjust_p_by_full_sch_and_last_stage_only_sch
    or adjust_r_by_full_sch_and_last_stage_only_sch
)
uses_only_pmtn = (
    adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
    or adjust_r_by_full_sch_and_last_stage_only_pmtn_sch
)
if uses_only and uses_only_pmtn:
    raise ValueError(
        "apply_lb_by_mcf: cannot combine "
        "adjust_*_by_full_sch_and_last_stage_only_sch with "
        "adjust_*_by_full_sch_and_last_stage_only_pmtn_sch in a "
        "single call; pick one reference schedule."
    )
```

c. **Inner closure** (replaces the current `_ensure_makespans` at
   L510-530). The closure now computes one delta against whichever
   reference is requested:

```python
incumbent_makespan: int | None = None
ls_only_makespan: int | None = None
ls_only_pmtn_makespan: int | None = None
makespan_delta: int | None = None

def _ensure_makespans() -> None:
    nonlocal incumbent_makespan, ls_only_makespan
    nonlocal ls_only_pmtn_makespan, makespan_delta
    if makespan_delta is not None:
        return
    incumbent = self.solution_manager.get_incumbent()
    if incumbent is None or incumbent.schedule is None:
        raise ValueError(
            "apply_lb_by_mcf with adjust_(p|r)_by_full_sch_and_..."
            " requires an incumbent schedule on self.solution_manager."
        )
    incumbent_makespan = int(incumbent.schedule.makespan)
    if uses_only_pmtn:
        if self.mcf_preemptive_schedule is None:
            raise ValueError(
                "apply_lb_by_mcf with "
                "adjust_(p|r)_by_full_sch_and_last_stage_only_pmtn_sch"
                "=True requires self.mcf_preemptive_schedule set by a "
                "prior step."
            )
        ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
        makespan_delta = max(incumbent_makespan - ls_only_pmtn_makespan, 0)
    else:  # uses_only
        if (
            self.last_stage_only_sol is None
            or self.last_stage_only_sol.schedule is None
        ):
            raise ValueError(
                "apply_lb_by_mcf with "
                "adjust_(p|r)_by_full_sch_and_last_stage_only_sch=True "
                "requires self.last_stage_only_sol.schedule set by a "
                "prior step."
            )
        ls_only_makespan = int(self.last_stage_only_sol.schedule.makespan)
        makespan_delta = max(incumbent_makespan - ls_only_makespan, 0)
```

d. **p_adjust / r_adjust branches** (L534-567): each existing
   branch fires when its `_only_sch` flag OR its `_only_pmtn_sch`
   flag is `True`. The body is unchanged except the log line names
   the active reference schedule. Pseudocode:

```python
fire_p = (
    adjust_p_by_full_sch_and_last_stage_only_sch
    or adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
)
if fire_p:
    _ensure_makespans()
    n = self.instance.job_count
    m_last = self.instance.last_stage_mc_count
    p_adjust = math.ceil(makespan_delta * m_last / n)
    ref_label = "ls_only_pmtn" if uses_only_pmtn else "ls_only"
    ref_value = ls_only_pmtn_makespan if uses_only_pmtn else ls_only_makespan
    self.logger.info(
        "apply_lb_by_mcf: adjust_p_by_full_sch_and_last_stage_%s_sch=True, "
        "incumbent makespan=%d, %s makespan=%d, delta=%d, "
        "n=%d, m_last=%d, p_adjust=%d",
        ref_label, incumbent_makespan, ref_label, ref_value,
        makespan_delta, n, m_last, p_adjust,
    )
    effective_p_increment = p_increment + p_adjust
```

(Same pattern for `fire_r` mirroring the existing r-branch.)

e. **Diagnostic write block** (L573-583): write whichever reference
   field is populated, leave the other as `None`:

```python
if uses_only or uses_only_pmtn:
    if uses_only_pmtn:
        diag.adjust_params_last_stage_only_pmtn_makespan = ls_only_pmtn_makespan
    else:
        diag.adjust_params_last_stage_only_makespan = ls_only_makespan
    diag.adjust_params_incumbent_makespan = incumbent_makespan
    diag.adjust_params_makespan_delta = makespan_delta
if fire_p:
    diag.adjust_p_increment_added = p_adjust
if fire_r:
    diag.adjust_r_increment_added = r_adjust
```

### 3. Controller — `heuristic_last_stage_only_sch_from_mcf_lb`

Apply the same five sub-changes (a)-(e) at the second method's
counterparts (L912-921, L996-1020, L1022-1059, L1061-1073). The
existing per-method precondition guard at L985-989
(`mcf_preemptive_schedule is None → raise`) already guarantees the
preemptive reference is available without duplication, so the
closure's `uses_only_pmtn` branch here can omit its own preemptive
guard if that's more readable.

### 4. Reporting CSV

In `reporting.py:786-870`, extend the row tuple and the writer:

- Add `ls_only_pmtn_makespan = diag.get("adjust_params_last_stage_only_pmtn_makespan")`.
- Header: insert `lastStageOnlyPmtnMakespan` after
  `lastStageOnlyMakespan`.
- Row tuple: insert `"" if ls_only_pmtn_makespan is None else int(ls_only_pmtn_makespan)`
  in the same position. For the existing
  `lastStageOnlyMakespan` value, also coerce to `"" if None else int(...)`
  (it can now legitimately be `None` when the `_only_pmtn_sch`
  flag was used).

Tuple type annotation widens from
`tuple[str, int | None, str, int, int, int, int | None, int | None]` to
`tuple[str, int | None, str, int | None, int | None, int, int, int | None, int | None]`
(reflecting that both reference makespans can be optional).

### 5. YAML configs

Replace the flag names in the six `metadata/20260504/*.yaml` files
listed above. A single sed pass works:

```sh
sed -i \
  -e 's/adjust_p_by_full_sch_and_last_stage_only_sch:/adjust_p_by_full_sch_and_last_stage_only_pmtn_sch:/g' \
  -e 's/adjust_r_by_full_sch_and_last_stage_only_sch:/adjust_r_by_full_sch_and_last_stage_only_pmtn_sch:/g' \
  metadata/20260504/mcf_lb_init_35_config.yaml \
  metadata/20260504/mcf_lb_init_36_config.yaml \
  metadata/20260504/mcf_lb_init_adjust_pj_debug_config.yaml \
  metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml \
  metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml \
  metadata/20260504/mcf_lb_init_adjust_rj_debug_config.yaml
```

After the sed, verify with
`grep -rn "_only_sch:" metadata/20260504/` returns no matches (the
remaining `_only_pmtn_sch:` matches don't satisfy the trailing
literal `_only_sch:` anchor).

### 6. Move plan to repo

Per project memory (plans live under `plans/<YYYYMMDD>/<slug>.md`),
copy this plan to `plans/experiment/20260505/adjust_full_sch_only_pmtn_sch_flag.md`
once plan mode exits, so the design history stays with the repo.

## Verification

1. `uv run ruff check src/ffc_ddw_sum_et` — clean.
2. `uv run ruff format src/ffc_ddw_sum_et metadata/20260504` — no
   diff (or formats the YAMLs idempotently).
3. **Static check on configs**:
   - `grep -rEn "adjust_.*last_stage_only_sch:" metadata/20260504/`
     returns no matches.
   - `grep -rEn "adjust_.*last_stage_only_pmtn_sch:" metadata/20260504/`
     returns the expected 38 occurrences.
4. **Behavioral spot-check** — run one debug config end-to-end, e.g.:

   ```sh
   uv run python -m ffc_ddw_sum_et.main \
     --config metadata/20260504/mcf_lb_init_adjust_pj_debug_config.yaml
   ```

   Confirm:
   - Run completes without `ValueError`.
   - `adjust_params_by_makespan_delta_*.csv` artifact is written.
   - Header includes `lastStageOnlyPmtnMakespan`; rows have
     `lastStageOnlyMakespan = ""` and `lastStageOnlyPmtnMakespan`
     populated (since the YAMLs migrated to the `_only_pmtn_sch`
     flag).
5. **Mutual-exclusion guard** — quick negative test by hand-editing
   one debug YAML to set both flag pairs to `true` and re-running:
   expect `ValueError` with the new message. Revert the YAML.
6. **Original semantics still reachable** — hand-edit one debug
   YAML to set `_only_sch: true` and `_only_pmtn_sch: false`,
   re-run: expect successful run with `lastStageOnlyMakespan`
   populated and `lastStageOnlyPmtnMakespan = ""`. Revert the YAML.

## Out of scope

- The unrelated `int(...)` cast removal that the staged diff also
  applied to the makespan reads is not required by this plan and
  can be either preserved or restored according to the reviewer's
  preference; the plan code samples keep the casts for safety
  (`MCFPreemptiveSchedule.makespan` already returns `int`, but
  `incumbent.schedule.makespan` typing is not audited here).
- `MCFPreemptiveSchedule.makespan` property itself stays as-added in
  the staged diff.
