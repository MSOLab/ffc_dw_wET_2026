# CpsatAdapter post-process > CP-SAT 목적함수 warning: 진단 및 TDD 해결 계획

> 자매 문서: `plans/experiment/20260713/sw_cp_rj_warning_investigation.md`
> (sw_cp dispatcher RJ warning). 본 문서는 **같은 뿌리**(coarse-grid
> `insert_idle_time` 비최적)를 **다른 경로**(CpsatAdapter reconstruction)에서
> 재발견한 건이다. 이 문서는 진단 + TDD(빨간불 먼저) 설계까지만; 구현은 별도.

## Context — 관측된 warning

`csr_full_wdp` 시나리오(초기화 비교, tail 없음) 실행 로그에서 발생:

```txt
CpsatAdapter: post-process objective 149.000 > CP-SAT objective 0.000
```

- 발생지: `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py:224-235`
  (`obj_value > cp_obj` 분기).
- 로그: `output/20260713_csr_init_methods/20260713T195341_009592/csr_full_wdp/Instance_150_5_5_0,2_1_20_Rep0/…_SingleInstanceRunner.log:8`
- 인스턴스: `…_coarsenp4` → **CSR coarsening, `time_factor = 4`**.
- 진입 경로: `controller.solve_base_model_cpsat`
  (`controller.py:2080-2097`) → `CpsatOption(time_factor=self.time_factor)`
  → `CpsatAdapter.run`.
- (참고) 같은 인스턴스 로그에 sw_cp RJ warning
  (`dispatcher.py:242 … insert_idle_time left E/T on the table`)도 공존 →
  두 warning이 **동일 원인**의 두 지문임을 강하게 시사.

## 진단 — 왜 `cp_obj = 0`인데 post-process = 149 인가

CP-SAT 목적함수는 이미 coarse-scaled E/T이다
(`cumulative.py:_define_objective`, `scaled_C = time_factor * C^c`를 **원본**
due window와 비교). `cp_obj = 0` ⇒ **모든 job에 대해 `d_lo ≤ K·C^c ≤ d_hi`**
가 성립하는 coarse 해가 존재/발견됨 = 각 윈도우 안에 `K`의 배수가 있고 CP-SAT가
거기에 착지시킴. 즉 **coarse 격자에서 E/T=0을 증명**한 것.

그런데 `CpsatAdapter.run`의 reconstruction
(`cpsat_adapter.py:210-222`)이 그 최적 배치를 보존하지 못한다:

1. `build_schedule_from_op_starts` — CP-SAT의 op start/end로 스케줄 복원
   (이 시점엔 `K·C`가 in-window → E/T=0).
2. `make_semi_active` (`ffc_schedule.py:1024`) — **모든 op을 as-soon-as-possible
   좌측 이동** → last-stage 완료시각이 윈도우 앞으로 당겨지며 **earliness 발생**.
   CP-SAT가 넣어둔 in-window idle이 여기서 소거된다.
3. `insert_idle_time(...)` — **`idle_mode` 미지정 → 기본 `"flooring"`**
   (`cpsat_adapter.py:212`, `ffc_schedule.py:1655`). flooring은 S_E 타깃을
   `d_lo // K`로 잡아 `K ∤ d_lo`일 때 **언더슈트** → 잔여 earliness 149를
   회수하지 못한다 (자매 문서 §3과 동일 메커니즘).
4. `compute_weighted_earliness_tardiness(time_factor=4)` → 149.

핵심 불변식 위반: **고정 시퀀스에서 `insert_idle_time`이 (의도상) E/T 최적이라면,
reconstruction된 obj는 그 시퀀스의 임의 feasible 배치의 obj 이하여야 한다.**
CP-SAT의 배치는 그 시퀀스의 한 feasible 배치이고 그 값이 `cp_obj`이므로
**`obj_value ≤ cp_obj`가 성립해야 한다.** `K == 1`에서는 `insert_idle_time`이
논문상 최적(자매 문서 §2)이라 항상 성립 → warning 불가. `K > 1`에서 flooring
언더슈트가 이 불변식을 깬다 → warning.

### CpsatAdapter 고유의 두 가지 사실 (sw_cp와 대비)

- **더 날카로운 반례**: sw_cp RJ warning은 incumbent가 "느슨한 UB"라 비교
  기준이 약하지만, 여기선 **CP-SAT가 격자 위에서 obj=0을 증명**한 witness가
  있으므로 "reconstruction이 최적을 놓쳤다"가 명백하다.
- **idle_mode 배관 자체가 없음**: `CpsatOption`에는 `idle_mode` 필드가 없어
  `csr_*` 시나리오가 `lookahead`를 골라도 CpsatAdapter 경로는 **무조건
  flooring**이다. 이는 TODO의 "sw_cp incumbent prep should honour
  option.idle_mode"(`dispatcher.py:101` 비대칭)와 **동형 문제**이며,
  CpsatAdapter에는 그 필드가 아예 없다는 점에서 더 심하다.

## TODO.md(commit c36fa5e) 연결

`c36fa5e`가 추가한 TODO 항목들과의 대응:

| TODO 항목 | 본 건과의 관계 |
| --- | --- |
| **sw_cp incumbent prep should honour `option.idle_mode`** (`dispatcher.py:101`) | 동형. CpsatAdapter는 idle_mode 필드조차 없이 flooring 고정 — 같은 "내부 timing이 flooring으로 downgrade" 비대칭. |
| **Drop the `idle_mode` knob, hardcode `"lookahead"`** | lookahead가 flooring보다 낫지만 자매 문서 §3에 따르면 **coarse 격자 exact가 아님** → lookahead로도 본 불변식(`obj_value ≤ cp_obj`)은 원리적으로 항상 성립하지 않음. 아래 RED 테스트가 이를 falsify. |
| **coarse-grid exact timing (방향 B)** | 본 건의 **근본 해**. `insert_idle_time`이 고정 시퀀스·K-격자에서 정수 최적을 내면 `obj_value ≤ cp_obj`가 복원됨. |
| **`K*(C+1)` partition 회귀 테스트 부재** / **lookahead `<=` 격리** | 아래 테스트 하네스에 함께 태울 수 있음(같은 coarse timing 경로). |

## TDD 계획 — 빨간불 먼저

두 층위로 실패 테스트를 심는다. **불변식은 `obj_value ≤ cp_obj + FP_TOL`**
(post-process가 CP-SAT 증명값을 초과하면 안 된다).

### RED-1 (빠른 단위 테스트) — `insert_idle_time`이 coarse 최적을 회수해야 한다

`tests/solution/test_ffc_schedule.py` (기존 `insert_idle_time` 블록, `:481-`)에
추가. CpsatAdapter/CP-SAT 없이 순수 스케줄 레벨로 결함을 고정한다.

- **구성**: last-stage 단일 머신 고정 시퀀스 + coarse 윈도우로,
  **E/T=0(또는 값 V)인 배치가 존재하지만 `K ∤ d_lo`라 flooring이 언더슈트**하는
  최소 반례. `_make_iit_schedule` 패턴(`:485`) 재사용.
  - 반례 탐색: coarse 단일 머신 시퀀스에 대한 **소규모 brute-force**
    (TODO "Regression test for the `K*(C+1)` partition"이 권한 방법)로
    `d_lo`가 `K`의 배수가 아니고 in-window `K`-배수가 존재하는 케이스를 찾는다.
- **절차**: 최적 배치에서 `make_semi_active`로 좌측 압축 → `insert_idle_time`
  (flooring) 호출 → `compute_weighted_earliness_tardiness(time_factor=K)`.
- **단언(RED)**: `et_after <= et_optimal_witness + FP_TOL`.
  현재 flooring에서 **실패**(잔여 earliness 남음). `idle_mode="lookahead"`
  변형도 파라미터화하여 함께 돌린다 — 자매 문서 §3 예측대로 lookahead도
  일부 K에서 실패하면 "lookahead ≠ exact"가 테스트로 확증된다.
- **대조군(GREEN 유지)**: `K == 1`에서 동일 단언은 **통과**해야 한다
  (falsifiable control; `test_insert_idle_time_factor1_all_modes_identical`
  `:671`과 정합). K=1에서 실패하면 진단 오류 또는 구현 버그 신호.

### RED-2 (통합 테스트) — CpsatAdapter 불변식

새 파일 `tests/algorithm/test_cpsat_adapter_coarse_reconstruct.py`.

- **구성**: `time_factor=K(>1)`이고 CP-SAT가 낮은/0 목적함수를 증명하는 소형
  coarsened 인스턴스로 `CpsatAdapter().run(spec)` 실행
  (`CpsatOption(time_factor=K, timelimit_sec=<작게>)`).
- **단언(RED)**:
  `record.result.obj_value <= record.result.metrics["cpsat_obj_value"] + FP_TOL`.
  현재 149 > 0 로 **실패**.
- **부가 관측**: `metrics`의 `sum_earliness`/`sum_tardiness`로 잔여가 전부
  earliness임을 확인(진단과 일치). warning 텍스트(개선된 로그의
  `time_factor`, `gap`, `sum_e/sum_t`)도 caplog로 검증 가능.
- 비용상 무거우면 `@pytest.mark.slow` 등으로 분리하고 RED-1을 1차 게이트로.

## GREEN — 해결 방향 (설계만; 선택·구현 별도)

### 방향 A — 일관성: CpsatAdapter에 `idle_mode` 배관 + lookahead (실용, 부분)

- `CpsatOption`에 `idle_mode` 필드 추가, `cpsat_adapter.py:212`의
  `insert_idle_time`에 전달. `controller.solve_base_model_cpsat`가
  시나리오 값(csr_*는 lookahead)을 넘기게 함.
- **효과**: warning **건수·크기 감소**. 단 자매 문서 §3대로 coarse 격자에서
  lookahead도 exact가 아니므로 **RED-1/RED-2를 항상 green으로 만들지는 못함**.
- TODO "sw_cp incumbent prep should honour option.idle_mode" 및
  "hardcode lookahead"와 함께 처리하면 배관 일관성 확보.

### 방향 B — 근본: coarse-grid exact timing (원칙, 완전)

- 고정 시퀀스 + `K`-스케일 breakpoint에서 weighted E/T를 **정수 최적**으로 푸는
  timing(자매 문서 방향 B, 논문 인용 Hendel–Sourd류의 coarse 확장).
- **효과**: `insert_idle_time`이 고정 시퀀스 최적을 회수 → **`obj_value ≤
  cp_obj` 복원 → RED-1/RED-2 green, warning 원천 차단.** sw_cp RJ warning도
  동시 해소(공유 근본 해).
- 비용 큼: 정확성 증명 + `K==1` byte-identity 유지 + 기존 position-asserting
  테스트 재검토 필요.

### 권고

1. **RED-1을 먼저 심어 빨간불 확인**(빠르고 CP-SAT 불필요) → 결함을 격자 레벨로
   고정. lookahead 파라미터화로 "lookahead≠exact"까지 한 테스트로 falsify.
2. RED-2로 CpsatAdapter 경로의 불변식을 통합 레벨에서 고정.
3. 방향 A(배관 일관화)로 **부분 개선 + 건수 측정**. 단 A만으로 green이 안 되는
   케이스가 남음을 RED-1/2가 증언 → 이것이 **방향 B의 정당화**.
4. warning **소멸/불변식 보장**이 목표면 방향 B 착수. B 착수 시 TODO의
   "`K*(C+1)` partition 회귀 테스트"·"lookahead `<=` 격리"를 같은 하네스에 포함.

## 대상 파일

- `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`
  - `:210-222` reconstruction (make_semi_active → insert_idle_time; **flooring 고정**)
  - `:224-235` warning 지점 (2026-07-14 로그 보강: `time_factor`/`gap`/`sum_e,t`/`makespan`)
- `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py::CpsatOption` — `idle_mode` 필드 부재(방향 A)
- `src/ffc_ddw_sum_et/orchestration/controller.py:2080-2097` — `CpsatOption` 구성부
- `src/ffc_ddw_sum_et/solution/ffc_schedule.py`
  - `insert_idle_time` `:1648` (flooring/ceiling/lookahead `:1746/1762/1777`)
  - `make_semi_active` `:1024` (좌측 이동 = idle 제거)
- 테스트: `tests/solution/test_ffc_schedule.py`(RED-1),
  `tests/algorithm/test_cpsat_adapter_coarse_reconstruct.py`(RED-2, 신규)

## 진행 로그 (2026-07-14)

### ✅ 재현 (RED 확정 — 통합 레벨)

`metadata/20260714/csr_init_debug.yaml`(instance 835 =
`Instance_150_5_5_0,2_1_20_Rep0`, `csr_full_wdp` 단일 시나리오로 축소)로
`main.py` 실행 → warning 재현 (보강된 로그):

```txt
CpsatAdapter: post-process objective 149.000 > CP-SAT objective 0.000 (gap=149.000)
  for instance=..._coarsenp4 status=OPTIMAL time_factor=4:
  ... Residual split: sum_e=149.000 sum_t=0.000, makespan=510.
```

- 잔여 149가 **전부 earliness** (`sum_t=0`) — 진단(§27-47)과 일치.
- 같은 run에 sw_cp RJ warning (`rj obj 30 < incumbent 34` 등) 공존 — 공유 근본.

### ✅ 근본 원인 정밀화 (brute-force 탐색)

`scratchpad/explore_iit.py`: 마지막 스테이지 단일 머신 고정 시퀀스에 대해
`insert_idle_time` 3-mode 결과 vs **coarse 격자 정수 최적(brute-force)** 비교.
두 개의 독립 결함을 격리:

1. **`+1` 분류 오류 (모든 mode 공통, `ffc_schedule.py:1724`)** — 파티션이
   `K*(C+1)`로 s_e/s_d/s_t를 나눔. 실제 완료 `K*C`는 윈도우 아래(early)인데
   `K*(C+1)`이 윈도우 안이면 **s_d(정시)로 오분류** → shift 게이트(`sum_e>sum_t`)가
   발화하지 않아 earliness가 방치됨. 이것이 production 149(전부 earliness)의 정체.
2. **floor delta1 언더슈트 (flooring 전용)** — §43-46. ceiling/lookahead는
   완화하지만 결함 1 때문에 여전히 비최적.

**최소 반례 (RED-1 핵심, 세 mode 모두 실패):** 단일 job, `K=50`, 윈도우
`(60,130)`, 좌측압축 `C=1`(real 50). brute 최적 `C=2`(real 100)→ E/T=0. 그러나
flooring/ceiling/lookahead 모두 `C=1`에 정체 → cost 10. `K=1` 대조군은 세 mode
모두 최적 유지.

| case | K | 최적 | flooring | ceiling | lookahead |
| --- | --- | --- | --- | --- | --- |
| E 단일 job (60,130) C=1 | 50 | 0 | 10 | 10 | 10 |
| D 2-job 블록(가중 5) | 50 | 0 | 160 | 10 | 10 |
| B 좁은 윈도우(110,120) | 50 | 10 | 10 | 10 | 10 (이미 최적) |

→ **lookahead도 coarse-exact 아님**을 반례로 확증 = 방향 B 정당화.

### 계획: GREEN = 방향 B (coarse-grid exact block shift)

`insert_idle_time`의 `K>1` 경로를 **블록별 정수 최적 shift**로 교체
(K==1은 기존 검증된 경로 그대로 → byte-identity·비-CSR 무영향):

- 블록 `[j..block_end]`의 공통 shift δ∈[0, delta2]에 대해 `block_cost(δ)`는
  **볼록 조각선형** → 후보 breakpoint(`ceil(d_lo_i/K)-C_i`, `floor(d_hi_i/K)-C_i`,
  0, delta2)에서만 평가해 최소를 취함(동점 시 최소 δ = 불필요한 idle 억제).
- δ==delta2면 다음 블록과 병합(기존 `j` 고정 재평가 유지). δ==0이면 `j-=1`.
- brute-force oracle로 무작위 소규모 케이스 전수 검증(회귀 property test).

**Blast radius (K>1 동작 변경 → 결과 개선):** `test_ffc_schedule.py`에서
suboptimal 배치를 고정한 테스트(`termination_on_delta1_zero`,
`floor_vs_ceil_divergence_wide_window`, `ceiling_vs_flooring_diverge*`,
`ceiling_never_stalls*`)는 **exact 최적으로 갱신** 필요. sw_cp/coarsen/dispatcher
등 K>1 결과가 개선되므로 전체 스위트 재실행으로 실측 후 최종 판단.

### ✅ GREEN 완료 (방향 B — coarse-exact block shift)

`ffc_schedule.py:insert_idle_time`의 `K>1` 경로를 **CSR idle-time-insertion**
(`vault/20260713_p3_csr.pdf`)로 교체 (`K==1`은 기존 heuristic 그대로 →
byte-identity 유지, `idle_mode`는 `K>1`에서 무효). 블록별로:

- **Gate (magnitude-aware)**: 한 셀(+K real) 이동 시의 **실제 Δobj**를 계산 —
  earliness 절감(early job이 창 안에 머물면 `K·w⁻`, 창을 벗어나는 셀은 부분항
  `w⁻(d_lo−K·C)`) vs tardiness 증가(이미 tardy면 `K·w⁺`, 새로 넘어가는 셀은
  `w⁺(K(C+1)−d_hi)`). `earliness_saved > tardiness_added`면 shift. **가중치가 아닌
  magnitude 비교**라, 좁은 윈도우(coarsen으로 in-grid 셀이 사라져 한 셀에 early→
  tardy로 건너뛰는 경우)도 정확.
- **Δ1 (jump + unit-step)**: 모든 job이 같은 조각(early/on-time/tardy)에 머무는
  **full-marginal 구간**만큼 점프(`⌊d_lo/K⌋−C` early, `⌊d_hi/K⌋−C` on-time), 창
  경계를 넘는 셀에서는 1칸씩(gate 재평가). `delta2`로 캡.

**설계 반복 (효율성 ↔ 정확성) — 복잡도 측정 + brute-force로 도출:**

1. *후보 열거*: 윈도우 edge의 floor·ceil을 후보로 `block_obj` 최소. 정확하지만
   O(b²)/블록·병합 재평가로 **전체 O(n³)** (slope 3.14, n=800 13.4s).
2. *marginal weight-gate*: `Σw⁻ > Σw⁺`. O(n²)지만 **비정확** — 좁은 윈도우에서
   가중치가 magnitude를 오판(brute 3600 중 510 실패). straddler 처리 실패.
3. *ternary search*: 볼록 `block_obj`를 O(log)회 평가. 정확·O(n²)지만 상수 큼
   (n=800 2.2s).
4. *magnitude gate + jump* (**채택**, PDF 알고리즘): gate가 실제 Δobj라 straddler
   정확, jump는 full-marginal 구간 점프라 O(n²)·상수 작음. **핵심 보정 두 가지:**
   (a) gate를 weight→magnitude(창 진입/이탈 부분항 대칭 반영), (b) Δ1 target을
   `⌈d_lo/K⌉`(창 진입, overshoot)이 아니라 `⌊d_lo/K⌋`(마지막 full-marginal 셀)로
   + 경계 셀은 unit-step. (b)가 없으면 부분 진입 셀에서 marginal이 셀 중간에 바뀌어
   joint optimum을 지나침 (예: `[1,3]`,K=50,j0(179,274)/j1(22,92) → 832 vs 777).

검증:

- RED-1(단위 + 400-case property, 3-mode) / RED-2(통합) 전부 GREEN.
- brute-force **10,000+ 케이스**(`n≤4`, `K∈{1..50}`, zero-width 윈도우 포함)
  **fails=0**. `scratchpad/test_efficient.py`.
- 복잡도(`scratchpad/complexity2.py`): `K==1` slope 2.0, `K>1` slope ~2.0,
  n=800에서 133ms (ternary 2.2s·후보열거 13.4s 대비). K=1과 상수 ~2.7×.
- 전체 스위트 **626 passed**, ruff clean. Blast radius = suboptimal 배치를 고정하던
  `test_ffc_schedule.py` 4개 → exact 최적으로 갱신(2개는 `coarse_exact_modes_agree`
  하나로 통합).
- **E2E:** `main.py`(instance 835, csr_full_wdp) 재실행 → **warning 전무**
  (CpsatAdapter + sw_cp RJ 동시 소멸), `bestObj 149 → 0.0`(bestBound 0.0, 최적 회수).

대상 코드: `src/ffc_ddw_sum_et/solution/ffc_schedule.py`
(`insert_idle_time` K>1 branch + docstring). 방향 A(CpsatOption에 idle_mode
배관)는 **불필요**해짐 — B가 근본 해결이라 CpsatAdapter의 flooring 고정도 무해.

### ✅ Full-grid 검증 (2026-07-14, csr_neh_d2wp × 1440)

E2E(위)는 instance 835 단건이었으므로, TODO 정리 전 **보수적 게이트**로
`csr_neh_d2wp` 시나리오를 **full 1440-instance grid**로 재실행해 warning 전무를
실측 확인했다.

- **Config**: `metadata/20260714/csr_neh_d2wp_full.yaml` (csr_neh_d2wp 단일
  시나리오, `factor: 4`, solver 설정은 `metadata/20260713/csr_init_methods.yaml`의
  동명 시나리오와 동일, plotting만 비활성).
- **Run**: `output/20260714_csr_neh_d2wp_full/20260714T100653_963836/`,
  12 workers, 1440/1440 정상 완료 (~39분, `Total elapsed 2361s`).

**전/후 비교 (동일 조건 = csr_neh_d2wp, 1440 instances):**

| warning | 수정 전 (`20260713T195341_009592`) | 수정 후 (9b7ad2a, 본 run) |
| --- | --- | --- |
| CpsatAdapter `post-process ... > CP-SAT` | **27** | **0** |
| sw_cp `insert_idle_time left E/T on the table` | 0 | 0 |

- **Positive control**: 수정 전 run 195341의 전 시나리오 합 101건, `csr_neh_d2wp`
  하위만 27건을 grep으로 검출 — grep 방법론이 유효(경고 있으면 잡힘)함을 확인한 뒤
  본 run에 적용. (참고로 195341의 다른 CSR 시나리오도 pre-fix warning 보유:
  csr_full_d2wp=29, csr_full_wdp=19, csr_neh_wdp=26; 모두 같은 근본.)
- **본 run 전수 검사**: 전체 `.log`(SingleInstanceRunner + SubroutineController
  각 1440) 대상 `[WARNING]` 라인 0, ERROR/AssertionError/Traceback 0.
- csr_neh_d2wp는 pre-fix에도 sw_cp RJ가 0(195341의 `K*(C+1)` partition fix가 sw_cp
  경로는 이미 처리)이므로, 본 시나리오의 의미 있는 delta는 **CpsatAdapter 27→0**.
  sw_cp RJ 0→0은 무회귀 확인.

**Caveat**: 본 full-grid 검증은 `factor: 4` (K=4) 단일 조건이다. 더 높은
K(8, 16)의 grid 검증은 아직 안 됐다 — 단, 알고리즘 정확성은 9b7ad2a에 딸린
brute-force property test(`test_insert_idle_time_coarse_exact_random_property`,
K∈{1..50}, 10,000+ 케이스 fails=0)가 커버한다.

**TODO 정리 결과** (본 검증 통과 후 실행): `TODO.md`의 idle_mode 관련 6개 중
2개 삭제(`sw_cp incumbent prep should honour option.idle_mode` — idle_mode가 이제
모든 K에서 no-op; `Regression test for the K*(C+1) partition` — partition 교체 +
테스트 이미 shipped), 4개는 `Status (2026-07-14): superseded by 9b7ad2a` 주석 추가
(K>4 미검증이라 삭제 대신 보존).

## 참고

- 자매 진단: `plans/experiment/20260713/sw_cp_rj_warning_investigation.md` (§2 K=1 최적,
  §3 coarse 언더슈트, 방향 A/B).
- TODO 항목: commit `c36fa5e` (idle_mode 배관/lookahead 통합/partition 회귀).
- CSR idle_mode 실험 정당화: `vault/20260702_진행사항_P3.pdf` (p.5-12).
- 논문: `vault/pan_et_al_2017.html` §3.2 (NBM 최적성은 정수 격자 전제).
