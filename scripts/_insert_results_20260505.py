"""Re-insert per-RUN result tables and 결과 요약 section into the 2026-05-05
chronological doc. Uses the new BKS=0-included aggregation
(``analysis/results_index_20260505_agg.json``).

Throwaway helper for the 2026-05-05 weekly review.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "reviews" / "20260505_weekly_experiments.md"
AGG = REPO / "analysis" / "results_index_20260505_agg.json"

RUN_TS = {
    1: "20260429T233115_006438", 2: "20260430T110852_547352",
    3: "20260501T162650_028232", 4: "20260502T002742_596323",
    5: "20260502T025451_273045", 6: "20260502T032313_670203",
    7: "20260502T131546_402074", 8: "20260502T133412_116270",
    9: "20260502T145150_590013", 10: "20260502T165007_640181",
    11: "20260502T184531_518809", 12: "20260502T193831_290902",
    13: "20260503T022442_340817", 14: "20260503T170549_147724",
    15: "20260503T170658_834025", 16: "20260503T181635_000784",
    17: "20260503T191906_135722", 18: "20260503T215803_006004",
    19: "20260503T230126_683476", 20: "20260504T003732_433340",
    21: "20260504T004917_785558", 22: "20260504T010002_965646",
    23: "20260504T030753_945843", 24: "20260504T031049_337896",
    25: "20260504T031422_467379", 26: "20260504T032002_269531",
    27: "20260504T032732_697925", 28: "20260504T082749_666067",
    29: "20260504T093058_016949", 30: "20260504T135233_268173",
    31: "20260504T142221_504713", 32: "20260505T014813_804225",
    33: "20260505T025805_689859", 34: "20260505T102202_582058",
    35: "20260505T191440_984385", 36: "20260505T192009_887337",
}


def fmt_int(x):
    return f"{x:,.0f}" if x is not None else "—"


def per_run_table(records: list[dict]) -> str:
    metrics = {r["metric"] for r in records}
    if metrics == {"mcfLb (no incumbent)"}:
        rows = [f"  | {r['scen']} | {fmt_int(r['mean_value'])} | {r['n']} |" for r in records]
        return (
            "- **결과** *(no incumbent — algorithm did not register a full schedule;"
            " only `mcfLb` populated)*:\n\n"
            "  | scenarioName | mean mcfLb | n |\n"
            "  |---|---|---|\n"
            + "\n".join(rows)
        )
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
        "  | scenarioName | mean RPDf | mean bestObj | n |\n"
        "  |---|---|---|---|\n"
        + "\n".join(rows)
    )


def build_summary(records: list[dict]) -> str:
    with_obj = sorted(
        [r for r in records if r["metric"] == "bestObj"],
        key=lambda x: x["mean_RPDf"],
    )
    no_inc = sorted({r["run"] for r in records if r["metric"].startswith("mcfLb")})

    lines = [
        "## 결과 요약",
        "",
        "**Source**: `analysis/results_index_20260505.csv` → `scripts/aggregate_results_index.py` → `analysis/results_index_20260505_agg.json`. 35 RUN × **1440 인스턴스 전부** (BKS=0 인 58개 인스턴스 포함). RUN 15는 hjt5950x 산출물 미보유로 빌더에서 제외.",
        "",
        "### Top 20 시나리오 (mean RPDf 오름차)",
        "",
        "| 순위 | RUN | timestamp | scenarioName | mean RPDf | mean bestObj |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(with_obj[:20], 1):
        ts = RUN_TS.get(r["run"], "?")
        lines.append(f"| {i} | {r['run']} | `{ts}` | `{r['scen']}` | {r['mean_RPDf']:.4f} | {fmt_int(r['mean_value'])} |")

    lines += [
        "",
        "### Bottom 10 (mean RPDf 내림차)",
        "",
        "| 순위 | RUN | scenarioName | mean RPDf | mean bestObj |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(with_obj[-10:][::-1], 1):
        lines.append(f"| {i} | {r['run']} | `{r['scen']}` | {r['mean_RPDf']:.4f} | {fmt_int(r['mean_value'])} |")

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

    if no_inc:
        lines += [
            "",
            "### 결과 없음 (incumbent 미등록 — `mcfLb`만 채워진 RUN)",
            "",
            f"RUN {', '.join(str(r) for r in no_inc)} — Phase 3·4의 `single_pass_last_stage_only_sch_from_mcf_lb` 도입기 RUN(4~9)는 `hasIncumbent=False`, `reportCount=0`. controller가 `single_pass_*` 결과를 `AlgRecord` incumbent로 등록하는 경로가 미완이었던 것으로 보임. RUN 21은 `mcf_lb_only` (LB-only) — 설계상 schedule 없음.",
            "",
            "이들 RUN은 모두 `mcfLb_mean ≈ 39,202` (1440 인스턴스).",
        ]

    best = with_obj[0]
    best_new = next((r for r in with_obj if r["run"] >= 17), None)
    lines += [
        "",
        "### 주요 관찰",
        "",
        f"- **최우수**: RUN {best['run']} `{best['scen']}` — mean RPDf {best['mean_RPDf']:.4f} (전주 best-of NEH-CP+MCF-LB 직렬 재현).",
        f"- **이번 주 알고리즘 라인의 최우수**: RUN {best_new['run']} `{best_new['scen']}` — mean RPDf {best_new['mean_RPDf']:.4f}. 전주 `best` 대비 약 +{best_new['mean_RPDf']-best['mean_RPDf']:.2f} RPDf — 신규 `apply_lb_by_mcf → heuristic_last_stage → build_full_sch + adjust_*` 라인은 아직 NEH-CP+MCF-LB 직렬에 못 미침.",
        "- **Last-stage-only 단독 vs full-schedule 격차**: last-stage-only obj는 80.2%(1155/1440) 인스턴스에서 BKS 이김 (algorithms doc Q1 참고), full-schedule wET는 ~14% 인스턴스로 감소 — reverse-dispatch 단계의 손실이 알고리즘의 주된 약점.",
        "- **`mcf_lb_then_neh_cp` 통합 step (RUN 3, RPDf 0.7330)**: 분리된 직렬 (RUN 1·2 `best`, RPDf 0.27)보다 훨씬 나쁨 — controller-level 통합이 NEH-CP에 넘기는 sequence/seed 정보를 일부 잃은 것으로 추정.",
        "- **p_increment 효과** (RUNs 13/14/16): p_inc 0→8 까지는 RPDf 미세 개선, 16부터 turnaround, 64에서 RPDf 1.06 폭락.",
        "- **r_multiplier 효과** (RUN 17): 1.0→1.5 까지 개선, 2.0부터 악화. r_mult 8.0에서 1.30로 최악.",
        "- **r_increment 효과** (RUN 19): r_inc 0~256 plateau, 512부터 악화, 4096에서 1.20.",
        "- **p×r 그리드** (RUN 27/28/29): 최저 RPDf ≈ 0.66 영역 (p+8, r+128 근방). p_inc≥32에서 일관 악화.",
        "- **adjust_*** (RUNs 30~36): `p_adjust + r_half_adjust` 조합이 가장 우수 (RUN 35: 0.5889). single-pass `base` 대비 약 0.13~0.20 RPDf 개선.",
        "- **`_only_pmtn_sch` 변종** (RUN 33→34): adjust 입력을 last-stage-only schedule → preemptive schedule로 교체. p_adjust 계열 -0.005~-0.018 개선, r_adjust 단독 +0.007 약간 후퇴 (mixed).",
        "- **dispatch try-both** (RUN 34→35): 같은 config 위에서 6 시나리오 모두 개선, 폭은 -0.001(`r_half_adjust`)~ -0.057(`p_adjust_r_adjust`).",
        "- **composite step `calc_mcf_lb_and_derive_full_sch`** (RUN 36): 4 시나리오 결과가 RUN 35와 동치 (`adjust_pr` 0.5889 = RUN 35 `p_adjust_r_half_adjust`). round 2 스킵 로직이 결과를 망치지 않으면서 6-step YAML과 등가.",
        "- **`4ca477d` perf change** (RUNs 23·24·25·26): 시간만 단축, wET 결과 거의 동일 — `build_full_sch_p_inc_0` (RUN 23/26): 0.6863/0.6914, `build_full_sch_p+16_rx2` (RUN 24/25): 0.7338/0.7352. body 메모상 60.90→33.05s, 83.24→36.72s (~46~56% 단축).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    records = json.loads(AGG.read_text())
    by_run: dict[int, list[dict]] = {}
    for r in records:
        by_run.setdefault(r["run"], []).append(r)

    doc = DOC.read_text()

    # Insert per-RUN snippets using next-heading anchor (### RUN / #### RUN / ## ).
    pattern = re.compile(
        r"(^#{3,4} RUN (\d+) — `([^`]+)` \([^)]+\)\n(?:.*\n)*?)"
        r"(?=^#{2,4} |\Z)",
        re.MULTILINE,
    )

    def replace(m):
        block = m.group(1)
        run_no = int(m.group(2))
        if "- **결과**" in block:
            return block  # already inserted
        if run_no == 15:
            snippet = "- **결과**: 본 저장소에 산출물 없음 (hjt5950x 머신). RUN 16(`mcf_lb_init_26` mso02 재실행)으로 비교."
        elif run_no in by_run:
            snippet = per_run_table(by_run[run_no])
        else:
            return block
        # Block ends with content + (maybe) trailing blank line. Add snippet then blank line before next heading.
        return block.rstrip() + "\n\n" + snippet + "\n\n"

    new_doc, n = pattern.subn(replace, doc)
    print(f"Inserted result snippets at {n} RUN headings")

    # Insert 결과 요약 before 큰 흐름 요약
    summary = build_summary(records)
    if "## 큰 흐름 요약" not in new_doc:
        raise SystemExit("Cannot find '## 큰 흐름 요약' anchor")
    new_doc = new_doc.replace("## 큰 흐름 요약", summary + "\n---\n\n## 큰 흐름 요약")

    DOC.write_text(new_doc)
    print(f"Wrote {DOC.relative_to(REPO)}")


if __name__ == "__main__":
    main()
