"""Shared constants for PRA2017 benchmark scripts."""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent
MATCH_CSV = HERE / "pra2017_hybrid_match.csv"
BEST_SEQ_DIR = HERE / "best_seq_large"
OUT_CSV = HERE / "pra2017_instance_table.csv"

# Column headers (snake_case ↔ camelCase)
COLHEAD_INS_INDEX = "insIndex"
COLHEAD_TOTAL_MC_COUNT = "totalMcCount"
