# `job_batch_cp` vs `incremental_sw_cp` — flow tail 자리 교체 비교

작성일: 2026-08-05 / 슬라이스: PRA2017 large `(T, R) = (0.6, 0.2)` 160 인스턴스

이 문서가 이 분석의 **single source of truth**다. 벌크 산출물
(`analysis/20260805_job_batch_cp_vs_isw_cp/`, 병합 리포트 HTML)은 gitignore되므로
여기의 표만으로 결론이 서야 한다.

---

## 1. 질문

`20260804_job_batch_cp_compare`가 "`job_batch_cp`는 `neh_cp`를 in-place 대체할 수
없다"를 확정했다 (mean RPDf 0.187 vs 0.128). 남은 질문은 **반대쪽 교체**다:
`job_batch_cp`와 `incremental_sw_cp`는 둘 다 완성된 incumbent 위에서 도는
destroy-repair refiner이므로, 정직한 맞대결 자리는 NEH 자리가 아니라 **flow의
tail**이다.

- **Q1** — 같은 flow·같은 cap에서, 순서로 자른 **job 배치**를 훑는 것이
  op을 **시간창**으로 미는 것보다 나은가?
- **Q2** — 총예산을 1/3로 줄이면 둘 중 누가 덜 무너지는가?

공통 flow: `dispatch_v4 → MCF-LB → FMM → neh_cp_completion_seq → <TAIL> → base CP`

---

## 2. 소스 run 디렉터리

| arm | 소스 | 비고 |
|---|---|---|
| `dv4_mcf_fmm_neh_isw_t090` | `output/20260801_neh_cp_budget_allocation/20260801T183302_770739/dv4_mcf_fmm_comp_x1_base` | 차용 (1440 중 160 추출) |
| `dv4_mcf_fmm_neh_isw_t090_jp` | `output/20260728_dispatch_v4_init_tl/20260728T202801_339672/dv4_c5init_f40` | 차용, NEH 순서만 다름 (`neh_cp`) |
| `dv4_mcf_fmm_neh_jbc_t090` | `output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_jbc_t090` | 신규 |
| `dv4_mcf_fmm_neh_isw_t030` | `output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_isw_t030` | 신규 |
| `dv4_mcf_fmm_neh_jbc_t030` | `output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_jbc_t030` | 신규 |

초기예산 축 참조점: `output/20260728_dispatch_v4_init_tl/20260728T202801_339672`의
`dv4_c5init_f10` / `f20` / `f40`.

병합 run 디렉터리: `output/20260805_merge_job_batch_cp_vs_isw_cp/20260805T123906_688975`

## 3. 재현

```bash
# 1) 병합 run 디렉터리 (심링크) 생성
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260805_merge_job_batch_cp_vs_isw_cp --intersect-instances \
    output/20260801_neh_cp_budget_allocation/20260801T183302_770739/dv4_mcf_fmm_comp_x1_base=dv4_mcf_fmm_neh_isw_t090 \
    output/20260728_dispatch_v4_init_tl/20260728T202801_339672/dv4_c5init_f40=dv4_mcf_fmm_neh_isw_t090_jp \
    output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_jbc_t090 \
    output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_isw_t030 \
    output/20260805_job_batch_cp_vs_isw_cp/20260805T104621_844665/dv4_mcf_fmm_neh_jbc_t030

# 2) 대시보드 / report.xlsx  (analysis_dir_path를 1)의 출력으로 갱신할 것)
uv run python main.py --config metadata/20260805/merge_job_batch_cp_vs_isw_cp.yaml

# 3) 이 문서의 모든 표
uv run python scripts/20260805/analyze_jbc_vs_isw.py
```

---

## 4. 결과

### 4.1 arm별 요약 (160 인스턴스)

| arm | mean RPDf | median | mean obj | mean 초 |
|---|---|---|---|---|
| `isw_t090` | **0.1460** | 0.1315 | 203 250 | 84.4 |
| `isw_t090_jp` | **0.1464** | 0.1277 | 203 826 | 84.4 |
| `jbc_t090` | 0.1868 | 0.1769 | 213 794 | 84.4 |
| `isw_t030` | 0.1754 | 0.1705 | 209 723 | 28.2 |
| `jbc_t030` | 0.2204 | 0.2121 | 220 604 | 28.2 |

### 4.2 페어드 대비 (인스턴스별 차이의 평균)

| 대비 | ΔRPDf (pp) | Δobj (%) | 승 / 무 / 패 |
|---|---|---|---|
| **노이즈 자** `isw_t090_jp − isw_t090` | **+0.045** | +0.28 | 81 / 0 / 79 |
| **Q1** `jbc_t090 − isw_t090` | **+4.08** | +5.19 | 23 / 0 / 137 |
| Q1 (`_jp` 기준) `jbc_t090 − isw_t090_jp` | +4.03 | +4.89 | 26 / 0 / 134 |
| **Q2** `jbc_t030 − isw_t030` | **+4.50** | +5.19 | 12 / 0 / 148 |
| `isw_t030 − isw_t090` | +2.94 | +3.18 | 27 / 0 / 133 |
| `jbc_t030 − jbc_t090` | +3.37 | +3.19 | 20 / 0 / 140 |
| **`jbc_t090 − isw_t030`** (예산 3배) | **+1.13** | +1.94 | 53 / 0 / 107 |

RPDf는 대칭 정의 `2(obj−ref)/(obj+ref)`, ref는 `BKS_data`. 양수 = 나쁨.

### 4.3 (n, c) 분해 — Q1 / Q2 격차 (pp, 양수 = JBC 열세)

| n | c | Q1 (t090) | Q2 (t030) |
|---|---|---|---|
| 50 | 5 | 4.29 | 3.69 |
| 50 | 10 | 1.12 | 2.66 |
| 100 | 5 | 3.13 | 4.14 |
| 100 | 10 | 3.88 | 5.29 |
| 150 | 5 | 3.69 | 4.71 |
| 150 | 10 | 5.16 | 5.73 |
| 200 | 5 | 5.82 | 5.26 |
| 200 | 10 | 5.54 | 4.52 |

### 4.4 `job_batch_cp` sweep 동작 (step log)

| arm | pass/인스턴스 (평균) | 배치 수락률 | pass 8(batch_size 50) 도달 |
|---|---|---|---|
| `jbc_t090` | 7.56 (7–8) | 83.9 % (4822/5748) | 160 중 89 |
| `jbc_t030` | 2.66 (2–3) | 88.5 % (2220/2509) | 0 (pass 3까지 106) |

설계 시 예측(t090 7.3 pass, t030 2.2 pass)과 일치. 비례 TL로 pass 비용이
`κ·n·c`가 되어 인스턴스 크기와 무관하게 일정하다는 계산도 실측으로 확인됐다.

---

## 5. `t030`은 의도한 실험이 아니었다 — 무엇이 측정됐나

**의도**: `20260728_dispatch_v4_init_tl`의 축을 잇는 것. 그 실험은 cap을 `0.09nc`로
**고정한 채** 초기화 예산만 20260710 `kappa_0.005` 기준선(FMM `0.009nc`,
NEH `0.027nc`)의 10 / 20 / 40 %로 줄였다. `f40` = `0.0036nc / 0.0108nc`이고,
이것이 이 실험 t090 arm들이 쓰는 바로 그 초기화 예산이다. 즉 **t090은 이미
"초기화 40 %" 지점**이고, 다음 발걸음은 40 % → 더 축소였다.

**실제로 만들어진 것**: t030은 초기화를 40 % → 13.3 %로 줄이면서 **cap도 1/3로**
같이 줄였다. 20260728 축이 변수 하나(초기화)만 움직인 것과 달리 두 개가 함께
움직였으므로, 두 실험은 같은 축의 연장이 아니라 **직교하는 축**이다.

**다행히 분해가 가능하다** — 초기화 축이 이미 같은 슬라이스에 존재한다
(cap `0.09nc` 고정, NEH 순서는 세 arm 모두 `neh_cp`):

| arm | 초기화 (기준선 대비) | mean RPDf | f40 대비 (pp) |
|---|---|---|---|
| `dv4_c5init_f10` | 10 % | 0.1457 | **−0.075** |
| `dv4_c5init_f20` | 20 % | 0.1486 | +0.220 |
| `dv4_c5init_f40` | 40 % | 0.1464 | 0 |

노이즈 자가 0.045 pp이므로 세 점은 **사실상 평평하다**. 따라서:

| 축 | 변화 | 대가 |
|---|---|---|
| 초기화 40 % → 10 % (cap 고정) | 초기화만 | **−0.08 pp (무료)** |
| cap `0.09nc` → `0.03nc` (초기화도 축소) | 총예산 | **+2.94 pp** |

**t030 페널티는 사실상 전부 cap 때문이지 초기화 축소 때문이 아니다.** 의도했던
질문("초기화를 더 줄여도 되나?")의 답은 이 데이터로 이미 나와 있다 — **된다,
10 %까지는 공짜다.** 다만 그것은 이번 run이 아니라 20260728 run이 답한 것이다.

---

## 6. 결론

1. **`job_batch_cp`는 `incremental_sw_cp`를 tail 자리에서 대체할 수 없다.**
   같은 cap에서 +4.08 pp 열세, 160개 중 137개 패. 두 개의 독립 관측
   (`isw_t090`, `isw_t090_jp`)이 0.045 pp 안에서 일치하므로 이 격차는 노이즈의
   **90배**다. 예산을 1/3로 줄여도 결론은 같다 (+4.50 pp, 148개 패).

2. **예산 3배를 줘도 진다.** `jbc_t090`(84.4초)이 `isw_t030`(28.2초)보다 +1.13 pp
   나쁘다 (107/160 패). 즉 이것은 튜닝으로 좁힐 격차가 아니라 이웃 구조의 차이다.

3. **원인은 예산 낭비가 아니다.** sweep은 설계대로 돌았다 — pass 7.56회, 배치
   수락률 84 %, 비례 TL도 `κ·n·c` 그대로. 전 job을 정확히 한 번씩 재삽입하는
   커버리지가 sw_cp의 시간창보다 단순히 약한 이웃이다.

4. **n이 커질수록 격차가 벌어진다** (n=50에서 1.1–4.3 pp → n=200에서 5.5–5.8 pp).
   비례 TL 도입으로 이 n 의존성이 약해질 것이라던 사전 예측은 **빗나갔다**.
   `job_batch_cp`의 CP 모델은 매 배치마다 전체 `n·c` op이 시간 고정 없이 자유인
   반면 `sw_cp`는 창 밖을 시간 고정하므로, n이 커질수록 모델당 자유 변수가 선형
   증가하는 쪽이 불리하다는 설명과 맞는다.

5. **초기화 예산은 10 %까지 줄여도 무료**지만, 이는 20260728 데이터의 재해석이지
   이번 run의 결과가 아니다.

## 7. 한계

- **한 슬라이스**다. `(T, R) = (0.6, 0.2)` 160개, 셀당 20개. Q1/Q2의 격차는
  전 셀에서 같은 부호라 방향은 견고하지만, 크기는 셀마다 1.1–5.8 pp로 흔들린다.
- **Q1은 cross-run 대비**다. 다만 노이즈 자가 0.045 pp로 실측됐으므로 실질적
  위험은 없다. 단, 이 일치는 슬라이스 한정이다 — 전체 1440 그리드에서는 같은 두
  시나리오가 1.86 pp 벌어진다 (−0.1267 vs −0.1081). **이 결론을 full grid로
  일반화하려면 재확인이 필요하다.**
- `_jp` arm은 순수 replicate가 아니다 (NEH 순서 `neh_cp` vs `neh_cp_completion_seq`).
  0.045 pp는 "cross-run 노이즈 + NEH 순서 효과"의 합에 대한 상한이다.
- `<run>_rpdf_comparison.csv`의 `timelimit` / `time%` 컬럼은 **t030 행에서 신뢰할 수
  없다** — 모든 arm에 `0.09nc` 기준값이 채워져 t030이 `time%` 0.33으로 찍힌다.
  per-instance `_instance_result.yaml`의 `timelimit`은 정확하다 (0.03nc, 캡 구속
  확인). 분석 스크립트는 이 두 컬럼을 버리고 `elapsedTime`을 쓴다.

## 8. 후속

- **`job_batch_cp`를 tail refiner로 미는 것은 여기서 접는다.** 남은 쓸모가 있다면
  `incremental_sw_cp` **대체**가 아니라 **교대 실행**(둘을 번갈아)인데, 격차 크기를
  보면 우선순위는 낮다.
- 초기화 축을 더 밀어보고 싶다면 (10 % 미만), 20260728 config를 그대로 확장하는
  것이 맞다 — cap은 `0.09nc`로 **고정**해야 같은 축이 된다.
- full grid 재확인은 `isw_t090` / `jbc_t090` 두 arm만 1440으로 돌리면 된다
  (~5.2 h).
