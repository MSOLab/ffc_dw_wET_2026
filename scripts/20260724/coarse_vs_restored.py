"""Compare coarse-scale objective (before reconstruction) vs restored objective
(after reconstruction) for semi vs active, per (kappa, TL) cell.

For each instance it reads ``progress/<inst>_csr_candidates.csv`` and takes the
WINNER row = the valid candidate with the minimum ``restored_obj`` (that row's
restored_obj is what becomes the incumbent). It reports, per cell:

  coarse_obj  -- the winner's coarse-scale objective (same inner solve_flow for
                 both modes, so semi ~= active up to CP wall-clock noise);
  restored_obj -- after the mode's reconstruction (this is where they diverge).

If coarse_obj matches across modes but restored_obj does not, the difference is
attributable to the reconstruction step alone.

Usage:
    uv run python scripts/20260724/coarse_vs_restored.py [RUN_DIR] [--cells k1_tl15 ...]
"""

from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path
from statistics import mean

from ffc_ddw_sum_et.report.csr_candidate_analysis import read_csr_winner

DEFAULT_RUN = Path("output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252")


def cell_stats(run: Path, scn: str) -> tuple[int, float, float]:
    coarse, restored = [], []
    for inst_dir in glob.glob(str(run / scn / "Instance_*")):
        cands = glob.glob(f"{inst_dir}/progress/*_csr_candidates.csv")
        if not cands:
            continue
        w = read_csr_winner(cands[0])
        if w is None:
            continue
        coarse.append(w[0])
        restored.append(w[1])
    n = len(restored)
    return (
        n,
        (mean(coarse) if coarse else float("nan")),
        (mean(restored) if restored else float("nan")),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", nargs="?", type=Path, default=DEFAULT_RUN)
    ap.add_argument(
        "--cells",
        nargs="*",
        default=None,
        help="e.g. k1_tl15 k8_tl05; default = all k{1,2,4,8} x tl{05,10,15}",
    )
    args = ap.parse_args()

    if args.cells:
        cells = args.cells
    else:
        cells = [f"k{k}_tl{f:02d}" for k in (1, 2, 4, 8) for f in (5, 10, 15)]

    hdr = (
        f"{'cell':10s} {'n':>5} "
        f"{'coarse_semi':>12} {'coarse_act':>12} {'coarse_dpct':>10}   "
        f"{'restor_semi':>12} {'restor_act':>12} {'restor_dpct':>10}"
    )
    print(hdr)
    print("-" * len(hdr))
    for cell in cells:
        n_s, cs, rs = cell_stats(args.run_dir, f"csr_{cell}_semi")
        n_a, ca, ra = cell_stats(args.run_dir, f"csr_{cell}_active")
        cdp = (ca - cs) / cs * 100 if cs else float("nan")
        rdp = (ra - rs) / rs * 100 if rs else float("nan")
        m = re.match(r"k(\d+)_tl(\d+)", cell)
        label = f"k{m.group(1)}_tl{m.group(2)}" if m else cell
        print(
            f"{label:10s} {min(n_s, n_a):5d} "
            f"{cs:12.1f} {ca:12.1f} {cdp:+9.2f}%   "
            f"{rs:12.1f} {ra:12.1f} {rdp:+9.2f}%"
        )
    print(
        "\ncoarse_dpct = (active-semi)/semi of coarse_obj  -> ~0 expected "
        "(same inner solve)"
    )
    print("restor_dpct = same for restored_obj             -> the reconstruction gap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
