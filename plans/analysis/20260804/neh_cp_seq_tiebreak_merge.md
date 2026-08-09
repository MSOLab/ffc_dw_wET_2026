# NEH-CP 삽입 순서 6종 통합 리포트 (1440 그리드, 교차 run)

작성일: 2026-08-04 / 브랜치: `20260731_neh_cp`

이 문서는 **교차 run 통합 리포트**의 SSOT다. run C(`midpoint` tie-break 실험)의 3개
arm과 run A(`neh_cp_seq_source_compare`)의 `dv4_mcf_fmm_*` 3개 시나리오를 하나의
합성 run 디렉터리로 묶어 리포터를 한 번 더 돌린 결과를 담는다.

> **범위 주의.** 여기 있는 숫자는 전부 **flow 최종 `bestObj`** 기준이다. 계획서
> `plans/experiment/20260803/neh_cp_midpoint_tiebreak.md` §3.1/§7.1이 지정한
> 본 분석의 측정면(= NEH 스텝 자체 산출물, `obj_log` 스텝 경계)은 **아직 만들지
> 않았다** (`scripts/20260803/analyze_neh_midpoint_tiebreak.py` 미작성). flow
> 값은 `min(seed, NEH)`이므로 NEH가 seed를 못 이긴 인스턴스에서 모든 arm이 같은
> 숫자를 보고한다 — 아래 표에서 시나리오당 460~480개(약 1/3)가 정확히 동률인
> 것이 그 흔적이다. 따라서 이 문서는 **arm 간 flow 수준 요약**이고, tie-break
> 질문의 최종 답은 스텝 수준 분석이 나온 뒤에 확정한다.

---

## 1. 질문

동일 flow(`dispatch_v4 -> MCF-LB -> FMM -> NEH-CP`, ISW-CP/base CP 꼬리 없음)와
동일 예산에서, NEH-CP의 **job 삽입 순서를 어디서 가져오는가**가 flow 결과를
움직이는가. 순서 후보 6종을 한 표에서 비교한다.

## 2. 소스 run과 시나리오

| 시나리오 라벨 | 소스 run | 순서 |
|---|---|---|
| `dv4_mcf_fmm_neh_cp_midpoint_seq` | run A | `midpoint` (`m` → `fs` → rank) |
| `dv4_mcf_fmm_neh_cp_midpoint_seq_rep` | run C | 위와 **설정 동일** (replicate) |
| `dv4_mcf_fmm_neh_cp_midpoint2_seq` | run C | `midpoint` + `seq_tiebreak: completion` |
| `dv4_mcf_fmm_neh_cp_first_stage_seq` | run A | `first_stage` |
| `dv4_mcf_fmm_neh_cp_completion_seq` | run A | `completion` |
| `dv4_mcf_fmm_neh_cp_job_priority` | run C | 인스턴스 규칙 `due2-weight-pos` |

- run A: `output/20260731_neh_cp_seq_source_compare/20260801T012922_726471`
- run C: `output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533`
- 합성 run: `output/20260804_merge_neh_cp_seq_tiebreak/20260804T083910_601120`
  (6 시나리오 × 1440, 인스턴스 디렉터리는 두 소스 run으로의 심링크)
- 통합 config: `metadata/20260804/merge_neh_cp_seq_tiebreak.yaml`

두 run의 `dv4_mcf_fmm_neh_cp_midpoint_seq` **`subroutine_flow`가 완전히 동일**함을
config 스냅샷 비교로 확인했다(그래서 라벨 충돌을 `_rep` 접미사로 풀었다). 즉 이
쌍은 무료 run-to-run 재현성 검사다(§4.1).

## 3. 재현

```bash
uv run python scripts/build_merged_run_dir.py \
  --dest output/20260804_merge_neh_cp_seq_tiebreak \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_midpoint_seq \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_midpoint_seq=dv4_mcf_fmm_neh_cp_midpoint_seq_rep \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_midpoint2_seq \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_first_stage_seq \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_completion_seq \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_job_priority

uv run python main.py --config metadata/20260804/merge_neh_cp_seq_tiebreak.yaml
```

`--run-id`를 주지 않으면 타임스탬프가 새로 생기므로, 재실행 시 config의
`analysis_dir_path`를 새 경로로 바꿔야 한다. `draw_gantt` / `draw_progress_plot`는
반드시 `false` — 두 painter는 인스턴스 디렉터리 **안에** 쓰는데 그 디렉터리가
소스 run으로의 심링크다.

산출물(모두 합성 run 디렉터리 안, gitignored):
`*_report.xlsx`, `*_rpdf_comparison.csv`, `*_rpdf_dashboard.html`,
`*_win_tie_dashboard.html`, `*_time_p_dashboard.html`,
`*_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`,
`*_multi_scenario_subroutine_flow_comparison.html`, `*_mcf_lb_dashboard.html`.

## 4. 결과

RPDf는 `RPDf_BKS_data`(대칭 RPDf, `pra2017-instance-params` 스킬 정의)를 %p로 읽는다.

### 4.0 통합 평균 (1440)

| 시나리오 | mean RPDf (%p) | mean elapsed (s) | mean time% |
|---|---:|---:|---:|
| `midpoint_seq` (run A) | 14.036 | 15.200 | 0.174 |
| `midpoint_seq_rep` (run C) | 13.621 | 15.237 | 0.175 |
| `midpoint2_seq` | 13.458 | 15.224 | 0.174 |
| `first_stage_seq` | 15.151 | 15.172 | 0.174 |
| `completion_seq` | 13.751 | 15.243 | 0.175 |
| `job_priority` | 15.759 | 15.648 | 0.179 |

노력은 균질하다 — 6개 arm의 평균 소요가 15.17~15.65 s 안에 있고 시나리오 cap
(`0.09nc`)은 어디서도 바인딩되지 않았다(time% 최대 0.179).

### 4.1 paired 대비 (기준 = `midpoint_seq_rep`, 1440)

| 대비 | Δmean (%p) | SE | win/tie/loss |
|---|---:|---:|---|
| `midpoint_seq` (run A) − rep | +0.416 | 0.259 | 455 / 482 / 503 |
| `midpoint2_seq` − rep | −0.162 | 0.316 | 482 / 466 / 492 |
| `first_stage_seq` − rep | +1.531 | 0.413 | 422 / 463 / 555 |
| `completion_seq` − rep | +0.130 | 0.409 | 469 / 439 / 532 |
| `job_priority` − rep | +2.139 | 0.422 | 284 / 467 / 689 |

(양수 = 기준보다 나쁨. tie가 439~482개인 것이 위 "범위 주의"에서 말한 flow 포화다.)

**재현성 검사 통과**: 설정이 같은 두 replicate의 차이는 +0.416 %p (1.6 σ)로,
`plans/analysis/20260801/neh_cp_seq_replicate.md`가 실측한 ±0.451 %p 밴드 안이다.
run C의 실행 환경(코어 점유·경합)을 의심할 근거는 없다.

### 4.2 T별 분해 (필수 — 통합 평균은 부호가 상쇄된다)

mean RPDf (%p):

| 시나리오 | T=0.2 | T=0.4 | T=0.6 |
|---|---:|---:|---:|
| `midpoint_seq` (run A) | −17.517 | 32.868 | 26.757 |
| `midpoint_seq_rep` | −17.764 | 32.064 | 26.561 |
| `midpoint2_seq` | −19.119 | 32.722 | 26.772 |
| `first_stage_seq` | −16.030 | 34.535 | 26.949 |
| `completion_seq` | −18.075 | 32.332 | 26.995 |
| `job_priority` | −19.036 | 35.368 | 30.946 |

paired 대비 (기준 = `midpoint_seq_rep`, 슬라이스당 480):

| 대비 | T=0.2 | T=0.4 | T=0.6 |
|---|---|---|---|
| `midpoint_seq` (run A) | +0.247 (0.4 σ) | +0.804 (1.8 σ) | +0.196 (1.3 σ) |
| `midpoint2_seq` | −1.356 (−1.7 σ) | +0.658 (1.3 σ) | +0.211 (1.4 σ) |
| `first_stage_seq` | +1.734 (1.6 σ) | +2.471 (4.0 σ) | +0.387 (2.3 σ) |
| `completion_seq` | −0.312 (−0.3 σ) | +0.268 (0.5 σ) | +0.434 (2.5 σ) |
| `job_priority` | −1.272 (−1.3 σ) | +3.303 (4.7 σ) | +4.385 (14.3 σ) |

T=0.2에서 `job_priority`와 `midpoint2`가 유일하게 기준보다 좋게 나오고 T=0.4/0.6에서
뒤집히는 것이, 통합 평균을 읽으면 안 되는 이유의 또 한 사례다
(`plans/analysis/20260802/neh_cp_budget_allocation.md` 조치 4).

### 4.3 (n, c) 분해 — mean RPDf (%p)

| n | c | midpoint (A) | rep | midpoint2 | first_stage | completion | job_priority |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 5 | 22.32 | 22.44 | 22.19 | 26.04 | 25.36 | 25.48 |
| 50 | 10 | 28.19 | 28.29 | 26.75 | 27.21 | 28.47 | 31.48 |
| 100 | 5 | 18.97 | 16.40 | 17.27 | 17.50 | 16.71 | 16.78 |
| 100 | 10 | 23.20 | 23.93 | 22.66 | 24.38 | 22.58 | 25.74 |
| 150 | 5 | 5.69 | 5.93 | 4.44 | 6.72 | 5.40 | 5.93 |
| 150 | 10 | 8.69 | 8.64 | 8.58 | 10.49 | 8.30 | 12.84 |
| 200 | 5 | 2.33 | 1.04 | 2.15 | 3.55 | 0.54 | 1.74 |
| 200 | 10 | 2.89 | 2.30 | 3.63 | 5.32 | 2.64 | 6.08 |

`job_priority`의 열세는 **c=10에 몰려 있다** — 네 개의 c=10 셀 전부에서 가장 나쁘고
(최대 +4.2 %p, n=150), c=5 셀에서는 중간이다. 셀 필터가 붙은 대화형 표는
`*_rpdf_dashboard.html`과 `*_report.xlsx`에 있다.

## 5. 결론 (flow 수준, 잠정)

1. **`job_priority`(인스턴스 규칙)는 유도 순서보다 확실히 나쁘다.** 동일 prefix·
   동일 예산에서 +2.14 %p (5.1 σ), T=0.6 슬라이스에서 +4.39 %p (14.3 σ). 계획서
   §7.2의 질문 2에 대한 답이며, 파일럿이 "이 런으로 답할 수 없다"고 남긴 교란
   (baseline에 seeding prefix가 없었음)을 제거한 상태의 답이다.
2. **tie-break 키(`midpoint` vs `midpoint2`)는 flow 수준에서 구분되지 않는다.**
   −0.16 %p (0.5 σ)로, replicate 노이즈(±0.45 %p)와 같은 자릿수다. 계획서 §4가
   예고한 "상한 결론"과 부합하지만, **이 대비의 확정은 스텝 수준 분석이 필요**하다
   (flow가 1/3의 인스턴스에서 두 arm을 동률로 만든다).
3. 모드 간 순위는 flow 수준에서도 `first_stage`가 가장 나쁘다(+1.53 %p, 3.7 σ).
   `midpoint`와 `completion`은 구분되지 않는다(+0.13 %p, 0.3 σ) — 선행
   `neh_cp_seq_source_full.md` 결과 0과 같은 그림이다.

## 6. 다음 단계

계획서 §7의 스텝 수준 분석(`scripts/20260803/analyze_neh_midpoint_tiebreak.py`)을
작성해 `seed_obj` / `neh_obj` / `neh_best`로 다시 잰다. 그 문서는
`plans/analysis/20260803/neh_cp_midpoint_tiebreak.md`가 되고, 본 문서의 결론 2가
거기서 확정 또는 정정된다. 파싱 전 `docs/artifacts/obj_log.md`를 읽을 것.
