"""Analyze the 20260721 CSR-init + ISW-CP-batch experiment.

Reads a run's ``*_rpdf_comparison.csv`` (already carries T/R/n/c and
``RPDf_BKS_data`` per instance × scenario) and prints the pooled means, the
paired contrasts, the batch-vs-init decomposition, and the T-stratified cut
that plans/experiment/20260721/csr_init_isw_cp_batch_size.md §3 requires.

Usage:
    uv run python scripts/20260721/analyze_csr_init_isw_batch.py <run_dir>

All RPDf figures are in percentage points (RPDf_BKS_data × 100); lower is
better. Contrasts are signed x−y, so a positive C−A means C is *worse* than A.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd

ARMS = {
    "a_c5_batch_m": "A",
    "c_c5_batch_m_plus_2": "C",
    "b20_csr_k1_f20_batch_m_plus_2": "B20",
    "b30_csr_k1_f30_batch_m_plus_2": "B30",
}
ORDER = ["A", "C", "B20", "B30"]


def load(run_dir: Path) -> pd.DataFrame:
    csv = glob.glob(str(run_dir / "*_rpdf_comparison.csv"))
    if not csv:
        raise FileNotFoundError(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(csv[0], dtype={"insIndex": str})
    df["RPDf"] = df["RPDf_BKS_data"] * 100.0
    df["arm"] = df["scenarioName"].map(ARMS)
    if df["arm"].isna().any():
        raise ValueError("unexpected scenarioName(s); update ARMS mapping")
    return df


def pivot(df: pd.DataFrame) -> pd.DataFrame:
    piv = df.pivot_table(index="insIndex", columns="arm", values="RPDf")[ORDER]
    if not piv.notna().all().all():
        raise ValueError("missing (instance, arm) cells — run incomplete")
    meta = df.drop_duplicates("insIndex").set_index("insIndex")[["T", "R", "n", "c"]]
    return piv.join(meta)


def contrasts(piv: pd.DataFrame) -> None:
    pairs = [
        ("C-A", "C", "A"),
        ("B20-C", "B20", "C"),
        ("B30-C", "B30", "C"),
        ("B30-B20", "B30", "B20"),
        ("B20-A", "B20", "A"),
        ("B30-A", "B30", "A"),
    ]
    for lbl, x, y in pairs:
        d = piv[x] - piv[y]
        print(
            f"  {lbl:<8} mean={d.mean():+7.2f}  median={d.median():+7.2f}  "
            f"win(x<y)={100 * (d < 0).mean():4.1f}%"
        )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: analyze_csr_init_isw_batch.py <run_dir>")
    run_dir = Path(sys.argv[1])
    df = load(run_dir)
    piv = pivot(df)
    n_ins = len(piv)

    print(f"run: {run_dir}")
    print(f"instances: {n_ins}  arms: {ORDER}\n")

    print("== pooled mean RPDf (%p vs BKS_data, lower better) ==")
    for a in ORDER:
        print(f"  {a:<4} {piv[a].mean():7.2f}")

    print("\n== paired contrasts (pooled) ==")
    contrasts(piv)

    print("\n== decomposition (pooled means, %p) ==")
    c_a = (piv["C"] - piv["A"]).mean()
    print(f"  batch m->m+2, isolated   C-A   = {c_a:+.2f}")
    print(f"  init swap @ m+2, f=20%   B20-C = {(piv['B20'] - piv['C']).mean():+.2f}")
    print(f"  init swap @ m+2, f=30%   B30-C = {(piv['B30'] - piv['C']).mean():+.2f}")

    print("\n== mean RPDf by T ==")
    print(piv.groupby("T")[ORDER].mean().round(2).to_string())

    print("\n== (T,R)=(0.6,0.2) hard slice ==")
    hs = piv[(piv["T"] == 0.6) & (piv["R"] == 0.2)]
    print(f"  n={len(hs)}")
    for a in ORDER:
        print(f"  {a:<4} {hs[a].mean():7.2f}")
    for lbl, x, y in [("C-A", "C", "A"), ("B20-A", "B20", "A"), ("B30-A", "B30", "A")]:
        d = hs[x] - hs[y]
        print(f"  {lbl:<7} mean={d.mean():+6.2f} win={100 * (d < 0).mean():4.1f}%")

    print("\n== B*-A by n (size) ==")
    for n, s in piv.groupby("n"):
        print(
            f"  n={n:3d} ({len(s):4d}): C-A={(s['C'] - s['A']).mean():+5.2f}  "
            f"B20-A={(s['B20'] - s['A']).mean():+5.2f}  "
            f"B30-A={(s['B30'] - s['A']).mean():+5.2f}"
        )


if __name__ == "__main__":
    main()
