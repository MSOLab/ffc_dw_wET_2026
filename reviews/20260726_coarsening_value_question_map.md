# "coarsening은 가치가 있는가" — 질문 지도 (question map)

**작성일**: 2026-07-26 · **종류**: 질문 구조화 문서 (결과 문서 아님)
**대상 작업 범위**: `98d37c2`(2026-07-21, cumulative mode 추가) → `HEAD`(`31b2ee4`) — 42 커밋
**목적**: 마스터 질문을 답할 수 있는 하위질문으로 쪼개고, 각 하위질문이 **이미 답해졌는지 /
기존 데이터 재분석으로 답할 수 있는지 / 새 런이 필요한지 / 이 데이터로는 원리적으로 답 불가인지**를
분류한다. 각 답에는 tracked SSOT 문서와 런 디렉터리를 붙인다.

---

## 0. 마스터 질문과 용어 고정

> **Q0.** coarsening은 가치가 있는가? 구체적으로 —
> **coarsen → (짧은 예산으로) solve → reconstruct** 가 **initialization**으로서
> **기존 방식보다** 나은가?

이 문장에는 정의가 필요한 단어가 넷 있다. 하위질문을 쪼개기 전에 고정한다.

| 단어 | 이 저장소에서의 조작적 정의 |
|---|---|
| **coarsening** | `FFcDDWParameters.coarsen_processing_times(factor=K, mode)` — 처리시간을 K로 나눠 정수로 스냅. **K=1은 항등**이므로 `csr_k1`은 "coarsening 없음"의 정확한 대조군이다(코드로 검증됨, `plans/analysis/20260719/csr_init_k_budget_consolidation.md`). |
| **가치 있다** | 같은 벽시계 예산에서 **paired dRPDf = RPDf(coarse) − RPDf(K=1) < 0** 이고 win ≥ loss. RPDf는 대칭형 `2(obj−ref)/(obj+ref)`, `BKS_data` 기준, pp 단위. |
| **initialization** | 지금까지의 K 실험은 전부 **outer `subroutine_flow: [coarsen_solve_reconstruct]` 단일 스텝**이다(`metadata/20260725/coarsening_crossover.yaml` 210 시나리오 전부). outer cap `0.09nc` 중 실제 소모는 4 % 수준 — 즉 **tail(outer isw / base CP-SAT)이 없다.** 그러므로 이 숫자들은 전부 "고정 예산에서의 **초기해 품질**"이지 최종해 품질이 아니다. |
| **기존 방식** | 두 가지가 혼용돼 왔다. ① **같은 inner flow의 K=1**(= 해상도만 다름, 지금까지의 주 대조군) ② **C5 initializer**(mcf→flip→neh_cp, CSR 껍데기 없음). ①과 ②는 다른 질문이며 아래 A축에서 분리한다. |

**이 구분이 지도의 뼈대다.** 42개 커밋의 작업은 거의 전부 "①에 대한 A2(초기해 품질) 축"에 있고,
②·A1(최종해 품질) 축은 20260721 이후 **한 번도 측정되지 않았다.**

---

## 1. 질문을 쪼개는 4개 축

```
Q0 "coarsening은 가치가 있는가"
 ├─ A축  무엇을 '가치'로 재는가          (측정 대상의 분해)
 ├─ B축  coarsening 편에 최선을 다 줬는가 (steelman — 설정 자유도)
 ├─ C축  왜 이기거나 지는가              (메커니즘)
 └─ D축  어디까지 일반화되는가           (범위와 반증 조건)
     └─ E축  그 답을 믿을 수 있는가       (방법론 신뢰성)
```

축을 이 순서로 두는 이유: **B가 다 채워지지 않으면 A의 부정 답은 "설정을 잘못 골랐다"로 반박된다.**
C는 A의 답에 인과를 붙여 D(일반화)로 가는 다리다. E는 A~D 전체에 곱해지는 계수다.

범례 — **✅ 답함** / **🟡 부분적으로 답함** / **❌ 미답**
그리고 답하는 비용 — **[재분석]** 기존 런 데이터로 가능 / **[새 런]** 새 실험 필요 / **[불가]** 이 데이터로는 원리적으로 불가

---

## 2. A축 — 무엇을 '가치'로 재는가

| # | 하위질문 | 상태 | 답 (요지) | 근거 |
|---|---|---|---|---|
| **A1** | 같은 벽시계에서 **최종해**(tail 포함 end-to-end)가 K>1에서 더 좋은가? | ❌ **[새 런]** | **K>1에서는 한 번도 측정된 적 없다.** 모든 K 실험이 CSR 단일 스텝(tail 없음). 이것이 지도 전체에서 가장 큰 구멍이다. | (부재) — §6-P1 |
| **A2** | 고정 init 예산에서 **초기해 품질**이 K>1에서 더 좋은가? | ✅ | **아니다.** f∈[1,15] % 전 구간, 4개 rounding, 3개 reconstruct mode, 4개 arm 어디에서도 dRPDf > 0. 최솟값조차 **+1.60 pp**(arm a, k2, cumulative). | `plans/analysis/20260726/mcf_lb_atomic_rerun_verdict.md` §5.1 (0/200 반례) · `plans/analysis/20260724/lastsemi_rounding_robustness.md` §4.2 (27/27 셀) |
| **A3** | K>1이 **해를 낼 수 있는 범위**를 넓혀 주는가 (feasibility)? | ✅ | **아니다 — 한때 그렇게 보였으나 버그였다.** f=1 %에서 K=1이 20/160에 해를 못 내던 현상은 `calc_mcf_lb_and_derive_full_sch`의 의도치 않은 stop gate. 원자화 후 `coarse_only_feasible` **378 → 0**, 그 20개에서 K≥4가 K=1에 **20/20 패배**. | `plans/analysis/20260726/mcf_lb_atomic_rerun_verdict.md` §5.2, §6.1 |
| **A4** | K>1이 **시간을 벌어 주는가**? | ✅ | **스텝에 따라 다르다.** MCF-LB 단독(arm b)은 (200,10)에서 3.90 s → 2.33 s(**−40 %**), dispatch-only(arm a)는 **0 %**, full flow(m1)는 예산 cap이 binding이라 **0 %**. 즉 절감은 실재하지만 예산제 아래에서는 관측되지 않는다. | `plans/analysis/20260726/coarsening_short_budget_crossover.md` §5 |
| **A5** | init 우위(혹은 열위)가 **downstream을 통과해 살아남는가**? | 🟡 **[새 런]** | **K=1에서만 측정됐고, 그 답은 "거의 지워진다"**: CSR κ=1 init이 C5 init을 **13.15 pp** 앞섰으나 full 0.09nc 파이프라인 후 그 우위는 **≤1.16 pp**로 수축. `incremental_sw_cp`가 출발점 차이를 지우는 것이 기전. **K>1의 −27 pp 열위가 같은 흡수를 받는지는 미측정.** | `plans/analysis/20260721/csr_init_isw_batch_result.md` §"13 %p 마진이 지워진다" |
| **A6** | ②의 의미에서, **CSR 껍데기 자체**(K=1)가 기존 C5 initializer보다 나은가? | 🟡 **[새 런]** | **초기해로는 그렇다(+13.15 pp), 최종해로는 미정.** 배치폭 `m+2`와 묶여 측정돼 순효과가 오염됐다(`m+2`가 단독 +2.54 pp 손해). 배치 `m` 고정 재측정(`B30@m`)이 **명시적으로 권고됐으나 실행되지 않았다.** | 같은 문서 §"Gate reading" 권고 3 |

> **A축의 핵심 비대칭**: A2/A3는 **닫혔다**(대량·다각도로 부정). A1/A5는 **열려 있다**.
> Q0의 문장이 "initialization에 도움이 되는가"이므로 A2가 1차 답이지만,
> initialization의 존재 이유가 "최종해를 좋게 하는 것"이라면 A1/A5 없이는 완결되지 않는다.

---

## 3. B축 — coarsening 편에 최선을 다 줬는가 (steelman)

부정 결론의 신뢰도는 이 축이 얼마나 채워졌는지에 비례한다. **6개 자유도 중 5개가 소진됐다.**

| # | 하위질문 | 상태 | 답 (요지) | 근거 |
|---|---|---|---|---|
| **B1** | **K**를 잘 고르면 이기는가? | ✅ | 아니다. K ∈ {2,4,8,16,32} 전부 패배하고 **K에 대해 단조로 나빠진다**. K=2가 항상 최선의 coarse arm. | 20260726 verdict §5.1 · 20260721 `csr_coarsen_mode_result.md` (K=16까지) |
| **B2** | **rounding mode**를 잘 고르면 이기는가? | ✅ | 아니다. `{ceil, floor, round, cumulative}` 4개 × k{2,4,8} × f{5,10,15} = **27셀 전부 패배**. severity는 `round ≈ cumulative < ceil < floor`(f≥5 %), 짧은 f에서는 순서가 흔들리나 **격차가 2 pp 이내**라 결론에 무관. | `plans/analysis/20260724/lastsemi_rounding_robustness.md` §4.1–4.2 |
| **B3** | **reconstruct mode**를 잘 고르면 이기는가? | ✅ | 아니다. `active`(+29.19 pp 악화) → `semi` → `active_but_last_semi`(semi 대비 −3.17 pp, 최선)로 개선했고, **최선 모드에서도** coarsening은 +27~35 pp 패배. 재구성 결함은 고쳤지만 해상도 손실은 되돌리지 못한다. | `plans/analysis/20260724/csr_reconstruct_mode_active_vs_semi.md` · `.../csr_reconstruct_mode_lastsemi.md` 결과 2 |
| **B4** | **inner solve_flow**를 바꾸면 이기는가? | ✅ | 아니다. 4개 arm(dispatch-only / mcf_lb / mcf_lb+flip CP / full flow) 전부에서 dRPDf > 0. **CP 유무와 무관**하다는 점이 중요 — 손해가 CP의 특성이 아니라 해상도 자체에서 온다는 증거. | 20260726 crossover ladder §4.1 (arm a/b/c/m1) |
| **B5** | **예산 f**를 줄이면(=K=1이 굶으면) 이기는가? | ✅ | 아니다. f를 15 %→1 %까지 밀어도 crossover 없음. dRPDf는 **f=5 % 부근이 정점인 역U자(∩)**이고, f<5 %에서 penalty가 줄어드는 것은 crossover로의 수렴이 아니라 **K=1도 함께 굶어서** 생기는 착시. | 20260726 crossover ladder §4.2 · verdict §5.3 |
| **B6** | **인스턴스별 사후 최적 선택(oracle)**이면 이기는가? | 🟡 **[재분석]** | 20260721 슬라이스에서 16개 coarse 시나리오의 per-instance oracle조차 **+4.35 pp 패배**. 단 이는 K≤16·구버전 reconstruct 기준이고, **crossover 런(210 시나리오)에서 같은 oracle을 다시 계산한 적은 없다.** | `plans/analysis/20260721/csr_coarsen_mode_result.md` §"win rate & oracle" |
| **B7** | **seed_dispatch** 선택이 결론을 바꾸는가? | ❌ **[재분석 일부 / 새 런]** | crossover 런은 `v4` 고정. `job_wise / mixed / v3`는 K 축과 교차 측정된 적이 없다. 다만 arm a(= v4 dispatch만)의 penalty가 이미 최소(+1.60 pp)라 **다른 seed가 −1.60 pp를 뒤집을 여지는 좁다**. | `metadata/20260725/coarsening_crossover.yaml` (v4 고정) |

> **판정**: B1~B5는 소진됐다. 남은 것은 B6(재분석 한 시간)과 B7(약한 여지)뿐이므로,
> "설정을 잘못 골라서 진 것"이라는 반박은 **현재 거의 불가능하다.**

---

## 4. C축 — 왜 지는가 (메커니즘)

이 축이 D축(일반화)의 유일한 근거다. "왜"를 모르면 다른 셀·다른 문제로 옮길 수 없다.

| # | 하위질문 | 상태 | 답 (요지) | 근거 |
|---|---|---|---|---|
| **C1** | dRPDf를 **해상도 손실**과 **깊이 이득** 두 채널로 분해하면? | ✅ | 설계로 분리했다. arm a(CP 없음, 깊이 고정) = **해상도 채널 단독** → +1.60~+2.28 pp(작지만 **양수**). m1(두 채널 합) = +9~+33 pp. 즉 **깊이 이득은 실재하나 해상도 손실을 상쇄하지 못한다.** | crossover ladder §1.1, §4.3 |
| **C2** | 깊이 이득이 실제로 얼마나 생기는가? | ✅ | 크다. f=4 %·(200,10)에서 K=32는 **70 %(14/20)가 `incremental_sw_cp`까지 도달**(K=1은 100 %가 step-1·2에서 멈춤). 그런데도 dRPDf **+33.08 pp**. **깊이를 더 살수록 더 크게 진다.** | crossover ladder §4.3 (winner-source depth) |
| **C3** | 손해가 **인스턴스 크기**에 따라 커지는가? | ✅ | 커진다. n=50 +1.2 pp → n=200 **+24.7 pp**(k2_ceil 기준). "큰 인스턴스일수록 coarsening이 유리"라는 직관의 **정반대**. | `plans/analysis/20260721/csr_coarsen_mode_result.md` §"penalty grows with size" |
| **C4** | 왜 목적함수가 해상도에 이토록 민감한가? | 🟡 **[재분석]** | 가설: **weighted E/T는 due-window에 대한 정확한 타이밍 함수**라 시간 눈금을 K배로 뭉개면 창 안쪽 정렬이 통째로 깨진다(makespan류와 다름). 정황 증거(C3, floor가 최악, active 재구성이 earliness 폭증)는 모두 이 방향이지만 **직접 측정(E항/T항 분해)은 없다.** | 가설 — §6-P4 |
| **C5** | 지는 인스턴스와 **이기는 인스턴스**는 무엇이 다른가? | ❌ **[재분석]** | arm a에서 coarsening이 이기는 인스턴스가 **160개 중 41~59개** 존재한다(소수파이나 무시 못 할 수). 이들의 (n, c, mps, W) 특성은 한 번도 프로파일되지 않았다. **"언제 쓰면 되는가"의 유일한 단서.** | crossover ladder §4.3 |
| **C6** | rounding severity 순서(`floor` 최악)의 기전은? | ✅ | floor는 처리시간을 체계적으로 **과소평가**해 재구성이 압축 불가능한 골격에서 출발한다. k=8에서 +62.98 pp로 유일하게 K에 대해 급격 단조 증가. | `lastsemi_rounding_robustness.md` §5-2 |

---

## 5. D축 — 어디까지 일반화되는가 (+ E축 신뢰성)

| # | 하위질문 | 상태 | 답 (요지) | 근거 |
|---|---|---|---|---|
| **D1** | 다른 **(T, R) 셀**에서도 같은가? | 🟡 **[새 런]** | 크로스오버·rounding 실험의 정밀 측정은 전부 **(T,R)=(0.6,0.2) 160 인스턴스** 슬라이스. 단 `lastsemi_rounding_robustness`와 `cumulative_vs_ceil`은 **full 1440**에서도 같은 부호를 확인했다 → 방향은 일반화되나 **크기는 셀 의존**. | rounding robust(1440) vs crossover(160) |
| **D2** | 다른 **예산 범위**에서도 같은가? | ✅ | f ∈ [1,15] %가 연속으로 덮였고 전 구간 K=1 최선. f > 15 %는 K=1의 RPDf가 0.055 pp로 이미 포화라 자명. | 20260724 + 20260726 사다리 접합 |
| **D3** | **tail이 붙으면** 결론이 바뀌는가? | ❌ **[새 런]** | = A1. 미측정. **A5(K=1에서 13.15 pp → ≤1.16 pp 수축)를 근거로 외삽하면 K>1의 −27 pp도 수축하겠지만, 수축은 0을 향한 것이지 부호를 뒤집지 않는다** — 그러나 이는 추론이지 측정이 아니다. | §6-P1 |
| **D4** | **다른 목적함수/문제**(makespan 등)에서도 coarsening이 무가치한가? | ❌ **[불가]** | 이 저장소의 데이터로는 원리적으로 답할 수 없다. C4가 옳다면 **정반대**를 예상해야 한다: 시간 해상도에 둔감한 목적함수에서는 coarsening이 유효할 수 있다. **이것이 Q0의 부정 답에 반드시 붙어야 할 단서다.** | — |
| **E1** | 결론을 오염시킨 **구현 결함**은 없는가? | ✅ | 하나 발견·수정됨(mcf_lb round-1 stop gate). 수정 후 재실행에서 **negative control(arm a/b) 6,720행 bit-identical**, objective 결론 불변(0/200). | `mcf_lb_atomic_rerun_verdict.md` §4 |
| **E2** | **budget parity**가 성립하는가 (K=1이 시간을 더 쓴 것 아닌가)? | ✅ | 성립. f=5/10/15 %에서 elapsed가 k에 무관하게 동일(예: f5 k1 4.26 s vs k8 4.21 s). | `lastsemi_rounding_robustness.md` §4.4 |
| **E3** | **CP 노이즈**로 설명되는가? | ✅ | 아니다. mean dObj ≈ +45,000으로 노이즈 플로어(±350)의 **130배**. | 같은 문서 §4.2 |
| **E4** | 예산을 실제로 **지키고 있는가**? | ❌ (결론 무관, 설계 문제) | 아니다. 8개 (n,c) 셀 전부가 CSR budget을 1.1~1.67× 초과한다. MCF-LB가 mid-solve 중단 불가라 예산을 강제할 수단이 없다. 비교는 양쪽에 동일하게 적용되므로 결론을 뒤집지 않지만 **예산 설계 자체가 열린 문제**. | `mcf_lb_atomic_rerun_verdict.md` §6.4, §8-5 |

---

## 6. 남은 질문의 우선순위 (무엇을 다음에 하면 지도가 닫히는가)

| 우선 | 질문 | 비용 | 왜 이것인가 |
|---|---|---|---|
| **P1** | **A1/D3 — tail을 붙인 K>1 end-to-end 측정** | **[새 런]** 중간 (arm 축소 가능: k∈{1,2,8} × f∈{5,10} × lastsemi/ceil, 1440 또는 (0.6,0.2) 슬라이스) | 지도의 유일한 구조적 구멍. Q0가 "initialization"을 묻는 이상, "그 초기해로 끝까지 풀면 어떻게 되는가"가 답의 완결 조건이다. A5가 "downstream이 차이를 지운다"고 말하므로 **−27 pp가 −2 pp로 수축할 가능성이 실재**하고, 그 경우 결론의 *어조*가 바뀐다("해롭다" → "무의미하다"). |
| **P2** | **A6 — `B30@m` (CSR κ=1 init을 배치 `m`에서 재측정)** | **[새 런]** 소 (3 arm) | 20260721에 명시 권고됐으나 미실행. 예측치는 **A −1.16 pp**(작은 승). K 논쟁과 **독립**으로, "CSR 껍데기 자체"의 가치를 판정한다. K>1이 죽어도 K=1 CSR init은 살 수 있다. |
| **P3** | **C5 — coarsening이 이기는 41~59개 인스턴스 프로파일링** | **[재분석]** 소 | "coarsening을 언제 쓰면 되는가"의 유일한 단서. 부정 결론에 **처방적 가치**를 붙일 수 있는 유일한 항목. `drpdf_by_mode_k.csv` per-instance 레벨로 다시 열면 끝. |
| **P4** | **C4 — 목적함수 민감도의 직접 측정 (E항 / T항 분해)** | **[재분석]** 중 | D4(다른 문제로의 일반화)를 말할 수 있는 유일한 근거. 지금은 정황증거뿐이다. coarse vs K=1 해의 earliness/tardiness 성분을 나눠 보면 "무엇이 깨지는가"가 직접 보인다. |
| **P5** | **B6 — crossover 런에서 per-instance oracle 재계산** | **[재분석]** 소 | steelman의 마지막 칸. 210 시나리오 oracle이 여전히 K=1에 진다면 B축은 완전히 닫힌다. |
| **P6** | **D1 — 다른 (T,R) 셀로 확장** | **[새 런]** 대 | 방향은 이미 1440에서 확인됨. **P1~P5보다 한계 정보량이 낮다.** 결론의 어조를 바꾸지 못하고 크기만 바꾼다. |

> **의도적으로 하지 않을 것**: 과거 짧은-budget 실험의 선제적 전면 재측정.
> `mcf_lb_atomic_rerun_verdict.md` §9가 f≥5 %는 재측정 불필요로 판정했다.

### 6.1 결정 (2026-07-26) — 실행 계획은 로드맵으로 이관

위 우선순위에 대한 판단이 내려졌다. **집행 문서는
`plans/experiment/20260726/csr_init_roadmap.md`**이며, 아래는 요약이다.

| 항목 | 결정 |
|---|---|
| **P1** | **보류.** 게이트 충족 전까지 착수하지 않는다 — "총 시간제약의 40 % 이하로 초기화했을 때, `best(MCF-LB → FMM, NEH-CP)`보다 **9개 (T,R) 셀 전부**에서 RPDf가 이하". MCF-LB로 인한 예산 초과는 무시. |
| **P2** | **진행.** 단 **최종해가 아니라 초기해 축**. ① f=35 %, 40 % 확장 + 곡선 재측정(W2) ② CSR(τ=1) + ISW-CP(kappa 0.005) + base CP-SAT tail(W3). 선행 코드 작업으로 CSR 내부 단계 점을 scatter에 십자가로 표시(W1). |
| **P3** | **진행** (재분석). 계획: `plans/experiment/20260726/coarse_winner_instance_profiling.md` (W4). |
| **P4 / P5 / P6** | **폐기.** |

**표기**: coarsening factor를 앞으로 **τ**로 쓴다. 본 문서의 `K` / `κ`는 모두 τ와 같다.

---

## 7. 현재 시점의 종합 답 (초안)

세 문장으로 압축하면:

1. **초기해 품질 축(A2)에서 coarsening은 가치가 없다** — K, rounding, reconstruct mode,
   inner flow, 예산의 5개 자유도를 전부 소진해도 K=1을 이기는 조합이 **0/200**이며,
   최선의 조건에서도 최소 +1.60 pp 진다.
2. **그 이유는 "깊이 이득 < 해상도 손실"이라는 부등식**이며, 깊이 이득은 실재한다(K=32는
   70 %가 isw까지 도달) — 그런데도 지므로, **weighted E/T 목적함수의 시간 해상도 민감성**이
   지배 요인이라는 해석이 남는다(C4, 직접 측정 미완).
3. **그러나 Q0는 아직 완결되지 않았다** — 모든 측정이 **tail 없는 단일 스텝**이고,
   K=1에서 확인된 "downstream이 초기해 차이를 지운다"는 사실(A5)이 K>1에 적용되면
   결론은 "해롭다"에서 **"무의미하다"로 이동할 수 있다**(부호가 뒤집힐 근거는 없다).
   A1(P1)이 그 마지막 칸이다.

---

## 8. 최종 리뷰 문서를 쓴다면 (목차 제안)

이 지도를 그대로 서술 순서로 쓰면 읽는 사람이 "왜 이 실험을 했는가"를 되묻지 않는다.

```
1. 질문과 용어              — §0 (특히 "initialization = tail 없음" 고지를 맨 앞에)
2. 결론 먼저                — §7 세 문장
3. 반박 소진의 기록         — B축: K → rounding → reconstruct → inner flow → 예산 순
                              (각 절이 "이 설정이면 이기지 않을까?"에 대한 답)
4. 왜 지는가                — C축: 두 채널 분해 → 깊이 이득의 실측 → size 의존 → 목적함수 가설
5. 한때 이긴 것처럼 보였던 것 — A3 (버그와 그 철회) — 방법론 신뢰성의 증거로 배치
6. 아직 답하지 않은 것      — A1/A5/D4, 그리고 그것이 결론을 어떻게 바꿀 수 있는지
7. 다음 한 걸음            — P1, P2
```

**서술상의 주의 두 가지**

- **§5(버그)를 §3·§4보다 뒤에 두는 이유**: 앞에 두면 "결과가 버그투성이"라는 인상을 주고,
  뒤에 두면 "우리가 우리 결론을 스스로 뒤집을 준비가 돼 있었다"는 신뢰의 증거가 된다.
  실제로 그 재실행은 결론을 강화했다(f=1 % 20개에서 K≥4가 20/20 패).
- **§6에서 D4를 반드시 명시**: "coarsening이 무가치하다"가 아니라
  **"weighted E/T + 이 예산대에서 무가치하다"**가 실제로 증명된 것이다.

---

## 9. 참조 (tracked SSOT)

| 문서 | 답하는 하위질문 |
|---|---|
| `plans/analysis/20260726/mcf_lb_atomic_rerun_verdict.md` | A2, A3, E1 — **최신·최종 판정** |
| `plans/analysis/20260726/coarsening_short_budget_crossover.md` | A4, B4, B5, C1, C2 (§4.4는 결함 기록으로만 읽을 것) |
| `plans/analysis/20260724/lastsemi_rounding_robustness.md` | B2, C6, E2, E3 |
| `plans/analysis/20260724/csr_reconstruct_mode_lastsemi.md` | B3 |
| `plans/analysis/20260724/csr_reconstruct_mode_active_vs_semi.md` | B3 (active 폐기) |
| `plans/analysis/20260721/csr_coarsen_mode_result.md` | B1, B6, C3 |
| `plans/analysis/20260721/csr_cumulative_vs_ceil.md` | B2 (full 1440 확인) |
| `plans/analysis/20260721/csr_init_isw_batch_result.md` | **A5, A6** — 유일하게 tail을 포함한 측정 |
| `plans/analysis/20260719/csr_init_k_budget_consolidation.md` | §0 용어(K=1 항등 검증), A2의 선행 |

**주 런 디렉터리** (gitignored)

- `output/20260725_crossover_ladder/20260726T173841_347539` — 최신(원자화 후), 210 scn × 160 ins
- `output/20260725_crossover_ladder/20260726T002619_971440` — 그 이전(게이트 있음), 대조군
- `output/20260724_merge_rounding/20260725T231504_516446` — rounding robustness, 39 scn × 1440
- `output/20260724_merge_lastsemi_3way/20260724T203441_310017` — reconstruct 3-way, 36 scn × 1440
- `output/20260721_csr_init_isw_batch/20260721T015603_278451` — tail 포함 4 arm × 1440
