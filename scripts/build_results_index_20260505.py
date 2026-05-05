"""Build a single long-form CSV combining per-instance summary results from
the 35 RUNs catalogued in ``docs/reviews/20260505_weekly_experiments.md``
(2026-04-29 ~ 2026-05-05). RUN 15 is on hjt5950x and intentionally skipped
because the local repo has no copy of its summary.csv.

Output: ``analysis/results_index_20260505.csv`` — one row per
``(RUN, scenario, instance)`` with run-level provenance prepended,
``<timestamp>_summary.csv`` columns kept, and BKS / RPDf joined from the
benchmark tables.

Usage:
    uv run python scripts/build_results_index_20260505.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_INDEX = REPO_ROOT / "analysis" / "results_index_20260505.csv"
HYBRID_MATCH_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_hybrid_match.csv"
BKS_TABLE_CSV = REPO_ROOT / "benchmarks" / "PRA2017" / "pra2017_bks_table.csv"


# (run_number, timestamp, output_date_dir, config_relpath, source_commit,
#  machine, scope_label).
# RUN 15 (20260503T170658_834025, hjt5950x, mcf_lb_init_26) intentionally
# omitted — output not available locally.
RUNS: list[tuple[int, str, str, str, str, str, str]] = [
    (1,  "20260429T233115_006438", "20260429", "metadata/20260429/20260429_config.yaml",                       "e792136", "mso02", "full"),
    (2,  "20260430T110852_547352", "20260429", "metadata/20260429/20260429_config.yaml",                       "a9981c1", "mso02", "full"),
    (3,  "20260501T162650_028232", "20260501", "metadata/20260501/20260501_mcf_lb_then_neh_cp_config.yaml",    "c675c1a", "mso02", "full"),
    (4,  "20260502T002742_596323", "20260501", "metadata/20260501/mcf_lb_init_19_config.yaml",                 "9e74acc", "mso02", "full"),
    (5,  "20260502T025451_273045", "20260501", "metadata/20260501/mcf_lb_init_20_config.yaml",                 "f31fc87", "mso02", "full"),
    (6,  "20260502T032313_670203", "20260501", "metadata/20260501/mcf_lb_init_21_config.yaml",                 "c43f87b", "mso02", "full"),
    (7,  "20260502T131546_402074", "20260501", "metadata/20260501/mcf_lb_init_21_config.yaml",                 "9418208", "mso02", "full"),
    (8,  "20260502T133412_116270", "20260502", "metadata/20260502/mcf_lb_init_22_config.yaml",                 "8304421", "mso02", "full"),
    (9,  "20260502T145150_590013", "20260502", "metadata/20260502/mcf_lb_init_22_config.yaml",                 "051d8e1", "mso02", "full"),
    (10, "20260502T165007_640181", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "4f7fb0f", "mso02", "full"),
    (11, "20260502T184531_518809", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "f3f0e73", "mso02", "full"),
    (12, "20260502T193831_290902", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "6ecd356", "mso02", "full"),
    (13, "20260503T022442_340817", "20260502", "metadata/20260502/mcf_lb_init_24_config.yaml",                 "bab11e8", "mso02", "full"),
    (14, "20260503T170549_147724", "20260503", "metadata/20260503/mcf_lb_init_25_config.yaml",                 "5c4920a", "mso02", "full"),
    # 15 — hjt5950x — skipped
    (16, "20260503T181635_000784", "20260503", "metadata/20260503/mcf_lb_init_26_config.yaml",                 "725a912", "mso02", "full"),
    (17, "20260503T191906_135722", "20260503", "metadata/20260503/mcf_lb_init_27_config.yaml",                 "c4a790f", "mso02", "full"),
    (18, "20260503T215803_006004", "20260503", "metadata/20260503/mcf_lb_init_28_config.yaml",                 "33cce02", "mso02", "full"),
    (19, "20260503T230126_683476", "20260503", "metadata/20260503/mcf_lb_init_29_config.yaml",                 "f89ba73", "mso02", "full"),
    (20, "20260504T003732_433340", "20260503", "metadata/20260503/mcf_lb_init_30_config.yaml",                 "9df77f8", "mso02", "full"),
    (21, "20260504T004917_785558", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "3d546da", "mso02", "full"),
    (22, "20260504T010002_965646", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "3d07d20", "mso02", "full"),
    (23, "20260504T030753_945843", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "4ca477d", "mso02", "full"),
    (24, "20260504T031049_337896", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "4ca477d", "mso02", "full"),
    (25, "20260504T031422_467379", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "4ca477d", "mso02", "full"),
    (26, "20260504T032002_269531", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "4ca477d", "mso02", "full"),
    (27, "20260504T032732_697925", "20260503", "metadata/20260503/mcf_lb_init_32_config.yaml",                 "b090f84", "mso02", "full"),
    (28, "20260504T082749_666067", "20260504", "metadata/20260504/mcf_lb_init_33_config.yaml",                 "1abc4f4", "mso02", "full"),
    (29, "20260504T093058_016949", "20260504", "metadata/20260504/mcf_lb_init_34_config.yaml",                 "bff5eac", "mso02", "full"),
    (30, "20260504T135233_268173", "20260504", "metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml",        "78c6756", "mso02", "full"),
    (31, "20260504T142221_504713", "20260504", "metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml",        "a0a6974", "mso02", "full"),
    (32, "20260505T014813_804225", "20260504", "metadata/20260504/mcf_lb_init_35_config.yaml",                 "07d7e7f", "mso02", "full"),
    (33, "20260505T025805_689859", "20260504", "metadata/20260504/mcf_lb_init_36_config.yaml",                 "445bf53", "mso02", "full"),
    (34, "20260505T102202_582058", "20260505", "metadata/20260505/mcf_lb_init_37_config.yaml",                 "0a8a9b0", "mso02", "full"),
    (35, "20260505T191440_984385", "20260505", "metadata/20260505/mcf_lb_init_37_config.yaml",                 "c039ceb", "mso02", "full"),
    (36, "20260505T192009_887337", "20260505", "metadata/20260505/mcf_lb_init_38_config.yaml",                 "af944e3", "mso02", "full"),
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
    log = logging.getLogger("build_results_index_20260505")
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return log


def load_benchmark_tables() -> pd.DataFrame:
    hybrid = pd.read_csv(HYBRID_MATCH_CSV, dtype={"insIndex": str})
    hybrid["instanceName"] = hybrid["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    bks = pd.read_csv(BKS_TABLE_CSV, dtype={"insIndex": str})
    keep_bks = ["insIndex", "n", "c", "totalMcCount", "T", "R", "W", "BKS_data"]
    return hybrid[["insIndex", "instanceName"]].merge(
        bks[keep_bks], on="insIndex", how="left"
    )


def find_summary_csv(timestamp: str, output_date_dir: str) -> Path | None:
    direct = REPO_ROOT / "output" / output_date_dir / timestamp / f"{timestamp}_summary.csv"
    if direct.exists():
        return direct
    matches = list((REPO_ROOT / "output").glob(f"*/{timestamp}/{timestamp}_summary.csv"))
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
            log.warning("RUN %d (%s): summary.csv not found under output/%s/", run_number, ts, out_date)
            missing.append((run_number, ts))
            continue

        df = pd.read_csv(summary_path)
        df = df.merge(bench, on="instanceName", how="left")
        raw = (df["bestObj"] - df["BKS_data"]) / ((df["bestObj"] + df["BKS_data"]) / 2)
        df["RPDf_BKS_data"] = raw.where(~((df["bestObj"] == 0) & (df["BKS_data"] == 0)), 0.0)

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
        log.info("RUN %d (%s): %d rows from %s", run_number, ts, len(df), summary_path.relative_to(REPO_ROOT))

    if not frames:
        log.error("No summary.csv files found — aborting.")
        sys.exit(1)

    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(OUTPUT_INDEX, index=False)
    log.info("Wrote %s (%d rows, %d runs)", OUTPUT_INDEX.relative_to(REPO_ROOT), len(combined), len(frames))

    if missing:
        log.warning("Missing summary.csv for: %s", ", ".join(f"RUN {n} ({t})" for n, t in missing))


if __name__ == "__main__":
    main()
