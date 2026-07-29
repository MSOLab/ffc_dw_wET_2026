"""Assemble the 3-way reconstruct_mode merge (semi / active / active_but_last_semi).

Combines the existing semi+active full-1440 run with the new lastsemi full-1440
run into one synthetic POST_PROCESS_ONLY run, so the reporter emits a single
``*_rpdf_comparison.csv`` spanning all three modes per (kappa, TL) cell:

  * base run (24 scenarios):     ``csr_k{K}_tl{f}_semi`` and ``csr_k{K}_tl{f}_active``
    -- the reconstruct_mode AB from 20260724T005703, reused verbatim (no re-run).
  * lastsemi run (12 scenarios): ``csr_k{K}_tl{f}_lastsemi``
    -- reconstruct_mode=active_but_last_semi, identical inner solve_flow (verified
    at config-build time against the base semi scenarios).

All three mode names are already unique (distinct suffix), so no relabeling is
needed -- unlike scripts/20260724/build_recon_ab_merge.py, which had to suffix the
prior run whose bare names collided.

Steps mirror build_recon_ab_merge.py:
  1. symlink every scenario's instance dirs into a fresh merged run dir
     (via scripts/build_merged_run_dir.py -- no copies, source runs untouched);
  2. write a POST_PROCESS_ONLY config whose ``analysis_dir_path`` is that dir and
     whose ``scenarios`` list carries each source scenario's ``subroutine_flow``
     copied from its source config.

Usage:
    uv run python scripts/20260724/build_lastsemi_merge.py \
        --base-run     output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252 \
        --lastsemi-run output/20260724_lastsemi_fullgrid/<ts> \
        --dest         output/20260724_merge_lastsemi_3way \
        --config-out   metadata/20260724/merge_lastsemi_3way.yaml

Then run the reporter, then the analysis:
    uv run python main.py --config metadata/20260724/merge_lastsemi_3way.yaml
    uv run python scripts/20260724/analyze_recon_lastsemi.py <merged_run_dir>

Add ``--dry-run`` to assemble/validate the config only (no symlinks, no merged
dir); ``analysis_dir_path`` is left as a placeholder for inspection.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))  # build_merged_run_dir
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helpers
from build_merged_run_dir import (  # noqa: E402
    LAYOUT_SUFFIX,
    _find_layout_stamp,
    build_merged_run_dir,
)
from build_recon_ab_merge import (  # noqa: E402  -- reuse mechanical plumbing
    _PASSTHROUGH_KEYS,
    _find_config,
    _load,
    _scenario_names,
    _scrub_deprecated,
)

BASE_MODES = ("semi", "active")
NEW_MODE = "lastsemi"


def _mode_of(name: str) -> str:
    return name.rsplit("_", 1)[1]


def _validate_modes(base_names: list[str], new_names: list[str]) -> None:
    bad_base = [n for n in base_names if _mode_of(n) not in BASE_MODES]
    if bad_base:
        raise SystemExit(
            f"--base-run scenarios must end in {BASE_MODES}, offenders: {bad_base}"
        )
    bad_new = [n for n in new_names if _mode_of(n) != NEW_MODE]
    if bad_new:
        raise SystemExit(
            f"--lastsemi-run scenarios must end in _{NEW_MODE}, offenders: {bad_new}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--base-run",
        type=Path,
        required=True,
        help="the semi+active full-grid run (24 scenarios)",
    )
    ap.add_argument(
        "--lastsemi-run",
        type=Path,
        required=True,
        help="the active_but_last_semi full-grid run (12 scenarios)",
    )
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

    base_cfg = _load(_find_config(args.base_run))
    new_cfg = _load(_find_config(args.lastsemi_run))

    base_names = _scenario_names(base_cfg)
    new_names = _scenario_names(new_cfg)
    _validate_modes(base_names, new_names)

    # --- assemble the merged scenario list (base semi/active, then lastsemi) ---
    merged_scenarios = [_scrub_deprecated(s) for s in base_cfg["scenarios"]]
    merged_scenarios += [_scrub_deprecated(s) for s in new_cfg["scenarios"]]

    labels = [s["name"] for s in merged_scenarios]
    if len(set(labels)) != len(labels):
        dupes = sorted({lab for lab in labels if labels.count(lab) > 1})
        raise SystemExit(f"duplicate scenario labels: {dupes}")

    # --- build the merged run dir (symlinks) ---
    if args.dry_run:
        merged_dir = Path("<MERGED_RUN_DIR-set-by-real-run>")
    elif args.merged_dir is not None:
        if not args.merged_dir.is_dir():
            raise SystemExit(f"--merged-dir does not exist: {args.merged_dir}")
        merged_dir = args.merged_dir
        print(f"reusing merged run dir: {merged_dir}")
    else:
        specs = [f"{args.base_run}/{n}={n}" for n in base_names]
        specs += [f"{args.lastsemi_run}/{n}={n}" for n in new_names]
        merged_dir = build_merged_run_dir(
            specs,
            args.dest,
            run_id=None,
            intersect_instances=args.intersect_instances,
        )
        print(f"merged run dir: {merged_dir}")

    # build_merged_run_dir transplants the FIRST source's layout (the base run),
    # which predates the csr_analysis artifact kind and makes the reporter raise
    # KeyError('csr_analysis'). Restamp with the lastsemi run's layout (current
    # schema, a superset of kinds); its templates are {run_id}-keyed so they
    # transplant cleanly under the merged id.
    if not args.dry_run:
        ls_layout = _find_layout_stamp(args.lastsemi_run)
        shutil.copy2(ls_layout, merged_dir / f"{merged_dir.name}{LAYOUT_SUFFIX}")
        print(f"restamped artifact_layout from lastsemi run: {ls_layout.name}")

    # --- write the POST_PROCESS_ONLY config ---
    post_cfg: dict = {
        "run_mode": "POST_PROCESS_ONLY",
        "analysis_dir_path": str(merged_dir),
    }
    for key in _PASSTHROUGH_KEYS:
        if key in base_cfg:
            post_cfg[key] = base_cfg[key]
    post_cfg["draw_gantt"] = False
    post_cfg["draw_progress_plot"] = False
    post_cfg["painter_thread_cnt"] = base_cfg.get("painter_thread_cnt", 96)
    post_cfg["scenarios"] = merged_scenarios

    args.config_out.parent.mkdir(parents=True, exist_ok=True)
    with args.config_out.open("w") as fh:
        yaml.safe_dump(post_cfg, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote POST_PROCESS_ONLY config: {args.config_out}")
    print(
        f"  scenarios: {len(merged_scenarios)} "
        f"({len(base_names)} base semi/active + {len(new_names)} lastsemi)"
    )
    if args.dry_run:
        print(
            "  (dry-run: set analysis_dir_path to the real merged dir before "
            "running main.py)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
