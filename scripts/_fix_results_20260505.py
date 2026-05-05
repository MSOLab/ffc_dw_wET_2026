"""Repair the 2026-05-05 review doc:
1. Insert missing blank lines between an inserted result-table cell and the
   following `### RUN ` / `#### RUN ` heading.
2. Insert per-RUN result tables for RUN 23~26 (the `#### RUN ` subheadings
   under the Phase 9 perf-A/B/B/A parent — skipped by the first pass).

Throwaway helper for the 2026-05-05 weekly review.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "reviews" / "20260505_weekly_experiments.md"
AGG = REPO / "analysis" / "results_index_20260505_agg.json"


def fmt_int(x):
    return f"{x:,.0f}" if x is not None else "—"


def per_run_table(records: list[dict]) -> str:
    rows = []
    for r in records:
        rpdf = f"{r['mean_RPDf']:.4f}"
        val = fmt_int(r["mean_value"])
        rows.append(f"  | {r['scen']} | {rpdf} | {val} | {r['n']} |")
    return (
        "- **결과**:\n\n"
        "  | scenarioName | mean RPDf (BKS>0) | mean bestObj | n |\n"
        "  |---|---|---|---|\n"
        + "\n".join(rows)
    )


def main() -> None:
    doc = DOC.read_text()

    # Pass 1: fix missing newlines between table cell and following heading.
    # Pattern: `|<digits>|<#### RUN >` → `|<digits>|\n\n<#### RUN >`
    doc, n1 = re.subn(
        r"(\| \d+ \|)(####? RUN )",
        r"\1\n\n\2",
        doc,
    )
    print(f"Inserted {n1} missing blank lines between table cell and RUN heading")

    # Pass 2: insert results for RUN 23-26 (#### subheadings under Phase 9 parent).
    # The agg JSON has these as runs 23, 24, 25, 26.
    records = json.loads(AGG.read_text())
    by_run: dict[int, list[dict]] = {}
    for r in records:
        by_run.setdefault(r["run"], []).append(r)

    pattern = re.compile(
        r"^(#### RUN (\d+) — `([^`]+)` \([^)]+\)\n(?:.*\n)*?)(?=^####? |^---$)",
        re.MULTILINE,
    )

    def replace(m):
        block = m.group(1)
        run_no = int(m.group(2))
        if run_no not in by_run:
            return block
        # Has it already been inserted? Check for "- **결과**" within block.
        if "- **결과**" in block:
            return block
        snippet = per_run_table(by_run[run_no])
        return block.rstrip() + "\n\n" + snippet + "\n\n"

    doc, n2 = pattern.subn(replace, doc)
    print(f"Inserted {n2} result tables for #### RUN subheadings")

    DOC.write_text(doc)
    print(f"Wrote {DOC.relative_to(REPO)}")


if __name__ == "__main__":
    main()
