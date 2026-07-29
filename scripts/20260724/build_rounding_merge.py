"""Assemble the coarsen_mode(rounding) robustness merge.

Combines the new {ceil,floor,round} x k{2,4,8} x f{5,10,15} run (27 scenarios)
with the existing lastsemi full-grid run (12 scenarios: k1 + cumulative k{2,4,8})
into one synthetic POST_PROCESS_ONLY run, so the reporter emits a single
``*_rpdf_comparison.csv`` spanning the full rounding x k x f grid:

  * rounding run (27 scenarios): ``csr_k{K}_tl{f}_lastsemi_{ceil,floor,round}``
    -- K in {2,4,8}, the new rounding rules. Inner solve_flow / timelimit copied
    verbatim from the lastsemi_fullgrid cumulative k>1 blocks (verified at
    config-build time; only coarsen_mode + name differ).
  * lastsemi run (12 scenarios): ``csr_k{K}_tl{f}_lastsemi``
    -- K in {1,2,4,8}, coarsen_mode=cumulative. K=1 is rounding-invariant, so the
    single ``csr_k1_tl{f}_lastsemi`` represents EVERY mode's K=1 baseline; the
    k>1 blocks are the cumulative arm.

All 39 names are already unique (rounding blocks carry a ``_{mode}`` suffix, the
lastsemi blocks are bare), so no relabeling is needed -- like
build_lastsemi_merge.py, unlike build_recon_ab_merge.py.

Steps mirror build_lastsemi_merge.py:
  1. symlink every scenario's instance dirs into a fresh merged run dir
     (via scripts/build_merged_run_dir.py -- no copies, source runs untouched);
  2. write a POST_PROCESS_ONLY config whose ``analysis_dir_path`` is that dir and
     whose ``scenarios`` list carries each source scenario's ``subroutine_flow``.

Usage:
    uv run python scripts/20260724/build_rounding_merge.py \
        --rounding-run output/20260724_lastsemi_rounding_robust/<ts> \
        --lastsemi-run output/20260724_lastsemi_fullgrid/20260724T155337_875856 \
        --dest         output/20260724_merge_rounding \
        --config-out   metadata/20260724/merge_rounding.yaml

Then run the reporter, then the analysis:
    uv run python main.py --config metadata/20260724/merge_rounding.yaml
    uv run python scripts/20260724/analyze_rounding_robust.py <merged_run_dir>

Add ``--dry-run`` to assemble/validate the config only (no symlinks, no merged
dir); ``analysis_dir_path`` is left as a placeholder for inspection.
"""

from __future__ import annotations

import argparse
import re
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

# rounding run: coarsened arms only (K in {2,4,8}), explicit mode suffix.
_ROUNDING = re.compile(r"^csr_k([248])_tl(\d+)_lastsemi_(ceil|floor|round)$")
# lastsemi run: bare cumulative names incl. the K=1 baseline (K in {1,2,4,8}).
_LASTSEMI = re.compile(r"^csr_k([1248])_tl(\d+)_lastsemi$")


def _validate(rounding_names: list[str], lastsemi_names: list[str]) -> None:
    bad_r = [n for n in rounding_names if not _ROUNDING.match(n)]
    if bad_r:
        raise SystemExit(
            "--rounding-run scenarios must match "
            f"csr_k{{2,4,8}}_tl{{f}}_lastsemi_{{ceil,floor,round}}, offenders: {bad_r}"
        )
    bad_l = [n for n in lastsemi_names if not _LASTSEMI.match(n)]
    if bad_l:
        raise SystemExit(
            "--lastsemi-run scenarios must match "
            f"csr_k{{1,2,4,8}}_tl{{f}}_lastsemi, offenders: {bad_l}"
        )
    # the lastsemi arm must carry the K=1 baseline (it represents every mode's K=1)
    if not any(n.startswith("csr_k1_") for n in lastsemi_names):
        raise SystemExit(
            "--lastsemi-run has no csr_k1_* scenario; the K=1 baseline is required"
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--rounding-run",
        type=Path,
        required=True,
        help="the {ceil,floor,round} x k{2,4,8} run (27 scenarios)",
    )
    ap.add_argument(
        "--lastsemi-run",
        type=Path,
        required=True,
        help="the lastsemi full-grid run (12 scenarios: k1 + cumulative k>1)",
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

    rounding_cfg = _load(_find_config(args.rounding_run))
    lastsemi_cfg = _load(_find_config(args.lastsemi_run))

    rounding_names = _scenario_names(rounding_cfg)
    lastsemi_names = _scenario_names(lastsemi_cfg)
    _validate(rounding_names, lastsemi_names)

    # --- assemble merged scenario list (lastsemi baseline first, then rounding) ---
    merged_scenarios = [_scrub_deprecated(s) for s in lastsemi_cfg["scenarios"]]
    merged_scenarios += [_scrub_deprecated(s) for s in rounding_cfg["scenarios"]]

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
        specs = [f"{args.lastsemi_run}/{n}={n}" for n in lastsemi_names]
        specs += [f"{args.rounding_run}/{n}={n}" for n in rounding_names]
        merged_dir = build_merged_run_dir(
            specs,
            args.dest,
            run_id=None,
            intersect_instances=args.intersect_instances,
        )
        print(f"merged run dir: {merged_dir}")

    # build_merged_run_dir transplants the FIRST source's layout. Restamp with the
    # rounding run's layout (current schema, carries the csr_analysis kind) so the
    # reporter does not raise KeyError('csr_analysis'); its templates are
    # {run_id}-keyed and transplant cleanly under the merged id.
    if not args.dry_run:
        r_layout = _find_layout_stamp(args.rounding_run)
        shutil.copy2(r_layout, merged_dir / f"{merged_dir.name}{LAYOUT_SUFFIX}")
        print(f"restamped artifact_layout from rounding run: {r_layout.name}")

    # --- write the POST_PROCESS_ONLY config ---
    post_cfg: dict = {
        "run_mode": "POST_PROCESS_ONLY",
        "analysis_dir_path": str(merged_dir),
    }
    for key in _PASSTHROUGH_KEYS:
        if key in rounding_cfg:
            post_cfg[key] = rounding_cfg[key]
    post_cfg["draw_gantt"] = False
    post_cfg["draw_progress_plot"] = False
    post_cfg["painter_thread_cnt"] = rounding_cfg.get("painter_thread_cnt", 96)
    post_cfg["scenarios"] = merged_scenarios

    args.config_out.parent.mkdir(parents=True, exist_ok=True)
    with args.config_out.open("w") as fh:
        yaml.safe_dump(post_cfg, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote POST_PROCESS_ONLY config: {args.config_out}")
    print(
        f"  scenarios: {len(merged_scenarios)} "
        f"({len(lastsemi_names)} lastsemi + {len(rounding_names)} rounding)"
    )
    if args.dry_run:
        print(
            "  (dry-run: set analysis_dir_path to the real merged dir before "
            "running main.py)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
