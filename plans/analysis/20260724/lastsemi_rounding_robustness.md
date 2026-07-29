# coarsen_mode(rounding) robustness — "K=1 best"는 rounding에 견고하다 (사후 분석)

**작성일**: 2026-07-25 · **종류**: 병합 분석(merged analysis, 사후) · **판정: ROBUST (H0 확정)**
**사전 계획**: `plans/experiment/20260724/lastsemi_rounding_robustness.md`
**후속 계획(별개)**: `plans/experiment/20260725/coarsening_short_budget_crossover.md`
(sub-5% budget crossover — 본 분석에서 파생된 open question, 이번 배치와 무관)

---

## 1. 질문

지금까지 "coarsening은 손해, K=1이 최선"이라는 판정은 **`coarsen_mode: cumulative`
하나로만** 측정됐다. 본 분석은 나머지 rounding(`ceil`, `floor`, `round`)에서도
같은 판정이 나오는지 검증한다.

- **가설(귀무 H0)**: coarsening의 손해는 rounding 규칙의 세부가 아니라 **스케줄 해상도(정보)
  손실 그 자체**에서 온다 → 네 rounding 모두에서 K=1이 최선, 판정은 rounding-불변.
- **반증 시그널**: 어떤 mode에서 k>1 vs k=1 paired dRPDf가 0 근처/음수, 또는 승패가 뒤집힘.

`FFcDDWParameters.coarsen_processing_times`에서 `factor=1`이면 네 mode가 모두 항등이므로
**K=1은 rounding-무관** → K=1 baseline 1개(`csr_k1_tl{f}_lastsemi`)가 모든 mode의 K=1을 대표.

---

## 2. 소스 런 (full paths)

| 역할 | 경로 | 시나리오 |
|---|---|---|
| 신규 rounding 런 (FULL_RUN, calop4) | `output/20260724_lastsemi_rounding_robust/20260724T224228_409225` | 27 = `csr_k{2,4,8}_tl{05,10,15}_lastsemi_{ceil,floor,round}` |
| 재사용 lastsemi 풀그리드 | `output/20260724_lastsemi_fullgrid/20260724T155337_875856` | 12 = k1 + cumulative k{2,4,8}, f{05,10,15} |
| 병합 POST_PROCESS_ONLY 런 (본 분석 입력) | `output/20260724_merge_rounding/20260725T231504_516446` | 39 (12 + 27), 각 1440 인스턴스 |

- 신규 런의 각 시나리오 inner `solve_flow`·`timelimit`은 `lastsemi_fullgrid.yaml`의 대응
  k 블록에서 **그대로 복제**, `coarsen_mode`와 name만 변경 → cumulative 대비 순수 rounding 효과.
- `reconstruct_mode: active_but_last_semi` 고정(coarsening penalty가 가장 작은 = coarsening에
  가장 유리한 모드; 여기서 K=1이 이기면 semi에서는 자명).
- 병합은 symlink(소스 런 무변경), artifact_layout은 신규 rounding 런 것으로 restamp
  (`csr_analysis` KeyError 회피).

**커버리지**: 13개 (mode, k) × 3 f 셀 전부 정확히 1440. K=1은 cumulative 아래에만 존재(설계대로).

---

## 3. 재현 커맨드

```bash
# 1) 소스 실험 (이미 실행됨; run-setting 커밋 32dfe49, config = metadata/20260724/lastsemi_rounding_robust.yaml)
uv run python main.py            # -> output/20260724_lastsemi_rounding_robust/20260724T224228_409225

# 2) lastsemi 풀그리드(k1 + cumulative k{2,4,8})와 symlink 병합 -> 단일 POST_PROCESS_ONLY 런
uv run python scripts/20260724/build_rounding_merge.py \
    --rounding-run output/20260724_lastsemi_rounding_robust/20260724T224228_409225 \
    --lastsemi-run output/20260724_lastsemi_fullgrid/20260724T155337_875856 \
    --dest         output/20260724_merge_rounding \
    --config-out   metadata/20260724/merge_rounding.yaml
uv run python main.py --config metadata/20260724/merge_rounding.yaml
    # -> output/20260724_merge_rounding/20260725T231504_516446/..._rpdf_comparison.csv (56160 행 = 39×1440)

# 3) 판정 분석
uv run python scripts/20260724/analyze_rounding_robust.py \
    output/20260724_merge_rounding/20260725T231504_516446
    # -> analysis/20260724_rounding_robust/{mean_rpdf_by_cell,penalty_<mode>_cells,headline_dRPDf_by_mode_k}.csv
```

모든 RPDf 수치는 percentage points(`RPDf_BKS_data × 100`), lower is better. dRPDf = RPDf(mode, k>1)
− RPDf(k=1), 같은 (f, insIndex) per-instance paired; **> 0이면 coarsening 손해**.

---

## 4. 결과

### 4.1 HEADLINE — mean dRPDf (pp) by mode × k

```
k    cumulative     ceil    floor    round
2      +27.331   +27.140  +31.367  +26.962
4      +31.856   +34.323  +49.706  +31.346
8      +34.780   +38.887  +62.976  +30.167
```

**cumulative 열이 사전 계획의 기지값 +27.33 / +31.86 / +34.78 (k=2/4/8)을 정확히 재현** →
merge·pairing 배관 정상(built-in sanity gate 통과).

### 4.2 VERDICT — mode별 종합 (전 k, 전 f; 12,960 pair)

```
cumulative : mean dRPDf +31.322 pp | mean dObj +45,181 (signal) | win/tie/loss  2191/708/10061 | coarsening hurts
ceil       : mean dRPDf +33.450 pp | mean dObj +45,916 (signal) | win/tie/loss  1724/814/10422 | coarsening hurts
floor      : mean dRPDf +48.016 pp | mean dObj +47,508 (signal) | win/tie/loss  1587/275/11098 | coarsening hurts
round      : mean dRPDf +29.492 pp | mean dObj +45,019 (signal) | win/tie/loss  2214/771/9975  | coarsening hurts
=> ROBUST: 27개 (mode, k) 셀 전부에서 dRPDf > 0 이고 loss > win. 'K=1 best'는 rounding-불변.
```

- **모든 mean dObj ≈ +45,000** — CSR CP 노이즈 플로어(~±350, 1440 grid)의 약 130배. 확실한 실신호.
- coarsened가 지는 비율: cumulative 77.6 %, floor 85.6 %.

### 4.3 mean RPDf (pp) 절대값 by mode × k × f

```
mode      cumulative   ceil   floor   round
k  f
1  5         26.641    —       —       —
   10         5.926    —       —       —
   15         0.055    —       —       —
2  5         56.508  56.249  59.281  56.603
   10        33.293  33.408  38.017  32.994
   15        24.816  24.387  29.426  23.911
4  5         61.942  63.471  74.888  61.365
   10        38.231  40.980  57.309  37.731
   15        28.016  31.138  49.542  27.563
8  5         64.994  67.392  85.115  61.193
   10        40.627  45.609  70.737  35.331
   15        31.340  36.282  65.698  26.601
```
(K=1은 rounding-무관이므로 cumulative 열에만 기재; 이 값이 네 mode 공통 baseline.)

### 4.4 Budget parity — mean elapsedTime (s)

mode+f 안에서 elapsedTime이 k에 무관하게 동일 → equal-budget 비교 정당(모든 arm이 같은 벽시계
예산에서 경쟁).

```
mode        f=5              f=10             f=15
            k1   k2  k4  k8   k1   k2  k4  k8   k1    k2   k4   k8
cumulative 4.26 4.26 4.23 4.21 8.15 8.17 8.11 8.09 12.02 12.06 11.98 11.95
ceil        —   4.26 4.24 4.24  —   8.18 8.16 8.19  —    12.07 12.07 12.15
floor       —   4.26 4.21 4.17  —   8.15 8.05 7.95  —    12.02 11.87 11.73
round       —   4.26 4.23 4.21  —   8.17 8.11 8.10  —    12.06 11.98 11.97
```

---

## 5. 결론과 뉘앙스

1. **판정: ROBUST.** 네 rounding 모두에서 coarsening은 K=1에 손해(양의 dRPDf, loss > win)를 보이며,
   27개 (mode, k) 셀 어디에서도 뒤집히지 않는다. **"K=1이 최선"은 rounding-불변**이고,
   손해는 cumulative 특유의 왜곡이 아니라 **해상도(정보) 손실 그 자체**에서 온다(H0 확정).

2. **mode별 severity: round ≈ cumulative < ceil < floor.**
   - **floor가 압도적 최악** — k=8에서 +62.98 pp, 3,915/4,320 패배. 처리시간을 아래로 절삭 →
     정보 손실이 가장 큼. floor는 유일하게 penalty가 k에 대해 급격히 단조 증가한다.
   - **round는 가장 덜 해로움** — k=8에서 오히려 cumulative보다 낮음(+30.17 vs +34.78).
   - 가장 덜 해로운 round조차 +27~30 pp penalty → 결론을 강화한다.

3. **Budget gradient (측정 구간 [5,15]%): budget이 줄수록 손해가 커진다.**
   cumulative dRPDf를 (k, f)로 보면 f에 대해 단조 감소(= f가 줄수록 penalty 증가):
   ```
   k   f=5     f=10    f=15
   2  +29.87  +27.37  +24.76
   4  +35.30  +32.31  +27.96
   8  +38.35  +34.70  +31.29
   ```
   k2↔k8 스프레드도 f가 줄수록 벌어진다: **6.53 (f=15) → 7.33 (f=10) → 8.49 (f=5) pp.**
   절대 RPDf(§4.3)를 보면 budget 삭감 시 K=1도 나빠지지만(f15→f5: 0.06→26.6) **coarsened arm이
   더 빨리 나빠진다**(k=8: 31.3→65.0). 즉 [5,15]% 안에서는 "시간을 줄이면 coarsening이 유리"의
   **반대** 방향이다. (floor는 예외 — 너무 파괴적이라 k=8에서는 budget이 많을수록 격차가 벌어짐:
   K=1이 0으로 수렴하는데 floor는 65에서 안 내려옴.)

---

## 6. Open Questions

- **sub-5% budget crossover (미측정)**: §5-3의 gradient는 [5,15]% 안에서 coarsening이 점점
  불리해짐을 보이므로 crossover가 있다면 **f < 5%**, 그것도 K=1이 "쓸만한 incumbent를 아예 못
  만드는" regime이어야 한다. 그런데 **f=5%에서도 K=1은 RPDf 26.6**(coarsened 56~65)로 여전히
  실질 해를 뽑고 있어 그 벽 근처가 아니다. gradient는 crossover가 5%보다 한참 아래(양쪽 다 거의
  쓸모없는 regime)임을 시사한다. → **본 분석 결론(ROBUST)을 바꾸지 않는 별개 실험**으로 분리:
  `plans/experiment/20260725/coarsening_short_budget_crossover.md`.
- **semi 모드 대칭 확인**: 본 분석이 견고성을 확정했으므로 YAGNI(semi는 penalty가 더 커 자명).
- **f=20 / k=16 축 확장**: 사전 계획에서 이미 취소(cumulative·k=1 backfill 부재로 순수 재사용 불가).

---

## 7. 아티팩트

- per-cell / roll-up CSV: `analysis/20260724_rounding_robust/`
  (`mean_rpdf_by_cell.csv`, `penalty_{cumulative,ceil,floor,round}_cells.csv`,
  `headline_dRPDf_by_mode_k.csv`) — gitignored; 위 표가 self-contained 원본.
- 병합 config: `metadata/20260724/merge_rounding.yaml` (POST_PROCESS_ONLY).
- 분석 스크립트: `scripts/20260724/analyze_rounding_robust.py`,
  병합 스크립트: `scripts/20260724/build_rounding_merge.py`.
