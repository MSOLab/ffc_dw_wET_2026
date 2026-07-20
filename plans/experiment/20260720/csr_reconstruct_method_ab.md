# CSR reconstruction method — before/after A/B on the κ=1..32 grid

**Date:** 2026-07-20 · **Status:** planned (not executed)
**Config:** `metadata/20260720/csr_k1_32_recon_fix.yaml`
**Commit under test:** `a6c4150` *fix(csr): preserve machine assignment*

---

## 1. Background — what changed, and what did not

`a6c4150` replaced the algorithm `reconstruct_raw_coarse_schedule` uses to bring
a coarse-grid solution back to the original time scale.

| | before | after (`a6c4150`) |
| --- | --- | --- |
| what is carried over | operation **start times** (`× factor`) | operation → **machine assignment** and per-machine job order |
| how times are set | scaled start + original `p`, then `make_semi_active` | forward stage sweep: `start = max(prev stage end, machine end)` |
| machine assignment | **re-derived** by greedy interval coloring over the scaled times | **taken from the coarse solution** |
| precondition | `factor · coarse_p ≥ p` — otherwise `RuntimeError: No free machine` | none |

**Both produce a schedule that is valid for the original problem.** The old
method was not wrong, and this is not a bug fix. The two are different
algorithms that make different choices, and it is **not known which yields
better objective values**.

### Why it was changed

The precondition. `ceil` coarsening satisfies `factor · coarse_p ≥ p` by
construction, but `max(round(p/K), 1)` and `max(floor(p/K), 1)` do not: an
operation can end past its scaled coarse slot, collide, and be rejected. That
blocked the processing-time rounding-mode experiment
([`csr_coarsening_rounding_modes.md`](csr_coarsening_rounding_modes.md)) before it
could start. Carrying assignment instead of times removes the precondition
entirely — no rounding rule can violate it, because there is nothing left to
violate.

Preserving the coarse solver's machine assignment is a **consequence** of that
redesign, and arguably the more faithful reading of what a coarse solution
decides. But faithful is not the same as better on the objective, which is what
this experiment measures.

## 2. Question

1. **Does the reconstruction method change the objective, and in which
   direction?** Per scenario and per instance.
2. **Does it depend on κ?** The old method's greedy re-derivation had more slack
   to diverge at large κ (longer coarse operations, shorter original ones), so a
   κ-dependent effect is plausible.
3. **Does the K-gradient conclusion survive?** "Coarsening hurts at equal budget;
   K=1 wins" is the load-bearing claim of
   [`../../analysis/20260719/csr_init_k_budget_consolidation.md`](../../analysis/20260719/csr_init_k_budget_consolidation.md),
   and this grid is exactly the κ axis it was measured on.

## 3. Why this cannot be settled without running it

Reconstruction can be isolated offline by driving CSR with `solve: false`
(deterministic dispatch seed, no CP-SAT), and doing so does show the two methods
place a large fraction of operations on different machines. **That measurement
does not predict this experiment's outcome.**

In a real run the reconstructed schedule is not the answer — it is an input to
everything downstream. Inside CSR's `solve_flow` the coarse model is solved by
CP-SAT, candidates are harvested, each is reconstructed and re-scored at original
scale, and the argmin is registered. Changing reconstruction changes **which
candidate wins**, and from there the entire search trajectory. The effect can be
amplified (a different winner leads somewhere else entirely) or erased (later
steps re-optimize past the difference). Neither direction can be argued from the
offline number.

So the offline isolation is a statement about reconstruction; this run is the
statement about results. Only the latter may be cited in an analysis document.

## 4. Design

The comparison is a re-run of the scenarios that produced the κ-gradient table,
under the new code, against the run that produced it.

- **before:** `output/20260719_merge_csr_k1_32/20260720T154525_895426`
  (assembled by `scripts/build_merged_run_dir.py`, reported via
  `metadata/20260719/merge_csr_k1_32.yaml` in `POST_PROCESS_ONLY`)
- **after:** `output/20260720_csr_k1_32_recon_fix/<timestamp>` — this plan's run

`metadata/20260720/csr_k1_32_recon_fix.yaml` is a copy of
`metadata/20260719/merge_csr_k1_32.yaml` differing in **exactly three lines**:

```diff
-run_mode: POST_PROCESS_ONLY
-analysis_dir_path: output/20260719_merge_csr_k1_32/20260720T154525_895426
+run_mode: FULL_RUN
-output_dir: output/20260719_merge_csr_k1_32
+output_dir: output/20260720_csr_k1_32_recon_fix
```

Everything else is byte-identical: 12 scenarios
(`csr_{full,neh}_d2wp_k{1,2,4,8,16,32}`), the same 160-instance
`(T,R) = (0.6, 0.2)` cell, the same `0.09nc` scenario cap with a single-step
outer flow, the same inner `solve_flow`, `idle_mode: lookahead`,
`instance_worker_cnt: 12`, `solver_thread_cnt: 8`.

**Cost:** ≤ 3.75 h (12 workers × 8 solver threads = 96 cores).

**Pre-flight already done:** a 2-instance × 12-scenario smoke run completed in
68 s, confirming the config is valid under `FULL_RUN`.

### The one confound that cannot be removed

The "before" run was produced by **merging scenarios from two source runs** via
symlinks; the "after" run solves all 12 scenarios fresh in one go. Everything
about the algorithm and budget is held constant, but the two were not produced by
the same execution path.

Combined with CP-SAT's run-to-run variance under a wall-clock budget, this means
**a small per-scenario difference is not attributable to the reconstruction
change.** The prior analysis treats run-to-run variance as negligible at
1440-instance means; this grid is 160 instances, i.e. ~1/9 the sample, so the
noise floor here is correspondingly higher. Read per-scenario deltas as
directional and lean on the paired per-instance counts.

## 5. Reading the result — committed in advance

| outcome | reading |
| --- | --- |
| deltas are small and κ-independent | Reconstruction method is not a material lever. The prior κ-gradient conclusions stand as written; restate the figures on the new run and move on to rounding modes. |
| new method is **worse**, κ-dependent | The old method's greedy re-derivation was contributing real quality as an unintended side effect. That is worth capturing deliberately — as an explicit, named post-process — rather than reverting; `TODO.md` already carries the related "`make_semi_active` — allow machine reassignment" idea, and this would be its first evidence. |
| new method is **better** | Preserving the coarse solver's decision pays. Strengthens the case for CSR as a mechanism rather than as a wrapper. |
| the **κ-gradient flattens or reverses** | The largest possible finding here, and it would supersede the rounding-mode question. Stop and re-derive the K conclusions from this run before any further experiment. |

State the outcome as measured, in the analysis document, without borrowing the
offline isolation numbers of §3 to explain it.

## 6. What this does **not** answer

- **Rounding modes.** This is `ceil` only. It establishes the baseline the
  rounding comparison needs, nothing more.
- **The budget sweep.** f is fixed here. The `F_k1` @ T=0.6, f=30 finding
  (2.17 %p, paired 233/0/247) sits on a different axis and needs
  `20260714_csr_tl_scaling_sweep` re-run to be re-checked.
- **The full 1440 grid.** This is one (T, R) cell — the hardest one. Its RPDf
  level (~34 %) is not comparable to a full-grid mean (~15 %).
- **Final solution quality.** Every scenario here has a single-step outer flow,
  so this measures *initialization quality under a fixed initialization budget*,
  exactly as the prior analyses do.

## 7. Order of work

1. This A/B. ← establishes whether the method change moves results
2. Re-derive or restate the κ-gradient conclusions from it.
3. Only then, the rounding-mode experiment
   ([`csr_coarsening_rounding_modes.md`](csr_coarsening_rounding_modes.md)) —
   whose §7 gate requires a `ceil` baseline produced by the current code, since
   it compares rules at the same κ where the effect sought is itself small.

## 8. Execution

```bash
uv run python main.py --config metadata/20260720/csr_k1_32_recon_fix.yaml
```

Record the resulting run directory in a `run setting` provenance commit, then
write the comparison as an analysis document under `plans/analysis/20260720/`.
