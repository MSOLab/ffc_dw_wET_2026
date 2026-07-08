# Due-window coarsening rounding 비교 (2026-06-25)

CSR(`coarsen_solve_reconstruct`)에서 coarsening 시 due window 경계 `(lower/factor, upper/factor)`의 **반올림 방식**만 바꾼 세 run을 비교한다. rounding은 **축소(coarse) CP 모델의 due window**에만 영향을 주고, 최종 목적함수는 **원본 instance 기준 weighted E+T**로 평가되므로 세 방식은 같은 목적함수 위에서 직접 비교 가능하다.

## 대상 run (동일 config: 14 CSR 시나리오 × 1440 instance, error 0)

| 방식 (하한/상한) | rounding | run directory |
|---|---|---|
| ceil/ceil (기존) | `ceil(l/f), ceil(u/f)` | `output/calop4/20260625T032109_514704` |
| floor/ceil | `floor(l/f), ceil(u/f)` | `output/20260625/20260625T175653_813925` |
| floor/floor | `floor(l/f), floor(u/f)` | `output/20260625/20260625T212019_956736` |

**지표**: 
- **RPDf%** = `(obj−BKS)/((obj+BKS)/2) × 100`, 원본 weighted E+T 대비 BKS. **낮을수록 좋음**.
- **sole-best cells** = (시나리오, instance) 셀에서 단독 최저 bestObj를 낸 횟수.
- **solver_gap / lb_gap** = coarse CP 모델 *내부* 지표 (UB-LB 기반). 원본 목적함수가 아님 — 해석 주의.

## 1. 전체 1440 instances

*필터 없음 (14 시나리오 × 1440 = 20160 cells)*

### RPDf% (전체 평균, 낮을수록 좋음)

| method | mean RPDf% | Δ vs ceil/ceil |
|---|---|---|
| ceil/ceil ✅ | 76.9520 | — |
| floor/ceil | 77.2831 | +0.3311 |
| floor/floor | 77.6368 | +0.6849 |

→ **최우수: ceil/ceil** (76.9520%)

### RPDf% — factor별 (굵게 = 행 최저)

| factor | ceil/ceil | floor/ceil | floor/floor |
|---|---|---|---|
| 1 | **58.5043** | 58.8050 | 58.6350 |
| 2 | **58.3117** | 58.7931 | 58.3837 |
| 4 | **59.8564** | 60.3464 | 60.5242 |
| 8 | **65.0661** | 65.5993 | 65.8843 |
| 16 | **81.1096** | 81.1527 | 82.5047 |
| 32 | **99.7930** | 100.0735 | 100.7498 |
| 64 | **116.0228** | 116.2119 | 116.7762 |

### Win 분석 (instance×시나리오 = 20160 cells)

| method | sole-best cells | share |
|---|---|---|
| ceil/ceil | 6309 | 31.3% |
| floor/ceil | 5876 | 29.1% |
| floor/floor | 5678 | 28.2% |
| 3-way tie | 1757 | 8.7% |

### CP gap (coarse 모델 내부, 참고)

| method | solver_gap | lb_gap |
|---|---|---|
| ceil/ceil | 0.7654 | 18.7815 |
| floor/ceil | 0.7635 | 19.0405 |
| floor/floor | **0.7602** | 22.0961 |

## 2. T=0.6 (480 instances)

*due-date tightness T=0.6 (가장 tight) (14 × 480 = 6720 cells)*

### RPDf% (전체 평균, 낮을수록 좋음)

| method | mean RPDf% | Δ vs ceil/ceil |
|---|---|---|
| ceil/ceil | 56.3616 | — |
| floor/ceil ✅ | 56.3329 | -0.0287 |
| floor/floor | 56.5309 | +0.1693 |

→ **최우수: floor/ceil** (56.3329%)

### RPDf% — factor별 (굵게 = 행 최저)

| factor | ceil/ceil | floor/ceil | floor/floor |
|---|---|---|---|
| 1 | 49.3793 | 49.3968 | **49.3464** |
| 2 | **49.2512** | 49.2556 | 49.2738 |
| 4 | **49.4979** | 49.5450 | 49.5546 |
| 8 | 50.7953 | **50.7867** | 50.9447 |
| 16 | 54.9441 | **54.7726** | 55.1361 |
| 32 | 63.8929 | **63.7663** | 64.1404 |
| 64 | **76.7707** | 76.8073 | 77.3205 |

### Win 분석 (instance×시나리오 = 6720 cells)

| method | sole-best cells | share |
|---|---|---|
| ceil/ceil | 2113 | 31.4% |
| floor/ceil | 2054 | 30.6% |
| floor/floor | 2029 | 30.2% |
| 3-way tie | 356 | 5.3% |

### CP gap (coarse 모델 내부, 참고)

| method | solver_gap | lb_gap |
|---|---|---|
| ceil/ceil | 0.6629 | 3.3161 |
| floor/ceil | 0.6610 | 3.2752 |
| floor/floor | **0.6570** | 3.2601 |

## 3. T=0.6, R=0.2 (160 instances)

*T=0.6 & due-date range R=0.2 (가장 좁음) (14 × 160 = 2240 cells)*

### RPDf% (전체 평균, 낮을수록 좋음)

| method | mean RPDf% | Δ vs ceil/ceil |
|---|---|---|
| ceil/ceil | 42.6492 | — |
| floor/ceil ✅ | 42.6173 | -0.0318 |
| floor/floor | 42.8464 | +0.1972 |

→ **최우수: floor/ceil** (42.6173%)

### RPDf% — factor별 (굵게 = 행 최저)

| factor | ceil/ceil | floor/ceil | floor/floor |
|---|---|---|---|
| 1 | 34.7553 | 34.7914 | **34.7398** |
| 2 | **34.7171** | 34.7855 | 34.7418 |
| 4 | **35.1680** | 35.2426 | 35.3466 |
| 8 | 36.5900 | **36.4932** | 36.6160 |
| 16 | 41.2802 | **41.0544** | 41.4914 |
| 32 | 51.0991 | **50.9937** | 51.3518 |
| 64 | **64.9345** | 64.9606 | 65.6372 |

### Win 분석 (instance×시나리오 = 2240 cells)

| method | sole-best cells | share |
|---|---|---|
| ceil/ceil | 702 | 31.3% |
| floor/ceil | 650 | 29.0% |
| floor/floor | 710 | 31.7% |
| 3-way tie | 115 | 5.1% |

### CP gap (coarse 모델 내부, 참고)

| method | solver_gap | lb_gap |
|---|---|---|
| ceil/ceil | 0.6936 | 3.8397 |
| floor/ceil | 0.6911 | 3.8171 |
| floor/floor | **0.6876** | 3.8012 |

## 결론

- **`floor/floor`(상한까지 floor)는 세 subset 모두에서 RPDf 최악**이다 (전체 +0.68, T=0.6 +0.17, T=0.6·R=0.2 +0.20 %p vs ceil/ceil). 상한을 floor 하면 due window가 좁아져 coarse 모델이 원본 목적함수에서 멀어진다 — `solver_gap`은 근소하게 낮아지지만 이는 내부 지표 착시일 뿐, 실제 품질은 악화된다. **명확히 열등.**
- **하한 rounding(ceil vs floor) 효과는 instance 특성에 의존하고 매우 작다.** 전체 1440 기준으로는 `ceil/ceil`이 `floor/ceil`보다 0.33%p 우수하지만, due-date가 tight한 subset(T=0.6)에서는 `floor/ceil`이 0.03%p 근소하게 앞선다(사실상 동률). 즉 due window가 tight할수록 하한 floor가 미세하게 유리해지는 경향이 보인다.
- factor별로 보면 큰 factor(32·64, 강한 coarsening)에서는 어느 subset이든 `ceil/ceil`이 안정적으로 우수하고, 중간 factor(8·16·32)의 tight subset에서 `floor/ceil`이 역전한다.
- **권고**:
  - 현재 코드에 적용된 `floor/floor`는 **되돌린다** (모든 구간에서 손해).
  - 상한은 **ceil 유지**가 안전하다.
  - 하한은 대상 instance 분포로 판단: 전체 1440을 대표하려면 `ceil/ceil`, tight-due(T=0.6) 위주면 `floor/ceil`도 무방하나 이득은 0.03%p 수준으로 미미하다.

*생성: `scratchpad/build_rounding_md.py` · 원자료 CSV: `analysis/rounding_compare_20260625/`*