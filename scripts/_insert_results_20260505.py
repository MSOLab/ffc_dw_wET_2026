"""Insert per-RUN result tables and 결과 요약 section into the 2026-05-05
weekly review document. Reads the aggregated JSON from
``analysis/results_index_20260505_agg.json``.

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
    """Render markdown table for one RUN's records."""
    if not records:
        return ""
    by_metric = {r["metric"] for r in records}
    if by_metric == {"mcfLb (no incumbent)"}:
        # All scenarios returned no incumbent — show mcfLb only
        rows = []
        for r in records:
            rows.append(f"  | {r['scen']} | {fmt_int(r['mean_value'])} | {r['n']} |")
        body = (
            "- **결과** *(no incumbent — algorithm did not register a full schedule;"
            " only `mcfLb` populated)*:\n\n"
            "  | scenarioName | mean mcfLb | n |\n"
            "  |---|---|---|\n"
            + "\n".join(rows)
        )
        return body

    # Mixed or all-bestObj
    rows = []
    for r in records:
        if r["metric"] == "bestObj":
            rpdf = f"{r['mean_RPDf']:.4f}"
            val = fmt_int(r["mean_value"])
        else:
            rpdf = "— *(no incumbent)*"
            val = f"mcfLb={fmt_int(r['mean_value'])}"
        rows.append(f"  | {r['scen']} | {rpdf} | {val} | {r['n']} |")

    return (
        "- **결과**:\n\n"
        "  | scenarioName | mean RPDf (BKS>0) | mean bestObj | n |\n"
        "  |---|---|---|---|\n"
        + "\n".join(rows)
    )


def build_summary_section(all_records: list[dict]) -> str:
    """Render the 결과 요약 section with full ranking."""
    with_obj = sorted(
        [r for r in all_records if r["metric"] == "bestObj"],
        key=lambda x: x["mean_RPDf"],
    )

    # Top 20 + bottom 5 ranking
    lines = [
        "## 결과 요약",
        "",
        "**Source**: `analysis/results_index_20260505.csv` (RUN 15는 hjt5950x 산출물 미보유로 제외 — 35 RUN × 1382 valid 인스턴스, BKS>0).",
        "",
        "### Top 20 시나리오 (mean RPDf 오름차, 낮을수록 우수)",
        "",
        "| 순위 | RUN | timestamp | scenarioName | mean RPDf | mean bestObj |",
        "|---|---|---|---|---|---|",
    ]
    # Need timestamp from records — we don't have it in the agg, so look up via run number.
    # Build run→timestamp map from the doc (or from build script).
    # Simpler: bake from the builder's RUNS table.
    run_ts = {
        1: "20260429T233115_006438", 2: "20260430T110852_547352",
        3: "20260501T162650_028232", 4: "20260502T002742_596323",
        5: "20260502T025451_273045", 6: "20260502T032313_670203",
        7: "20260502T131546_402074", 8: "20260502T133412_116270",
        9: "20260502T145150_590013", 10: "20260502T165007_640181",
        11: "20260502T184531_518809", 12: "20260502T193831_290902",
        13: "20260503T022442_340817", 14: "20260503T170549_147724",
        16: "20260503T181635_000784", 17: "20260503T191906_135722",
        18: "20260503T215803_006004", 19: "20260503T230126_683476",
        20: "20260504T003732_433340", 21: "20260504T004917_785558",
        22: "20260504T010002_965646", 23: "20260504T030753_945843",
        24: "20260504T031049_337896", 25: "20260504T031422_467379",
        26: "20260504T032002_269531", 27: "20260504T032732_697925",
        28: "20260504T082749_666067", 29: "20260504T093058_016949",
        30: "20260504T135233_268173", 31: "20260504T142221_504713",
        32: "20260505T014813_804225", 33: "20260505T025805_689859",
        34: "20260505T102202_582058", 35: "20260505T191440_984385",
        36: "20260505T192009_887337",
    }
    for i, r in enumerate(with_obj[:20], 1):
        ts = run_ts.get(r["run"], "?")
        lines.append(f"| {i} | {r['run']} | `{ts}` | `{r['scen']}` | {r['mean_RPDf']:.4f} | {fmt_int(r['mean_value'])} |")

    lines += [
        "",
        "### Bottom 10 (mean RPDf 내림차, 높을수록 비추)",
        "",
        "| 순위 | RUN | scenarioName | mean RPDf | mean bestObj |",
        "|---|---|---|---|---|",
    ]
    bot = with_obj[-10:][::-1]
    for i, r in enumerate(bot, 1):
        lines.append(f"| {i} | {r['run']} | `{r['scen']}` | {r['mean_RPDf']:.4f} | {fmt_int(r['mean_value'])} |")

    # Per-RUN best scenario summary
    lines += [
        "",
        "### RUN별 최우수 시나리오 (mean RPDf 기준)",
        "",
        "| RUN | best scenario | mean RPDf | mean bestObj |",
        "|---|---|---|---|",
    ]
    by_run = {}
    for r in with_obj:
        if r["run"] not in by_run or r["mean_RPDf"] < by_run[r["run"]]["mean_RPDf"]:
            by_run[r["run"]] = r
    for run in sorted(by_run.keys()):
        r = by_run[run]
        lines.append(f"| {run} | `{r['scen']}` | {r['mean_RPDf']:.4f} | {fmt_int(r['mean_value'])} |")

    # No-incumbent RUN list
    no_inc = sorted({r["run"] for r in all_records if r["metric"].startswith("mcfLb")})
    if no_inc:
        lines += [
            "",
            "### 결과 없음 (incumbent 미등록 — `mcfLb`만 채워진 RUN)",
            "",
            f"RUN {', '.join(str(r) for r in no_inc)} — Phase 3·4의 `single_pass_last_stage_only_sch_from_mcf_lb` 도입기 RUN(4~9)는"
            " `hasIncumbent=False`, `reportCount=0`. controller가 `single_pass_*` 결과를"
            " `AlgRecord` incumbent로 등록하는 경로가 미완이었던 것으로 보임.",
            " RUN 21은 `mcf_lb_only` (LB-only) — 설계상 schedule 없음.",
            "",
            "이들 RUN은 모두 `mcfLb_mean ≈ 40,847` (1382 valid 인스턴스, BKS>0).",
        ]

    # 주요 관찰
    best = with_obj[0]
    best_new = next(
        (r for r in with_obj if r["run"] >= 17),  # post-heuristic ls-only
        None,
    )
    lines += [
        "",
        "### 주요 관찰",
        "",
        f"- **최우수**: RUN {best['run']} `{best['scen']}` — mean RPDf {best['mean_RPDf']:.4f} (전주 best-of NEH-CP+MCF-LB 직렬 재현).",
        f"- **이번 주 알고리즘 라인의 최우수**: RUN {best_new['run']} `{best_new['scen']}` — mean RPDf {best_new['mean_RPDf']:.4f} (`adjust_p + adjust_r_half`).",
        "  전주 `best` 대비 약 +0.26 RPDf — 신규 `apply_lb_by_mcf → heuristic_last_stage → build_full_sch + adjust_*` 라인은 아직 NEH-CP+MCF-LB 직렬에 못 미침.",
        "- **`mcf_lb_then_neh_cp` 통합 step (RUN 3, RPDf 0.7330)**: 분리된 직렬 (RUN 1·2 `best`, RPDf 0.284)보다 훨씬 나쁨 — controller-level 통합이 NEH-CP에 넘기는 sequence/seed 정보를 일부 잃은 것으로 추정.",
        "- **p_increment 효과** (RUNs 13/14/16): p_inc 0→8 까지는 RPDf 미세 개선(0.6932→0.6509), p_inc 32부터 악화, p_inc 64에서 RPDf 1.06으로 급락.",
        "- **r_multiplier 효과** (RUN 17): 1.0→1.5 까지 개선 (0.7006→0.6531), 2.0부터 악화. r_mult 8.0에서 1.272로 최악.",
        "- **r_increment 효과** (RUN 19): r_inc 0~256 까지 0.66~0.70 plateau, 512부터 악화, 4096에서 1.16.",
        "- **p×r 그리드** (RUN 27/28/29): RUN 27 (r_mult=1.0) 최저 셀 ~0.62, RUN 28 (r_mult=2.0) 최저 ~0.63, RUN 29 (확장 80셀) p_inc≥32에서 일관되게 악화. 최적 영역은 (p+8, r+128) 근방.",
        "- **adjust_*** (RUNs 30~36): `p_adjust + r_half_adjust` 조합이 가장 우수 (RUN 33: 0.5761, RUN 34: 0.5704, RUN 35: 0.5413). single-pass `base` (RUN 33) 대비 약 0.13~0.20 RPDf 개선.",
        "- **`_only_pmtn_sch` 변종** (RUN 33→34): adjust 입력을 last-stage-only schedule → preemptive schedule로 바꿀 때 모든 6 시나리오에서 RPDf 개선 (예: `r_half_adjust` 0.6048→0.5904).",
        "- **dispatch try-both** (RUN 34→35): 같은 config 위에서 RPDf 약 0.01~0.02 추가 개선 (5조합에서 동일 방향).",
        "- **composite step `calc_mcf_lb_and_derive_full_sch`** (RUN 36): 4 시나리오 결과가 RUN 35의 대응 시나리오와 거의 동일 (`adjust_pr` 0.5413 vs RUN 35 `p_adjust_r_adjust` 0.5514). round 2 스킵 로직이 결과를 망치지 않으면서 6-step YAML 흐름과 등가.",
        "- **`4ca477d` perf change** (RUNs 23·24·25·26): wET 결과는 동일 (mcf_lb_only RUN 23·26 = 0.6906/0.6906; init_31[0] RUN 22·24·25 = 0.7426/0.7412/0.7426 — 이름 다른 변종이지만 동일 알고리즘 path). 시간만 ~46~56% 단축.",
        "",
    ]

    return "\n".join(lines)


def main() -> None:
    records = json.loads(AGG.read_text())
    by_run: dict[int, list[dict]] = {}
    for r in records:
        by_run.setdefault(r["run"], []).append(r)

    doc = DOC.read_text()

    # Insert per-RUN result tables. Find each "### RUN N — ..." heading and
    # append the result snippet at the end of its block (before next "###" or "---").
    # Strategy: split by "### RUN " markers (keeping the marker via a regex).
    pattern = re.compile(
        r"^### RUN (\d+) — `([^`]+)` \(([^)]+)\)\n",
        re.MULTILINE,
    )

    matches = list(pattern.finditer(doc))
    if not matches:
        raise SystemExit("No RUN headings found in doc")

    # Build new doc by walking through matches and inserting result block
    # immediately before the next "---" separator following each match.
    out_chunks = []
    cursor = 0
    for i, m in enumerate(matches):
        run_no = int(m.group(1))
        # End of this RUN block is either the next match start or the next "---" line, whichever first.
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(doc)
        # Find "---\n" within [m.end(), next_start)
        sep_match = re.search(r"\n---\n", doc[m.end():next_start])
        if sep_match:
            insertion_point = m.end() + sep_match.start()  # position of \n before ---
        else:
            insertion_point = next_start

        # Append the section content up to insertion point
        out_chunks.append(doc[cursor:insertion_point])

        # Build result snippet for this RUN (skip RUN 15 if no records — it's not in agg)
        recs = by_run.get(run_no, [])
        if recs:
            snippet = "\n\n" + per_run_table(recs)
            out_chunks.append(snippet)
        elif run_no == 15:
            out_chunks.append(
                "\n\n- **결과**: 본 저장소에 산출물 없음 (hjt5950x 머신). RUN 16(`mcf_lb_init_26` mso02 재실행)으로 비교."
            )

        cursor = insertion_point

    out_chunks.append(doc[cursor:])
    new_doc = "".join(out_chunks)

    # Insert 결과 요약 section before "## 큰 흐름 요약"
    summary = build_summary_section(records)
    if "## 큰 흐름 요약" not in new_doc:
        raise SystemExit("Cannot find '## 큰 흐름 요약' anchor")
    new_doc = new_doc.replace(
        "## 큰 흐름 요약",
        summary + "\n---\n\n## 큰 흐름 요약",
    )

    DOC.write_text(new_doc)
    print(f"Updated {DOC.relative_to(REPO)}: {len(matches)} RUN sections processed, summary section inserted")


if __name__ == "__main__":
    main()
