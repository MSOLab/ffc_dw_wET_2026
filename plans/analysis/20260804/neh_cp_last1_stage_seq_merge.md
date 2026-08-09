# NEH-CP 삽입 순서 8종 통합 리포트 — `(last-1)` stage 축 추가 (1440 그리드, 교차 run)

작성일: 2026-08-04 / 브랜치: `20260804_job_sequences`

이 문서는 **교차 run 통합 리포트**의 SSOT다. 선행 6종 통합
(`plans/analysis/20260804/neh_cp_seq_tiebreak_merge.md`)에 run D의
`(last-1)` stage 처치군 2종을 더해, 지금까지 시도한 삽입 순서 **8종을 한 표**에
놓는다. 선행 문서를 대체하지 않고 확장한다 — 6종 부분의 숫자는 재계산해도 동일하다.

> **범위 주의 1 — 측정면.** 여기 있는 숫자는 전부 **flow 최종 `bestObj`** 기준이다.
> flow 값은 `min(seed, NEH)`이므로 NEH가 seed를 못 이긴 인스턴스에서 모든 arm이 같은
> 숫자를 보고한다(아래 표의 tie 444~482개, 약 1/3). 계획서
> `plans/experiment/20260804/neh_cp_last1_stage_seq.md` §7.1이 지정한 본 실험의
> 측정면(= NEH 스텝 자체 산출물, `obj_log` 스텝 경계)은 **아직 만들지 않았다**
> (`scripts/20260804/analyze_neh_last1_seq.py` 미작성). 그 분석 문서는
> `plans/analysis/20260804/neh_cp_last1_stage_seq.md`가 되고, 본 문서의 결론이
> 거기서 확정 또는 정정된다.
>
> **범위 주의 2 — 교차 run.** 계획서 §6.1은 통제군을 **같은 run 안에** 두라고
> 지정했지만, 실행된 run D는 wall time을 아끼려 처치군 2개만 돌렸다(run D config의
> 주석이 이를 명시한다). 따라서 이 문서의 **모든 처치 대비는 교차 run**이고,
> `plans/analysis/20260801/neh_cp_seq_replicate.md`가 실측한 **±0.451 pp** 재현성
> 밴드를 매 숫자 옆에 함께 읽어야 한다. §4.1이 이 run 쌍에서 그 밴드를 다시 실측한다.

---

## 1. 질문

동일 flow(`dispatch_v4 -> MCF-LB -> FMM -> NEH-CP`, ISW-CP/base CP 꼬리 없음)와
동일 예산에서, NEH-CP의 **job 삽입 순서를 어디서 가져오는가**가 flow 결과를
움직이는가. 이번에 더해진 축은 정렬 키의 **종료 stage**다: 마지막 stage 종료시각
`ls` 대신 `(last-1)` stage 종료시각 `ls'`를 쓴다 (`seq_end_stage: -2`). 근거는
계획서 §1 — `insert_idle_time`이 **마지막 stage에만** 유휴를 삽입하므로 `ls`는
스케줄 구조에 due window 성분이 섞인 값이다.

## 2. 소스 run과 시나리오

| 시나리오 라벨 | 소스 run | 순서 |
|---|---|---|
| `dv4_mcf_fmm_neh_cp_midpoint_seq` | run A | `midpoint` (`m` → `fs` → rank) |
| `dv4_mcf_fmm_neh_cp_midpoint_seq_rep` | run C | 위와 **설정 동일** (replicate) |
| `dv4_mcf_fmm_neh_cp_midpoint2_seq` | run C | `midpoint` + `seq_tiebreak: completion` |
| **`dv4_mcf_fmm_neh_cp_midpoint3_seq`** | **run D** | **`midpoint2` + `seq_end_stage: -2`** |
| `dv4_mcf_fmm_neh_cp_completion_seq` | run A | `completion` |
| **`dv4_mcf_fmm_neh_cp_completion3_seq`** | **run D** | **`completion` + `seq_end_stage: -2`** |
| `dv4_mcf_fmm_neh_cp_first_stage_seq` | run A | `first_stage` |
| `dv4_mcf_fmm_neh_cp_job_priority` | run C | 인스턴스 규칙 `due2-weight-pos` |

- run A: `output/20260731_neh_cp_seq_source_compare/20260801T012922_726471`
- run C: `output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533`
- run D: `output/20260804_neh_cp_last1_stage_seq/20260804T213652_830716` (run setting `e8b6d03`)
- 합성 run: `output/20260804_merge_neh_cp_last1_stage_seq/20260804T233244_618881`
  (8 시나리오 × 1440, 인스턴스 디렉터리는 세 소스 run으로의 심링크)
- 통합 config: `metadata/20260804/merge_neh_cp_last1_stage_seq.yaml`

세 run의 NEH 스텝 이외 설정(flow, 예산, `instance_worker_cnt`)은 동일하다.
처치군의 짝은 다음과 같이 맞춰져 있다 — `completion3`↔`completion`은 축만 다르고,
`midpoint3`↔`midpoint2`는 2차 키를 `completion`으로 맞춰 **축 효과만** 남긴다
(계획서 §6.1).

**run D 위생 검사**: `main.log` / `MultiScenarioRunner.log`에 fallback·permutation
보정·stage 클램프 warning **0건**(계획서 §6.3 확인 2), 두 시나리오 모두 1440/1440
인스턴스 완주.

## 3. 재현

```bash
uv run python scripts/build_merged_run_dir.py \
  --dest output/20260804_merge_neh_cp_last1_stage_seq \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_midpoint_seq \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_midpoint_seq=dv4_mcf_fmm_neh_cp_midpoint_seq_rep \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_midpoint2_seq \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_first_stage_seq \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_completion_seq \
  output/20260803_neh_cp_midpoint_tiebreak/20260804T001517_995533/dv4_mcf_fmm_neh_cp_job_priority \
  output/20260804_neh_cp_last1_stage_seq/20260804T213652_830716/dv4_mcf_fmm_neh_cp_completion3_seq \
  output/20260804_neh_cp_last1_stage_seq/20260804T213652_830716/dv4_mcf_fmm_neh_cp_midpoint3_seq

uv run python main.py --config metadata/20260804/merge_neh_cp_last1_stage_seq.yaml

uv run python scripts/20260804/summarize_seq_merge.py \
  output/20260804_merge_neh_cp_last1_stage_seq/20260804T233244_618881 \
  --contrast completion3_seq completion_seq \
  --contrast midpoint3_seq midpoint2_seq \
  --contrast midpoint_seq midpoint_seq_rep \
  --contrast completion3_seq midpoint3_seq
```

`--run-id`를 주지 않으면 타임스탬프가 새로 생기므로, 재실행 시 config의
`analysis_dir_path`를 새 경로로 바꿔야 한다. `draw_gantt` / `draw_progress_plot`는
반드시 `false` — 두 painter는 인스턴스 디렉터리 **안에** 쓰는데 그 디렉터리가
소스 run으로의 심링크다.

산출물(모두 합성 run 디렉터리 안, gitignored): `*_report.xlsx`,
`*_rpdf_comparison.csv`, `*_rpdf_dashboard.html`, `*_win_tie_dashboard.html`,
`*_time_p_dashboard.html`, `*_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`,
`*_multi_scenario_subroutine_flow_comparison.html`, `*_mcf_lb_dashboard.html`.

## 4. 결과

RPDf는 `RPDf_BKS_data`(대칭 RPDf, `pra2017-instance-params` 스킬 정의)를 %p로 읽는다.

### 4.0 통합 평균 (1440)

| 시나리오 | mean RPDf (%p) | mean elapsed (s) | mean time% |
|---|---:|---:|---:|
| **`completion3_seq`** (run D) | **12.842** | 15.238 | 0.174 |
| **`midpoint3_seq`** (run D) | **13.129** | 15.184 | 0.174 |
| `midpoint2_seq` | 13.458 | 15.224 | 0.174 |
| `midpoint_seq_rep` (run C) | 13.621 | 15.237 | 0.175 |
| `completion_seq` | 13.751 | 15.243 | 0.175 |
| `midpoint_seq` (run A) | 14.036 | 15.200 | 0.174 |
| `first_stage_seq` | 15.151 | 15.172 | 0.174 |
| `job_priority` | 15.759 | 15.648 | 0.179 |

노력은 균질하다 — 8개 arm의 평균 소요가 15.17~15.65 s 안에 있고 시나리오 cap
(`0.09nc`)은 어디서도 바인딩되지 않았다(time% 최대 0.179). **두 처치군이 1·2위**이고,
유도 순서 6종은 12.8~14.0 %p의 1.2 %p 폭 안에 몰려 있다 — 즉 "다 비슷비슷하다"는
인상은 대체로 맞고, 이 문서가 답해야 하는 것은 **그 좁은 폭 안의 차이가 run-to-run
잡음보다 큰가**이다.

### 4.1 재현성 밴드 (이 run 쌍에서의 실측 null)

처치 대비가 전부 교차 run이므로, 같은 설정을 두 번 돌렸을 때의 차이가 이 표의
**영가설 크기**다.

| 슬라이스 | `midpoint_seq`(A) − `midpoint_seq_rep`(C) |
|---|---|
| pooled (1440) | +0.416 %p (SE 0.259, 1.6 σ) |
| T=0.2 | +0.247 (0.4 σ) |
| T=0.4 | +0.804 (1.8 σ) |
| T=0.6 | +0.196 (1.3 σ) |
| c=5 | +0.876 (2.1 σ) |
| c=10 | −0.045 (−0.2 σ) |

`plans/analysis/20260801/neh_cp_seq_replicate.md`의 95 % 밴드 ±0.451 %p와 일관된다.
**c=5에서 드리프트가 +0.88 %p로 가장 크다**는 점은 아래 §4.3의 c 분해를 읽을 때
반드시 함께 봐야 한다.

### 4.2 축 변경 대비 (처치 − 짝지은 통제, paired)

| 대비 | pooled | T=0.2 | T=0.4 | T=0.6 |
|---|---|---|---|---|
| `completion3` − `completion` | **−0.909** (0.350, −2.6 σ) | −1.224 (−1.3 σ) | −1.022 (−2.2 σ) | **−0.481 (−2.9 σ)** |
| `midpoint3` − `midpoint2` | −0.329 (0.272, −1.2 σ) | −0.573 (−0.8 σ) | −0.166 (−0.4 σ) | −0.247 (−1.4 σ) |

(음수 = 처치가 좋음. 괄호는 SE와 σ.)

win/tie/loss (pooled): `completion3`−`completion` 551/444/445,
`midpoint3`−`midpoint2` 535/468/437.

두 대비의 부호가 **모든 슬라이스에서 음수**(처치 우세)로 일관된다. 크기는 다르다:

- **`completion3`의 −0.909 %p는 재현성 밴드(±0.451)의 약 2배**이고 pooled 2.6 σ다.
  잡음만으로는 설명되지 않는다.
- **`midpoint3`의 −0.329 %p는 밴드 안**이다(1.2 σ). 부호는 일관되지만 이 run 쌍만으로
  "구분된다"고 말할 수 없다.

T=0.6은 flow 포화(tie)가 거의 없는 슬라이스라(tie 10~12/480) SE가 작고, 계획서 §7.3이
결론을 우선하라고 지정한 슬라이스다. 거기서 `completion3`는 −0.481 %p / 2.9 σ이고,
같은 슬라이스의 재현성 드리프트는 +0.196 %p에 불과하다 — **효과가 드리프트의 2.5배**.

### 4.3 (n, c) 분해 — mean RPDf (%p)

| n | c | comp3 | comp | mid3 | mid2 | mid(rep) | mid(A) | first_stage | job_priority |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 5 | 22.94 | 25.36 | **20.56** | 22.19 | 22.44 | 22.32 | 26.04 | 25.48 |
| 50 | 10 | 28.84 | 28.47 | **26.52** | 26.75 | 28.29 | 28.19 | 27.21 | 31.48 |
| 100 | 5 | **16.21** | 16.71 | 17.19 | 17.27 | 16.40 | 18.97 | 17.50 | 16.78 |
| 100 | 10 | **21.43** | 22.58 | 22.57 | 22.66 | 23.93 | 23.20 | 24.38 | 25.74 |
| 150 | 5 | 4.67 | 5.40 | 5.73 | **4.44** | 5.93 | 5.69 | 6.72 | 5.93 |
| 150 | 10 | **6.91** | 8.30 | 8.37 | 8.58 | 8.64 | 8.69 | 10.49 | 12.84 |
| 200 | 5 | **−0.47** | 0.54 | 1.65 | 2.15 | 1.04 | 2.33 | 3.55 | 1.74 |
| 200 | 10 | **2.21** | 2.64 | 2.43 | 3.63 | 2.30 | 2.89 | 5.32 | 6.08 |

`completion3`가 8셀 중 5셀에서 최상이고, 유일하게 음수 RPDf 셀(n=200, c=5에서
−0.47 %p, 즉 평균적으로 BKS보다 좋음)을 만든다.

축 효과를 c로 나누면:

| 대비 | c=5 | c=10 |
|---|---|---|
| `completion3` − `completion` | −1.164 (−2.2 σ) | −0.654 (−1.5 σ) |
| `midpoint3` − `midpoint2` | −0.229 (−0.5 σ) | −0.429 (−1.4 σ) |
| *(null)* replicate 드리프트 | +0.876 (+2.1 σ) | −0.045 (−0.2 σ) |

계획서 §2.2는 c=5(stage가 적음)에서 축 변경의 순서 섭동이 더 크다고 예측했고
(`completion3`↔`completion` 거리 c=5 0.0882 vs c=10 0.0679), `completion3`의 c=5
효과가 더 큰 것은 그 예측과 부합한다. **다만 c=5는 재현성 드리프트도 +0.88 %p로
가장 큰 슬라이스다** — 부호가 반대(드리프트는 처치에 불리한 방향)이므로 효과를
가짜로 만들지는 않지만, c별 크기 비교는 이 run 쌍만으로 확정하지 않는다.

### 4.4 두 처치군 사이

`completion3` − `midpoint3` = −0.288 %p (SE 0.426, −0.7 σ), 487/449/504. pooled로는
**구분되지 않는다.** T=0.4에서만 −1.246 (−2.6 σ)로 갈리고 T=0.2/T=0.6에서는 0에
가깝다(+0.393 / −0.011). 6종 리포트에서 `midpoint`와 `completion`이 구분되지 않았던
것과 같은 그림이며, 축을 바꿔도 그 관계는 유지된다.

### 4.5 계획서 §4의 외삽 가정 — 과대예측

계획서 §4는 순서 거리(§2.2)에서 효과를 선형 외삽해 `completion3` ≈ 2 %p,
`midpoint3` ≈ 1.3 %p를 예측했다. 실측은 −0.909 %p와 −0.329 %p로 **각각 약 2배 / 4배
작다.** 즉 "순서를 많이 흔들수록 효과가 비례해서 커진다"는 가정은 이 두 점에서
성립하지 않고, 예측은 낙관적이다. 다만 이것은 arm 평균 2점에 의한 판단이고,
계획서 §7.4가 지정한 **인스턴스별 거리 대 효과 산점도**(진단 라인의
`dist_to_same_source_last_stage` 파싱)가 이 가정의 제대로 된 검정이다 — 아직 하지
않았다.

## 5. 결론 (flow 수준, 잠정)

1. **축을 `(last-1)` stage로 옮기는 것은 `completion` 계열에서 실효과가 있다.**
   −0.909 %p (2.6 σ), 재현성 밴드의 약 2배. 부호가 세 T 슬라이스 전부와 두 c
   슬라이스 전부에서 일관되고, 계획서가 우선하라고 한 T=0.6에서 −0.481 %p (2.9 σ,
   같은 슬라이스 드리프트 +0.196의 2.5배)다. `completion3_seq`는 8종 중 pooled 최상
   (12.842 %p)이며 (n,c) 8셀 중 5셀 최상이다.
2. **`midpoint` 계열에서는 축 효과가 밴드 안이다.** −0.329 %p (1.2 σ). 부호는
   `completion` 계열과 같지만 이 run만으로 구분된다고 말할 수 없다. `midpoint3`가
   pooled 2위(13.129 %p)인 것도 이 대비를 확정하지 못한다 — 통제군과의 차이가
   드리프트와 같은 자릿수다.
3. **전체 그림은 "대체로 비슷"이 맞다.** 유도 순서 6종은 12.8~14.0 %p 안에 있고,
   서로 간 대비 대부분이 1 σ 수준이다. 명확히 갈리는 것은 여전히 두 열등 arm뿐이다
   — `first_stage` (+2.31 %p vs `completion3`, 4.8 σ)와 인스턴스 규칙 `job_priority`
   (+2.92 %p, 7.9 σ). 6종 리포트의 결론 1·3은 그대로 유지된다.
4. **확정은 스텝 수준 분석에 달려 있다.** flow가 인스턴스의 약 1/3을 동률로 만들고
   있어, 여기의 모든 효과 크기는 **희석된 하한**이다. 진짜 대비는 NEH 스텝 자체
   산출물에서 재야 한다(계획서 §7.1).

## 6. 다음 단계

1. **스텝 수준 분석** (계획서 §7.1) — `scripts/20260804/analyze_neh_last1_seq.py`를
   작성해 `seed_obj` / `neh_obj` / `neh_best`로 다시 잰다. 파싱 전
   `docs/artifacts/obj_log.md`를 읽을 것. 결과 문서는
   `plans/analysis/20260804/neh_cp_last1_stage_seq.md`. 여기 결론 1·2가 거기서
   확정 또는 정정된다.
2. **거리 대 효과 산점도** (계획서 §7.4) — §4.5의 과대예측을 인스턴스 수준에서
   검정한다.
3. **`completion3`의 in-run 확인** — 결론 1을 실무 기본값으로 승격하려면 교차 run
   대비가 아니라 **같은 run 안의 통제군**과의 대비가 필요하다(계획서 §6.1이 원래
   지정했던 구성). 다음 순서 실험에 `completion_seq`를 통제군으로 함께 태운다.
4. 결과가 확정되면 `TODO.md`에 효과 크기와 부호를 한 줄로 남긴다(계획서 §8).
