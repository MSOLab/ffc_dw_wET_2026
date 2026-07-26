# CSR 초기화 로드맵 — 무엇을 언제 하고, 무엇을 안 하는가 (사전 작성)

**작성일**: 2026-07-26 · **종류**: 상위 계획(로드맵). 개별 작업은 각 하위 문서에서 실행.
**질문 지도**: `reviews/20260726_coarsening_value_question_map.md`
**최신 판정**: `plans/analysis/20260726/mcf_lb_atomic_rerun_verdict.md`

> 이 문서는 **인덱스이자 게이트 정의서**다. 실제 작업은 아래 W1~W4 하위 문서를 각각
> **별도 대화**에서 집어 들고 수행한다. 각 하위 문서는 그 자체로 self-contained하다.

---

## 0. 표기 변경 — K / κ → **τ**

coarsening factor를 앞으로 **τ**로 쓴다. 코드 심볼(`factor`)과 기존 문서의 `K` / `κ`는
모두 같은 것이다. 과거 문서를 읽을 때 `K=1` = `κ=1` = **τ=1** = coarsening 없음(항등).

| 문맥 | 표기 |
|---|---|
| 코드 (`CoarsenSolveReconstructOption.factor`, config `factor:`) | `factor` (변경 없음) |
| 2026-07-26 이전 문서 / 시나리오 이름 (`csr_k1_...`) | `K`, `κ` (그대로 둠, 개명하지 않음) |
| 2026-07-26 이후 신규 문서·분석 | **τ** |

---

## 1. 현재 확정된 것 (재논의하지 않음)

1. **τ>1(coarsening)은 초기해 품질에서 가치가 없다.** τ, rounding mode, reconstruct
   mode, inner flow, 예산 f의 5개 자유도를 소진해도 τ=1을 이기는 조합이 **0/200**,
   최솟값 +1.60 pp. → `mcf_lb_atomic_rerun_verdict.md` §5.1
2. **f=1 %의 "coarsening이 이긴다"는 관측은 구현 결함이었다.** 수정 후 철회됨.
3. 따라서 **τ>1 자체를 더 밀지 않는다.** 아래 W1~W3는 전부 **τ=1**, 즉
   "coarsening 없는 CSR 껍데기"의 가치를 묻는 작업이다.

---

## 2. P1 게이트 — τ>1 end-to-end 측정의 진입 조건

**τ>1에 tail(outer `incremental_sw_cp` + `solve_base_model_cpsat`)을 붙인 end-to-end
측정은 하지 않는다.** 아래 조건이 충족되기 전에는 착수하지 않는다.

> **게이트 (의미 있는 초기화 결과의 정의)**
>
> 총 시간제약의 **40 % 이하**를 초기화에 쓰도록 설정하고 돌렸을 때,
> **지금의 방식** — `best(MCF-LB → FMM, NEH-CP)`, 즉 3-step C5 initializer —
> 보다 **모든 (T, R) 구간(9셀 전부)** 에서 RPDf가 **이하**로 나올 것.
>
> - MCF-LB가 중단 불가라 예산을 다소 초과하는 것은 **무시한다**
>   (`mcf_lb_atomic_rerun_verdict.md` §6.4가 1.1~1.67× 초과를 기록).
> - "이하"는 셀별 mean RPDf 기준. 9셀 중 하나라도 초과하면 미충족.

**40 %라는 숫자의 근거**: C5 initializer의 예산이 정확히 `0.036nc = 0.09nc × 40 %`다
(`metadata/20260721/csr_init_isw_batch.yaml` arm `a_c5_batch_m`: flip `0.009nc` +
neh `0.027nc`, mcf_lb는 시간 인자 없음). 즉 이 게이트는 **동일 예산 상한에서의
initializer 대결**이다.

게이트를 통과하는 것은 지금으로선 **τ=1 CSR**뿐일 가능성이 높고, 그 판정이 W2다.
τ>1이 이 게이트를 통과할 근거가 새로 나오지 않는 한 P1은 계속 보류한다.

---

## 3. 작업 순서

```
W1  보고서 수정 (코드)      ── W2의 선행. 단독으로도 유용.
     └ CSR 내부 단계 점을 scatter에 십자가로 표시 + τ=1 LB 유효화
W2  config 1 (실험)         ── P1 게이트 판정. f=35 %, 40 % 확장 + 곡선 재측정
     └ 산출: "τ=1 CSR init이 C5 init을 9개 (T,R) 셀 전부에서 이기는가"
W3  config 2 (실험)         ── W2 다음. CSR(τ=1) + isw(kappa 0.005) + base CP-SAT
     └ 20260721이 권고했으나 실행되지 않은 `B{f}@m` 측정을 닫는다
W4  재분석 (런 없음)        ── 독립. 언제 해도 됨.
     └ coarsening이 이기는 소수 인스턴스의 정체
```

| # | 문서 | 종류 | 선행 | 비용 |
|---|---|---|---|---|
| **W1** | `plans/experiment/20260726/csr_report_inner_step_points.md` | 코드(TDD) | 없음 | 반나절 |
| **W2** | `plans/experiment/20260726/csr_init_tl_f35_f40.md` | 실험 config + 런 | W1 권장 | 런 ~5–6 h |
| **W3** | `plans/experiment/20260726/csr_init_isw_tail_kappa0005.md` | 실험 config + 런 | W2 | 런 ~8–11 h |
| **W4** | `plans/experiment/20260726/coarse_winner_instance_profiling.md` | 재분석(스크립트) | 없음 | 1–2 h |

---

## 4. 하지 않는 것 (명시적 폐기 / 보류)

| 항목 | 결정 | 이유 |
|---|---|---|
| **P1** τ>1 end-to-end (tail 부착) | **보류** | §2 게이트 미충족. 게이트 통과 전에는 착수하지 않는다. |
| **P4** 목적함수 E항/T항 분해 | **폐기** | 관심 없음. (D4 "다른 문제로의 일반화"를 말할 근거가 없어지는 것은 감수) |
| **P5** crossover 런 per-instance oracle 재계산 | **폐기** | 관심 없음. B축 steelman은 이미 충분. |
| **P6** 다른 (T,R) 셀로 coarsening 확장 | **폐기** | (0.6,0.2)에서 좋지 않으므로 확장 계획 없음. 단 **W2의 게이트는 9셀 전부를 요구**하므로 τ=1 축에서는 여전히 full 1440을 돈다. |

---

## 5. 이 로드맵을 갱신해야 하는 시점

- W2가 게이트를 **통과**하면 → P1의 보류를 해제하고 τ>1 end-to-end를 재검토한다.
- W2가 게이트를 **미통과**하면 → CSR 껍데기 자체의 초기화 가치가 부정된 것이므로,
  W3를 계속할지(최종해 축에서는 다를 수 있다) 여부를 그 시점에 판단한다.
  W3는 W2의 결과와 무관하게 20260721의 미결 권고를 닫는 값어치가 있으므로
  **자동 취소되지는 않는다**.
