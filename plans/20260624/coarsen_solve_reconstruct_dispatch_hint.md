# Plan: `coarsen_solve_reconstruct` dispatching 초기해 hint (job-wise vs mixed 비교)

## Context

`coarsen_solve_reconstruct`는 현재 coarsened instance를 만든 뒤 곧바로 base
CP-SAT(`BaseModelBuilder.build`)로 풀고, 그 해를 원척도로 reconstruct한다
(`src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`).

목표: **quantized(coarsened) problem을 풀기 전에 dispatching으로 초기해를 만들어
solver에 warm-start hint로 제공**한다. 이번 실험에서는 **두 가지 seed 전략을
옵션으로 선택**해 그 차이를 비교한다.

- **(1) `job_wise`**: job-wise dispatching 단일 schedule을 seed로 사용.
- **(2) `mixed`**: mixed dispatching이 만드는 head-size 후보들 중, 후보별 idle
  삽입 후 weighted E/T(wET) 최소 schedule을 seed로 선택.

두 전략은 다음을 **공통**으로 한다:

- Dispatch 우선순위(작은 key 먼저):
  1. `d^+_j` 오름차순 (due window upper bound, EDD on latest due)
  2. 동률이면 `w^+_j` 내림차순 (tardiness weight 큰 job 먼저)
  3. 최종 tie-break는 **given job sequence**(`job_id_list` 원래 순서)
- Dispatch 완료 후 **마지막 stage에 idle time insertion** 수행
  (`FFcSchedule.insert_idle_time`, coarsened 척도 due window/weight 사용).
- 선택된 seed schedule을 `apply_hints_from_schedule`로 solve 전에 warm-start.

이전 결정("Option 없이 기본 동작")은 **철회**한다: 두 case를 같은 인스턴스/
timelimit에서 비교하려면 옵션 선택이 필요하다.

### 읽은 코드/문서

- `docs/problem-description.md`: `d^+_j` = due window upper bound, `w^+_j` =
  tardiness weight. 목적함수는 마지막 stage completion 기준
  `sum_j (w^-_j E_j + w^+_j T_j)`.
- `docs/algorithm-principles.md`: 알고리즘 실행 단위는 `AlgSpec -> run ->
  AlgRecord` 계약 내부. controller/reporting concern을 algorithm에 끌어오지 않음.
- `algorithm/coarsen_solve_reconstruct.py`:
  - `run_coarsen_solve_reconstruct` → coarsen → `_solve_coarsened_model` → reconstruct.
  - `_solve_coarsened_model`: `mdl, params, op_vars, _ = builder.build(...)`;
    4번째 반환값(`_`)이 `EarlinessTardinessVars`(hint에 필요).
- `algorithm/cumulative.py`:
  - `BaseModelBuilder.apply_hints_from_schedule(mdl, params, op_vars, et_vars,
    ref_schedule)` → start/end op-time + E/T hint를 한 번에 적용. 내부에서
    `ref_schedule.get_jik_2_{start,end}_time_map()` 사용 → ref_schedule은
    **coarsened scale** `FFcSchedule`.
- `algorithm/dispatcher/mixed.py`, `dispatcher/utils.py`:
  - `MixedDispatcher.get_best_mixed_schedule_by_sequence`는 head 크기 후보
    `np_list`(`_get_np_candidates`: `job_count, ceil/2, ..., 0`)마다 schedule을 만들어
    `criteria`("weighted_et" 기본 / "makespan")로 최적 1개 선택. 점수는
    `self.instance` 기준이므로 dispatcher를 coarsened로 만들면 coarsened wET를 잼.
  - `np=job_count` 후보 = 모든 job을 job-wise로 통과시키는 케이스.
  - `dispatch_job_sequence_by_stages(schedule, seq, job_2_stage_2_p)`(utils): 각 job을
    sequence 순서대로 모든 stage에 통과(pure job-wise). `job_wise` 전략에 사용.
  - `BaseDispatcher._create_empty_schedule(instance)`: 빈 `FFcSchedule` 생성.
- `solution/ffc_schedule.py`:
  - `insert_idle_time(due_window_map, ewt_map, twt_map)`은 docstring상 **마지막
    stage에만** idle을 삽입해 wET를 줄인다.
- `orchestration/controller.py:2490` `coarsen_solve_reconstruct` step method:
  YAML scenario dict의 키(`factor`, `timelimit`, `solver_thread_cnt`,
  `emit_phase_schedules`, ...)가 method kwarg로 전달되어
  `CoarsenSolveReconstructOption(...)`을 구성 → `run_coarsen_solve_reconstruct`.
  새 kwarg를 추가하면 YAML 키로 그대로 선택 가능.
- `TODO.md`: 충돌하는 deferred TODO 없음.

## Shared Design

### S1. Dispatch seed 우선순위 (공통)

```python
def _dispatch_seed_job_sequence(coarsened: FFcDDWParameters) -> list[str]:
    dw_ub = coarsened.job_2_dw_ub_map      # d^+_j
    twt = coarsened.job_2_twt_map          # w^+_j
    given_index = {j: idx for idx, j in enumerate(coarsened.job_id_list)}
    return sorted(
        coarsened.job_id_list,
        key=lambda j: (dw_ub[j], -twt[j], given_index[j]),
    )
```

`(d^+_j ↑, w^+_j ↓, given index ↑)` → 요구 3단 우선순위와 일치.

### S2. Seed 전략 (옵션 분기)

```python
SeedDispatch = Literal["job_wise", "mixed"]

def _build_dispatch_seed_schedule(
    coarsened: FFcDDWParameters, strategy: SeedDispatch
) -> FFcSchedule:
    seq = _dispatch_seed_job_sequence(coarsened)
    dw = coarsened.job_2_due_window_map
    ewt = coarsened.job_2_ewt_map
    twt = coarsened.job_2_twt_map

    if strategy == "job_wise":
        schedule = BaseDispatcher(coarsened)._create_empty_schedule(coarsened)
        dispatch_job_sequence_by_stages(schedule, seq, coarsened.job_2_stage_2_p_map)
        schedule.insert_idle_time(dw, ewt, twt)
        return schedule

    # strategy == "mixed": 후보별 idle 삽입 후 coarsened wET 최소 선택
    dispatcher = MixedDispatcher(coarsened)
    best_obj: float | None = None
    best_sch: FFcSchedule | None = None
    for cand in dispatcher.iter_mixed_schedules_by_sequence(seq):
        cand.insert_idle_time(dw, ewt, twt)
        sum_e, sum_t = compute_weighted_earliness_tardiness(cand, coarsened)
        obj = sum_e + sum_t
        if best_obj is None or obj < best_obj:
            best_obj, best_sch = obj, cand
    if best_sch is None:
        raise RuntimeError(
            f"_build_dispatch_seed_schedule(mixed): no feasible candidate "
            f"for {coarsened.name}."
        )
    return best_sch
```

- **공통**: EDD 시퀀스 → dispatch → 마지막 stage `insert_idle_time` → seed.
- `mixed`의 `np=job_count` 후보가 곧 `job_wise` 후보이므로, mixed는 coarsened wET
  기준으로 job_wise를 약하게 지배한다(비교 시 기대되는 관계 — 검증 항목).
- `make_semi_active`는 호출하지 않는다(dispatch 결과는 이미 active, 요청 범위는
  idle insertion만).
- 빈 schedule 생성은 `BaseDispatcher._create_empty_schedule` 재사용. private이라
  부담되면 `FFcSchedule` 직접 생성.

### S3. Hint 주입 (`_solve_coarsened_model`)

`build` 직후, `solve` 전:

```python
mdl, params, op_vars, et_vars = builder.build(coarsened_instance, horizon=horizon)
seed_schedule = _build_dispatch_seed_schedule(coarsened_instance, seed_dispatch)
BaseModelBuilder.apply_hints_from_schedule(mdl, params, op_vars, et_vars, seed_schedule)
```

- 기존 `_`(4번째 반환값)을 `et_vars`로 수신.
- `_solve_coarsened_model`에 `seed_dispatch: SeedDispatch` 파라미터 추가.
- seed의 coarsened wET를 metrics용으로 반환 튜플에 추가(아래 WP-2 참조).

---

## Work Packages

각 WP는 **단일 파일(또는 단일 신규 파일)** 범위로, 서브에이전트에 개별 위임할 수
있도록 입력/산출/계약/검증을 명시한다. 의존 순서: **WP-1 → WP-2 → WP-3 → WP-4**,
테스트 WP-5는 WP-1·WP-2 완료 후. WP-1과 WP-2는 인터페이스(메서드 시그니처)만
합의되면 병렬 가능.

> 협업 주의: 동일 worktree를 공유하는 서브에이전트는 `git` 명령(checkout/stash
> 등)을 실행하지 않는다(공유 worktree에서의 git 조작은 미커밋 작업을 파괴할 수
> 있음). 각 WP는 자기 파일만 편집한다.

### WP-1 — `src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py`

**목적**: 후보 생성과 선택 정책을 분리해, 호출자가 후보별 idle 삽입 + wET 선택을
할 수 있게 한다.

**변경**:
1. 신규 public 메서드
   `iter_mixed_schedules_by_sequence(job_sequence, *, schedule=None,
   from_stage=None, job_2_release_t=None, machine_then_job=False,
   head_for_all_stages=False, use_palmer_index=False) -> Iterator[FFcSchedule]`:
   `np_list`의 각 후보 schedule을 **점수 매기지 않고** yield. `ValueError`나는
   후보는 skip.
2. np→stage_2_head 분기를 `_stage_2_head_for_np(np, *, from_stage,
   head_for_all_stages)` private 헬퍼로 추출(두 곳 공유). 리팩터 부담 크면 generator
   내부 인라인 허용(우선순위는 동작 보존).
3. `get_best_mixed_schedule_by_sequence`를 `iter_mixed_schedules_by_sequence` 위
   thin wrapper로 축약: generator를 돌며 `criteria`로 best 선택. **동작 불변**.

**계약**: 기존 `get_best_mixed_schedule_by_sequence`의 입출력/선택 결과는 변하지
않는다(회귀 테스트로 보증). 신규 generator는 best 선택 전의 후보들을 그대로 노출.

**검증**: WP-5의 mixed 회귀/후보 테스트.

### WP-2 — `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`

**의존**: WP-1(`iter_mixed_schedules_by_sequence`).

**변경**:
1. `CoarsenSolveReconstructOption`에 필드 추가:
   `seed_dispatch: Literal["job_wise", "mixed"] = "mixed"`(기본값은 Decisions 참조).
2. import 추가: `BaseDispatcher`(`.dispatcher.base`),
   `MixedDispatcher`(`.dispatcher.mixed`),
   `dispatch_job_sequence_by_stages`(`.dispatcher.utils`). `Literal` typing.
3. 신규 헬퍼 `_dispatch_seed_job_sequence`(S1), `_build_dispatch_seed_schedule`(S2).
4. `_solve_coarsened_model`:
   - 시그니처에 `seed_dispatch: SeedDispatch` 추가.
   - `_` → `et_vars` 수신.
   - build 직후 seed 생성 + `apply_hints_from_schedule`(S3).
   - 반환 튜플에 `dispatch_seed_obj`(coarsened wET, `float`) 추가
     — `compute_weighted_earliness_tardiness(seed_schedule, coarsened_instance)`.
5. `run_coarsen_solve_reconstruct`: `_solve_coarsened_model(..., seed_dispatch=
   option.seed_dispatch)` 전달. metrics에 `seed_dispatch`,
   `dispatch_seed_coarsened_obj` 추가(무해/유해 경로 모두).
6. 모듈 docstring 상단에 1문장: "solve 전에 EDD(d^+↑, w^+↓, given) dispatch +
   마지막 stage idle insertion으로 만든 seed를 warm-start hint로 적용하며, seed
   생성 전략은 `seed_dispatch`(`job_wise`/`mixed`)로 선택한다."

**계약**: `_build_dispatch_seed_schedule(coarsened, strategy)` → coarsened scale
feasible `FFcSchedule`. hint는 최종해를 바꾸지 않고 탐색을 가속(동일 timelimit에서
obj 비퇴행).

**검증**: WP-5.

### WP-3 — `src/ffc_ddw_sum_et/orchestration/controller.py`

**의존**: WP-2(option 필드명 `seed_dispatch`).

**변경**: `coarsen_solve_reconstruct` step method(라인 ~2490)에
`seed_dispatch: str = "mixed"` kwarg 추가 → `CoarsenSolveReconstructOption(...,
seed_dispatch=seed_dispatch)`에 전달. docstring에 의미 1줄 추가. 서브루틴 step
계약(단일 `_register`, `elapsed_time` 측정 직전 무작업)은 그대로 유지(변경 없음 —
option 구성만 추가).

**계약**: YAML scenario의 `seed_dispatch: job_wise|mixed` 키가 그대로 method로
흐른다(기존 kwarg 패턴과 동일).

### WP-4 — 실험 config (`metadata/20260624/csr_seed_compare_config.yaml`)

**의존**: WP-3(kwarg 이름).

**변경**: 기존 `coarsen_solve_reconstruct_2_config.yaml` 형식을 따라, **factor·
timelimit·solver_thread_cnt를 동일**하게 고정하고 `seed_dispatch`만 다른 두
scenario를 둔다(seed 효과 격리):

```yaml
scenarios:
  - name: csr10_job_wise
    output_subdir: csr10_job_wise
    subroutine_flow:
      - method: coarsen_solve_reconstruct
        factor: 10
        timelimit: "0.03nc"
        solver_thread_cnt: 8
        seed_dispatch: job_wise
        emit_phase_schedules: true
        draw_cp_trajectory: true
  - name: csr10_mixed
    output_subdir: csr10_mixed
    subroutine_flow:
      - method: coarsen_solve_reconstruct
        factor: 10
        timelimit: "0.03nc"
        solver_thread_cnt: 8
        seed_dispatch: mixed
        emit_phase_schedules: true
        draw_cp_trajectory: true
```

(상단 공통 키 `run_mode`/`benchmark_dir`/`output_dir`/`instance_worker_cnt` 등은
기존 config에서 복사. 필요하면 factor 25/50 쌍도 추가.)

**계약**: 두 scenario의 유일한 차이는 `seed_dispatch`.

### WP-5 — 테스트

**의존**: WP-1, WP-2.

**대상/파일**(기존 테스트 레이아웃에 맞춰 배치):
1. `mixed.py`:
   - 회귀: 작은 instance에서 `get_best_mixed_schedule_by_sequence`(makespan,
     weighted_et) 결과가 리팩터 전후 동일.
   - `iter_mixed_schedules_by_sequence`가 기대 후보 수(중복/ValueError 제외)를 yield.
2. `coarsen_solve_reconstruct.py`:
   - `_dispatch_seed_job_sequence` 순서: (A) `d^+` 상이→오름차순, (B) `d^+` 동률·
     `w^+` 상이→내림차순, (C) 둘 다 동률→given 순서.
   - `_build_dispatch_seed_schedule`: `job_wise`/`mixed` 모두 feasible(precedence/
     machine 충돌 없음), idle 삽입이 마지막 stage 완료시간만 비감소 이동.
   - **비교 관계**: 동일 coarsened instance에서 `mixed`의 seed coarsened wET ≤
     `job_wise`의 seed coarsened wET (mixed가 job_wise 후보를 포함하므로).
   - 통합: hint 적용 후 동일 timelimit에서 최종 obj 비퇴행(<=).
3. `uv run ruff check`, `uv run ruff format`.

---

## 검증 계획 (전체)

1. WP별 단위 테스트(WP-5) red→green.
2. `uv run ruff check` / `uv run ruff format`.
3. WP-4 config로 실제 인스턴스 1개 스폿 실행 → `csrN_job_wise` vs `csrN_mixed`의
   coarsened solve 시간 / reconstructed obj 비교. mixed seed의 coarsened wET가
   job_wise 이하인지, 최종 reconstructed obj 차이가 RQ에 답하는지 확인.

## Decisions

- **`seed_dispatch` 기본값**: `"mixed"`(더 강한 seed). 실험 config는 두 값을
  명시하므로 기본값은 비-config 경로에만 영향. job_wise를 기본 베이스라인으로 두고
  싶으면 `"job_wise"`로 변경 가능 — **결정 필요(경미)**.
- **seed 진단값 metrics 포함**: 포함(`dispatch_seed_coarsened_obj`,
  `seed_dispatch`). 두 case 비교에 직접 필요하므로 YAGNI 예외로 채택.
- **MixedDispatcher 리팩터 vs 콜백**: 후보 생성/선택 분리(WP-1)를 채택. shared
  dispatcher에 use-case 전용 `post_process` 콜백을 남기는 대안은 미채택.
- **dispatch 후보 범위(mixed)**: 기본 파라미터(head at first stage, np_list 전체)만
  사용. `head_for_all_stages`/`machine_then_job`/`use_palmer_index` 변형은 YAGNI.
