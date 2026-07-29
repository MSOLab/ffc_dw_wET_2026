# Active schedule 재구성 — machine assignment 고정 해제 (2단계 순차 실험)

**작성일**: 2026-07-23 · **종류**: 코드 변경 + 실험 실행 계획(사전 작성)
**선행 맥락**: `20260721_csr_init_isw_batch` 배치의 `b30_csr_k1_f30_batch_m` 재실행에서
관측된 평균 obj 미세 악화(+33, +0.038%)는 8-worker wall-clock CP-SAT의 run-to-run
노이즈(±350 탐지 하한)로 판명됨. 본 변경은 그 노이즈보다 큰 **구조적** 효과를
기대하는 알고리즘 개선이다.

> 구현은 실험 1 → 실험 2 순서로, **각각 별도 대화에서** 하나씩 진행한다. 두 변경을
> 한 번에 실험하지 않는다(효과 분리 목적).

---

## 1. 목표

`coarsen_solve_reconstruct`(CSR)와 `make_semi_active`가 공유하는 **machine assignment
고정** 제약을 풀어, 스케줄을 **active schedule**로 다시 만든다.

- **실험 1 (먼저)**: CSR의 **R**(reconstruction)을 semi-active → **active**로 교체.
- **실험 2 (그 다음)**: `FFcSchedule.make_semi_active` 사용처를 **active** 재구성으로 교체.

active schedule 생성 규칙(양쪽 공통):

1. stage를 **맨 앞(stage 1)부터** 순서대로 처리한다.
2. 각 stage 내 operation의 **제1 정렬순위 = 입력 스케줄에서의 operation 시작시각**
   (machine 무관 — 어느 machine에 있었는지는 무시하고 시작시각만 본다).
3. **tie-breaking = `get_due2_weight_pos_job_sequence()` 순서**(그 시퀀스 내 위치 index).
4. 그 순서대로 각 operation을 **가장 빨리 시작 가능한 machine**에 배치한다
   (machine 재배정 허용 = active).

즉 "입력 스케줄이 정한 **operation 순서(시퀀스)는 보존**하되, **machine 배정만
자유롭게 다시** 한다." tie-break(due2-weight-pos)는 입력 시작시각이 같은 operation
사이에서만 작동하는 **2차 신호**다.

---

## 2. 배경 — 왜 machine 고정이 손해인가

### 2.1 두 지점 모두 machine 배정을 얼린다

| 위치 | 파일·함수 | 동작 | machine 배정 |
|---|---|---|---|
| CSR의 R | `solution/schedule_build.py::reconstruct_raw_coarse_schedule` | coarse 스케줄의 machine별 job 순서를 **그대로** 옮기고 fine-scale로 시각만 재계산(`start=max(prev_end, machine_end)`) | **coarse에서 고정** |
| 후처리 정규화 | `solution/ffc_schedule.py::make_semi_active` | 각 machine의 job tuple 순서를 **고정**한 채 left-shift만 수행 | **입력에서 고정** |

`make_semi_active`(ffc_schedule.py:1024): `self.__stage_2_mc_2_job_tuple_seq[stage][mc]`의
job 순서를 유지하며 `start=max(release, machine_available)`로만 당긴다 — machine은 절대
안 바뀐다.

### 2.2 CSR에서 고정되는 machine 배정은 사실 "CP의 결정"이 아니라 greedy 아티팩트다

coarse CP 모델(`algorithm/cumulative.py`, cumulative 제약 기반)은 **machine 배정 변수를
갖지 않는다** — stage별 `AddCumulative(capacity=|M_i|)`로 동시성만 제한한다.
그래서 coarse 해에는 machine identity가 없고, machine 배정은 **`build_schedule_from_op_starts`**
(schedule_build.py:17)가 CP 시작시각을 기준으로 하는 **greedy interval-graph coloring**
(`sorted by (start, end, job)` → `first machine with machine_end<=start`)으로 사후 부여한다.

따라서 R이 "고정"하는 machine 배정은 **coarse-scale coloring 아티팩트**이며,
fine-scale 처리시간(coarsen 시 반올림으로 달라짐)에는 최적이 아니다. R을 active로
바꾸면 이 아티팩트만 버리고 **CP의 진짜 결정(operation 순서)은 §1.2로 보존**한다.

### 2.3 active ⊂ semi-active — 불변식은 깨지지 않는다

active schedule은 정의상 semi-active이기도 하다(더 이상 left-shift 불가). 즉 active로
교체해도 기존 semi-active 불변식(예: `reconstruct_coarse_schedule`이 `make_semi_active`
호출을 생략해도 되는 근거)은 유지된다. active는 machine 배정까지 추가로 개선할 뿐이다.

### 2.4 정직한 트레이드오프

active 재배정은 machine을 **earliest-start greedy**로 고른다 — 이는 makespan 지향이지
E/T 지향이 아니다. 반면 뒤따르는 `insert_idle_time`이 E/T를 위해 우측 이동을 담당한다.
"machine 자유도 확대"가 순이득인지는 **경험적 문제**이므로(특히 E/T 목적함수는 non-regular)
§6의 측정으로 확인한다. 손해 가능성도 열어 둔다.

---

## 3. 재사용할 기존 기계장치

active 규칙은 이미 코드에 있는 primitive로 대부분 조립된다.

- **machine 재배정 dispatch**: `FFcSchedule.dispatch_stage_by_jobs(stage, job_seq, dur,
  job_2_release=None, force_job_id_seq_as_priority=True)` (ffc_schedule.py:506).
  `force_job_id_seq_as_priority=True`면 **주어진 job 순서 그대로** 각 job을
  `add_operation_2_stage`로 배치한다.
- **earliest-start machine 선택**: `add_operation_2_stage`(457) →
  `select_machine_by_earliest_start_then_idle`(256). 선행 stage 완료시각은
  `get_prev_stage_end_time`으로 자동 반영(precedence 보장) → **어떤 순서를 줘도 feasible**.
- **입력 스케줄의 operation 시작시각**: `reference.get_job_sequence(stage, mc)`가
  `(job, start, end)` 튜플을 준다 → machine을 순회해 `ref_start[job]=start` 수집.
- **due2-weight-pos 우선순위**: `instance.get_due2_weight_pos_job_sequence()`
  (ffc_ddw_params.py:774) → `{job: index}` 맵으로 tie-break.

### 3.1 FAM과의 차이(왜 FAM을 그대로 못 쓰나)

`algorithm/fam.py`의 `FAMDispatcher`는 이미 active(First Available Machine) 빌더지만,
stage별 순서를 **`(fine prev-stage 완료시각, slack, initial_pos)`**로 정한다 —
**입력(coarse) 해의 순서를 무시**하는 순수 list-schedule이다. 이를 R에 쓰면 coarse
CP의 해가 통째로 버려져 "coarsen-**solve**-reconstruct"의 solve가 무의미해진다.
본 계획의 빌더는 제1키가 **입력 스케줄 시작시각**이라 CP의 시퀀스를 보존한다는 점이
FAM과 근본적으로 다르다. (FAM은 tie-break 규칙 참고용으로만 유용.)

---

## 4. 공통 코어 — active 재구성 함수

두 실험이 공유할 순수 함수(신규). 위치 후보: `solution/schedule_build.py`
(도메인 solution 계층, 기존 reconstruct 함수와 동거).

```python
def build_active_from_reference(
    reference: FFcSchedule,
    instance: FFcDDWParameters,
    stage_2_job_2_duration: Mapping[str, Mapping[str, int]],
) -> FFcSchedule:
    """reference의 operation 시작시각 순서를 보존하되 machine은 재배정한 active schedule."""
    prio = {j: k for k, j in enumerate(instance.get_due2_weight_pos_job_sequence())}
    new = FFcSchedule(instance.job_id_list, instance.stage_id_list,
                      instance.stage_2_machines_map)
    for stage in instance.stage_id_list:            # 맨 앞 stage부터
        ref_start: dict[str, int] = {}
        for mc in instance.stage_2_machines_map[stage]:
            for j, s, _e in reference.get_job_sequence(stage, mc):
                ref_start[j] = s
        order = sorted(ref_start, key=lambda j: (ref_start[j], prio[j]))  # 제1키 시작시각, tie-break due2wp
        new.dispatch_stage_by_jobs(
            stage, order, stage_2_job_2_duration[stage],
            force_job_id_seq_as_priority=True,      # 순서 보존 + earliest-start machine 재배정
        )
    return new
```

주의:

- precedence는 `add_operation_2_stage` 내부에서 자동 반영되므로 `order`는 순서만 정하고
  실제 시작시각은 재계산된다(입력 시작시각을 그대로 쓰지 않는다).
- `reference`의 시작시각 스케일(coarse든 fine이든) **절대값은 무의미**, 상대 순서만 쓴다.

---

## 5. 실험 1 — CSR의 R을 active로

### 5.1 변경점 (단일 지점)

`algorithm/coarsen_solve_reconstruct.py::run_coarsen_solve_reconstruct` (543–551):

```python
reconstructed_raw_schedule = reconstruct_raw_coarse_schedule(coarse_schedule, instance, factor)
final_schedule            = reconstruct_coarse_schedule(coarse_schedule, instance, factor)
```

를 active 버전으로:

```python
active_raw     = build_active_from_reference(coarse_schedule, instance, instance.stage_2_job_2_p_map)
final_schedule = <active_raw + insert_idle_time>   # reconstruct_coarse_schedule과 동일한 후처리
```

(`stage_2_job_2_p_map[stage][job]` = `dispatch_stage_by_jobs`가 받는 `{job: dur}` 형태.
`reconstruct_raw_coarse_schedule`이 쓰는 `job_2_stage_2_p_map[job][stage]`와 축 순서가 반대이니 주의.)

- 신설 `reconstruct_active_coarse_schedule(coarse_schedule, instance, factor)`:
  `build_active_from_reference` → `insert_idle_time(dw, ewt, twt)` (기존
  `reconstruct_coarse_schedule`과 동일 후처리). **`factor`는 여전히 미사용**(fine p로 재계산).
- **scored 경로는 `final_schedule`** 이므로 최소 변경은 여기만 active로 바꾸는 것.

### 5.2 raw 스냅샷 처리 (확인 필요 — §7 D1)

`reconstructed_raw_schedule`은 trace용 스냅샷이며 채점에 안 쓰인다. 선택:

- **(권장)** raw도 active로 통일(`active_raw`) — trace가 실제 재구성을 반영. active⊂semi-active
  이므로 `test_reconstruct_raw_is_semi_active`(tests/solution/test_schedule_build.py)의
  "semi-active" 성질은 여전히 성립하나, 그 테스트가 **machine 배정 보존**까지 검사한다면
  갱신 필요.
- (대안) raw는 semi-active 그대로 두고 final만 active — 최소 변경이지만 raw와 final의
  machine 배정이 달라져 trace 해석이 헷갈릴 수 있음.

### 5.3 seed-only 모드

`option.solve=False`(seed-only)에서도 `coarse_schedule=seed_schedule`을 reference로
동일하게 active 재구성하면 된다 — 코드 경로 공유(543행 이후는 solve/seed 공통).

### 5.4 대상 scenario 주의 (factor=1)

현행 배치는 `csr_k1`=**factor 1**(coarsen 없음). factor=1이면 coarse=fine scale이라
R의 이득은 §2.2의 **greedy coloring 재배정** 효과에 국한된다(rounding 아티팩트 없음).
효과가 더 큰 곳은 **factor>1(K≥2)**이므로, §6 실험에 K>1 scenario를 1개 이상 포함할 것을
권장한다(현 config의 factor만 바꾼 변형).

---

## 6. 측정 방법 — 노이즈에서 신호 분리

선행 분석 교훈: 8-worker wall-clock CP-SAT 때문에 full-pipeline 최종 obj는 ±350
run-to-run 노이즈를 갖는다. R 교체 효과(deterministic transform)를 노이즈에 묻지 않으려면
**paired deterministic 측정을 1차 지표로** 삼는다.

### 6.1 1차 — paired deterministic micro-benchmark (노이즈 0)

**같은 coarse 해**에 두 재구성을 각각 적용해 `insert_idle_time` 후 obj를 비교한다.
CP 노이즈가 완전히 상쇄된다.

- coarse 해 생성을 결정론적으로: `option.solve=False`(seed-only) 또는 CP를
  `num_workers=1`+고정 seed로 1회 고정.
- 각 인스턴스 i에 대해 `obj_semi(i)` vs `obj_active(i)` (동일 coarse 입력) → paired diff.
- 대규모 인스턴스(PRA2017 1440 전수 또는 대표 grid)에서 paired t-test / 부호검정.
  이게 "노이즈보다 효과가 크다"는 사용자 기대를 **직접** 검증한다.

### 6.2 2차 — full-pipeline 배치 A/B (현실 성능)

`csr_init_isw_batch_3.yaml`와 동일 config로 baseline(semi-active) vs active를 각각 실행,
`meanObjValue`·`RPDf_BKS_data` 비교. 노이즈 포함이므로 **반복 실행(≥3)**으로 분포 비교
권장. 신규 run은 `output/20260723_active_recon_R/`(가칭)에 provenance 커밋.

### 6.3 기대

효과가 있으면 §6.1 paired diff의 평균이 0에서 유의하게 벗어나고, §6.2 배치 평균이
노이즈 밴드(±350)를 초과해 이동한다. 없으면 paired diff≈0 → active 도입 근거 없음(정직히 기록).

---

## 7. 확정된 결정 (2026-07-23 합의)

- **D1. raw 스냅샷 → active로 통일.** `active_raw` 사용. active⊂semi-active이므로
  "semi-active" 성질 테스트는 유지되나, `test_reconstruct_raw_is_semi_active`가 machine
  배정 보존까지 검사하면 갱신.
- **D2. "operation별 시작시각" = 입력(coarse) 해의 시작시각** (§1.2, §3.1 해석 확정).
- **D3. machine 선택 = `select_machine_by_earliest_start_then_idle`** (earliest start → idle) 그대로.
- **D4. 실험 grid에 K>1 scenario 추가** (factor>1 변형 포함).

### 7.1 option화 — active vs semi-active를 config 스위치로

**결론: 실험 1은 쉽고 권장, 실험 2는 가능하나 여러 option type을 관통해야 함.**
option 기본값을 `semi_active`로 두면 **기존 동작 보존 = breaking 아님**이 되고, A/B가 한
config 안 두 scenario로 깔끔해진다(§6.1 paired 측정과 정합).

- **실험 1 (CSR R) — 기존 `coarsen_mode`/`seed_dispatch` 패턴 그대로**:
  1. `CoarsenSolveReconstructOption`에 `reconstruct_mode: Literal["semi_active","active"]
     = "semi_active"` 추가 + `__post_init__` 검증(coarsen_mode 검증과 동일 형태).
  2. 컨트롤러 스텝 `controller.py::coarsen_solve_reconstruct`(현재 2652 부근)에 kwarg
     추가 → 옵션 생성 지점(2742 부근)에 전달. main.py의 step-kwarg 검증은 스텝
     시그니처를 근거로 하므로 kwarg 추가가 곧 allowlist 확장.
  3. `run_coarsen_solve_reconstruct`(543–551)에서 `option.reconstruct_mode`로 분기.
  4. config: 스텝 아래 `reconstruct_mode: active` 한 줄. → **실험 1은 이 방식이면
     branch/파괴 없이 A/B 가능.**

- **실험 2 (make_semi_active) — algorithm-option 계층에서 스위치**:
  - `make_semi_active` 메서드 자체에 mode 플래그를 **넣지 않는다**(SRP: 부분 정규화
    start_from_stage/operation_set 기능을 가진 named primitive는 그대로 둔다).
    `build_active_from_reference`를 형제 primitive로 둔다.
  - post-CP **전체 정규화** 호출부(§9 주 대상)가 속한 각 option type에
    `post_cp_normalize: Literal["semi_active","active"] = "semi_active"`를 추가하고
    dispatcher가 어느 primitive를 부를지 선택. 호출부가 여러 알고리즘 계층(sw_cp,
    neh_cp, cpsat_adapter, controller)에 흩어져 **단일 chokepoint가 없다**는 게 "까다로움"의
    핵심. 정석 해법 두 가지:
    - (a) 각 option type에 필드를 각각 추가(표면 넓지만 기계적), 또는
    - (b) `normalize_post_cp(schedule, instance, mode)` 공용 헬퍼를 신설해 모든 post-CP
      정규화 호출을 그리로 라우팅 → chokepoint를 만들며 동시에 option화(권장 리팩터).
  - 전역 플래그/모듈 상태는 금지(algorithm-principles Rule 8: 동작에 영향 주는 값은
    option에 담아야 함).
  - **breaking 위험은 실제로 실험 2에 있다.** 기본값 semi_active로 gating하면 실험 2도
    비파괴가 되지만, 부분 정규화(swap_* / operation_set / start_from_stage) 호출부는
    active로 못 바꾸므로 **무조건 semi_active 유지** — blanket swap 불가가 실험 1과의 차이.

> 장기 정리: 실험 후 active가 명확히 우세하면 option을 접고(semi_active 제거) dead
> config를 없앤다(YAGNI). 그 전까지는 A/B·롤백을 위해 option 유지.

---

## 8. 테스트 계획

- `build_active_from_reference` 단위테스트:
  - active 성질: 어떤 operation도 (다른 op를 밀지 않고는) 더 못 당김.
  - 순서 보존: 입력 시작시각 순서 + due2wp tie-break가 stage별 dispatch 순서와 일치.
  - machine 재배정이 실제로 일어나는 케이스(입력과 다른 배정) 최소 1개.
  - precedence·no-overlap feasibility (`check_feasibility`).
- `reconstruct_active_coarse_schedule`: 모든 `(job,stage)` 커버(누락 시 raise), factor 무관.
- 회귀: 기존 `reconstruct_*` 테스트는 유지(semi-active 함수는 실험 2까지 존치).
- `uv run ruff format` / `uv run ruff check` / `uv run pytest`.

---

## 9. 실험 2 — make_semi_active 교체 (개요, 별도 대화에서 상세화)

`make_semi_active` 사용처(post-CP 정규화)를 active 재구성으로 교체.

- **주 대상(full-schedule 정규화)**: `algorithm/cpsat_adapter.py:211`,
  `algorithm/sw_cp/dispatcher.py:100,364`, `algorithm/neh_cp/dispatcher.py:214`,
  `orchestration/controller.py:299` — CP 원해를 machine 고정 left-shift로 정규화하던 자리.
  여기서 `schedule.make_semi_active(p)`를 `schedule = build_active_from_reference(schedule, ...)`
  로 대체(뒤따르는 `insert_idle_time`은 유지).
- **제외 대상(부분 정규화)**: `swap_two_operations_within_stage` /
  `swap_stage_machine_operation_sets`의 `do_make_semi_active`, 그리고
  `start_from_stage`·`operation_set`·`job_2_release_map`을 쓰는 부분(partial) 호출.
  active는 **전체 재빌드**라서 부분 정규화 의미와 안 맞음 → 이 자리는 손대지 않는다.
- 측정: §6과 동일(paired deterministic 우선). 실험 1과 **독립적으로** A/B.

---

## 10. 작업 순서

1. (이 문서 합의) §7 D1–D4 확정.
2. **실험 1** — 별도 대화: `build_active_from_reference` +
   `reconstruct_active_coarse_schedule` 구현, `run_coarsen_solve_reconstruct` 교체,
   테스트(§8), §6.1 paired 측정 → 유의하면 §6.2 배치 A/B.
3. **실험 2** — 별도 대화: §9 주 대상 호출부 교체, 테스트, §6 측정.

> 두 실험은 config·run 디렉토리를 분리해 provenance를 남긴다(`docs/CLAUDE.md`의 run
> setting 커밋 규약).
