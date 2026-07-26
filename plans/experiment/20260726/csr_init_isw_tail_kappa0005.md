# W3 — config 2: CSR(τ=1) 뒤에 ISW-CP(kappa 0.005) + base CP-SAT 붙이기 (사전 작성)

**작성일**: 2026-07-26 · **종류**: 실험 실행 계획(사전 작성, 미실행)
**상위**: `plans/experiment/20260726/csr_init_roadmap.md` (W3)
**선행**: **W2** (`csr_init_tl_f35_f40.md`) — 여기서 쓸 f를 W2가 고른다
**닫는 미결**: `plans/analysis/20260721/csr_init_isw_batch_result.md` §"Gate reading"
권고 3 (`B20@m` / `B30@m`) — 권고만 되고 **한 번도 실행되지 않았다**

> **가정 확인 필요**: "두 번째 config는 첫 번째 config 다음에" 로 읽었다. W2의 결과와
> 무관하게 착수해도 되는 성격이라면 순서를 바꿔도 무방하다 (로드맵 §5 참조).

---

## 1. 질문

> **τ=1 CSR 껍데기를 initializer로 쓰고 그 뒤에 ISW-CP(kappa 0.005) + base CP-SAT
> tail을 붙이면, 기존 3-step C5 prefix + 동일 tail보다 나은가?**

W2가 **초기해 축**을 판정한다면, W3는 그 초기해가 **tail을 통과해도 살아남는지**를
판정한다. τ>1은 관여하지 않는다 — 이것은 P1(τ>1 end-to-end, 보류)과 **다른 질문**이다.

### 1.1 왜 이 측정이 비어 있는가

20260721 실험은 initializer 교체와 **ISW-CP 배치폭 `m→m+2` 확대를 묶어서** 쟀다.
분해 결과:

| 대비 | 의미 | mean %p |
|---|---|---|
| `C−A` | 배치 `m`→`m+2` 단독 | **+2.54** (일관된 손해) |
| `B20−C` | 배치 `m+2` 고정, init 교체 (f=20 %) | −0.15 |
| `B30−C` | 배치 `m+2` 고정, init 교체 (f=30 %) | **−1.16** |

즉 **init 교체 자체는 이득**인데 `m+2` 페널티에 묻혀 end-to-end로는 졌다. 그래서
그 문서는 "**배치 `m`을 유지한 채** 승자 initializer를 재측정하라"고 권고했고
(예측: `B30@m ≈ A − 1.16 %p`), 그 런은 실행되지 않았다. W3가 그 자리를 채운다.

### 1.2 tail을 20260710 kappa 0.005 블록으로 바꾸는 이유

20260721 arm A의 tail은 isw `total_timelimit: 0.018nc`를 걸었다. 본 실험은
`metadata/20260710/sw_cp_tl_kappa_0.005.yaml`의 tail을 **그대로** 쓴다:

```yaml
- method: incremental_sw_cp
  solver_thread_cnt: 8
  batch_size: "m"                                  # ← m 유지 (m+2 아님)
  step_size: 1
  unfixed_batch_count_min: 2
  unfixed_batch_count_max: 8
  increment_unfixed_batch_count_flag: "always"
  left_profile_fixed_batch_count: 2
  right_profile_fixed_batch_count: 2
  enable_promotion_profile_fixed: true
  pf_method: "PF1"
  batch_tl_mode: "proportional"
  non_time_fixed_op_time_limit_multiplier: 0.005
- method: solve_base_model_cpsat
  solver_thread_cnt: 8
```

- **`total_timelimit`이 없다** → ISW-CP가 **남은 예산 전부**를 쓴다. 그 결과 모든
  arm이 outer `0.09nc`를 동일하게 소진하므로 **budget parity가 자동으로 성립**한다
  (initializer가 아낀 시간은 그대로 tail로 흘러간다 — 20260721이 "freed initializer
  budget is spent on more ISW-CP iterations"라고 진단한 바로 그 구조).
- `batch_size: "m"`이므로 20260721의 +2.54 pp 오염원이 제거된다.

---

## 2. 실행 설정

**grid**: **full 1440**.

**arm 3개** (f 두 값은 W2가 고른다 — 기본 후보 `{30 %, 40 %}`):

| arm | prefix | tail | 비고 |
|---|---|---|---|
| **A** | `calc_mcf_lb_and_derive_full_sch → run_flip_makespan_cp_from_incumbent(0.009nc) → neh_cp(0.027nc)` | §1.2 블록 | 기준. = 20260710 kappa_0.005 flow |
| **B_f1** | `coarsen_solve_reconstruct(factor=1, f=f1)` | §1.2 블록 | init 교체 |
| **B_f2** | `coarsen_solve_reconstruct(factor=1, f=f2)` | §1.2 블록 | f 방향성 확인 |

- **A를 이 런 안에서 새로 돈다.** 20260710 런은 `run_mode: RESUME`이고 base가
  `output/20260709T231643_016242/mcf_lb_fmm_neh_cp`라 **코드·머신·부하가 다르다**.
  재사용하면 W2 §1.2와 같은 드리프트 문제가 그대로 재발한다.
- B arm의 CSR inner TL은 W2와 **동일한 비례식**(`0.0009·f·nc` 등, W2 §2 표)을 쓴다.
- `factor: 1`, `seed_dispatch: v4`. `idle_mode` 키 금지(`d442ac0`).

```bash
uv run python scripts/20260726/build_csr_isw_tail_config.py --f1 30 --f2 40
    # -> metadata/20260726/csr_init_isw_tail.yaml
uv run python main.py --config metadata/20260726/csr_init_isw_tail.yaml
    # -> output/20260726_csr_init_isw_tail/<timestamp>/
```

**예상 소요**: 3 arm × 1440 @ 0.09nc 완전 소진 ≈ **2.6 h/arm × 3 ≈ 8 h** (calop4,
20260721 실측 기반). tail이 예산을 다 쓰므로 f와 무관하게 arm당 비용이 같다.

---

## 3. 판정 방법

**주 판정** — pooled mean RPDf 및 per-instance paired 대비:

```
B_f − A   < 0  이면 init 교체가 end-to-end로 이득
```

20260721의 예측치는 `B30@m − A ≈ −1.16 %p`다. 이를 **사전 등록된 예측**으로 두고,
실측이 예측과 부호·크기에서 얼마나 맞는지 함께 보고한다(예측이 빗나가면 "배치
페널티가 initializer에 대해 가법적이지 않다"는 20260721의 유보가 확인되는 것).

**판정 게이트**:

| 결과 | 해석 | 다음 |
|---|---|---|
| `B_f − A ≤ −1 %p` (그리고 win > loss) | init 교체 채택 근거 | f를 위로 스윕(45 %, 50 %) |
| `−1 < B_f − A < +1 %p` | tail이 차이를 지운다 — 20260719/20260721 패턴 재확인 | CSR 껍데기는 최종해에 무관, 초기해 축(W2)만 남음 |
| `B_f − A ≥ +1 %p` | init 교체가 해롭다 | 왜 tail이 CSR 초기해를 못 살리는지 별건 조사 |

**병기 필수**:

1. **T별 층화** (T ∈ {0.2, 0.4, 0.6}) — 20260721에서 arm 간 격차가 T에 따라 3.1배
   달랐다. pooled만 보면 안 된다.
2. **9개 (T,R) 셀 표** — W2의 게이트 표와 나란히 두면 "초기해에서 이긴 셀이
   최종해에서도 이기는가"를 직접 읽을 수 있다. **이것이 W2·W3를 잇는 핵심 표다.**
3. **`elapsedTime` / `time%`** — 세 arm이 정말 0.09nc를 동일하게 썼는지.
4. **initializer 시점의 RPDf** (obj_log의 prefix 종료 endpoint) — 초기해 마진이
   얼마였고 tail 후 얼마나 남았는지를 **같은 런 안에서** 계산한다. 20260719처럼
   다른 런에서 가져오지 않는다.
5. 노이즈 바닥 ±350 (1440 격자) 대조.

---

## 4. 산출물

- 사후 분석 문서: `plans/analysis/20260726/csr_init_isw_tail.md` (tracked SSOT)
- 분석 스크립트: `scripts/20260726/analyze_csr_isw_tail.py`
- config 생성기: `scripts/20260726/build_csr_isw_tail_config.py`
- 커밋: run setting + merged analysis (CLAUDE.md provenance 규약)

## 5. 판독 시 주의

- **이 실험은 P1이 아니다.** τ=1 고정이므로 coarsening의 end-to-end 가치와 무관하다.
  결과가 좋아도 τ>1 보류는 유지된다(로드맵 §2 게이트는 W2가 판정).
- **A가 기존 방식이라는 등식이 성립하는지 확인할 것**: 로드맵 §2의 게이트가 말하는
  "지금의 방식"은 `best(MCF-LB → FMM, NEH-CP)`이고, arm A의 prefix가 정확히 그것이다.
  단 A는 tail까지 포함하므로 **게이트 판정에는 A의 prefix 종료 시점 값**(§3 병기 4)을
  쓰고, W2의 `c5_init_only`와 교차 검산한다. 두 값이 크게 다르면 측정 경로에 문제가
  있는 것이다.
