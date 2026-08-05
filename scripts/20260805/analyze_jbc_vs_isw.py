"""job_batch_cp vs incremental_sw_cp at the flow tail: 5-arm merged analysis.

Three arms come from the 20260805 run; two borrowed observations of the
ISW-CP @ 0.09nc cell come from earlier full-grid runs and are restricted to the
same 160-instance (T, R) = (0.6, 0.2) slice.

Writes CSVs to analysis/20260805_job_batch_cp_vs_isw_cp/ and prints every table
that the write-up in plans/analysis/20260805/ quotes.
"""

from __future__ import annotations

import pathlib

import pandas as pd

RUN = "output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665"
# The 20260728 init-budget axis: cap FIXED at 0.09nc, initialization scaled to
# 10/20/40 % of the 20260710 kappa_0.005 baseline (fmm 0.009nc, neh 0.027nc).
# f40 == the t090 arms' init budget, so this axis says what shrinking init
# FURTHER costs, which is the question the t030 level was assumed to extend.
INIT_AXIS_RUN = "output/20260728_dispatch_v4_init_tl/20260728T202801_339672"
INIT_AXIS = ["dv4_c5init_f10", "dv4_c5init_f20", "dv4_c5init_f40"]

BORROW = {
    "dv4_mcf_fmm_neh_isw_t090": (
        "output/20260801_neh_cp_budget_allocation/20260801T183302_770739",
        "dv4_mcf_fmm_comp_x1_base",
    ),
    "dv4_mcf_fmm_neh_isw_t090_jp": (
        "output/20260728_dispatch_v4_init_tl/20260728T202801_339672",
        "dv4_c5init_f40",
    ),
}
OUT = pathlib.Path("analysis/20260805_job_batch_cp_vs_isw_cp")
ORDER = [
    "dv4_mcf_fmm_neh_isw_t090",
    "dv4_mcf_fmm_neh_isw_t090_jp",
    "dv4_mcf_fmm_neh_jbc_t090",
    "dv4_mcf_fmm_neh_isw_t030",
    "dv4_mcf_fmm_neh_jbc_t030",
]


def _rpdf_csv(run_dir: str) -> pd.DataFrame:
    stem = pathlib.Path(run_dir).name
    return pd.read_csv(f"{run_dir}/{stem}_rpdf_comparison.csv")


def load() -> pd.DataFrame:
    frames = [_rpdf_csv(RUN)]
    target = set(frames[0]["insIndex"])
    for label, (run_dir, scenario) in BORROW.items():
        df = _rpdf_csv(run_dir)
        df = df[(df["scenarioName"] == scenario) & (df["insIndex"].isin(target))].copy()
        assert len(df) == len(target), (
            f"{label}: {len(df)} rows, expected {len(target)}"
        )
        df["scenarioName"] = label
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    # `timelimit` / `time%` are unusable: the pivot filled them from the 0.09nc
    # reference for every arm, so the t030 rows read ~0.33 instead of ~1.0.
    # elapsedTime is correct and is what the write-up quotes.
    return out.drop(columns=["timelimit", "time%"])


def pivot(df: pd.DataFrame, value: str) -> pd.DataFrame:
    return df.pivot(index="insIndex", columns="scenarioName", values=value)[ORDER]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = load()
    df.to_csv(OUT / "merged_rpdf.csv", index=False)

    print(f"=== arms (n={df['insIndex'].nunique()} instances, (T,R)=(0.6,0.2)) ===")
    summary = (
        df.groupby("scenarioName")
        .agg(
            meanRPDf=("RPDf_BKS_data", "mean"),
            medRPDf=("RPDf_BKS_data", "median"),
            meanObj=("bestObj", "mean"),
            meanSec=("elapsedTime", "mean"),
        )
        .reindex(ORDER)
        .round(4)
    )
    print(summary.to_string())
    summary.to_csv(OUT / "arm_summary.csv")

    rp, obj = pivot(df, "RPDf_BKS_data"), pivot(df, "bestObj")
    contrasts = [
        ("noise yardstick", "dv4_mcf_fmm_neh_isw_t090_jp", "dv4_mcf_fmm_neh_isw_t090"),
        ("Q1 t090 jbc-isw", "dv4_mcf_fmm_neh_jbc_t090", "dv4_mcf_fmm_neh_isw_t090"),
        (
            "Q1 t090 jbc-isw_jp",
            "dv4_mcf_fmm_neh_jbc_t090",
            "dv4_mcf_fmm_neh_isw_t090_jp",
        ),
        ("Q2 t030 jbc-isw", "dv4_mcf_fmm_neh_jbc_t030", "dv4_mcf_fmm_neh_isw_t030"),
        ("isw t030-t090", "dv4_mcf_fmm_neh_isw_t030", "dv4_mcf_fmm_neh_isw_t090"),
        ("jbc t030-t090", "dv4_mcf_fmm_neh_jbc_t030", "dv4_mcf_fmm_neh_jbc_t090"),
    ]
    print("\n=== paired contrasts (per instance, then aggregated) ===")
    rows = []
    for name, a, b in contrasts:
        d, do = rp[a] - rp[b], obj[a] - obj[b]
        rows.append(
            {
                "contrast": name,
                "dRPDf_pp": round(100 * d.mean(), 3),
                "dObj_%": round(100 * (obj[a].sum() / obj[b].sum() - 1), 2),
                "a_wins": int((do < 0).sum()),
                "ties": int((do == 0).sum()),
                "b_wins": int((do > 0).sum()),
            }
        )
    cdf = pd.DataFrame(rows)
    print(cdf.to_string(index=False))
    cdf.to_csv(OUT / "contrasts.csv", index=False)

    print("\n=== mean RPDf by (n, c) ===")
    cell = (
        df.groupby(["n", "c", "scenarioName"])["RPDf_BKS_data"]
        .mean()
        .unstack("scenarioName")[ORDER]
        .round(4)
    )
    print(cell.to_string())
    cell.to_csv(OUT / "by_n_c.csv")

    print("\n=== Q1/Q2 gap (pp) by (n, c) ===")
    gap = pd.DataFrame(
        {
            "Q1_t090": 100
            * (cell["dv4_mcf_fmm_neh_jbc_t090"] - cell["dv4_mcf_fmm_neh_isw_t090"]),
            "Q2_t030": 100
            * (cell["dv4_mcf_fmm_neh_jbc_t030"] - cell["dv4_mcf_fmm_neh_isw_t030"]),
        }
    ).round(2)
    print(gap.to_string())
    gap.to_csv(OUT / "gap_by_n_c.csv")
    print("\n=== init-budget axis (20260728), cap FIXED at 0.09nc ===")
    ax = _rpdf_csv(INIT_AXIS_RUN)
    ax = ax[
        ax["scenarioName"].isin(INIT_AXIS) & ax["insIndex"].isin(set(df["insIndex"]))
    ]
    axs = (
        ax.groupby("scenarioName")
        .agg(meanRPDf=("RPDf_BKS_data", "mean"), meanSec=("elapsedTime", "mean"))
        .reindex(INIT_AXIS)
        .round(4)
    )
    axp = ax.pivot(index="insIndex", columns="scenarioName", values="RPDf_BKS_data")
    axs["vs_f40_pp"] = [
        round(100 * (axp[k] - axp["dv4_c5init_f40"]).mean(), 3) for k in INIT_AXIS
    ]
    print(axs.to_string())
    axs.to_csv(OUT / "init_axis.csv")

    print("\n=== decomposition of the t090 -> t030 ISW penalty ===")
    init_cut = 100 * (axp["dv4_c5init_f10"] - axp["dv4_c5init_f40"]).mean()
    cap_cut = (
        100 * (rp["dv4_mcf_fmm_neh_isw_t030"] - rp["dv4_mcf_fmm_neh_isw_t090"]).mean()
    )
    print(f"  init 40% -> 10% of baseline, cap held : {init_cut:+.2f} pp")
    print(f"  cap 0.09nc -> 0.03nc (init also cut)  : {cap_cut:+.2f} pp")

    print(f"\nartifacts -> {OUT}/")


if __name__ == "__main__":
    main()
