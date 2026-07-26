# mcf_lb 라운드 1 원자화 후 재측정: objective 결론 불변, feasibility crossover 소멸 (사후 분석)

**작성일**: 2026-07-26 · **종류**: 사후 분석 (버그 수정 재실행 판정)
**판정: 게이트 3개 전부 통과. 원 결론의 objective 축은 불변(0/200), feasibility 축은
철회된다 — f=1%의 "coarsening이 이긴다"는 관측은 구현 결함이었고, 수정 후 그
20개에서 K=1이 오히려 압승한다(k≥4에 20/20).**
**사전 계획**: `plans/experiment/20260726/mcf_lb_atomic_gate_removal.md`
**대체 대상**: `plans/analysis/20260726/coarsening_short_budget_crossover.md`
§4.4 / §7-1b — 아래 §7 참조

---

## 1. 질문

선행 분석(`coarsening_short_budget_crossover.md`)은 두 축으로 결론을 냈다.

1. **objective crossover 부재** — 200개 (arm, f, k, mode) 조합 전부에서
   dRPDf > 0 AND win < loss.
2. **f=1% feasibility crossover** — `m1_k1_f01`이 160개 중 20개에서 incumbent를
   하나도 등록하지 못하고(`obj_value: null`), 같은 20개에서 K≥4는 20/20 해를 낸다.

그런데 그 문서 §4.4 자체가 축 2를 **알고리즘 성질이 아니라
`calc_mcf_lb_and_derive_full_sch`의 의도치 않은 stop gate**로 진단했다. MCF LP는
mid-solve 중단이 불가능한데(`lb_last_stage_pmtn.py:81-84`) 게이트 2·3이 마치
가능한 것처럼 스테이지 사이에서 컷을 시도하고, 그 결과가 "해 없음"이었다.

라운드 1을 원자화(게이트 2·3 제거, `apply_lb_by_mcf(stop_predicate=None)`)한 뒤,
**동일 config로 재실행**해 두 질문에 답한다.

- **Q1 (버그)**: 결측이 사라졌는가, 그리고 그 변경이 mcf_lb 밖으로 새지 않았는가?
- **Q2 (결론)**: 결측이 없는 조건에서 두 축을 다시 읽으면 무엇이 남는가?

> ⚠️ 성공 기준은 "숫자가 좋아지는 것"이 **아니다.** 계획서 §2.3이 미리 밝힌 대로
> 이 수정은 K=1에 유리하기만 한 변경이 아니다 — mcf_lb가 항상 완주하므로 K=1이
> child budget을 더 쓰고 downstream에 남는 예산이 줄어든다. 그래서 재실행이
> 필요했다.

---

## 2. 소스 런 (full paths)

| 역할 | 경로 | 코드 |
|---|---|---|
| **before** (FULL_RUN, calop4) | `output/20260725_crossover_ladder/20260726T002619_971440` | 게이트 있음 (run setting `a116e4c`) |
| **after** (FULL_RUN, calop4) | `output/20260725_crossover_ladder/20260726T173841_347539` | 라운드 1 원자화 (run setting `adb8e60`) |

> ⚠️ **두 런이 같은 base dir을 공유한다.** config를 무수정 재사용했으므로
> `output_dir`도 동일하다 — **timestamp로만** 구분된다. 이 문서에서 런을 지칭할 때는
> 항상 timestamp까지 적는다.

**공통**: `metadata/20260725/coarsening_crossover.yaml` (무수정 재사용),
210 scenarios × 160 instances = 33,600 row, (T,R)=(0.6,0.2) 슬라이스,
K 21종 = k1 + {2,4,8,16,32} × {cumulative, ceil, floor, round},
arm 4종 = m1(84) / a(21) / b(21) / c(84).

**소요**: before 약 1시간 42분 (00:26:19 → 02:08:10),
after 약 1시간 42분 (17:38:41 → 19:20:57). 두 런의 벽시계 소요가 사실상 같다 —
게이트 제거로 늘어난 비용은 §6.4가 보이듯 2개 셀에 국한된다.

**config가 동일하므로 두 런의 차이는 코드뿐**이다. 이것이 아래 §3 G2의 negative
control을 성립시키는 전제다.

---

## 3. 재현 커맨드

```bash
# 1. 재실행 (before와 동일 config)
uv run python main.py --config metadata/20260725/coarsening_crossover.yaml

# 2. §4 / §5 / §6 의 모든 판정 + 결론 재독 (before/after 기본값이 하드코딩됨)
uv run python scripts/20260726/verdict_mcf_lb_atomic.py

# 3. §6 의 (n=200,c=5) depth 표 — 결함이 살던 셀
uv run python scripts/20260725/analyze_csr_winner_source.py \
    output/20260725_crossover_ladder/20260726T173841_347539 --n 200 --c 5
```

`verdict_mcf_lb_atomic.py`는 `load_run` / `paired_drpdf`를
`analyze_crossover_ladder.py`에서 import하므로, 판정이 선행 문서가 인용한 표와
드리프트할 수 없다. `*_rpdf_comparison.csv`의 존재를 완주 신호로 쓰므로 미완주
런에 대고 돌리면 진행률만 찍고 exit 1 한다.

---

## 4. 1차 게이트: 버그가 고쳐졌는가 (계획서 §4.1)

**세 게이트 전부 PASS, exit code 0.**

| 게이트 | 내용 | 결과 |
|---|---|---|
| **G1** | 런 전체에 `obj_value: null`이 0건 | `m1_k1_f01` **140/160 → 160/160**, null **0 / 33,600** ✅ |
| **G2** | arm `a`·`b`가 before와 bit-identical | arm a **3,360행 전부 일치**, arm b **3,360행 전부 일치** ✅ |
| **G3** | arm `c`가 CP 노이즈 바닥 이내 | 13,440 paired, mean `bestObj` 델타 **+27.8** (바닥 ±350), mean RPDf 델타 **+0.004 pp** ✅ |

**G2가 완전 일치라는 사실이 이 재실행의 해석 가능성을 담보한다.** arm `a`는
mcf_lb를 아예 호출하지 않고(dispatch-only, `solve: false`), arm `b`는 CSR
timelimit이 글로벌 캡 `0.09nc`라 mcf_lb 단계에서 예산이 binding된 적이 없다.
둘 중 하나라도 움직였다면 변경이 의도한 범위를 벗어났다는 뜻이고, 계획서 §4.1은
그 경우 **숫자를 해석하지 말고 코드부터 다시 읽으라**고 규정했다(판정 스크립트도
G2 실패 시 결론 섹션 출력을 억제한다). 실제로는 6,720행이 전부 일치했다.

G3의 +27.8은 바닥의 8% 수준이다. 다만 이 바닥은 1440-instance 그리드에서 정립된
값이고 여기는 160-instance 슬라이스라 셀당 더 시끄러우므로, **"노이즈와 구분되지
않는다"로 읽어야 하며 "변화가 없음이 증명되었다"로 읽어서는 안 된다.**

---

## 5. 결론 재독 (계획서 §4.2)

### 5.1 축 1 — objective crossover: 불변

| | 반례 셀 수 | min mean dRPDf | coarse 최다 승 |
|---|---|---|---|
| before | **0 / 200** | +1.60 pp | 54/160 (a, k2, cumulative) |
| after | **0 / 200** | +1.60 pp (a, k2, cumulative) | 59/160 (a, k2, ceil) |

반례 = `dRPDf > 0 AND win < loss`를 만족하지 **않는** 셀. 재실행 후에도 200개 조합
전부에서 dRPDf > 0이고 win < loss다. **음수 평균 dRPDf 셀은 하나도 없다**(최솟값
+1.60 pp, arm a k=2 cumulative). coarsening이 가장 선전한 셀조차 160개 중 59승에
그친다.

**축 1은 버그와 무관하게 유효함이 확정되었다.** 이제 200개 셀 전부가
`n_paired = 160`이므로, 결측으로 인한 편향 없이 성립하는 진술이다.

**mode severity** (k=8, after): m1 f=4% `ceil +26.32 < round +26.46 ≈ cumulative
+26.47 < floor +27.93`, arm a `ceil +2.12 < cumulative +2.15 < round +2.28 =
floor +2.28`, arm b `ceil +34.97 < round +35.19 < cumulative +35.37 < floor
+36.89`. 순서와 규모 모두 before와 같고, 여전히 격차는 arm별 2 pp 이내다 —
선행 문서 §4.1의 "순서를 결론으로 삼지 않는다"는 유보를 그대로 유지한다.

### 5.2 축 2 — f=1% feasibility crossover: 소멸

| | `coarse_only_feasible` 합계 (m1 f=1%, 20 셀) | 셀당 최대 |
|---|---|---|
| before | **378** | **20** |
| after | **0** | **0** |

`coarse_only_feasible` = coarse arm은 해를 냈고 K=1은 못 낸 인스턴스 수. 완전히
0이 되었다. **선행 문서 §4.4의 진단이 옳았다** — 축 2는 알고리즘 비교 결과가
아니라 구현 결함의 관측 기록이었다.

### 5.3 m1 dRPDf 사다리 (k=2, pp): before → after

| mode | f=1% | f=2% | f=3% | f=4% |
|---|---|---|---|---|
| ceil | +9.65 → **+9.64** | +12.11 → **+12.24** | +16.55 → **+16.52** | +17.67 → **+17.42** |
| cumulative | +10.40 → **+10.61** | +12.34 → **+12.63** | +17.62 → **+16.99** | +18.48 → **+18.26** |
| round | +10.23 → **+10.53** | +12.90 → **+12.50** | +17.06 → **+16.70** | +18.10 → **+17.41** |
| floor | +10.99 → **+11.72** | +12.81 → **+13.13** | +17.03 → **+16.88** | +17.96 → **+17.81** |

16개 셀 전부가 ±0.75 pp 이내로 움직였다. **선행 문서가 경고한 편향의 방향은
실현되지 않았다.** 그 문서 §4.4는 "f=1% 행은 K=1이 가장 심하게 잘린 20개를 뺀
값이므로 K=1에게 유리하게 편향돼 있다"고 정정을 달았는데, 이는 20개를 되돌리면
dRPDf가 **작아질**(coarsening이 덜 나쁠) 가능성을 열어둔 것이었다. 실제로는 f=1%
4개 mode 중 3개가 오히려 **커졌다**(cumulative +10.40 → +10.61, round +10.23 →
+10.53, floor +10.99 → +11.72). 즉 편향은 존재했으나 **부호가 반대**였고 크기도
1 pp 미만이었다. 이유는 §6에 있다.

20260724 f={5,10,15}% 사다리와 이어 붙일 때 쓰는 `cumulative` 행은 이제
`+10.61 / +12.63 / +16.99 / +18.26 | +29.87 / +27.37 / +24.76`이다. 선행 문서 §4.2의
**역U자(∩) 형태는 그대로 유지된다** — f=5% 부근이 정점이고 양쪽으로 감소한다.

---

## 6. 결측 20개에서 실제로 무슨 일이 일어났는가 (핵심 반전)

축 2가 "사라졌다"는 것만으로는 부족하다. **그 20개에서 이제 누가 이기는가**가
실질적 발견이다. 20개는 (n=150,c=5) 10개 + (n=200,c=5) 10개(각 셀 20개 중 절반)로,
CSR budget `0.0009·f·n·c`가 전 그리드에서 가장 작은 곳이다.

### 6.1 그 20개만의 RPDf (after)

| scenario | RPDf% (20개 평균) | coarse W/L vs K=1 | dRPDf |
|---|---|---|---|
| `m1_k1_f01` | **66.30%** | — | — |
| `m1_k2_ceil_f01` | 76.12% | 2 / 18 | +9.82 pp |
| `m1_k4_ceil_f01` | 81.56% | **0 / 20** | +15.26 pp |
| `m1_k8_ceil_f01` | 83.58% | **0 / 20** | +17.28 pp |
| `m1_k16_ceil_f01` | 101.74% | — | — |
| `m1_k32_ceil_f01` | 124.84% | **0 / 20** | +58.54 pp |

`ceil`은 coarsening에 가장 유리한 mode다. 그런데도 **K≥4는 20개 전부에서 K=1에게
진다.**

**before에서 "K≥4가 20/20 승리"였던 것이 after에서 "K≥4가 20/20 패배"로 완전히
뒤집혔다.** before의 승리는 coarsening의 우월성이 아니라, coarsened 인스턴스의 LP가
더 빨라 **우연히 게이트를 통과했기 때문**이었음이 실증되었다. 게이트가 사라져 K=1도
해를 내자, 이 20개는 오히려 **coarsening penalty가 가장 큰** 셀로 드러난다
(k=8에서 +17.28 pp, 전체 160개 평균 +18.45 pp와 동급).

### 6.2 K=1의 RPDf는 어디서 움직였는가

| 집합 | before | after |
|---|---|---|
| 동일 140개 (before 생존분) | 55.37% | **55.35%** ← like-for-like |
| 그 20개 | (해 없음) | **66.30%** |
| 전체 160개 | (측정 불가) | **56.72%** |

`m1_k1_f01`의 RPDf가 55.37% → 56.72%로 오른 것은 **성능 저하가 아니다.** 동일
140개로 보면 55.37 → 55.35로 사실상 불변이고, 상승분 전체가 새로 편입된 20개의
66.30%에서 온다. 이 20개는 원래 가장 어려운(예산이 가장 작은) 셀이므로 평균을
끌어올리는 것이 당연하다.

**이것이 §5.3에서 f=1% dRPDf가 오히려 커진 이유다.** 20개가 K=1 쪽 평균에 66.30%로
들어오는 동시에 coarse 쪽 평균에는 76~125%로 들어온다 — 양쪽이 함께 나빠지지만
coarse가 더 크게 나빠지므로 차이(dRPDf)는 벌어진다.

### 6.3 depth: 결함이 살던 셀에서만 움직였다

`winner_depth`는 inner `solve_flow`가 도달한 step index다.

**(n=200, c=5), 20 인스턴스** — 결함이 살던 셀:

| scenario | before | after |
|---|---|---|
| `m1_k1_f01` | step1:10 (**10개 winner 없음**) | **step1:20** |
| `m1_k1_f02` | step1:20 | step1:20 |
| `m1_k1_f03` | step1:13 step2:7 | step1:14 step2:6 |
| `m1_k1_f04` | step1:12 step2:8 | step1:12 step2:8 |

**(n=200, c=10), 20 인스턴스** — 예산이 게이트에 걸린 적 없는 셀:

| scenario | before | after |
|---|---|---|
| `m1_k1_f01` | step1:20 | step1:20 |
| `m1_k1_f02` | step1:17 step2:3 | step1:16 step2:4 |
| `m1_k1_f03` | step1:5 step2:15 | step1:5 step2:15 |
| `m1_k1_f04` | step1:5 step2:15 | step1:5 step2:15 |
| `m1_k8_cumulative_f04` | step1:1 step2:7 step3:4 step4:8 | step1:2 step2:9 step3:3 step4:6 |
| `m1_k32_cumulative_f04` | step3:6 step4:14 | step3:10 step4:10 |

계획서 §4.3은 "mcf_lb가 항상 완주하면 winner_source가 step-1에 더 몰릴 수 있다"고
예상했다. **(200,10)에서는 그 이동이 관측되지 않는다** — K=1의 f=1/3/4%는 완전히
동일하고 f=2%만 1개 이동했다. 예산이 게이트에 걸린 적이 없던 셀이므로 당연한
결과다. 이동은 **(200,5)의 f=1%에만, 그것도 "winner 없음 → step1"의 형태로**
나타났다. K≥8의 step 분포 변동(k32 f=4%의 step3 6→10)은 CP 비결정성 범위로 읽는다.

### 6.4 elapsed: 추가 비용도 그 셀에만 국한된다

`m1_k1_f01` instance elapsed 평균, before → after (budget = `0.0009·1·n·c`):

| n, c | budget | before | after | Δ |
|---|---|---|---|---|
| 50, 5 | 0.225s | 0.25s | 0.25s | +0.00 |
| 50, 10 | 0.450s | 0.48s | 0.49s | +0.01 |
| 100, 5 | 0.450s | 0.67s | 0.69s | +0.02 |
| 100, 10 | 0.900s | 1.05s | 1.08s | +0.03 |
| **150, 5** | 0.675s | 0.97s | **1.07s** | **+0.10** |
| 150, 10 | 1.350s | 1.81s | 1.83s | +0.02 |
| **200, 5** | 0.900s | 1.31s | **1.50s** | **+0.19** |
| 200, 10 | 1.800s | 2.36s | 2.39s | +0.03 |

**게이트가 발화했던 두 셀에서만 유의미하게 늘었다.** 나머지 6개 셀의 +0.00~+0.03s는
노이즈 수준이다 — 완전 결정론인 arm `a`(K=1, CP 없음)의 elapsed도 2.623 → 2.644s로
+0.021s 움직였으므로, 이 정도가 이 환경의 elapsed 측정 노이즈 스케일이다.

**그리고 여전히 8개 셀 전부가 예산을 초과한다** (예: (200,5)는 1.50s vs 예산
0.900s = **1.67×**). 계획서 §1.2의 진단 — 예산은 스테이지 내부에서 강제되지
않으며 게이트는 "예산 준수"를 산출하지 못한 채 결과만 0으로 만들었다 — 이 재실행
후에도 유효하다. **원자화는 "해가 반드시 나온다"를 보장했을 뿐, "예산을 지킨다"를
보장하지 않는다.** 예산 설계 자체는 여전히 열린 문제다(§8).

---

## 7. 선행 문서와의 관계 (무엇이 대체되는가)

`plans/analysis/20260726/coarsening_short_budget_crossover.md`는 **버그가 있는
상태의 관측 기록으로서 남긴다**(계획서 §5의 처리 방침). 다만 아래 부분은 본
문서로 대체된다.

| 선행 문서 위치 | 상태 |
|---|---|
| §4.1 m1 행 (f=1% `n_paired`=140) | **대체** — 본 문서 §5.1/§5.3의 160-instance 값 |
| §4.2 사다리의 f=1% 열 | **대체** — cumulative +10.40 → **+10.61** |
| §4.2 depth 표 (f=1%, 20/20 step-1) | **유지** — (200,10)은 불변 (§6.3) |
| §4.4 전체 | **결함 기록으로 확정** — 진단은 옳았고, 예상대로 소멸 (§5.2) |
| §4.4 말미 "정정" (K=1에 유리한 편향) | **정정** — 편향은 부호가 반대였다 (§5.3) |
| §7-1b "f=1%의 예외는 버그다" | **확정** — 나아가 그 20개에서 K=1이 압승 (§6.1) |
| §7-2 역U자 형태 | **유지** — 정점과 형태 불변 (§5.3) |
| §7-5 "f=1% feasibility 예외" 유보 | **삭제 가능** — 유보 조건이 해소됨 |
| §4.3 arm a/b/c, §5, §6 전체 | **유지** — G2/G3로 불변 확인 (§4) |

선행 문서 §7-5는 "K=1 최선은 **단 목적함수 축에 한하며**, f=1%의 feasibility
예외는 위 1b"라는 유보를 달고 있었다. **그 유보는 이제 불필요하다** — feasibility
축에서도 K=1이 이긴다(160/160 해를 내고, 문제의 20개에서 k≥4에 20/20 승).

---

## 8. 종합 판정

1. **버그는 고쳐졌다.** 런 전체 `obj_value: null` 0/33,600, `m1_k1_f01` 160/160.
   변경은 mcf_lb 내부에 머물렀다 — negative control arm a·b가 6,720행 전부 일치.

2. **objective crossover는 여전히 존재하지 않는다.** 200개 (arm, f, k, mode) 조합
   전부 dRPDf > 0 AND win < loss, 음수 평균 셀 0개, 최솟값 +1.60 pp. 이제 모든
   셀이 `n_paired = 160`이므로 **결측 편향 없는 진술**이다. f ∈ [1,15]%에서
   K=1 최선이라는 verdict는 유지된다.

3. **feasibility crossover는 철회된다.** `coarse_only_feasible` 378 → 0. 더욱이
   문제의 20개에서 K=1이 k≥4에 **20/20으로 이긴다**(before는 0/20 패). 선행 문서가
   "coarsening의 장점으로 해석해서는 안 된다"고 경고한 것보다 강한 결과다 — 그
   셀은 오히려 coarsening penalty가 가장 큰 곳이었다.

4. **수정의 부작용은 예상보다 작고 국소적이다.** 계획서 §2.3은 K=1이 child budget을
   더 써서 짧은 f에서 불리해질 수 있다고 예상했으나, 동일 140개 기준 RPDf는
   55.37 → 55.35로 불변이고 elapsed 증가는 게이트가 발화했던 2개 셀
   (+0.10s / +0.19s)에 국한된다. (200,10)의 depth는 이동하지 않았다.

5. **예산 미준수는 남은 문제다.** 8개 (n,c) 셀 전부가 여전히 CSR budget을
   초과하며 (200,5)는 1.67×다. 원자화는 "시작하면 해를 낸다"를 보장한 것이고,
   "예산 안에 끝낸다"는 애초에 MCF-LB에 걸 수 없는 요구다 — 예산 설계를 다시 볼
   근거로 기록한다.

---

## 9. 후속 과제

- **같은 패턴의 다른 생산자 스텝** (계획서 §5): `_make_stop_report`를 register 없이
  반환하는 지점이 `controller.py`에 6곳(603, 1402, 1956, 2180, 2343, 2766행)
  있다. 1402행이 본 수정 대상이었다. 나머지 중 **incumbent를 생산하는 스텝이
  있다면 동일 결함**이다 — 소비자 스텝(incumbent를 받아 개선)은 무해하다.
  본 재실행 범위에 넣지 않았으므로 별건으로 훑는다.
- **과거 짧은-budget 실험의 파급 범위**: CSR child budget이 mcf_lb 완주 시간에
  근접하는 실험(20260714 budget sweep의 f=5%, 20260724 f=5% 등)이 같은 컷에
  노출됐을 수 있다. 본 재실행에서 **결측은 f=1%·c=5·큰 n에만** 나타났고 f≥2%는
  before에서도 160/160이었으므로, **f≥5% 실험은 재측정 불필요로 판단한다.**
  다만 이는 (T,R)=(0.6,0.2) 슬라이스의 관측이므로, 다른 셀에서 더 작은 예산이
  나오는 조합이 있으면 재검토한다. **선제적으로 다 돌리지 않는다.**
- **`tests/orchestration/test_csr_solve_flow.py`의 통합 테스트 미작성** (계획서
  §2.2 잔여): "child flow가 `candidates=0`으로 끝나지 않는다"를 pin하는 테스트가
  아직 없다. 현재는 `test_stop_after_r1_entry_still_produces_full_schedule`이
  파이프라인 수준에서만 invariant를 잡는다.
- **예산 설계 재검토**: 모든 셀이 CSR budget을 1.1~1.7× 초과한다. mcf_lb가
  중단 불가라는 사실을 예산 배분에 반영할지(예: mcf_lb 완주 시간을 예산에서
  선차감), 혹은 초과를 허용된 동작으로 문서화할지 결정이 필요하다.
- 선행 문서의 나머지 후속 과제(arm a의 dispatch-only 경쟁력, size별 threshold,
  다른 (T,R) cell 확장)는 그대로 유효하다.

---

## 10. 아티팩트

모두 gitignored이며, 위 표들은 이 문서만으로 self-contained하다.

- 판정 + 결론 재독: `scripts/20260726/verdict_mcf_lb_atomic.py` 콘솔 출력
- after 런 dRPDf / W/L / mode severity / elapsed:
  `analysis/20260726T173841_347539_crossover_ladder/`
  (`drpdf_by_mode_k.csv` 200행 — 전 행 `n_paired=160`,
  `coarse_only_feasible=0`; `arm_summary.csv`, `m1_ladder.csv`,
  `elapsed_by_scenario.csv`)
- after 런 winner-source depth (n=200,c=10):
  `analysis/20260726T173841_347539_winner_source/`
- before 런의 대응 아티팩트:
  `analysis/20260726T002619_971440_crossover_ladder/`,
  `analysis/20260726T002619_971440_winner_source/`
- §6.3의 (n=200,c=5) depth 표는 `--n 200 --c 5`로 재생성 (§3 커맨드 3),
  임시 outdir을 쓰고 지웠으므로 상주 아티팩트 없음
- 분석 스크립트: `scripts/20260726/verdict_mcf_lb_atomic.py`,
  `scripts/20260726/analyze_crossover_ladder.py`,
  `scripts/20260725/analyze_csr_winner_source.py`
- config: `metadata/20260725/coarsening_crossover.yaml` (before와 동일)
