# Resume-from-base — follow-ups (harden the `feat(resume)` commit)

**Date:** 2026-07-09 · **Branch:** `20260708_more_timelimit`
**Parent:** `plans/experiment/20260709/resume_from_base.md` (the feature; implemented &
verified). **Commit under review:** `feat(resume): seed tail from base run
incumbent`.

Two **warnings** were raised in the `/git-workflow` review of that commit. Neither
blocks it, but both should be closed before the resume path is considered
production-hardened. This doc is the actionable record so the next session can
execute without re-deriving context. Do them in order (tests first — they lock in
current behaviour before the refactor in W2 touches `main.py`).

---

## W1 — Add a regression test for the resume *execution* path

**Why:** the commit ships the whole resume runtime but only one automated test
(`tests/io/test_schedule_json_roundtrip.py`, the JSON loader). The behaviour that
actually makes resume correct — prefix-skip, incumbent seeding, clock back-dating,
obj_log merge — was verified **only by two manual integration runs**. There is no
pytest guard, so a future refactor could silently break it.

**Cheap fixtures already exist** — reuse them (no CP solves needed):
`tests/orchestration/test_controller.py::_make_instance` (tiny 3-job / 2-stage
`FFcDDWParameters`) and `_make_controller`. `run_fam` is a fast, CP-free step —
ideal filler for a multi-step test flow.

### Units to cover (most→least isolated)

1. **`FFcDDWSubroutineControllerCore.run(flow_resume_idx)`** — prefix skip.
   Build a controller with a 3-step flow of cheap steps (e.g.
   `[{"method":"run_fam"}, {"method":"run_fam"}, {"method":"run_fam"}]`), call
   `run(flow_resume_idx=2)`, assert `method_call_counts["run_fam"] == 1` (only the
   tail ran). Also assert `run(flow_resume_idx=-1)` runs all 3 (back-compat), and
   that a method listed in `method_names_to_run_before_resume` **does** re-run in
   the prefix pass.

   **Caveat to lock — the empty `method_names_to_run_before_resume` (§ 2 safety
   assumption).** FFcDDW's set is empty; the sibling `../hybridflowshop`'s is
   **non-empty** (`{set_random_seed, set_cp_model_as_base_cp_model,
   set_final_time_reserve}`) because its CP tail reads controller state a prefix
   *setup* step establishes. FFcDDW's empty set is only safe if the tail
   (`incremental_sw_cp`, `solve_base_model_cpsat`) **self-initializes** — i.e. it
   does not read any attribute a skipped prefix step would have set (beyond the
   restored incumbent + LB). Add an assertion for this: seed a bare incumbent (as
   unit 2 does), run **only** the tail (`run(flow_resume_idx=<prefix_len>)`) with
   the prefix pure-skipped, and assert the tail completes and registers without
   `AttributeError`/`None`-state failures. This is the guard that makes the empty
   run-before set correct, and it must be re-checked whenever a new prefix step is
   added.

2. **`seed_resume_incumbent`** — build a controller, hand it an `FFcDDWSolution`
   (schedule from `_make_instance` via `run_fam` once, or a hand-built
   `FFcSchedule`), call `seed_resume_incumbent(sol, obj_value=X, obj_bound=Y)`.
   Assert: `solution_manager.get_incumbent()` is that solution;
   `best_obj_bound == Y`; the single history entry's `report.step_label` matches
   `^\d+-resume_seed$` (i.e. **not** `"ROOT"` — this is the § 7 bug's guard).

3. **`FFcDDWSingleInstanceRunner._merge_base_obj_log`** — pure & easy, highest
   value. Write a fake base `<ins>_obj_log.json` under a tmp
   `resume_root/<ins>/`, put `resume_root` in `output_metadata`, call
   `_merge_base_obj_log(value_data, value_notes, bound_data, bound_notes)` with
   empty dicts. Assert base data+notes landed and it returned `True`; and that a
   missing file returns `False` + leaves dicts untouched (no raise). Then a
   `_save_obj_log`-level test: history containing a `resume_seed`-labelled report
   → assert the merged output **drops** the `resume_seed` note but keeps its data
   point, and keeps the base's real prefix notes.

4. **`FFcDDWSingleInstanceRunner._apply_resume`** — set `resume_solution` +
   `resume_elapsed_time` on a runner (constructed with a layout — see
   `tests/orchestration/test_reporting.py` / `test_multi_scenario_runner.py` for
   how they wire a layout), call `_apply_resume()`, assert the incumbent is
   registered and `ctrlr.timer` was back-dated (`elapsed_sec ≈ resume_elapsed`).
   Assert it **raises** when `resume_solution is None` (the silent-load backstop).

5. **`FFcDDWMultiInstanceRunner._load_resume_data`** — build a tmp resume_root with
   one instance's `_solution.json` (`dump_solution_json`) + `_instance_result.yaml`
   (`dump_yaml` of a minimal `InstanceResult`-shaped dict), construct the runner in
   RESUME mode, assert each SIR got `resume_solution` (obj_value/obj_bound from the
   manifest) + `resume_elapsed_time`; and that a missing file makes it `raise`.

6. **`main._resolve_resume_dir`** — tmp dir with / without `subroutine_flow.yaml`;
   assert it returns the path when the cache exists and raises
   `FileNotFoundError` otherwise, and `ValueError` when `resume_dir` key absent.

### Optional but ideal — one end-to-end resume test

A single small integration test that runs a base FULL_RUN then a RESUME run on the
tiny instance with an all-`run_fam` flow (base = 1 step, resume flow = 2 steps),
asserting: prefix `run_fam` count == 0 in the resume manifest's
`method_call_counts`, resume `first_obj_value == base obj_value`, and the resume
obj_log spans `[~0, …]` (base merge). This mirrors the manual § 5 verification but
in CI. Keep it fast (no CP steps); mark `@pytest.mark.slow` only if needed.

**Acceptance:** `uv run python -m pytest tests/orchestration tests/io` green with
the new tests; each of units 1–6 has ≥1 assertion; the `resume_seed`/`ROOT` label
guard (unit 2) and the obj_log-merge suppression (unit 3) are both asserted.

---

## W2 — Replace the `assert` in `main.py`'s RESUME branch

**Where:** `main()` scenario loop:

```python
if mode == RunMode.RESUME:
    assert flow_validator is not None and base_flow is not None   # ← this
    flow_resume_idx = flow_validator.validate_subroutine_flow_prefix(...)
```

**Why it's a warning:** `assert` is stripped under `python -O`, so in an optimized
run the type-narrowing vanishes; if `flow_validator`/`base_flow` were ever `None`
here the failure would surface later as an opaque `AttributeError` instead of a
clear message. It also trips the repo's standing "assert in production path"
concern (see `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` § 8 code-cleanup and
`TODO.md`).

**Recommended fix (cleanest):** extract the RESUME-specific computation into a
small helper that takes **non-optional** args, so no narrowing assert is needed and
the logic is unit-testable (feeds W1 unit 6):

```python
def _resume_flow_idx(
    validator: SubroutineFlowValidator, base_flow: list, scenario_flow: list
) -> int:
    return validator.validate_subroutine_flow_prefix(
        DynamicDataObject.from_obj(base_flow),
        DynamicDataObject.from_obj(scenario_flow),
    )
```

Then the loop calls it only inside the `if mode == RunMode.RESUME:` block where
`flow_validator`/`base_flow` are already known-set — no `assert`, no `-O` hazard.
(Alternative if you'd rather not refactor: replace the `assert` with an explicit
`if flow_validator is None or base_flow is None: raise RuntimeError("internal: "
"RESUME state not initialized")` — a real check that survives `-O`.)

**Acceptance:** no `assert` remains on the RESUME path in `main.py`; `uv run ruff
check main.py` clean; the base+resume smoke (`ins_index=[60,61,63]`) still exits 0.

---

## W3 — "do it better" review of the resume commit (done 2026-07-09)

Cross-read of the commit against **routix** (the vendored base) and
**`../hybridflowshop`** (the reference this feature was ported from), to find
anything the port could do better. Recorded here so it isn't re-derived.

### Reassurances (verified — leave as-is)

- **`run(flow_resume_idx)` reimplementation is faithful, not redundant.** routix's
  base `SubroutineController.run()` is only `_run_flow(flow); post_run_process()`
  and does **not** honour `flow_resume_idx` itself (the attribute lives on the
  runner classes). So the controller override is required, and it reproduces the
  base semantics exactly.
- **Timer back-dating is *better* than the reference.** FFcDDW back-dates the clock
  **once** from the base manifest's real `elapsed_time` (in `_apply_resume`).
  hybridflowshop instead re-times the skipped no-op prefix steps with an
  `ElapsedTimer` accumulator (near-zero) and re-adjusts — more convoluted and less
  accurate. No change wanted here.

### Actioned now

- **W3.1 — `resume_dir` auto-resolution (implemented).** `main._resolve_resume_dir`
  no longer requires a hand-updated timestamped scenario path. Two forms:
  (1) an explicit scenario dir holding `subroutine_flow.yaml` (verbatim, old
  behaviour); (2) **`latest:<base_scenario_name>`** → the newest run dir under
  `base_output_dir` that emitted that scenario (run dir names are `init_run_root`
  timestamps, so lexicographic == chronological). Signature gained
  `base_output_dir`; the RESUME resolve block moved below logging setup so the
  resolved dir is logged (`RESUME: resolved base scenario dir: …`). Removes the
  §4.7/§6 "re-point after each fresh base run" footgun.
  `sw_cp_tl_test_cases.yaml` → `resume_dir: latest:mcf_lb_fmm_neh_cp`.

  **Why the scenario name is mandatory (a bug caught in review — do not "simplify"
  this back).** The first cut of this used an unqualified `latest` = *newest
  `subroutine_flow.yaml` anywhere under `output_dir`* (via `rglob` + mtime). That is
  wrong: a **case** run's scenario dir also carries a flow cache **and** both resume
  artifacts (`_solution.json`, `_instance_result.yaml`), so nothing downstream
  rejects it. Re-running the cases config — the normal iteration loop — then
  resolved `latest` to the *previous cases run*, whose 5-step flow fully covers each
  case scenario. Two observed failure modes:
  - *loud* (multi-scenario cases config): `validate_subroutine_flow_prefix` raises
    `Subroutine flow prefix mismatch` (e.g. p70's kappa vs p50's) — safe, but it
    makes `latest` useless for re-runs;
  - *silent* (flows coincide, e.g. a single-scenario config): `flow_resume_idx ==
    len(flow)` → **every step skipped**, the seeded incumbent persisted as the
    result, and a full plausible-looking but computationally empty artifact set
    emitted. Verified: `identical flow -> flow_resume_idx = 5 / len = 5`.

  Naming the base scenario makes mis-resolution structurally impossible (base is
  `mcf_lb_fmm_neh_cp`; case scenarios are `p50` / `kappa_*` / `p60` / `p70`). Bare
  `latest` is now an explicit `ValueError`.

- **W3.1b — no-op resume guard (implemented).** `main()` raises when
  `flow_resume_idx >= len(scenario_flow)` ("scenario X would run no steps — its
  N-step flow is fully covered by the base flow at resume_dir"). This closes the
  *silent* mode above for **any** `resume_dir`, including the explicit-path form that
  predates W3.1 (pointing `resume_dir` at a case scenario dir was already reachable).

  **Verification (2026-07-09).** `_resolve_resume_dir` unit-checked for both forms +
  6 error cases, incl. an asserted "`latest:<base>` never picks a cases run" fixture.
  End-to-end, run **twice** to exercise the re-run path that broke the first cut:
  base `20260709T224155_215865` → cases `…224205_814044` (exit 0, 101.7 s) → cases
  again (exit 0, 101.7 s), the second resolving to the **base** even though a cases
  run was newer on disk. §5 invariants re-checked on the second cases run: prefix
  counts all 0, `incremental_sw_cp`=1, `first_obj_value`==base `obj_value` (49713),
  `first_obj_bound`==base `obj_bound` (22291), tail improved to 46799, obj_log 232
  pts spanning `t=[0.29, 22.51]`, no `ROOT`/`resume_seed` notes. Guard exercised
  separately (resume_dir → a cases `p50` with a p50-only config) → raises as designed.
- **W3.2 — `method_names_to_run_before_resume` empty-set guard → folded into W1
  unit 1** (see the caveat added under unit 1 above). The empty set is
  parity-justified but load-bearing on "the tail self-initializes"; W1 must assert a
  bare-seeded tail runs without prefix state.

### Deferred → recorded in `TODO.md` (do NOT act autonomously)

- **Persistent controller `obj_store` instead of `_merge_base_obj_log`.** The
  resume obj_log is reconstructed by re-reading + merging the base JSON, duplicating
  the obj_log schema across writer and reader. hybridflowshop uses a persistent
  `obj_store` the tail simply appends to. Broad change (touches *every* run's
  obj_log), YAGNI while the merge works.
- **Upstream the RESUME flow-skip loop into routix.** Both repos maintain divergent
  copies of the `flow_resume_idx` skip; bundle with the existing `# TODO: apply to
  routix` wall-clock change, adopting FFcDDW's cleaner slice + single-back-date form.

### Considered, not worth acting on

- `load_schedule_json` returns obj_value/obj_bound that `_load_resume_data`
  discards (manifest is SSOT) — but `elapsed_time` forces a 2-file read anyway, so
  collapsing to one file buys nothing. Leave.
- Prefix flow duplicated between base yaml and every case yaml — guarded by the
  validator (drift → error); explicit full-flow + validate is defensible KISS.

---

## Out of scope (don't do here)

- Making the base-obj_log merge opt-out via config — deferred until someone
  actually wants tail-only obj_logs (YAGNI; note in `resume_from_base.md` § 4.8).
- Generalising resume to N base scenarios / per-scenario-name mapping — current
  topology (1 base scenario → N case scenarios) is all these experiments need.

## Do NOT commit autonomously

Per working agreement, leave changes staged/unstaged for the user to review and
commit. `uv run ruff check` / `ruff format` after edits.
