# `initialize_by_dispatch_v4` 에 `reconstruct_mode` 옵션 추가

**작성일**: 2026-07-28 · **종류**: 코드 변경 계획(사전 작성) · **상태**:
**구현 완료** (2026-07-28). 설계 대비 이탈 없음; 구현 결과는 §9.
**대상**: `src/ffc_ddw_sum_et/orchestration/controller.py`
(`initialize_by_dispatch_v4` 단일 step 메서드),
`tests/orchestration/test_controller.py`,
`metadata/20260728/dispatch_v4_init_tl.yaml`
**계기**: `metadata/20260728/dispatch_v4_init_tl.yaml` 의 첫 step을
`coarsen_solve_reconstruct(factor=1, solve=false, seed_dispatch=v4)` 로 쓰고
있는데, 이는 "v4 dispatch로 초기 incumbent를 만든다"는 의도에 비해 CSR 래퍼를
빌려 쓰는 우회 경로다. 같은 일을 하는 전용 step `initialize_by_dispatch_v4` 가
이미 있으나 **재구성(reconstruction)을 하지 않아 결과가 다르다**.

---

## 0. 한 문장 정의

> `initialize_by_dispatch_v4` 에 optional keyword `reconstruct_mode` 를 추가해,
> v4 paired dispatch pool이 고른 최소 wET seed에 **active 재구성**을 적용한 뒤
> incumbent로 등록할 수 있게 한다. 기본값 `"none"` 은 현행 동작과 완전히 동일하다.

---

## 1. 문제: 두 경로가 같은 seed에서 다른 스케줄을 낸다

두 경로 모두 `build_v4_paired_dispatch_schedule` 을 같은 기본 priority set
(`V4_PRIORITY_SET` = {wxd2, wspt_twt, wxd7})으로 호출한다. 즉 **dispatch pool은
동일**하다. 차이는 그 뒤에 있다.

| | 경로 A: `initialize_by_dispatch_v4` | 경로 B: `coarsen_solve_reconstruct(factor=1, solve=false)` |
|---|---|---|
| seed 생성 | v4 pool → min-wET | (factor=1 coarsen 후) v4 pool → min-wET |
| 후처리 | **없음** — seed를 그대로 register | `reconstruct_active_except_last_coarse_schedule` — 마지막 stage를 제외한 전 stage를 start-order만 유지하고 machine 재배정, 이후 `insert_idle_time` |
| register obj | pool의 min wET | 재구성 스케줄의 wET |

### 1.1 측정: 재구성은 공짜로 seed를 개선한다

1440-instance PRA2017 large 그리드에서 48칸 간격 30개 instance를 뽑아 두 경로를
직접 실행해 비교했다(재현: 본 문서 §7).

| 항목 | 값 |
|---|---|
| 경로 B가 더 좋음 / 동일 / 더 나쁨 | **23 / 7 / 0** |
| 경로 A의 초과분 (경로 B 대비) | 평균 **2.17 %**, 중앙값 **1.18 %**, 최대 **9.01 %** |

추가 CP solve 없이(재구성은 순수 재계산) 한 방향으로만 개선되며, 30개 중
악화 사례는 없었다. 따라서 **경로 B를 버리고 경로 A로 갈아타면 초기 incumbent가
평균 2 %가량 나빠진다** — 이번 초기화-예산 축소 실험의 출발점이 흔들린다.

### 1.2 목표

경로 A에 재구성 옵션을 붙여 **경로 B와 값이 같으면서** CSR 코드 경로에 의존하지
않는 한 줄짜리 step으로 만든다. factor=1 coarsening은 항등이므로 등가성이
성립해야 하고, 실제로 6개 instance에서 **obj가 완전히 일치**함을 사전
확인했다(§7의 두 번째 스크립트).

---

## 2. 설계

### 2.1 시그니처

```python
def initialize_by_dispatch_v4(
    self,
    priorities: Sequence[DispatchSeqKey] = V4_PRIORITY_SET,
    reconstruct_mode: Literal["none", "active", "active_but_last_semi"] = "none",
) -> SubroutineReport:
```

- `"none"` (기본): 현행 동작. seed를 그대로 register.
- `"active"`: `reconstruct_active_coarse_schedule(seed, instance)`
- `"active_but_last_semi"`: `reconstruct_active_except_last_coarse_schedule(seed, instance)`

두 재구성 함수는 `solution/schedule_build.py` 에 이미 있고 CSR 경로에서 이미
검증·사용 중인 순수 함수다. 새 알고리즘 코드는 없다.

### 2.2 확정된 결정과 그 이유

**(a) 이름/어휘는 CSR의 `reconstruct_mode` 를 그대로 재사용한다.**
같은 개념에 다른 이름을 붙이면 config를 읽는 사람이 둘을 대응시켜야 한다.
CSR의 `"semi_active"` 만 제외한다 — 그것은 coarse 배정을 factor로 되돌리는
경로라 coarsening이 없는 여기서는 의미가 없다. 대신 "재구성 안 함"을 뜻하는
`"none"` 을 추가한다.

**(b) `"active"` 도 함께 넣는다 (YAGNI 대비 트레이드오프 명시).**
당장 필요한 것은 `"active_but_last_semi"` 뿐이다. 그럼에도 넣는 이유: 대응
함수가 이미 존재해 추가 코드가 분기 한 줄이고, CSR의 `reconstruct_mode` 와
어휘를 맞추면서 한 값만 빼면 "왜 여기만 없지"라는 비대칭이 생긴다. 비용은
분기 1개, 이득은 어휘 정합성 + 후속 비교 실험 시 config만으로 전환 가능.

**(c) 재구성은 pool 우승자 1개에만 적용한다.**
`2·|P|` 개 후보를 전부 재구성한 뒤 최소를 고르는 방식이 더 좋을 수 있으나,
(i) 경로 B(CSR)가 우승자만 재구성하므로 등가성 검증이 가능해지고,
(ii) 재구성 비용이 |P|배로 늘어난다. **후속 과제로 미룬다**(§8).

**(d) seed와 재구성 결과 중 좋은 쪽을 고르지 않는다.**
`min(seed, reconstructed)` 로 하면 항상 약우세가 되지만, 측정하려는 대상
(재구성의 효과)이 가려지고 경로 B와 값이 달라진다. 재구성 결과를 무조건
register한다. 악화 가능성은 이론적으로 남지만 30개 표본에서 0건이었고,
routix `SolutionManager.register` 는 어차피 더 좋을 때만 incumbent를 교체한다.

**(e) `initialize_by_dispatch_v3` 은 건드리지 않는다.**
요청 범위 밖이고, 두 메서드의 중복(정렬 pool 빌더만 다름)을 공통 helper로
뽑는 리팩터는 별건이다. v3에도 필요해지면 그때 helper 추출과 함께 한다.

### 2.3 step contract 준수
(`src/ffc_ddw_sum_et/orchestration/AGENTS.md`)

- **register 1회**: 재구성 여부와 무관하게 `_register` 는 정확히 한 번.
- **elapsed 측정**: 재구성은 "실제 작업"이므로 `elapsed = monotonic() - start`
  **앞**에 놓는다. `_register` 와 측정 사이에는 아무 작업도 두지 않는다.
  (진단 로그 `_log_dispatch_seed_diagnostics` 는 현행대로 `_register` **뒤**.)

### 2.4 obj 값

재구성을 하면 pool이 돌려준 `best_obj` 는 무효가 되므로
`compute_weighted_earliness_tardiness(schedule, instance)` 로 다시 계산해
`report.obj_value` 와 `FFcDDWSolution.obj_value` 에 동일하게 싣는다
(routix의 consistency check가 둘을 비교한다).

### 2.5 진단 로그 라벨

`"v4:{label}"` → 재구성 시 `"v4/{reconstruct_mode}:{label}"`.
로그만 보고 어느 경로였는지 구분할 수 있어야 한다.

---

## 3. TDD 순서 (Red → Green)

`tests/orchestration/test_controller.py` 의 기존 `initialize_by_dispatch_v4`
블록에 이어서 추가한다. 기존 3개 테스트(단일 register / min-of-N / 결정성)는
기본값 `"none"` 을 검증하는 회귀 테스트로 그대로 남는다.

1. **`test_initialize_by_dispatch_v4_reconstruct_mode_default_is_none`**
   인자 없이 호출한 값 == `reconstruct_mode="none"` 으로 호출한 값.
   (기본값 변경으로 기존 config가 조용히 달라지는 일을 막는 잠금 테스트)
2. **`test_initialize_by_dispatch_v4_active_but_last_semi_matches_csr_seed_only`**
   ★핵심. `initialize_by_dispatch_v4(reconstruct_mode="active_but_last_semi")`
   의 `obj_value` == `run_coarsen_solve_reconstruct(instance,
   CoarsenSolveReconstructOption(factor=1, coarsen_mode="round", solve=False,
   seed_dispatch="v4", reconstruct_mode="active_but_last_semi"))` 의
   `obj_value`. 경로 B를 이 step으로 대체해도 실험 결과가 바뀌지 않음을 잠근다.
3. **`test_initialize_by_dispatch_v4_reconstruct_registers_consistent_incumbent`**
   재구성 모드에서 history 길이 1, `incumbent.obj_value == report.obj_value`,
   `report.obj_value == wET(incumbent.schedule)`, 모든 (job, stage) 존재.
4. **`test_initialize_by_dispatch_v4_active_matches_reconstruct_helper`**
   `"active"` 가 실제로 다른 경로를 타는지(스케줄 또는 obj가 `"none"` 과
   구분되는지) 확인. 완전 동일한 인스턴스가 나올 수 있으므로, 값 비교가 아니라
   `reconstruct_active_coarse_schedule` 결과의 wET와 일치하는지로 검증한다.
5. **`test_initialize_by_dispatch_v4_rejects_unknown_reconstruct_mode`**
   오타를 `ValueError` 로 즉시 실패시킨다(무시하고 조용히 seed를 쓰지 않는다).

각 테스트는 먼저 실패(Red)를 확인한 뒤 구현한다.

---

## 4. 구현 체크리스트

- [x] `controller.py`: import 추가 — **불필요**. 두 재구성 함수 모두 CSR
      `solve_flow` 경로 때문에 이미 import되어 있었다.
- [x] `initialize_by_dispatch_v4` 에 `reconstruct_mode` 추가 + 분기 + obj 재계산
- [x] docstring 갱신 (§2.2의 결정 (c)(d) 요약 포함)
- [x] 테스트 5개 (§3)
- [x] `uv run ruff check` / `uv run ruff format`
- [x] `uv run pytest tests/` — 800 passed

## 5. config 반영

`metadata/20260728/dispatch_v4_init_tl.yaml` 의 3개 시나리오 첫 step을 교체:

```yaml
      - method: initialize_by_dispatch_v4
        reconstruct_mode: "active_but_last_semi"
```

(기존 8줄 → 2줄. §3-2 테스트가 값 동일성을 보장하므로 실험 의미는 불변.)
scenario 이름/`output_subdir` 는 `dv4_c5init_f{10,20,40}` 로 확정 — `csr` 접두사는
코드 경로가 CSR 이 아니게 되면서 오해를 부른다. `dv4`=v4 dispatch 전치,
`c5init`=3-step 초기화, `f__`=그 초기화 예산 비율.

## 6. 위험 요소

| 위험 | 완화 |
|---|---|
| 재구성이 특정 instance에서 seed보다 나쁨 | 30개 표본 0건. `SolutionManager` 가 더 나쁜 해로 incumbent를 바꾸지 않으므로 **후속 step의 출발점은 손해 보지 않는다**. 다만 register된 report obj는 나빠질 수 있음 → §3-3이 값 정합성만 잠근다. |
| 기본값 변경으로 과거 config 동작이 바뀜 | 기본 `"none"`, §3-1이 잠금 |
| CSR 경로와의 등가성이 코드 변경으로 깨짐 | §3-2가 두 경로를 직접 비교하는 회귀 테스트 |
| stage가 1개인 instance | `build_active_except_last_from_reference` 가 단일 stage를 semi-active로 위임 처리(구현 확인 완료). PRA2017 large는 stage ≥ 2라 실사용 영향 없음 |

## 7. 재현 (§1.1 / §1.2 측정)

두 스크립트 모두 임시 검증용이며 산출물을 남기지 않는다(순수 비교, run 아님).

```bash
# §1.1 30-instance 비교: 경로 A(direct v4) vs 경로 B(CSR factor=1 seed-only)
#   loader = BenchmarkLoader("benchmarks/PRA2017/large",
#                            ins_index_source=".../pra2017_hybrid_match.csv")
#   ins_index=list(range(1, 1441, 48))
#   A: build_v4_paired_dispatch_schedule(ins)[1]
#   B: run_coarsen_solve_reconstruct(ins, CoarsenSolveReconstructOption(
#        factor=1, coarsen_mode="round", reconstruct_mode="active_but_last_semi",
#        seed_dispatch="v4", solve=False, timelimit_sec=None)).obj_value

# §1.2 등가성: 제안 구현(seed → reconstruct_active_except_last_coarse_schedule)
#   과 경로 B의 obj가 6개 instance에서 완전 일치함을 확인
```

구현 후에는 §3-2 테스트가 이 검증을 항구적으로 대체한다.

## 8. 후속 과제 (이번 범위 아님)

- pool의 **모든** 후보를 재구성한 뒤 최소를 고르는 방식과의 비교 (§2.2-c)
- `initialize_by_dispatch_v3` / `_v4` 공통 helper 추출 (§2.2-e)
- `coarsen_solve_reconstruct(factor=1, solve=false)` 를 이 step으로 대체할 수
  있는지 — 과거 run(`output/20260726_csr_init_tl_curve` 등)과의 비교 가능성을
  해치지 않는 선에서만

---

## 9. 구현 결과 (2026-07-28)

설계 대비 이탈 없음. 갈린 지점만 기록한다.

- **검증 순서를 pool 실행보다 앞으로 뺐다.** 계획 §2.3은 "재구성은 elapsed 측정
  앞"만 규정했는데, 오타 검증까지 뒤에 두면 `2·|P|` 회 dispatch를 다 돌린 뒤에야
  실패한다. `reconstruct_mode` 유효성 검사를 `start_elapsed` **이전**에 두어
  fail-fast로 만들었다. 검증 자체는 시간에 잡히지 않는 편이 정확하기도 하다.
- **분기는 if/elif 대신 `{mode: 함수}` 매핑**으로 썼다. 값 추가 시 dict 한 줄만
  늘고, 유효성 검사와 분기가 같은 자료를 공유한다(§2.2-b의 "분기 1개" 비용을
  실제로는 dict 항목 1개로 낮춤).
- **테스트 5개 모두 Red 확인 후 구현.** 최종 `uv run pytest tests/` → 800 passed.
- **실제 instance 등가성 재확인**: 1440 그리드에서 144칸 간격 10개 instance로
  ① 새 step(`reconstruct_mode="active_but_last_semi"`) ② 경로 B(CSR factor=1)
  ③ `"none"` 세 값을 비교 — ①과 ②는 **10/10 완전 일치**, ③은 그보다 나쁘거나
  같음(§1.1과 동일 경향).

### config 반영 결과

`metadata/20260728/dispatch_v4_init_tl.yaml` 의 3개 시나리오 첫 step을 8줄
CSR 블록 → 2줄로 교체했고, 헤더 주석에 "이전 CSR 경로와 값이 동일하며 근거는
이 문서"라는 사실을 남겼다. scenario 이름은 §5대로 유지.
