# Plan: `calc_mcf_lb_and_derive_full_sch` composite subroutine

## Context

`metadata/20260505/mcf_lb_init_37_config.yaml`'s scenario
`build_full_sch_p_adjust_r_half_adjust` (lines 165–200) hardcodes a
6-step flow inside YAML:

1. `apply_lb_by_mcf` (base)
2. `heuristic_last_stage_only_sch_from_mcf_lb` (base)
3. `build_full_sch_from_last_stage_only_sch` → registers solution #1
4. `apply_lb_by_mcf` with `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch`,
   `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch`, `adjust_r_by_half`
5. `heuristic_last_stage_only_sch_from_mcf_lb` with the same three flags
6. `build_full_sch_from_last_stage_only_sch` → registers solution #2

Internally (controller.py L552, L565, L1089, L1102), the makespan
delta used by adjust-flag branches is clamped:
`makespan_delta = max(incumbent_makespan - <reference_makespan>, 0)`.
When the incumbent makespan is already ≤ MCF preemptive makespan,
`delta == 0`, so the second round (steps 4–6) runs with `p_adjust = 0`
and `r_adjust = 0` — a wasteful no-op that still produces a duplicate
solution registration.

Goal: package the 6-step flow into a single composite subroutine on
`FFcDDWSubroutineController`, with two behavioral changes baked in:

1. The makespan-delta gate computes the **raw**
   `incumbent − reference` without the `max(…, 0)` clamp.
2. When that raw delta is `≤ 0`, skip round 2 entirely. So solution
   is registered twice in the typical (`delta > 0`) case, once when
   `delta ≤ 0`.

The composite also exposes two boolean parameters,
`adjust_p` and `adjust_r`, that select what round 2 should do:

| `adjust_p` | `adjust_r` | round 2 forwards…                                                                                                                                                  |
|------------|------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `False`    | `False`    | round 2 is skipped — same effect as `makespan_delta ≤ 0`. Solution registered once.                                                                                  |
| `True`     | `False`    | `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True`                                                                                                            |
| `False`    | `True`     | `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=True` and `adjust_r_by_half=True` (the half-adjust behaviour is bundled with `adjust_r`)                          |
| `True`     | `True`     | all three: `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True`, `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=True`, `adjust_r_by_half=True`              |

The existing scenario `build_full_sch_p_adjust_r_half_adjust` stays
unchanged for back-compatibility; a new scenario is added that drives
the new method with `adjust_p: true, adjust_r: true`.

## Implementation

### 1. Add `calc_mcf_lb_and_derive_full_sch` to `FFcDDWSubroutineController`

**File**: `src/ffc_ddw_sum_et/orchestration/controller.py`

Place the new method between `build_full_sch_from_last_stage_only_sch`
(L1235–L1352) and `run_mcf_lb_4` (L1354), so it sits with the other
composite `run_*` / `*_from_*` helpers and follows
`run_mcf_lb_4`'s incumbent-registration discipline.

```python
def calc_mcf_lb_and_derive_full_sch(
    self,
    job_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_priority: Literal["contrib", "dist"] = "dist",
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    machine_then_job: bool = False,
    adjust_p: bool = False,
    adjust_r: bool = False,
) -> SubroutineReport:
    """Composite step: MCF-LB → full schedule, then a conditional second
    round with p/r adjustments.

    Round 1 always runs:
      1. ``apply_lb_by_mcf`` (base)
      2. ``heuristic_last_stage_only_sch_from_mcf_lb`` (base)
      3. ``build_full_sch_from_last_stage_only_sch`` → registers
         solution #1 on ``self.solution_manager``.

    Round 2 runs **only when both** of the following hold:
      * ``adjust_p or adjust_r`` is ``True``;
      * ``makespan_delta = incumbent_makespan −
        self.mcf_preemptive_schedule.makespan > 0`` (computed with no
        ``max(…, 0)`` clamp).

    When round 2 fires it forwards:
      4. ``apply_lb_by_mcf(
            adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=adjust_p,
            adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=adjust_r,
            adjust_r_by_half=adjust_r,
         )``
      5. ``heuristic_last_stage_only_sch_from_mcf_lb`` with the same
         three flags.
      6. ``build_full_sch_from_last_stage_only_sch`` → registers
         solution #2 (twice total).

    Args:
        job_priority: Forwarded to round-1 and round-2
            ``heuristic_last_stage_only_sch_from_mcf_lb``.
        last_stage_only_placement_priority: Forwarded as
            ``placement_priority`` to round-1 and round-2
            ``heuristic_last_stage_only_sch_from_mcf_lb``. Renamed at
            the composite layer to make clear it's the last-stage
            heuristic's tiebreak knob, not an MCF-step option.
        draw_pmtn_sch_heatmap: Forwarded as ``draw_heatmap`` to
            round-1 and round-2 ``apply_lb_by_mcf``. Renamed at the
            composite layer to make clear it controls the **MCF
            preemptive** schedule's C-cost heatmap (not a heatmap of
            the full schedule).
        heatmap_sort: Forwarded to round-1 and round-2
            ``apply_lb_by_mcf``.
        machine_then_job: Forwarded to both
            ``build_full_sch_from_last_stage_only_sch`` calls.
        adjust_p: When ``True``, round 2 enables
            ``adjust_p_by_full_sch_and_last_stage_only_pmtn_sch`` on
            both the MCF and heuristic steps. Default ``False``.
        adjust_r: When ``True``, round 2 enables
            ``adjust_r_by_full_sch_and_last_stage_only_pmtn_sch`` and
            ``adjust_r_by_half`` together (the half-adjust is bundled
            with ``adjust_r`` here — split it back into two knobs only
            if a future caller needs the non-halved variant).

    Returns:
        The most recent ``SubroutineReport`` (round-2's
        ``build_full_sch_from_last_stage_only_sch`` when round 2
        fires; otherwise round-1's).
    """
    self.apply_lb_by_mcf(
        draw_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
    )
    self.heuristic_last_stage_only_sch_from_mcf_lb(
        job_priority=job_priority,
        placement_priority=last_stage_only_placement_priority,
    )
    report = self.build_full_sch_from_last_stage_only_sch(
        machine_then_job=machine_then_job,
    )

    if not (adjust_p or adjust_r):
        return report

    incumbent = self.solution_manager.get_incumbent()
    if incumbent is None or incumbent.schedule is None:
        # build_full_sch reverse-dispatch produced no schedule (see
        # controller.py:1301–1310). Nothing to adjust against.
        return report
    incumbent_makespan = int(incumbent.schedule.makespan)
    ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
    makespan_delta = incumbent_makespan - ls_only_pmtn_makespan  # no clamp

    if makespan_delta <= 0:
        self.logger.info(
            "calc_mcf_lb_and_derive_full_sch: incumbent makespan=%d, "
            "ls_only_pmtn makespan=%d, delta=%d <= 0 — skipping adjust round",
            incumbent_makespan,
            ls_only_pmtn_makespan,
            makespan_delta,
        )
        return report

    self.apply_lb_by_mcf(
        draw_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=adjust_p,
        adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=adjust_r,
        adjust_r_by_half=adjust_r,
    )
    self.heuristic_last_stage_only_sch_from_mcf_lb(
        job_priority=job_priority,
        placement_priority=last_stage_only_placement_priority,
        adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=adjust_p,
        adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=adjust_r,
        adjust_r_by_half=adjust_r,
    )
    return self.build_full_sch_from_last_stage_only_sch(
        machine_then_job=machine_then_job,
    )
```

#### Why this shape

- **Reuses existing primitives** (`apply_lb_by_mcf`,
  `heuristic_last_stage_only_sch_from_mcf_lb`,
  `build_full_sch_from_last_stage_only_sch`) — no logic duplication.
- **The internal `max(…, 0)` clamp at L552 / L565 / L1089 / L1102 is
  irrelevant inside round 2** because round 2 only runs when raw
  `delta > 0` — so `max(positive, 0) == positive`. No need to touch
  those four sites.
- **Two short-circuits** match the spec verbatim:
  - `adjust_p == False and adjust_r == False` → return after round 1
    (one registration).
  - `delta <= 0` → return after round 1 (one registration).
  - Otherwise → round 2 runs (two registrations total).
- **`adjust_r` bundles `adjust_r_by_half`** because the requested
  semantics for the new scenario are the half-adjust variant; if a
  future caller wants full-delta `r` adjustments they can either
  stop using this composite or we extend the API later.
- **Defaults** (`job_priority="end_time"`,
  `placement_priority="dist"`) match the values used in scenarios
  `build_full_sch_*` in the YAML, so callers can omit them. YAML can
  still override.
- **Type imports** `PmPrmpSortKey` and `HeatmapSort` are already in
  the controller module (L49, L50) — no new imports needed.
- **Routix dispatch**: methods are looked up by name via reflection
  (`SubroutineController._call_method`), so adding the public method
  is enough — no registry edit required.

### 2. Add a new scenario to the experiment config

**File**: `metadata/20260505/mcf_lb_init_37_config.yaml`

Append a 7th scenario after `build_full_sch_p_adjust_r_half_adjust`
(line 200), preserving the existing 6:

```yaml
  - name: "calc_mcf_lb_and_derive_full_sch_p_adjust_r_half_adjust_pos_delta"
    timelimit: 300.0
    output_subdir: "calc_mcf_lb_and_derive_full_sch_p_adjust_r_half_adjust_pos_delta"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        job_priority: "end_time"
        last_stage_only_placement_priority: "dist"
        adjust_p: true
        adjust_r: true
```

(Scenario name proposed; rename in the implementation step if a
shorter form is preferred. The `output_subdir` must equal the
scenario `name` to match the rest of the file's convention and avoid
collision.)

## Critical files

- `src/ffc_ddw_sum_et/orchestration/controller.py` — add new method
  between L1352 and L1354.
- `metadata/20260505/mcf_lb_init_37_config.yaml` — append new scenario
  after L200.

## Reused (not modified)

- `apply_lb_by_mcf` (controller.py:426) — round-1 base call and
  round-2 adjust call.
- `heuristic_last_stage_only_sch_from_mcf_lb` (controller.py:964) —
  round-1 base call and round-2 adjust call.
- `build_full_sch_from_last_stage_only_sch` (controller.py:1235) —
  registers incumbent at L1344. Called once or twice.
- `self.solution_manager.get_incumbent()` and
  `self.mcf_preemptive_schedule` — both already populated by the
  round-1 base calls.

## Verification

1. **Lint**:
   - `uv run ruff check src/ffc_ddw_sum_et/orchestration/controller.py`
   - `uv run ruff format src/ffc_ddw_sum_et/orchestration/controller.py metadata/20260505/mcf_lb_init_37_config.yaml`
2. **Unit / integration**:
   - `uv run pytest tests/orchestration/ -v` (regression on existing
     subroutines).
3. **End-to-end smoke**: edit a copy of `mcf_lb_init_37_config.yaml`
   to keep only the new scenario plus a tiny `ins_index: [0, 1]`
   override, then:
   ```sh
   uv run python main.py --config metadata/20260505/mcf_lb_init_37_config.yaml
   ```
   Confirm in the per-instance log:
   - For instances where round 1's full-schedule makespan exceeds the
     MCF preemptive makespan (typical): both `apply_lb_by_mcf:
     adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True`
     log lines fire, and the per-instance schedule artifacts contain
     two `6_full_sch_from_ls_only_sch` registrations.
   - For instances where the gate fires (`delta <= 0`): the
     `calc_mcf_lb_and_derive_full_sch: … delta=… <= 0 — skipping
     adjust round` line appears, and only one full schedule is
     registered.
4. **Side-by-side**: leave the existing
   `build_full_sch_p_adjust_r_half_adjust` scenario in the same
   config and compare its `hfs_summary*.csv` row against the new
   scenario's row. Expect: identical objective for instances where
   `delta > 0` (round-1+round-2 mirrors the 6-step flow), and
   shorter elapsed time for instances where `delta <= 0` (the new
   scenario short-circuits round 2 instead of running it as a
   no-op).

## Move the plan into the repo

Per `feedback_plan_location` memory, after this plan is approved
copy the contents to `plans/20260505/calc_mcf_lb_and_derive_full_sch.md`
in the project repo so the design history travels with the codebase.
