"""Build a single long-form CSV combining per-instance summary results from
all 23 RUNs catalogued in ``docs/reviews/20260428_weekly_experiments.md``.

Output: ``analysis/results_index_20260428.csv`` — one row per
``(RUN, scenario, instance)`` with the run-level provenance columns prepended,
the original ``<timestamp>_summary.csv`` columns kept, and the BKS / RPDf
columns joined from the benchmark tables.

Usage:
    uv run python scripts/build_results_index.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_INDEX = REPO_ROOT / "analysis" / "results_index_20260428.csv"
HYBRID_MATCH_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_hybrid_match.csv"
BKS_TABLE_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_bks_table.csv"


# (run_number, timestamp, output_date_dir, config_relpath, source_commit,
#  machine, scope_label)
RUNS: list[tuple[int, str, str, str, str, str, str]] = [
    (
        1,
        "20260423T114900_417063",
        "20260423",
        "metadata/20260423/1_mcf_lb_init_13_config.yaml",
        "ad4a023",
        "mso02",
        "full",
    ),
    (
        2,
        "20260423T171935_897548",
        "20260423",
        "metadata/20260423/cmax_init_pfns_config.yaml",
        "6619d1f",
        "mso02",
        "full",
    ),
    (
        3,
        "20260423T173918_198369",
        "20260423",
        "metadata/20260423/cmax_init_pfns_config.yaml",
        "97849df",
        "mso02",
        "full",
    ),
    (
        4,
        "20260423T174736_400968",
        "20260423",
        "metadata/20260423/cmax_init_pfns_config_2.yaml",
        "f7102b9",
        "mso02",
        "full",
    ),
    (
        5,
        "20260423T221248_575301",
        "20260423",
        "metadata/20260423/1_mcf_lb_init_13_config.yaml",
        "10b1a5d",
        "mso02",
        "full",
    ),
    (
        6,
        "20260424T041848_326215",
        "20260423",
        "metadata/20260423/neh_cp_config_4.yaml",
        "7cbbb4b",
        "mso02",
        "full",
    ),
    (
        7,
        "20260424T213007_556893",
        "20260424",
        "metadata/20260424/neh_cp_config_5.yaml",
        "8195d63",
        "mso02",
        "full",
    ),
    (
        8,
        "20260425T034449_338686",
        "20260424",
        "metadata/20260424/neh_cp_config_8.yaml",
        "333dad3",
        "mso02",
        "full",
    ),
    (
        9,
        "20260425T200857_851387",
        "20260425",
        "metadata/20260425/neh_cp_config_9.yaml",
        "5bb35cc",
        "mso02",
        "full",
    ),
    (
        10,
        "20260425T205244_871000",
        "20260425",
        "metadata/20260425/neh_cp_config_9.yaml",
        "ba0f8d9",
        "mso02",
        "full",
    ),
    (
        11,
        "20260425T232836_063038",
        "20260425",
        "metadata/20260425/neh_cp_config_10.yaml",
        "4b52002",
        "mso02",
        "full",
    ),
    (
        12,
        "20260426T014532_241012",
        "20260425",
        "metadata/20260425/neh_cp_config_11.yaml",
        "9e636c2",
        "mso02",
        "full",
    ),
    (
        13,
        "20260426T174905_399637",
        "20260426",
        "metadata/20260426/neh_cp_config_12.yaml",
        "d4f0379",
        "mso02",
        "full",
    ),
    (
        14,
        "20260426T185350_366559",
        "20260426",
        "metadata/20260426/neh_cp_config_13.yaml",
        "8ee39b7",
        "mso02",
        "full",
    ),
    (
        15,
        "20260426T212121_069773",
        "20260426",
        "metadata/20260426/mcf_lb_init_14_config.yaml",
        "0bea6a1",
        "hjt5950x",
        "full",
    ),
    (
        16,
        "20260427T025803_513725",
        "20260426",
        "metadata/20260426/20260426_config.yaml",
        "4ddbfda",
        "mso02",
        "full",
    ),
    (
        17,
        "20260427T123656_726782",
        "20260427",
        "metadata/20260427/mcf_lb_init_16_config.yaml",
        "409a00e",
        "mso02",
        "full",
    ),
    (
        18,
        "20260427T173735_407299",
        "20260427",
        "metadata/20260427/mcf_lb_init_17_config.yaml",
        "fa4e16f",
        "mso02",
        "tail5",
    ),
    (
        19,
        "20260428T022941_371229",
        "20260427",
        "metadata/20260427/wxd2_1_config.yaml",
        "d9a0905",
        "mso02",
        "full",
    ),
    (
        20,
        "20260428T130925_989218",
        "20260428",
        "metadata/20260428/mcf_lb_only_config.yaml",
        "c0682f1",
        "mso02",
        "full",
    ),
    (
        21,
        "20260428T165900_623730",
        "20260428",
        "metadata/20260428/neh_cp_config_16.yaml",
        "2883b04",
        "mso02",
        "full",
    ),
    (
        22,
        "20260428T214400_957643",
        "20260428",
        "metadata/20260428/mcf_lb_init_18_config.yaml",
        "8c8723c",
        "mso02",
        "full",
    ),
    (
        23,
        "20260428T234426_736253",
        "20260428",
        "metadata/20260428/dispatch_wxd1_1_config.yaml",
        "848f725",
        "mso02",
        "full",
    ),
]

PROVENANCE_COLUMNS = [
    "runNumber",
    "timestamp",
    "sourceCommit",
    "machine",
    "scope",
    "configFile",
    "outputDir",
]


def _logger() -> logging.Logger:
    log = logging.getLogger("build_results_index")
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


def load_benchmark_tables() -> pd.DataFrame:
    """Build a per-instance lookup keyed by ``instanceName`` (no .txt suffix)
    with insIndex, n, c, totalMcCount, T, R, W, BKS_data columns.
    """
    hybrid = pd.read_csv(HYBRID_MATCH_CSV, dtype={"insIndex": str})
    hybrid["instanceName"] = hybrid["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    bks = pd.read_csv(BKS_TABLE_CSV, dtype={"insIndex": str})
    keep_bks = ["insIndex", "n", "c", "totalMcCount", "T", "R", "W", "BKS_data"]
    return hybrid[["insIndex", "instanceName"]].merge(
        bks[keep_bks], on="insIndex", how="left"
    )


def find_summary_csv(timestamp: str, output_date_dir: str) -> Path | None:
    """Return path to ``<output>/<date>/<timestamp>/<timestamp>_summary.csv``
    if it exists. Tries the recorded date dir first, then falls back to a
    repo-wide search (handles cases where output_dir was relocated)."""
    direct = (
        REPO_ROOT / "output" / output_date_dir / timestamp / f"{timestamp}_summary.csv"
    )
    if direct.exists():
        return direct
    matches = list(
        (REPO_ROOT / "output").glob(f"*/{timestamp}/{timestamp}_summary.csv")
    )
    return matches[0] if matches else None


def attach_provenance(
    df: pd.DataFrame,
    *,
    run_number: int,
    timestamp: str,
    config: str,
    commit: str,
    machine: str,
    scope: str,
    output_dir: Path,
) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "runNumber", run_number)
    df.insert(1, "timestamp", timestamp)
    df.insert(2, "sourceCommit", commit)
    df.insert(3, "machine", machine)
    df.insert(4, "scope", scope)
    df.insert(5, "configFile", config)
    df.insert(6, "outputDir", str(output_dir.relative_to(REPO_ROOT)))
    return df


def main() -> None:
    log = _logger()

    if not OUTPUT_INDEX.parent.exists():
        OUTPUT_INDEX.parent.mkdir(parents=True, exist_ok=True)

    bench = load_benchmark_tables()
    log.info("Loaded benchmark tables: %d instances", len(bench))

    frames: list[pd.DataFrame] = []
    missing: list[tuple[int, str]] = []

    for run_number, ts, out_date, config, commit, machine, scope in RUNS:
        summary_path = find_summary_csv(ts, out_date)
        if summary_path is None:
            log.warning(
                "RUN %d (%s): summary.csv not found under output/%s/",
                run_number,
                ts,
                out_date,
            )
            missing.append((run_number, ts))
            continue

        df = pd.read_csv(summary_path)
        df = df.merge(bench, on="instanceName", how="left")
        raw = (df["bestObj"] - df["BKS_data"]) / ((df["bestObj"] + df["BKS_data"]) / 2)
        # best=0 and BKS=0 → RPDf defined as 0
        df["RPDf_BKS_data"] = raw.where(
            ~((df["bestObj"] == 0) & (df["BKS_data"] == 0)), 0.0
        )

        df = attach_provenance(
            df,
            run_number=run_number,
            timestamp=ts,
            config=config,
            commit=commit,
            machine=machine,
            scope=scope,
            output_dir=summary_path.parent,
        )
        frames.append(df)
        log.info(
            "RUN %d (%s): %d rows from %s",
            run_number,
            ts,
            len(df),
            summary_path.relative_to(REPO_ROOT),
        )

    if not frames:
        log.error("No summary.csv files found — aborting.")
        sys.exit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(OUTPUT_INDEX, index=False)
    log.info(
        "Wrote %s (%d rows, %d runs)",
        OUTPUT_INDEX.relative_to(REPO_ROOT),
        len(combined),
        len(frames),
    )

    if missing:
        log.warning(
            "Missing summary.csv for: %s",
            ", ".join(f"RUN {n} ({t})" for n, t in missing),
        )


if __name__ == "__main__":
    main()
