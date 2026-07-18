# Context handoff: dispatch-rule (sd/rd × job priority) 통합 비교

> 작성일: 2026-06-25
> 목적: **다음 대화에서 모든 sd/rd × job-priority 룰을 한 번에 비교**하기 위한
>       사실/경로/지금까지 발견 스냅샷. (분석 결론이 아니라 재현 기반.)
> 선행: `plans/experiment/20260625/wxd5_dispatch.md`, `wxd5_dbar_completion_observation.md`,
>       `wxd6_wxd7_dispatch.md`

---

## 0. 통합 데이터: `analysis/20260625/dispatch_rpdf_combined.csv`

세 run 의 `*_rpdf_comparison.csv` 를 합친 long-format 파일.

- **60,480 행** = 42 scenario × 1440 large instance, `(insIndex, scenarioName)` 중복 없음.
- **42 scenario** = `sd_*` / `rd_*` 각 21개 job-priority 룰:
  `wxd1~7`, `wspt_twt`, `cpd_{mean,wmean,median,best}`, `edd`, `eddub_twt`,
  `lsl`, `osl`, `w1`, `weight_due_pos`, `due_weight_pos`, `due2_weight_pos`,
  `due_star_weight_pos`.
- **컬럼**: `insIndex, scenarioName, n, c, totalMcCount, T, R, W, BKS_data,
  bestObj, RPDf_BKS_data, elapsedTime, timelimit, time%, source_run`.
  - 지표: **`RPDf_BKS_data`** (RPDf vs BKS, **낮을수록 우수**).
  - `source_run` 만 통합 시 추가됨.
- **instance 격자**: `T ∈ {0.2, 0.4, 0.6}`, `R ∈ {0.2, 0.6, 1.0}`, `W` 다수,
  `n ∈ {50,150,...}`, `c=5`. (정확한 값 분포는 CSV `groupby` 로 확인.)

### source run 매핑
| source_run | 포함 scenario |
|---|---|
| `20260625T002044_922234` (full_sweep) | wxd2/3/4, wspt_twt, cpd_*, edd, lsl, osl, w1, wxd1, weight/due* 계열 (36개) |
| `20260625T014141_129123` (wxd5_only) | sd/rd_wxd5 |
| `20260625T021733_335312` (wxd67_only) | sd/rd_wxd6, sd/rd_wxd7 |

모두 동일 조건: `benchmarks/PRA2017/large`, timelimit `0.09nc`, 0.09nc.

### 로드 스니펫
```python
import csv
from collections import defaultdict
from pathlib import Path
rows = list(csv.DictReader(Path("analysis/20260625/dispatch_rpdf_combined.csv").open()))
# scenario -> {insIndex: rpdf}, 필요 시 T/R 필터
def mean_rpdf(scen, pred=lambda r: True):
    v = [float(r["RPDf_BKS_data"]) for r in rows
         if r["scenarioName"] == scen and pred(r)]
    return sum(v) / len(v)
# 예: T=0.6, R=0.2 에서 sd_wxd7
mean_rpdf("sd_wxd7", lambda r: r["T"]=="0.6" and r["R"]=="0.2")
```
(paired 비교는 공통 `insIndex` 교집합으로 join — `scratchpad/cmp_wxd67.py` 참조.)

---

## 1. 룰 정의 요약 (비교 대상 핵심만)

`src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`. partition = early/late 그룹
배정, 그 안에서 정렬.

- **wxd2**: partition·정렬 center = `d̄ = 윈도우 중점 평균`. 정렬 곱셈형
  aversion 키 `(w⁺−2w⁻+2ew_max)(d⁻−d̄)` / `(w⁻−2w⁺+2tw_max)(d⁺−d̄)`. tie`>=`→late.
- **wxd3/wxd4**: partition tie`<=`→early. 정렬 = 쌩 weighted penalty
  (`−tp`/`ep`). wxd3 center=d̄, wxd4 center=`max(min r_j+Σ_early p_last/m_last, d̄)`.
- **wxd5**: wxd2 와 동일하나 d̄ = `max(중점평균, min r_j + Σ_all p_last/(m_last·2))`.
  partition·정렬 모두 이 d̄.
- **wxd6/wxd7** (신규): partition = wxd5 d̄ 그대로. **정렬 center 만 그룹 분리**:
  `early_center = min r_j + Σ_all p_last/m_last` (÷2 없음, floor 없음),
  `late_center = min r_j`. wxd6 = 곱셈형 키, wxd7 = 쌩 penalty 키(`−tp`/`ep`).
- **wspt_twt**: 순수 weighted-SPT(twt 가중) 정렬.

---

## 2. 지금까지 발견 (RPDf, 낮을수록 우수)

### 2.1 전역 평균 (n=1440)
| | wxd2 | wxd5 | wxd6 | wxd7 | wxd3 | wxd4 | wspt_twt |
|---|---|---|---|---|---|---|---|
| sd | 0.99127 | **0.99035** | 1.00729 | 1.06607 | 1.09874 | 1.08828 | 1.09822 |
| rd | 1.00407 | **1.00374** | 1.02361 | 1.08499 | 1.10925 | 1.10008 | 1.10526 |

- 전역 1등은 **wxd5≈wxd2**. wxd6/wxd7 은 전역 평균 열위. wspt_twt 는 전역 꼴찌권.
- 단, **전역 평균은 영역 효과를 가린다** (아래 §2.2).

### 2.2 영역별 — tight-due(T=0.6) 에서 역전
**sd, T=0.6, R별 mean RPDf** (각 n=160):
| R | wxd2 | wxd5 | wxd6 | wxd7 | wspt_twt | 영역 승자 |
|---|---|---|---|---|---|---|
| 0.2 | 0.703 | 0.688 | 0.681 | 0.439 | **0.405** | wspt |
| 0.6 | 0.760 | 0.768 | 0.761 | 0.683 | **0.639** | wspt |
| 1.0 | 0.683 | 0.683 | 0.667 | **0.632** | 0.738 | **wxd7** |

- **T=0.6 (all R, n=480)**: sd wxd7=0.585 < wspt=0.594 (평균 1등, paired 228:252 박빙);
  rd wspt=0.620 < wxd7=0.633. wxd7−wxd5: sd Δ=−0.128(412:68), rd Δ=−0.118(414:66).
- **메커니즘 가설**: tight-due 영역은 거의 다 tardy → 사실상 weighted-tardiness 문제.
  R 작으면 due 가 뭉쳐 **wspt_twt(정통 WSPT)** 최강, wxd7 은 그 아류(2등). R 커지면
  earliness 가 다시 살아나 **wspt 붕괴**(0.405→0.738), wxd7 은 late group 의 `min r_j`
  earliness 항 덕에 견고(R=1.0 단독 1등).
- T=0.2/0.4(느슨) 영역에서는 wxd2/wxd5 우위 → 전역 평균 지배. (T=0.4 패턴 미확인.)

---

## 3. 다음 대화에서 다룰 질문 (열어둠)

- **전 T×R 격자 breakdown**: 42 scenario 전부에 대해 `(T,R)` 셀별 최우수 룰
  매트릭스. tight↔느슨, 좁은↔넓은 R 의 경계가 어디인가.
- **영역 라우팅 가치**: "R≤0.6 & T=0.6 → wspt_twt, R=1.0 & T=0.6 → wxd7,
  느슨 T → wxd2/wxd5" 식 oracle 라우팅이 단일 최강룰 대비 RPDf 를 얼마나 낮추나.
- **sd vs rd**: 같은 룰에서 simple/reversed dispatch 가 영역별로 갈리는가.
- **W(가중치) 차원**: T,R 외 W 별로도 우열이 갈리는지.
- wxd6 의 존재 가치: 곱셈형 키가 어떤 영역에서도 단독 1등을 못 내면 drop 후보.

---

## 4. 산출물/경로
- 통합 CSV: `analysis/20260625/dispatch_rpdf_combined.csv`
- 비교 스크립트(참고): `scratchpad/cmp_wxd67.py` (insIndex join, T/R 필터, paired win/lose)
- 룰 구현: `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`, 등록 `parameters/sorter.py`
- run config: `metadata/20260625/{dispatch_sequence_full_sweep,wxd5_only,wxd67_only}_config.yaml`
- 선행 분석 노트: `analysis/20260624_wspt_tight_narrow_region.md`,
  `analysis/20260624_cpd_strength_region.md` (영역별 분석 선례)
