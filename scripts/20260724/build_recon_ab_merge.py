"""Assemble the cross-run merge for the reconstruct_mode AB (active vs semi_active).

Combines two full-1440-grid CSR runs into one synthetic POST_PROCESS_ONLY run so
the reporter emits a single ``*_rpdf_comparison.csv`` spanning all three modes per
(kappa, TL) cell:

  * current run (24 scenarios): ``csr_k{K}_tl{f}_semi`` and ``csr_k{K}_tl{f}_active``
    -- coarsen_mode=cumulative, the reconstruct_mode AB.
  * prior run (12 scenarios):   ``csr_k{K}_tl{f}`` -> relabelled ``csr_k{K}_tl{f}_prior``
    -- the historical baseline (default reconstruct_mode=semi_active, same inner
    solve_flow). The prior ``_prior`` and current ``_semi`` should agree within
    the CP-SAT wall-clock noise floor; that agreement is the reproducibility check.

Steps:
  1. symlink every scenario's instance dirs into a fresh merged run dir
     (via scripts/build_merged_run_dir.py -- no copies, source runs untouched);
  2. write a POST_PROCESS_ONLY config whose ``analysis_dir_path`` is that dir and
     whose ``scenarios`` list carries the per-scenario ``subroutine_flow`` copied
     from each source config (prior blocks renamed with a ``_prior`` suffix).

Usage:
    uv run python scripts/20260724/build_recon_ab_merge.py \
        --cur-run  output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252 \
        --prior-run output/20260721_csr_k_f_cumulative/20260721T215135_772079 \
        --dest     output/20260724_merge_recon_ab_vs_prior \
        --config-out metadata/20260724/merge_recon_ab_vs_prior.yaml

Then run the reporter:
    uv run python main.py --config metadata/20260724/merge_recon_ab_vs_prior.yaml

Add ``--dry-run`` to assemble/validate the config only (no symlinks, no merged
dir); ``analysis_dir_path`` is left as a placeholder for inspection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from build_merged_run_dir import build_merged_run_dir  # noqa: E402

PRIOR_SUFFIX = "_prior"
# top-level keys the reporter needs, copied verbatim from the current config
_PASSTHROUGH_KEYS = (
    "benchmark_dir",
    "ins_index_source",
    "bks_table_csv_path",
)
# step kwargs removed after the prior run was recorded; main._reject_deprecated_
# step_kwargs rejects them. They are pure flow metadata here (results come from
# the symlinked instance dirs), so scrub them from the borrowed prior blocks.
_DEPRECATED_STEP_KEYS = ("idle_mode",)


def _scrub_deprecated(obj):
    """Recursively drop deprecated step kwargs from a scenario block."""
    if isinstance(obj, dict):
        return {
            k: _scrub_deprecated(v)
            for k, v in obj.items()
            if k not in _DEPRECATED_STEP_KEYS
        }
    if isinstance(obj, list):
        return [_scrub_deprecated(v) for v in obj]
    return obj


def _load(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _find_config(run_dir: Path) -> Path:
    """The FULL_RUN config is copied into the run dir as ``<name>.yaml``."""
    cands = [
        p
        for p in run_dir.glob("*.yaml")
        if not p.name.endswith("_artifact_layout.yaml")
    ]
    if len(cands) != 1:
        raise SystemExit(
            f"expected exactly one config yaml in {run_dir}, found {cands}"
        )
    return cands[0]


def _scenario_names(config: dict) -> list[str]:
    return [s["name"] for s in config["scenarios"]]


def _relabel_prior(scenario: dict) -> dict:
    """Copy a prior scenario block, suffixing name/output_subdir with _prior
    and scrubbing deprecated step kwargs."""
    out = _scrub_deprecated(scenario)
    out["name"] = scenario["name"] + PRIOR_SUFFIX
    out["output_subdir"] = (
        scenario.get("output_subdir", scenario["name"]) + PRIOR_SUFFIX
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cur-run", type=Path, required=True)
    ap.add_argument("--prior-run", type=Path, required=True)
    ap.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="parent dir for the synthetic merged run",
    )
    ap.add_argument("--config-out", type=Path, required=True)
    ap.add_argument(
        "--merged-dir",
        type=Path,
        default=None,
        help="reuse an already-built merged run dir (skip symlinking)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--intersect-instances",
        action="store_true",
        help="symlink only instances common to every scenario "
        "(defensive; both runs are the full 1440 grid)",
    )
    args = ap.parse_args()

    cur_cfg = _load(_find_config(args.cur_run))
    prior_cfg = _load(_find_config(args.prior_run))

    cur_names = _scenario_names(cur_cfg)
    prior_names = _scenario_names(prior_cfg)

    # --- assemble the merged scenario list (order: prior, semi, active per cell) ---
    prior_blocks = [_relabel_prior(s) for s in prior_cfg["scenarios"]]
    merged_scenarios = cur_cfg["scenarios"] + prior_blocks

    labels = [s["name"] for s in merged_scenarios]
    if len(set(labels)) != len(labels):
        dupes = sorted({lab for lab in labels if labels.count(lab) > 1})
        raise SystemExit(f"duplicate scenario labels after relabel: {dupes}")

    # --- build the merged run dir (symlinks) ---
    if args.dry_run:
        merged_dir = Path("<MERGED_RUN_DIR-set-by-real-run>")
    elif args.merged_dir is not None:
        if not args.merged_dir.is_dir():
            raise SystemExit(f"--merged-dir does not exist: {args.merged_dir}")
        merged_dir = args.merged_dir
        print(f"reusing merged run dir: {merged_dir}")
    else:
        specs = [f"{args.cur_run}/{n}={n}" for n in cur_names]
        specs += [f"{args.prior_run}/{n}={n}{PRIOR_SUFFIX}" for n in prior_names]
        merged_dir = build_merged_run_dir(
            specs,
            args.dest,
            run_id=None,
            intersect_instances=args.intersect_instances,
        )
        print(f"merged run dir: {merged_dir}")

    # --- write the POST_PROCESS_ONLY config ---
    post_cfg: dict = {
        "run_mode": "POST_PROCESS_ONLY",
        "analysis_dir_path": str(merged_dir),
    }
    for key in _PASSTHROUGH_KEYS:
        if key in cur_cfg:
            post_cfg[key] = cur_cfg[key]
    post_cfg["draw_gantt"] = False
    post_cfg["draw_progress_plot"] = False
    post_cfg["painter_thread_cnt"] = cur_cfg.get("painter_thread_cnt", 96)
    post_cfg["scenarios"] = merged_scenarios

    args.config_out.parent.mkdir(parents=True, exist_ok=True)
    with args.config_out.open("w") as fh:
        yaml.safe_dump(post_cfg, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote POST_PROCESS_ONLY config: {args.config_out}")
    print(
        f"  scenarios: {len(merged_scenarios)} "
        f"({len(cur_names)} current + {len(prior_names)} prior)"
    )
    if args.dry_run:
        print(
            "  (dry-run: set analysis_dir_path to the real merged dir before "
            "running main.py)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
